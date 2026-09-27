from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, Header, HTTPException

from .settings import get_settings


settings = get_settings()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Admin token required")


def create_session_token(user_id: int, course_id: int | None, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "course_id": course_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=12)).timestamp()),
    }
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


def read_session(lms_session: str | None = Cookie(default=None)) -> dict:
    if not lms_session:
        raise HTTPException(status_code=401, detail="No active LMS session")
    try:
        return jwt.decode(lms_session, settings.session_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid LMS session") from exc


def require_teacher(session: dict = Depends(read_session)) -> dict:
    if session.get("role") not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Teacher role required")
    return session


def create_scorm_token(registration_id: int, user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "scorm",
        "registration_id": registration_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=12)).timestamp()),
    }
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


def read_scorm_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid SCORM launch token") from exc
    if payload.get("type") != "scorm":
        raise HTTPException(status_code=401, detail="Invalid SCORM token type")
    return payload
