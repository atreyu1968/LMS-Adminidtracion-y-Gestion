import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from .db import get_db
from .models import Course, CourseModule, Membership, Module, ModulePermission, User
from .security import read_session, require_admin, require_teacher
from .version import __version__


router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"ok": True, "service": "lms-administracion-gestion", "version": __version__, "phase": "release-candidate"}


@router.get("/me")
def me(session: dict = Depends(read_session), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, int(session["sub"]))
    course = db.get(Course, session.get("course_id")) if session.get("course_id") else None
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return {
        "user": {
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
        },
        "course": (
            {
                "id": course.id,
                "title": course.title,
                "label": course.label,
                "context_id": course.context_id,
            }
            if course
            else None
        ),
        "role": session.get("role"),
    }


@router.get("/modules")
def modules(session: dict = Depends(read_session), db: Session = Depends(get_db)) -> list[dict]:
    user_id = int(session["sub"])
    role = session.get("role")
    course_id = int(session.get("course_id") or 0) or None

    if role in {"teacher", "admin"}:
        permitted_ids = select(ModulePermission.module_id).where(
            ModulePermission.user_id == user_id
        )
        assigned_ids = (
            select(CourseModule.module_id).where(
                CourseModule.course_id == course_id,
                CourseModule.active.is_(True),
            )
            if course_id
            else select(CourseModule.module_id).where(False)
        )
        stmt = select(Module).where(
            Module.active.is_(True),
            Module.id.in_(permitted_ids.union(assigned_ids)),
        )
    else:
        if not course_id:
            return []
        stmt = (
            select(Module)
            .join(CourseModule, CourseModule.module_id == Module.id)
            .where(
                CourseModule.course_id == course_id,
                CourseModule.active.is_(True),
                Module.active.is_(True),
            )
        )

    rows = list(db.scalars(stmt.order_by(Module.title)))
    return [
        {
            "id": row.id,
            "slug": row.slug,
            "code": row.code,
            "title": row.title,
            "description": row.description,
            "module_type": row.module_type,
            "version": row.version,
            "course_module_id": (
                db.scalar(
                    select(CourseModule.id).where(
                        CourseModule.course_id == course_id,
                        CourseModule.module_id == row.id,
                        CourseModule.active.is_(True),
                    )
                )
                if course_id
                else None
            ),
        }
        for row in rows
    ]


@router.get("/modules/mine")
def my_modules(
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    rows = db.execute(
        select(Module, ModulePermission)
        .join(ModulePermission, ModulePermission.module_id == Module.id)
        .where(
            ModulePermission.user_id == user_id,
            Module.active.is_(True),
        )
        .order_by(Module.title)
    ).all()
    return [
        {
            "id": module.id,
            "slug": module.slug,
            "code": module.code,
            "title": module.title,
            "description": module.description,
            "module_type": module.module_type,
            "version": module.version,
            "permission": permission.permission,
        }
        for module, permission in rows
    ]


@router.post("/admin/modules/seed-gth", dependencies=[Depends(require_admin)])
def seed_gth(db: Session = Depends(get_db)) -> dict:
    module = db.scalar(select(Module).where(Module.slug == "gth-0652"))
    if not module:
        module = Module(
            slug="gth-0652",
            code="0652",
            title="Gestión de Recursos Humanos",
            description="Primer módulo migrado desde CFGSAF/grh0652.",
            module_type="native+scorm",
            version="migration-0",
            metadata_json={
                "source_repository": "atreyu1968/CFGSAF",
                "source_path": "grh0652",
                "learning_results": 4,
            },
        )
        db.add(module)
        db.commit()
        db.refresh(module)
    return {"id": module.id, "slug": module.slug}


class ModuleCreate(BaseModel):
    slug: str | None = Field(
        default=None,
        min_length=2,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    code: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=2, max_length=300)
    description: str = ""
    module_type: str = "scorm"


class ModuleUpdate(BaseModel):
    code: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, min_length=2, max_length=300)
    description: str | None = None


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (slug or "modulo")[:100]


def _unique_module_slug(db: Session, desired: str, user_id: int) -> str:
    base = _slugify(desired)
    candidates = [base, f"{base}-u{user_id}"]
    candidates.extend(f"{base}-u{user_id}-{n}" for n in range(2, 100))
    for candidate in candidates:
        if not db.scalar(select(Module.id).where(Module.slug == candidate)):
            return candidate
    raise HTTPException(status_code=409, detail="Could not allocate a unique module identifier")


def _teacher_membership(db: Session, course_id: int, user_id: int) -> Membership:
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership or membership.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Teacher membership required for this course")
    return membership


def _module_editor(db: Session, module_id: int, user_id: int) -> ModulePermission:
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if not permission or permission.permission not in {"owner", "editor"}:
        raise HTTPException(status_code=403, detail="Module edit permission required")
    return permission


