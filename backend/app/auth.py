from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Course, LocalCredential, Membership, ModulePermission, User
from .security import create_session_token, read_session
from .settings import get_settings


router = APIRouter(prefix="/api/auth")
settings = get_settings()

PBKDF2_ITERATIONS = 600_000
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


class LoginIn(BaseModel):
    login: str = Field(min_length=2, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class LocalCredentialIn(BaseModel):
    login: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=1024)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _normalize_login(value: str) -> str:
    return value.strip().casefold()


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return (
        "pbkdf2_sha256$"
        + str(PBKDF2_ITERATIONS)
        + "$"
        + base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
        + "$"
        + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    )


def _decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = _decode_b64(salt_raw)
        expected = _decode_b64(digest_raw)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def _memberships(db: Session, user_id: int) -> list[tuple[Membership, Course]]:
    return list(
        db.execute(
            select(Membership, Course)
            .join(Course, Course.id == Membership.course_id)
            .where(
                Membership.user_id == user_id,
                Membership.active.is_(True),
                Course.active.is_(True),
            )
            .order_by(Course.title)
        ).all()
    )


def _global_role(db: Session, user_id: int, memberships: list[tuple[Membership, Course]]) -> str:
    roles = {membership.role for membership, _ in memberships}
    if "admin" in roles:
        return "admin"
    if "teacher" in roles:
        return "teacher"
    if db.scalar(select(ModulePermission.id).where(ModulePermission.user_id == user_id).limit(1)):
        return "teacher"
    return "student"


def _course_payload(rows: list[tuple[Membership, Course]]) -> list[dict]:
    return [
        {
            "id": course.id,
            "title": course.title,
            "label": course.label,
            "source_type": course.source_type,
            "role": membership.role,
        }
        for membership, course in rows
    ]


def _same_origin(request: Request) -> None:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and origin != settings.base_url:
        raise HTTPException(status_code=403, detail="Cross-site request rejected")


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    normalized = _normalize_login(payload.login)
    credential = db.scalar(
        select(LocalCredential).where(LocalCredential.login_normalized == normalized)
    )
    invalid = HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    if not credential:
        raise invalid

    now = _now()
    locked_until = _aware(credential.locked_until)
    if locked_until and locked_until > now:
        raise HTTPException(
            status_code=429,
            detail="Acceso temporalmente bloqueado por varios intentos fallidos",
        )

    user = db.get(User, credential.user_id)
    if not user or not user.active or not _verify_password(payload.password, credential.password_hash):
        credential.failed_attempts = int(credential.failed_attempts or 0) + 1
        if credential.failed_attempts >= MAX_FAILED_ATTEMPTS:
            credential.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            credential.failed_attempts = 0
        db.commit()
        raise invalid

    credential.failed_attempts = 0
    credential.locked_until = None
    credential.last_login_at = now
    db.commit()

    rows = _memberships(db, user.id)
    role = _global_role(db, user.id, rows)
    course_id = rows[0][1].id if len(rows) == 1 else None
    if course_id is not None:
        role = rows[0][0].role

    token = create_session_token(user.id, course_id, role)
    response = JSONResponse(
        {
            "ok": True,
            "user": {"id": user.id, "display_name": user.display_name, "email": user.email},
            "role": role,
            "course_id": course_id,
            "courses": _course_payload(rows),
        }
    )
    response.set_cookie(
        "lms_session",
        token,
        httponly=True,
        secure=settings.base_url.startswith("https://"),
        samesite="lax",
        max_age=12 * 60 * 60,
    )
    return response


@router.post("/logout")
def logout(request: Request):
    _same_origin(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie("lms_session")
    return response


@router.get("/courses")
def courses(
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> list[dict]:
    return _course_payload(_memberships(db, int(session["sub"])))


@router.get("/local-credential")
def local_credential_status(
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    row = db.scalar(
        select(LocalCredential).where(LocalCredential.user_id == user_id)
    )
    return {
        "configured": row is not None,
        "login": row.login_display if row else "",
    }


@router.post("/local-credential")
def set_local_credential(
    payload: LocalCredentialIn,
    request: Request,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    _same_origin(request)
    user_id = int(session["sub"])
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Usuario no disponible")

    login_display = payload.login.strip()
    normalized = _normalize_login(login_display)
    if not normalized:
        raise HTTPException(status_code=400, detail="El usuario de acceso no puede estar vacío")

    collision = db.scalar(
        select(LocalCredential).where(
            LocalCredential.login_normalized == normalized,
            LocalCredential.user_id != user_id,
        )
    )
    if collision:
        raise HTTPException(status_code=409, detail="Ese usuario de acceso ya está en uso")

    row = db.scalar(select(LocalCredential).where(LocalCredential.user_id == user_id))
    if row:
        row.login_display = login_display
        row.login_normalized = normalized
        row.password_hash = _hash_password(payload.password)
        row.failed_attempts = 0
        row.locked_until = None
        row.updated_at = _now()
    else:
        row = LocalCredential(
            user_id=user_id,
            login_display=login_display,
            login_normalized=normalized,
            password_hash=_hash_password(payload.password),
        )
        db.add(row)

    db.commit()
    return {"ok": True, "configured": True, "login": login_display}
