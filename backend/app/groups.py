from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Course, CourseModule, Membership, Module, ModulePermission, User
from .security import create_session_token, read_session, require_teacher
from .settings import get_settings


router = APIRouter(prefix="/api/groups")
settings = get_settings()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_join_code() -> str:
    return secrets.token_urlsafe(7).replace("-", "").replace("_", "")[:10].upper()


def _group_teacher(db: Session, group_id: int, user_id: int) -> tuple[Course, Membership]:
    group = db.get(Course, group_id)
    if not group or not group.active:
        raise HTTPException(status_code=404, detail="Group not found")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == group_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership or membership.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Teacher membership required for this group")
    return group, membership


def _group_owner(db: Session, group_id: int, user_id: int) -> Course:
    group, _ = _group_teacher(db, group_id, user_id)
    if group.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Group owner permission required")
    return group


def _unique_join_code(db: Session) -> str:
    for _ in range(20):
        code = _new_join_code()
        if not db.scalar(select(Course.id).where(Course.join_code == code)):
            return code
    raise HTTPException(status_code=500, detail="Could not allocate a group join code")


def _module_access(db: Session, module_id: int, user_id: int) -> Module:
    module = db.get(Module, module_id)
    if not module or not module.active:
        raise HTTPException(status_code=404, detail="Module not found")
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if not permission or permission.permission not in {"owner", "editor", "viewer"}:
        raise HTTPException(status_code=403, detail="You do not have access to this module")
    return module