@router.post("/modules")
def create_module(
    payload: ModuleCreate,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    slug = _unique_module_slug(db, payload.slug or payload.title, user_id)
    module = Module(
        slug=slug,
        code=payload.code,
        title=payload.title,
        description=payload.description,
        module_type=payload.module_type,
        version="0.1.0",
    )
    db.add(module)
    db.flush()
    db.add(ModulePermission(module_id=module.id, user_id=user_id, permission="owner"))
    db.commit()
    db.refresh(module)
    return {"id": module.id, "slug": module.slug, "title": module.title}


@router.patch("/modules/{module_id}")
def update_module(
    module_id: int,
    payload: ModuleUpdate,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _module_editor(db, module_id, user_id)
    module = db.get(Module, module_id)
    if not module or not module.active:
        raise HTTPException(status_code=404, detail="Module not found")

    changes = payload.model_dump(exclude_unset=True)
    if "code" in changes:
        module.code = changes["code"]
    if "title" in changes and changes["title"] is not None:
        module.title = changes["title"]
    if "description" in changes:
        module.description = changes["description"] or ""
    db.commit()
    return {
        "id": module.id,
        "slug": module.slug,
        "code": module.code,
        "title": module.title,
        "description": module.description,
        "version": module.version,
    }


@router.post("/courses/{course_id}/modules/{module_id}")
def attach_module(
    course_id: int,
    module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _teacher_membership(db, course_id, user_id)
    _module_editor(db, module_id, user_id)
    if not db.get(Module, module_id):
        raise HTTPException(status_code=404, detail="Module not found")
    existing = db.scalar(
        select(CourseModule).where(
            CourseModule.course_id == course_id,
            CourseModule.module_id == module_id,
        )
    )
    if existing:
        existing.active = True
        db.commit()
        return {"id": existing.id, "course_id": course_id, "module_id": module_id}
    row = CourseModule(course_id=course_id, module_id=module_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "course_id": course_id, "module_id": module_id}


@router.get("/courses/{course_id}/modules")
def course_modules(
    course_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> list[dict]:
    if int(session.get("course_id") or 0) != course_id:
        raise HTTPException(status_code=403, detail="Course context mismatch")
    user_id = int(session["sub"])
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="Not enrolled in this course")
    rows = db.execute(
        select(CourseModule, Module)
        .join(Module, Module.id == CourseModule.module_id)
        .where(CourseModule.course_id == course_id, CourseModule.active.is_(True))
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
        }
        for course_module, module in rows
    ]


class ModulePermissionGrant(BaseModel):
    user_id: int
    permission: str = Field(default="editor", pattern=r"^(owner|editor|viewer)$")


def _module_owner(db: Session, module_id: int, user_id: int) -> ModulePermission:
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
            ModulePermission.permission == "owner",
        )
    )
    if not permission:
        raise HTTPException(status_code=403, detail="Module owner permission required")
    return permission


@router.get("/modules/{module_id}/permissions")
def module_permissions(
    module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    _module_editor(db, module_id, int(session["sub"]))
    rows = db.execute(
        select(ModulePermission, User)
        .join(User, User.id == ModulePermission.user_id)
        .where(ModulePermission.module_id == module_id)
        .order_by(User.display_name)
    ).all()
    return [
        {
            "user_id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "permission": permission.permission,
        }
        for permission, user in rows
    ]


@router.post("/modules/{module_id}/permissions")
def grant_module_permission(
    module_id: int,
    payload: ModulePermissionGrant,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    owner_id = int(session["sub"])
    _module_owner(db, module_id, owner_id)
    if not db.get(User, payload.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    row = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == payload.user_id,
        )
    )
    if row:
        row.permission = payload.permission
    else:
        row = ModulePermission(
            module_id=module_id,
            user_id=payload.user_id,
            permission=payload.permission,
        )
        db.add(row)
    db.commit()
    return {
        "module_id": module_id,
        "user_id": payload.user_id,
        "permission": payload.permission,
    }


@router.delete("/modules/{module_id}/permissions/{user_id}")
def revoke_module_permission(
    module_id: int,
    user_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    owner_id = int(session["sub"])
    _module_owner(db, module_id, owner_id)
    if user_id == owner_id:
        raise HTTPException(status_code=409, detail="An owner cannot revoke their own ownership here")
    row = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Module permission not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post(
    "/admin/modules/{module_id}/owners/{user_id}",
    dependencies=[Depends(require_admin)],
)
def admin_grant_module_owner(
    module_id: int,
    user_id: int,
    db: Session = Depends(get_db),
) -> dict:
    if not db.get(Module, module_id):
        raise HTTPException(status_code=404, detail="Module not found")
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    row = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if row:
        row.permission = "owner"
    else:
        db.add(ModulePermission(module_id=module_id, user_id=user_id, permission="owner"))
    db.commit()
    return {"module_id": module_id, "user_id": user_id, "permission": "owner"}
