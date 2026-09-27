from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    AssessmentAttempt,
    AssessmentCriterion,
    AssessmentItem,
    ContentExemption,
    ContentReleaseRule,
    CourseModule,
    EvaluationResult,
    LearnerContentException,
    LearningResult,
    Membership,
    ScormRegistration,
    UserPreference,
)
from .security import read_session, require_teacher


router = APIRouter(prefix="/api/learning")
CONTENT_TYPES = {"learning_result", "item", "criterion", "scorm"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _course_access(
    db: Session,
    course_module_id: int,
    user_id: int,
) -> tuple[CourseModule, Membership]:
    cm = db.get(CourseModule, course_module_id)
    if not cm or not cm.active:
        raise HTTPException(status_code=404, detail="Módulo del grupo no encontrado")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == cm.course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No perteneces a este grupo")
    return cm, membership


def _teacher_access(db: Session, course_module_id: int, user_id: int) -> CourseModule:
    cm, membership = _course_access(db, course_module_id, user_id)
    if membership.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Se requiere rol docente")
    return cm


def get_learner_exception(
    db: Session,
    course_module_id: int,
    user_id: int,
    content_type: str,
    content_key: str | int,
) -> LearnerContentException | None:
    return db.scalar(
        select(LearnerContentException).where(
            LearnerContentException.course_module_id == course_module_id,
            LearnerContentException.user_id == user_id,
            LearnerContentException.content_type == str(content_type),
            LearnerContentException.content_key == str(content_key),
            LearnerContentException.active.is_(True),
        )
    )


def is_exempt(
    db: Session,
    course_module_id: int,
    user_id: int,
    content_type: str,
    content_key: str | int,
) -> bool:
    return bool(
        db.scalar(
            select(ContentExemption.id).where(
                ContentExemption.course_module_id == course_module_id,
                ContentExemption.user_id == user_id,
                ContentExemption.content_type == str(content_type),
                ContentExemption.content_key == str(content_key),
                ContentExemption.active.is_(True),
            )
        )
    )


def _content_state(
    db: Session,
    course_module_id: int,
    user_id: int,
    content_type: str,
    content_key: str,
) -> dict:
    if is_exempt(db, course_module_id, user_id, content_type, content_key):
        return {"completed": True, "score": None, "exempt": True}

    if content_type == "learning_result":
        result = db.scalar(
            select(EvaluationResult).where(
                EvaluationResult.course_module_id == course_module_id,
                EvaluationResult.user_id == user_id,
                EvaluationResult.learning_result_id == int(content_key),
            )
        )
        return {
            "completed": bool(result and (result.final_score is not None or result.passed)),
            "score": float(result.final_score) if result and result.final_score is not None else None,
            "exempt": False,
        }
    if content_type == "item":
        attempt = db.scalar(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.course_module_id == course_module_id,
                AssessmentAttempt.user_id == user_id,
                AssessmentAttempt.item_id == int(content_key),
                AssessmentAttempt.status == "submitted",
            )
            .order_by(AssessmentAttempt.attempt_no.desc(), AssessmentAttempt.id.desc())
        )
        return {
            "completed": bool(attempt),
            "score": float(attempt.score) if attempt and attempt.score is not None else None,
            "exempt": False,
        }
    if content_type == "criterion":
        criterion = db.get(AssessmentCriterion, int(content_key))
        if not criterion:
            return {"completed": False, "score": None, "exempt": False}
        items = list(
            db.scalars(
                select(AssessmentItem.id).where(
                    AssessmentItem.criterion_id == criterion.id,
                    AssessmentItem.active.is_(True),
                    AssessmentItem.evaluable.is_(True),
                    AssessmentItem.instrument == "portfolio",
                )
            )
        )
        if not items:
            return {"completed": False, "score": None, "exempt": False}
        scores = []
        all_done = True
        for item_id in items:
            if is_exempt(db, course_module_id, user_id, "item", item_id):
                continue
            attempt = db.scalar(
                select(AssessmentAttempt)
                .where(
                    AssessmentAttempt.course_module_id == course_module_id,
                    AssessmentAttempt.user_id == user_id,
                    AssessmentAttempt.item_id == item_id,
                    AssessmentAttempt.status == "submitted",
                )
                .order_by(AssessmentAttempt.attempt_no.desc(), AssessmentAttempt.id.desc())
            )
            if not attempt or attempt.score is None or attempt.pending_review:
                all_done = False
            else:
                scores.append(float(attempt.score))
        return {
            "completed": all_done,
            "score": round(sum(scores) / len(scores), 2) if scores else None,
            "exempt": False,
        }
    if content_type == "scorm":
        registration = db.scalar(
            select(ScormRegistration)
            .where(
                ScormRegistration.course_module_id == course_module_id,
                ScormRegistration.user_id == user_id,
                ScormRegistration.package_id == int(content_key),
            )
            .order_by(ScormRegistration.updated_at.desc(), ScormRegistration.id.desc())
        )
        terminal = {"completed", "passed", "failed"}
        return {
            "completed": bool(registration and registration.lesson_status in terminal),
            "score": float(registration.score_raw) if registration and registration.score_raw is not None else None,
            "exempt": False,
        }
    raise HTTPException(status_code=400, detail="Tipo de contenido no válido")


