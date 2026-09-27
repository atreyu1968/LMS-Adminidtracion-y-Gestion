from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Course, Membership, Module, User
from .security import read_session, require_admin


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
