from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Course, CourseModule, Membership, Module, ModulePermission, User
from .security import read_session, require_admin, require_teacher


router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"ok": True, "service": "lms-administracion-gestion", "phase": "foundation"}


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
    rows = db.scalars(select(Module).where(Module.active.is_(True)).order_by(Module.title)).all()
    return [
        {
            "id": row.id,
            "slug": row.slug,
            "code": row.code,
            "title": row.title,
            "description": row.description,
            "module_type": row.module_type,
            "version": row.version,
        }
        for row in rows
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
    slug: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    code: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=2, max_length=300)
    description: str = ""
    module_type: str = "scorm"


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
    if db.scalar(select(Module).where(Module.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Module slug already exists")
    user_id = int(session["sub"])
    module = Module(
        slug=payload.slug,
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