def access_decision(
    db: Session,
    course_module_id: int,
    user_id: int,
    content_type: str,
    content_key: str | int,
) -> dict:
    ctype = str(content_type)
    ckey = str(content_key)
    if ctype not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de contenido no válido")

    exception = get_learner_exception(
        db, course_module_id, user_id, ctype, ckey
    )
    rule = db.scalar(
        select(ContentReleaseRule).where(
            ContentReleaseRule.course_module_id == course_module_id,
            ContentReleaseRule.content_type == ctype,
            ContentReleaseRule.content_key == ckey,
            ContentReleaseRule.active.is_(True),
        )
    )
    if not rule:
        return {
            "available": True,
            "reason": None,
            "open_at": None,
            "close_at": None,
            "requirements": [],
            "exception": _exception_public(exception),
        }

    audience = rule.audience_json or {}
    allowed_users = {int(x) for x in audience.get("user_ids", []) if str(x).isdigit()}
    excluded_users = {int(x) for x in audience.get("exclude_user_ids", []) if str(x).isdigit()}
    if user_id in excluded_users:
        return {
            "available": False,
            "reason": "No disponible para este alumno",
            "open_at": rule.open_at,
            "close_at": rule.close_at,
            "requirements": rule.requirements_json or [],
            "exception": _exception_public(exception),
        }
    if allowed_users and user_id not in allowed_users:
        return {
            "available": False,
            "reason": "Disponible solo para alumnado seleccionado",
            "open_at": rule.open_at,
            "close_at": rule.close_at,
            "requirements": rule.requirements_json or [],
            "exception": _exception_public(exception),
        }

    open_at = _aware(exception.open_at_override) if exception and exception.open_at_override else _aware(rule.open_at)
    close_at = _aware(exception.close_at_override) if exception and exception.close_at_override else _aware(rule.close_at)
    now = _now()
    if open_at and now < open_at:
        return {
            "available": False,
            "reason": "Todavía no está disponible",
            "open_at": open_at,
            "close_at": close_at,
            "requirements": rule.requirements_json or [],
            "exception": _exception_public(exception),
        }
    if close_at and now > close_at:
        return {
            "available": False,
            "reason": "El periodo de disponibilidad ha finalizado",
            "open_at": open_at,
            "close_at": close_at,
            "requirements": rule.requirements_json or [],
            "exception": _exception_public(exception),
        }

    unmet = []
    for requirement in rule.requirements_json or []:
        if not isinstance(requirement, dict):
            continue
        rtype = str(requirement.get("type") or "")
        rkey = str(requirement.get("key") or "")
        if rtype not in CONTENT_TYPES or not rkey:
            continue
        state = _content_state(db, course_module_id, user_id, rtype, rkey)
        if requirement.get("completion", True) and not state["completed"]:
            unmet.append({
                "type": rtype,
                "key": rkey,
                "reason": "completion",
            })
            continue
        min_score = requirement.get("min_score")
        if min_score is not None:
            score = state.get("score")
            if score is None or float(score) < float(min_score):
                unmet.append({
                    "type": rtype,
                    "key": rkey,
                    "reason": "score",
                    "min_score": float(min_score),
                    "score": score,
                })

    return {
        "available": not unmet,
        "reason": "Faltan prerrequisitos" if unmet else None,
        "open_at": open_at,
        "close_at": close_at,
        "requirements": rule.requirements_json or [],
        "unmet": unmet,
        "exception": _exception_public(exception),
    }