class GroupCreate(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    label: str | None = Field(default=None, max_length=120)
    description: str = ""
    academic_year: str | None = Field(default=None, max_length=40)
    settings: dict = {}


class JoinGroupIn(BaseModel):
    code: str = Field(min_length=4, max_length=40)


class GroupUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    label: str | None = Field(default=None, max_length=120)
    description: str | None = None
    academic_year: str | None = Field(default=None, max_length=40)
    settings: dict | None = None


class MemberCreate(BaseModel):
    user_id: int | None = None
    display_name: str | None = Field(default=None, max_length=250)
    email: str | None = Field(default=None, max_length=320)
    role: str = Field(default="student", pattern=r"^(student|teacher)$")


class AssignModuleIn(BaseModel):
    settings: dict = {}


@router.post("")
def create_group(
    payload: GroupCreate,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    teacher = db.get(User, teacher_id)
    if not teacher:
        raise HTTPException(status_code=401, detail="Teacher user not found")

    context_id = "local:" + secrets.token_urlsafe(20)
    group = Course(
        owner_user_id=teacher_id,
        platform_issuer="local://lms-administracion-gestion",
        context_id=context_id,
        source_type="local",
        title=payload.title,
        label=payload.label,
        description=payload.description,
        academic_year=payload.academic_year,
        join_code=_unique_join_code(db),
        settings_json={
            "allow_self_enrol": False,
            "show_scores": True,
            "max_attempts_default": 0,
            **(payload.settings or {}),
        },
    )
    db.add(group)
    db.flush()
    db.add(
        Membership(
            course_id=group.id,
            user_id=teacher_id,
            role="teacher",
            lti_roles=[],
            active=True,
        )
    )
    db.commit()
    db.refresh(group)
    return _group_dict(group, "teacher", owned=True)


@router.get("")
def my_groups(
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    rows = db.execute(
        select(Course, Membership)
        .join(Membership, Membership.course_id == Course.id)
        .where(
            Membership.user_id == user_id,
            Membership.active.is_(True),
            Membership.role.in_(["teacher", "admin"]),
            Course.active.is_(True),
        )
        .order_by(Course.academic_year.desc(), Course.title)
    ).all()
    return [
        _group_dict(group, membership.role, owned=(group.owner_user_id == user_id))
        for group, membership in rows
    ]




@router.post("/join")
def join_group(
    payload: JoinGroupIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    if not db.get(User, user_id):
        raise HTTPException(status_code=401, detail="User not found")
    code = payload.code.strip().upper()
    group = db.scalar(
        select(Course).where(
            Course.join_code == code,
            Course.active.is_(True),
            Course.source_type == "local",
        )
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group code not found")
    settings_json = group.settings_json or {}
    if not settings_json.get("allow_self_enrol", False):
        raise HTTPException(status_code=403, detail="Self-enrolment is disabled for this group")

    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == group.id,
            Membership.user_id == user_id,
        )
    )
    if membership:
        membership.active = True
        if membership.role not in {"teacher", "admin"}:
            membership.role = "student"
        membership.updated_at = _now()
    else:
        membership = Membership(
            course_id=group.id,
            user_id=user_id,
            role="student",
            lti_roles=[],
            active=True,
        )
        db.add(membership)
    db.commit()
    return {
        "ok": True,
        "group_id": group.id,
        "title": group.title,
        "role": membership.role,
    }


@router.delete("/{group_id}")
def close_group(
    group_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    group = _group_owner(db, group_id, user_id)
    if group.source_type != "local":
        raise HTTPException(
            status_code=409,
            detail="CAMPUS groups are managed from the LTI platform and cannot be deleted here",
        )
    group.active = False
    db.commit()
    return {"ok": True, "group_id": group_id}


@router.get("/{group_id}")
def group_detail(
    group_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    group, membership = _group_teacher(db, group_id, user_id)
    result = _group_dict(group, membership.role, owned=(group.owner_user_id == user_id))
    result["member_count"] = db.scalar(
        select(__import__("sqlalchemy").func.count(Membership.id)).where(
            Membership.course_id == group_id,
            Membership.active.is_(True),
        )
    ) or 0
    result["module_count"] = db.scalar(
        select(__import__("sqlalchemy").func.count(CourseModule.id)).where(
            CourseModule.course_id == group_id,
            CourseModule.active.is_(True),
        )
    ) or 0
    return result


@router.patch("/{group_id}")
def update_group(
    group_id: int,
    payload: GroupUpdate,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    group, membership = _group_teacher(db, group_id, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        group.title = changes["title"]
    if "label" in changes:
        group.label = changes["label"]
    if "description" in changes:
        group.description = changes["description"] or ""
    if "academic_year" in changes:
        group.academic_year = changes["academic_year"]
    if "settings" in changes:
        group.settings_json = changes["settings"] or {}
    db.commit()
    return _group_dict(group, membership.role, owned=(group.owner_user_id == user_id))


@router.post("/{group_id}/rotate-join-code")
def rotate_join_code(
    group_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    group = _group_owner(db, group_id, user_id)
    group.join_code = _unique_join_code(db)
    db.commit()
    return {"group_id": group.id, "join_code": group.join_code}


@router.get("/{group_id}/members")
def list_members(
    group_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    _group_teacher(db, group_id, user_id)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.course_id == group_id, Membership.active.is_(True))
        .order_by(Membership.role.desc(), User.display_name)
    ).all()
    return [
        {
            "user_id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "role": membership.role,
        }
        for membership, user in rows
    ]


def _resolve_member_user(db: Session, payload: MemberCreate) -> User:
    if payload.user_id is not None:
        user = db.get(User, payload.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    email = (payload.email or "").strip().lower()
    display_name = (payload.display_name or "").strip()
    if not email and not display_name:
        raise HTTPException(status_code=400, detail="Provide user_id, email or display_name")

    user = None
    if email:
        user = db.scalar(select(User).where(__import__("sqlalchemy").func.lower(User.email) == email))
    if user:
        if display_name:
            user.display_name = display_name
        return user

    user = User(
        display_name=display_name or email,
        email=email or None,
        active=True,
    )
    db.add(user)
    db.flush()
    return user


@router.post("/{group_id}/members")
def add_member(
    group_id: int,
    payload: MemberCreate,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    group, _ = _group_teacher(db, group_id, teacher_id)
    if payload.role == "teacher" and group.owner_user_id != teacher_id:
        raise HTTPException(status_code=403, detail="Only the group owner can add co-teachers")

    user = _resolve_member_user(db, payload)
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == group_id,
            Membership.user_id == user.id,
        )
    )
    if membership:
        membership.role = payload.role
        membership.active = True
        membership.updated_at = _now()
    else:
        membership = Membership(
            course_id=group_id,
            user_id=user.id,
            role=payload.role,
            lti_roles=[],
            active=True,
        )
        db.add(membership)
    db.commit()
    return {
        "group_id": group_id,
        "user_id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "role": membership.role,
    }


@router.delete("/{group_id}/members/{user_id}")
def remove_member(
    group_id: int,
    user_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    group, _ = _group_teacher(db, group_id, teacher_id)
    if group.owner_user_id == user_id:
        raise HTTPException(status_code=409, detail="The group owner cannot be removed")

    target = db.scalar(
        select(Membership).where(
            Membership.course_id == group_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not target:
        raise HTTPException(status_code=404, detail="Group member not found")
    if target.role in {"teacher", "admin"} and group.owner_user_id != teacher_id:
        raise HTTPException(status_code=403, detail="Only the group owner can remove co-teachers")
    target.active = False
    target.updated_at = _now()
    db.commit()
    return {"ok": True}


@router.post("/{group_id}/members/import-csv")
async def import_members_csv(
    group_id: int,
    file: UploadFile = File(...),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    group, _ = _group_teacher(db, group_id, teacher_id)
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV roster exceeds 2 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header")
    normalized = {name.strip().lower(): name for name in reader.fieldnames}
    email_col = normalized.get("email") or normalized.get("correo")
    name_col = (
        normalized.get("display_name")
        or normalized.get("nombre")
        or normalized.get("name")
        or normalized.get("alumno")
    )
    role_col = normalized.get("role") or normalized.get("rol")
    if not email_col and not name_col:
        raise HTTPException(
            status_code=400,
            detail="CSV needs email/correo or nombre/display_name column",
        )

    imported = 0
    skipped = 0
    for row in reader:
        role = str(row.get(role_col, "student") if role_col else "student").strip().lower()
        if role not in {"student", "teacher"}:
            role = "student"
        if role == "teacher" and group.owner_user_id != teacher_id:
            skipped += 1
            continue
        payload = MemberCreate(
            email=(row.get(email_col) or "").strip() if email_col else None,
            display_name=(row.get(name_col) or "").strip() if name_col else None,
            role=role,
        )
        if not (payload.email or payload.display_name):
            skipped += 1
            continue
        user = _resolve_member_user(db, payload)
        membership = db.scalar(
            select(Membership).where(
                Membership.course_id == group_id,
                Membership.user_id == user.id,
            )
        )
        if membership:
            membership.role = role
            membership.active = True
            membership.updated_at = _now()
        else:
            db.add(
                Membership(
                    course_id=group_id,
                    user_id=user.id,
                    role=role,
                    lti_roles=[],
                    active=True,
                )
            )
        imported += 1
    db.commit()
    return {"group_id": group_id, "imported": imported, "skipped": skipped}


@router.post("/{group_id}/modules/{module_id}")
def assign_module(
    group_id: int,
    module_id: int,
    payload: AssignModuleIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    _group_teacher(db, group_id, teacher_id)
    _module_access(db, module_id, teacher_id)
    row = db.scalar(
        select(CourseModule).where(
            CourseModule.course_id == group_id,
            CourseModule.module_id == module_id,
        )
    )
    if row:
        row.active = True
        row.settings_json = payload.settings
    else:
        row = CourseModule(
            course_id=group_id,
            module_id=module_id,
            settings_json=payload.settings,
            active=True,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"course_module_id": row.id, "group_id": group_id, "module_id": module_id}


@router.delete("/{group_id}/modules/{module_id}")
def unassign_module(
    group_id: int,
    module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    _group_teacher(db, group_id, teacher_id)
    row = db.scalar(
        select(CourseModule).where(
            CourseModule.course_id == group_id,
            CourseModule.module_id == module_id,
            CourseModule.active.is_(True),
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Module is not assigned to this group")
    row.active = False
    db.commit()
    return {"ok": True}


def _group_dict(group: Course, role: str, owned: bool) -> dict:
    return {
        "id": group.id,
        "title": group.title,
        "label": group.label,
        "description": group.description,
        "academic_year": group.academic_year,
        "source_type": group.source_type,
        "join_code": group.join_code if owned else None,
        "settings": group.settings_json or {},
        "role": role,
        "owned": owned,
    }


@router.post("/{group_id}/activate")
def activate_group(
    group_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
):
    user_id = int(session["sub"])
    group = db.get(Course, group_id)
    if not group or not group.active:
        raise HTTPException(status_code=404, detail="Group not found")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == group_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="You are not enrolled in this group")
    token = create_session_token(user_id, group_id, membership.role)
    response = JSONResponse(
        {
            "ok": True,
            "group_id": group_id,
            "title": group.title,
            "role": membership.role,
        }
    )
    response.set_cookie(
        "lms_session",
        token,
        httponly=True,
        secure=settings.base_url.startswith("https://"),
        samesite="none" if settings.base_url.startswith("https://") else "lax",
        max_age=12 * 60 * 60,
    )
    return response


@router.get("/{group_id}/modules")
def group_modules(
    group_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    teacher_id = int(session["sub"])
    _group_teacher(db, group_id, teacher_id)
    rows = db.execute(
        select(CourseModule, Module)
        .join(Module, Module.id == CourseModule.module_id)
        .where(
            CourseModule.course_id == group_id,
            CourseModule.active.is_(True),
            Module.active.is_(True),
        )
        .order_by(Module.title)
    ).all()
    return [
        {
            "course_module_id": course_module.id,
            "module_id": module.id,
            "slug": module.slug,
            "code": module.code,
            "title": module.title,
            "version": module.version,
            "settings": course_module.settings_json or {},
        }
        for course_module, module in rows
    ]