def _exception_public(row: LearnerContentException | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row.id,
        "extra_attempts": row.extra_attempts,
        "extra_time_minutes": row.extra_time_minutes,
        "open_at_override": row.open_at_override,
        "close_at_override": row.close_at_override,
        "notes": row.notes,
    }


class ReleaseRuleIn(BaseModel):
    open_at: datetime | None = None
    close_at: datetime | None = None
    requirements: list[dict] = []
    audience: dict = {}


class ExceptionIn(BaseModel):
    extra_attempts: int = Field(default=0, ge=0, le=100)
    extra_time_minutes: int = Field(default=0, ge=0, le=1440)
    open_at_override: datetime | None = None
    close_at_override: datetime | None = None
    notes: str = Field(default="", max_length=5000)


class ExemptionIn(BaseModel):
    reason: str = Field(default="", max_length=5000)


class PreferenceIn(BaseModel):
    value: dict


@router.get("/course-modules/{course_module_id}/rules")
def list_rules(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    _teacher_access(db, course_module_id, user_id)
    rows = list(
        db.scalars(
            select(ContentReleaseRule)
            .where(
                ContentReleaseRule.course_module_id == course_module_id,
                ContentReleaseRule.active.is_(True),
            )
            .order_by(ContentReleaseRule.content_type, ContentReleaseRule.content_key)
        )
    )
    return [
        {
            "id": row.id,
            "content_type": row.content_type,
            "content_key": row.content_key,
            "open_at": row.open_at,
            "close_at": row.close_at,
            "requirements": row.requirements_json or [],
            "audience": row.audience_json or {},
        }
        for row in rows
    ]


@router.put("/course-modules/{course_module_id}/rules/{content_type}/{content_key}")
def put_rule(
    course_module_id: int,
    content_type: str,
    content_key: str,
    payload: ReleaseRuleIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _teacher_access(db, course_module_id, user_id)
    if content_type not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de contenido no válido")
    row = db.scalar(
        select(ContentReleaseRule).where(
            ContentReleaseRule.course_module_id == course_module_id,
            ContentReleaseRule.content_type == content_type,
            ContentReleaseRule.content_key == content_key,
        )
    )
    if not row:
        row = ContentReleaseRule(
            course_module_id=course_module_id,
            content_type=content_type,
            content_key=content_key,
            created_by_user_id=user_id,
        )
        db.add(row)
    row.open_at = payload.open_at
    row.close_at = payload.close_at
    row.requirements_json = payload.requirements or []
    row.audience_json = payload.audience or {}
    row.active = True
    row.updated_at = _now()
    db.commit()
    return {
        "id": row.id,
        "content_type": row.content_type,
        "content_key": row.content_key,
        "open_at": row.open_at,
        "close_at": row.close_at,
        "requirements": row.requirements_json,
        "audience": row.audience_json,
    }


@router.delete("/course-modules/{course_module_id}/rules/{content_type}/{content_key}")
def delete_rule(
    course_module_id: int,
    content_type: str,
    content_key: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _teacher_access(db, course_module_id, user_id)
    row = db.scalar(
        select(ContentReleaseRule).where(
            ContentReleaseRule.course_module_id == course_module_id,
            ContentReleaseRule.content_type == content_type,
            ContentReleaseRule.content_key == content_key,
        )
    )
    if row:
        row.active = False
        row.updated_at = _now()
        db.commit()
    return {"ok": True}


@router.get("/course-modules/{course_module_id}/access/{content_type}/{content_key}")
def get_access(
    course_module_id: int,
    content_type: str,
    content_key: str,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _course_access(db, course_module_id, user_id)
    return access_decision(
        db, course_module_id, user_id, content_type, content_key
    )


@router.get("/course-modules/{course_module_id}/exceptions")
def list_exceptions(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _teacher_access(db, course_module_id, user_id)
    exceptions = list(
        db.scalars(
            select(LearnerContentException).where(
                LearnerContentException.course_module_id == course_module_id,
                LearnerContentException.active.is_(True),
            )
        )
    )
    exemptions = list(
        db.scalars(
            select(ContentExemption).where(
                ContentExemption.course_module_id == course_module_id,
                ContentExemption.active.is_(True),
            )
        )
    )
    return {
        "exceptions": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "content_type": row.content_type,
                "content_key": row.content_key,
                **(_exception_public(row) or {}),
            }
            for row in exceptions
        ],
        "exemptions": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "content_type": row.content_type,
                "content_key": row.content_key,
                "reason": row.reason,
            }
            for row in exemptions
        ],
    }


@router.put("/course-modules/{course_module_id}/students/{student_id}/exceptions/{content_type}/{content_key}")
def put_exception(
    course_module_id: int,
    student_id: int,
    content_type: str,
    content_key: str,
    payload: ExceptionIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    cm = _teacher_access(db, course_module_id, teacher_id)
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == cm.course_id,
            Membership.user_id == student_id,
            Membership.active.is_(True),
            Membership.role == "student",
        )
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Alumno no encontrado en el grupo")
    if content_type not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de contenido no válido")
    row = db.scalar(
        select(LearnerContentException).where(
            LearnerContentException.course_module_id == course_module_id,
            LearnerContentException.user_id == student_id,
            LearnerContentException.content_type == content_type,
            LearnerContentException.content_key == content_key,
        )
    )
    if not row:
        row = LearnerContentException(
            course_module_id=course_module_id,
            user_id=student_id,
            content_type=content_type,
            content_key=content_key,
        )
        db.add(row)
    row.extra_attempts = payload.extra_attempts
    row.extra_time_minutes = payload.extra_time_minutes
    row.open_at_override = payload.open_at_override
    row.close_at_override = payload.close_at_override
    row.notes = payload.notes
    row.active = True
    row.updated_by_user_id = teacher_id
    row.updated_at = _now()
    db.commit()
    return {"ok": True, **(_exception_public(row) or {})}


@router.put("/course-modules/{course_module_id}/students/{student_id}/exemptions/{content_type}/{content_key}")
def put_exemption(
    course_module_id: int,
    student_id: int,
    content_type: str,
    content_key: str,
    payload: ExemptionIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    cm = _teacher_access(db, course_module_id, teacher_id)
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == cm.course_id,
            Membership.user_id == student_id,
            Membership.active.is_(True),
            Membership.role == "student",
        )
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Alumno no encontrado en el grupo")
    if content_type not in CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de contenido no válido")
    row = db.scalar(
        select(ContentExemption).where(
            ContentExemption.course_module_id == course_module_id,
            ContentExemption.user_id == student_id,
            ContentExemption.content_type == content_type,
            ContentExemption.content_key == content_key,
        )
    )
    if not row:
        row = ContentExemption(
            course_module_id=course_module_id,
            user_id=student_id,
            content_type=content_type,
            content_key=content_key,
            created_by_user_id=teacher_id,
        )
        db.add(row)
    row.reason = payload.reason
    row.active = True
    db.commit()
    return {"ok": True, "id": row.id}


@router.delete("/course-modules/{course_module_id}/students/{student_id}/exemptions/{content_type}/{content_key}")
def delete_exemption(
    course_module_id: int,
    student_id: int,
    content_type: str,
    content_key: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    _teacher_access(db, course_module_id, teacher_id)
    row = db.scalar(
        select(ContentExemption).where(
            ContentExemption.course_module_id == course_module_id,
            ContentExemption.user_id == student_id,
            ContentExemption.content_type == content_type,
            ContentExemption.content_key == content_key,
        )
    )
    if row:
        row.active = False
        db.commit()
    return {"ok": True}


@router.get("/preferences/{pref_key}")
def get_preference(
    pref_key: str,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    row = db.scalar(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.pref_key == pref_key,
        )
    )
    return {"key": pref_key, "value": row.value_json if row else {}}


@router.put("/preferences/{pref_key}")
def put_preference(
    pref_key: str,
    payload: PreferenceIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    if len(pref_key) > 160:
        raise HTTPException(status_code=400, detail="Preferencia no válida")
    row = db.scalar(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.pref_key == pref_key,
        )
    )
    if not row:
        row = UserPreference(user_id=user_id, pref_key=pref_key)
        db.add(row)
    row.value_json = payload.value or {}
    row.updated_at = _now()
    db.commit()
    return {"key": pref_key, "value": row.value_json}
