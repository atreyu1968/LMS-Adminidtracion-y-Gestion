from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai import grade_with_ai, resolve_ai_for_course_module
from .db import get_db
from .models import (
    AssessmentAttempt,
    AssessmentCriterion,
    AssessmentItem,
    AssessmentKey,
    AssessmentReview,
    CourseModule,
    EvaluationConfig,
    EvaluationResult,
    ExamSession,
    LearningResult,
    Membership,
    Module,
    ModulePermission,
    RecoveryPlan,
    User,
)
from .security import read_session, require_admin, require_teacher


router = APIRouter(prefix="/api/evaluation")
SEMANTIC_KINDS = {"free", "text", "case", "calculation"}
OBJECTIVE_KINDS = {"choice", "tf", "multi", "order", "match"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _course_module_access(
    db: Session,
    course_module_id: int,
    user_id: int,
) -> tuple[CourseModule, Membership]:
    course_module = db.get(CourseModule, course_module_id)
    if not course_module or not course_module.active:
        raise HTTPException(status_code=404, detail="Módulo del grupo no encontrado")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_module.course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No perteneces a este grupo")
    return course_module, membership


def _teacher_course_module(
    db: Session,
    course_module_id: int,
    user_id: int,
) -> CourseModule:
    course_module, membership = _course_module_access(db, course_module_id, user_id)
    if membership.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Se requiere rol docente")
    return course_module


def _module_editor(db: Session, module_id: int, user_id: int) -> None:
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
            ModulePermission.permission.in_(["owner", "editor"]),
        )
    )
    if not permission:
        raise HTTPException(status_code=403, detail="Se requiere permiso de edición del módulo")


def _effective_config(db: Session, course_module: CourseModule) -> dict:
    module = db.get(Module, course_module.module_id)
    defaults = dict((module.metadata_json or {}).get("evaluation_defaults") or {}) if module else {}
    assignment = dict(course_module.settings_json or {})
    assignment_eval = assignment.get("evaluation")
    if isinstance(assignment_eval, dict):
        defaults.update(assignment_eval)
    latest = db.scalar(
        select(EvaluationConfig)
        .where(
            EvaluationConfig.course_module_id == course_module.id,
            EvaluationConfig.active.is_(True),
        )
        .order_by(EvaluationConfig.version.desc())
    )
    if latest:
        defaults.update(latest.config_json or {})
    return {
        "practice_max_attempts": int(defaults.get("practice_max_attempts", 3)),
        "portfolio_max_attempts": int(defaults.get("portfolio_max_attempts", 2)),
        "exam_max_attempts": int(defaults.get("exam_max_attempts", 1)),
        "recovery_max_attempts": int(defaults.get("recovery_max_attempts", 1)),
        "portfolio_weight": float(defaults.get("portfolio_weight", 40)),
        "exam_weight": float(defaults.get("exam_weight", 60)),
        "pass_score": float(defaults.get("pass_score", 50)),
        "ce_pass_score": float(defaults.get("ce_pass_score", 50)),
        "ce_pass_percent": int(defaults.get("ce_pass_percent", 80)),
        "require_both_instruments": bool(defaults.get("require_both_instruments", False)),
        "exam_enabled": bool(defaults.get("exam_enabled", False)),
        "exam_questions_per_ce": int(defaults.get("exam_questions_per_ce", 3)),
        "exam_minutes": int(defaults.get("exam_minutes", 45)),
        "exam_integrity_enabled": bool(defaults.get("exam_integrity_enabled", True)),
        "exam_fullscreen_required": bool(defaults.get("exam_fullscreen_required", True)),
        "exam_incident_limit": int(defaults.get("exam_incident_limit", 3)),
        "exam_incident_policy": str(defaults.get("exam_incident_policy", "submit")),
    }


def _item_belongs_to_module(
    db: Session,
    item_id: int,
    module_id: int,
) -> tuple[AssessmentItem, AssessmentCriterion, LearningResult]:
    row = db.execute(
        select(AssessmentItem, AssessmentCriterion, LearningResult)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .join(
            LearningResult,
            LearningResult.id == AssessmentCriterion.learning_result_id,
        )
        .where(
            AssessmentItem.id == item_id,
            AssessmentItem.active.is_(True),
            AssessmentCriterion.active.is_(True),
            LearningResult.active.is_(True),
            LearningResult.module_id == module_id,
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Actividad no encontrada en este módulo")
    return row[0], row[1], row[2]


def _unwrap_answer(key: AssessmentKey | None) -> object:
    if not key:
        return None
    payload = key.answer_json or {}
    return payload.get("value")


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(",", ".")
        try:
            result = float(text)
            return result if math.isfinite(result) else None
        except ValueError:
            return None
    return None


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _objective_score(kind: str, given: object, expected: object) -> float | None:
    if kind == "multi":
        if not isinstance(given, list) or not isinstance(expected, list):
            return 0.0
        return 100.0 if sorted(map(str, given)) == sorted(map(str, expected)) else 0.0
    if kind == "order":
        return 100.0 if given == expected else 0.0
    if kind == "match":
        if not isinstance(given, list) or not isinstance(expected, list):
            return 0.0
        return 100.0 if [str(v) for v in given] == [str(v) for v in expected] else 0.0
    if kind == "calculation":
        a = _numeric(given)
        b = _numeric(expected)
        if a is not None and b is not None:
            tolerance = max(0.01, abs(b) * 1e-6)
            return 100.0 if abs(a - b) <= tolerance else None
        return None
    if kind in {"choice", "tf"}:
        return 100.0 if given == expected else 0.0
    if kind in SEMANTIC_KINDS and _normalized_text(given) == _normalized_text(expected):
        return 100.0
    return None


def _review_dict(
    review: AssessmentReview,
    attempt: AssessmentAttempt,
    item: AssessmentItem,
    criterion: AssessmentCriterion,
    lr: LearningResult,
    user: User,
) -> dict:
    return {
        "review_id": review.id,
        "attempt_id": attempt.id,
        "status": review.status,
        "source": review.source,
        "proposed_score": review.proposed_score,
        "confidence": review.confidence,
        "verdict": review.verdict,
        "feedback": review.feedback,
        "breakdown": review.breakdown_json or [],
        "teacher_score": review.teacher_score,
        "teacher_feedback": review.teacher_feedback,
        "student": {
            "id": user.id,
            "display_name": user.display_name,
        },
        "learning_result": {"id": lr.id, "code": lr.code, "title": lr.title},
        "criterion": {"id": criterion.id, "code": criterion.code, "title": criterion.title},
        "item": {
            "id": item.id,
            "item_key": item.item_key,
            "type": item.item_type,
            "prompt": item.prompt,
        },
        "response": (attempt.response_json or {}).get("value"),
        "attempt_no": attempt.attempt_no,
        "created_at": review.created_at,
        "reviewed_at": review.reviewed_at,
    }


def _upsert_review(
    db: Session,
    attempt: AssessmentAttempt,
    *,
    ai_teacher_user_id: int | None,
    source: str,
    proposed_score: float | None = None,
    confidence: float | None = None,
    verdict: str = "",
    feedback: str = "",
    breakdown: list | None = None,
    status: str = "pending",
) -> AssessmentReview:
    review = db.scalar(
        select(AssessmentReview).where(AssessmentReview.attempt_id == attempt.id)
    )
    if not review:
        review = AssessmentReview(attempt_id=attempt.id)
        db.add(review)
    review.ai_teacher_user_id = ai_teacher_user_id
    review.source = source
    review.status = status
    review.proposed_score = proposed_score
    review.confidence = confidence
    review.verdict = verdict
    review.feedback = feedback
    review.breakdown_json = breakdown or []
    return review


def _latest_attempts_for_items(
    db: Session,
    course_module_id: int,
    user_id: int,
    item_ids: list[int],
) -> dict[int, AssessmentAttempt]:
    if not item_ids:
        return {}
    rows = list(
        db.scalars(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.course_module_id == course_module_id,
                AssessmentAttempt.user_id == user_id,
                AssessmentAttempt.item_id.in_(item_ids),
                AssessmentAttempt.status == "submitted",
            )
            .order_by(
                AssessmentAttempt.item_id,
                AssessmentAttempt.attempt_no.desc(),
            )
        )
    )
    latest: dict[int, AssessmentAttempt] = {}
    for attempt in rows:
        latest.setdefault(attempt.item_id, attempt)
    return latest


def recompute_learning_result(
    db: Session,
    course_module_id: int,
    user_id: int,
    learning_result_id: int,
) -> dict:
    course_module = db.get(CourseModule, course_module_id)
    lr = db.get(LearningResult, learning_result_id)
    if not course_module or not lr or lr.module_id != course_module.module_id:
        raise HTTPException(status_code=404, detail="Resultado de aprendizaje no válido")
    config = _effective_config(db, course_module)
    criteria = list(
        db.scalars(
            select(AssessmentCriterion)
            .where(
                AssessmentCriterion.learning_result_id == lr.id,
                AssessmentCriterion.active.is_(True),
            )
            .order_by(AssessmentCriterion.position, AssessmentCriterion.id)
        )
    )

    latest_exam = db.scalar(
        select(ExamSession)
        .where(
            ExamSession.course_module_id == course_module_id,
            ExamSession.user_id == user_id,
            ExamSession.learning_result_id == lr.id,
            ExamSession.status == "submitted",
        )
        .order_by(ExamSession.attempt_no.desc(), ExamSession.id.desc())
    )
    exam_by_ce = {}
    if latest_exam:
        exam_by_ce = dict((latest_exam.response_json or {}).get("by_ce") or {})

    details: dict[str, dict] = {}
    portfolio_values: list[float] = []
    exam_values: list[float] = []
    final_values: list[float] = []
    passed_count = 0
    all_portfolio_complete = True
    all_exam_complete = bool(latest_exam)

    pw = float(config["portfolio_weight"]) / 100.0
    ew = float(config["exam_weight"]) / 100.0

    for criterion in criteria:
        items = list(
            db.scalars(
                select(AssessmentItem)
                .where(
                    AssessmentItem.criterion_id == criterion.id,
                    AssessmentItem.instrument == "portfolio",
                    AssessmentItem.active.is_(True),
                    AssessmentItem.evaluable.is_(True),
                )
                .order_by(AssessmentItem.position, AssessmentItem.id)
            )
        )
        attempts = _latest_attempts_for_items(
            db,
            course_module_id,
            user_id,
            [item.id for item in items],
        )
        scored = [
            float(attempts[item.id].score)
            for item in items
            if item.id in attempts and attempts[item.id].score is not None
        ]
        pending = sum(
            1
            for item in items
            if item.id in attempts and attempts[item.id].pending_review
        )
        portfolio_complete = bool(items) and len(scored) == len(items) and pending == 0
        all_portfolio_complete = all_portfolio_complete and portfolio_complete
        portfolio_ce = round(sum(scored) / len(scored), 2) if scored else 0.0
        portfolio_values.append(portfolio_ce)

        exam_data = exam_by_ce.get(criterion.code) or {}
        exam_n = int(exam_data.get("n") or 0)
        exam_ok = int(exam_data.get("ok") or 0)
        exam_ce = round(exam_ok / exam_n * 100.0, 2) if exam_n else None
        exam_complete = exam_ce is not None
        if config["exam_enabled"]:
            all_exam_complete = all_exam_complete and exam_complete
        if exam_ce is not None:
            exam_values.append(exam_ce)

        final_ce = None
        criterion_passed = False
        if config["exam_enabled"] and portfolio_complete and exam_complete:
            final_ce = round(portfolio_ce * pw + float(exam_ce) * ew, 2)
            criterion_passed = final_ce >= float(config["ce_pass_score"])
            final_values.append(final_ce)
        elif not config["exam_enabled"]:
            # Mientras el examen esté desactivado, el Portafolio es progreso,
            # no una calificación final oficial del RA.
            final_ce = None

        if criterion_passed:
            passed_count += 1

        details[criterion.code] = {
            "criterion_id": criterion.id,
            "portfolio": portfolio_ce,
            "portfolio_items_total": len(items),
            "portfolio_items_scored": len(scored),
            "pending_review": pending,
            "portfolio_complete": portfolio_complete,
            "exam": exam_ce,
            "exam_questions": exam_n,
            "final": final_ce,
            "passed": criterion_passed,
        }

    portfolio_score = (
        round(sum(portfolio_values) / len(portfolio_values), 2)
        if portfolio_values
        else 0.0
    )
    exam_score = (
        round(sum(exam_values) / len(exam_values), 2)
        if exam_values
        else None
    )
    criteria_total = len(criteria)
    needed = math.ceil(criteria_total * config["ce_pass_percent"] / 100) if criteria_total else 0

    final_score = None
    ra_passed = False
    if not config["exam_enabled"]:
        final_state = "portfolio-progress"
    elif not latest_exam:
        final_state = "exam-pending"
    elif not all_portfolio_complete:
        final_state = "portfolio-incomplete"
    elif not all_exam_complete:
        final_state = "exam-incomplete"
    else:
        final_score = round(
            portfolio_score * pw + float(exam_score or 0.0) * ew,
            2,
        )
        both_ok = (
            not config["require_both_instruments"]
            or (
                portfolio_score >= float(config["pass_score"])
                and float(exam_score or 0) >= float(config["pass_score"])
            )
        )
        ra_passed = (
            final_score >= float(config["pass_score"])
            and passed_count >= needed
            and both_ok
        )
        final_state = "passed" if ra_passed else "recovery-required"

    failed_criteria = [
        code
        for code, data in details.items()
        if config["exam_enabled"] and data.get("final") is not None and not data.get("passed")
    ]
    recovery = db.scalar(
        select(RecoveryPlan).where(
            RecoveryPlan.course_module_id == course_module_id,
            RecoveryPlan.user_id == user_id,
            RecoveryPlan.learning_result_id == learning_result_id,
        )
    )
    if final_state == "recovery-required":
        if not recovery:
            recovery = RecoveryPlan(
                course_module_id=course_module_id,
                user_id=user_id,
                learning_result_id=learning_result_id,
            )
            db.add(recovery)
        recovery.criteria_json = failed_criteria
        recovery.status = "pending"
        recovery.updated_at = _now()
    elif recovery and ra_passed:
        recovery.criteria_json = []
        recovery.status = "passed"
        recovery.updated_at = _now()

    result = db.scalar(
        select(EvaluationResult).where(
            EvaluationResult.course_module_id == course_module_id,
            EvaluationResult.user_id == user_id,
            EvaluationResult.learning_result_id == learning_result_id,
        )
    )
    if not result:
        result = EvaluationResult(
            course_module_id=course_module_id,
            user_id=user_id,
            learning_result_id=learning_result_id,
        )
        db.add(result)
    result.portfolio_score = portfolio_score
    result.exam_score = exam_score
    result.final_score = final_score
    result.criteria_passed = passed_count
    result.criteria_total = criteria_total
    result.passed = ra_passed
    result.details_json = {
        "status": final_state,
        "criteria_needed": needed,
        "criteria": details,
        "recovery": failed_criteria,
        "exam_attempt": latest_exam.attempt_no if latest_exam else None,
        "config": {
            "portfolio_weight": config["portfolio_weight"],
            "exam_weight": config["exam_weight"],
            "pass_score": config["pass_score"],
            "ce_pass_score": config["ce_pass_score"],
            "ce_pass_percent": config["ce_pass_percent"],
            "require_both_instruments": config["require_both_instruments"],
        },
    }
    result.updated_at = _now()
    db.flush()

    return {
        "learning_result_id": lr.id,
        "code": lr.code,
        "portfolio_score": portfolio_score,
        "exam_score": exam_score,
        "final_score": final_score,
        "criteria_passed": passed_count,
        "criteria_total": criteria_total,
        "criteria_needed": needed,
        "passed": ra_passed,
        "status": final_state,
        "recovery": failed_criteria,
        "criteria": details,
    }


class StartAttemptIn(BaseModel):
    metadata: dict = {}


class SubmitAttemptIn(BaseModel):
    response: object
    metadata: dict = {}


class ReviewDecisionIn(BaseModel):
    score: float = Field(ge=0, le=100)
    feedback: str = Field(default="", max_length=5000)
    status: str = Field(default="accepted", pattern=r"^(accepted|rejected)$")


class EvaluationConfigIn(BaseModel):
    practice_max_attempts: int | None = Field(default=None, ge=1, le=20)
    portfolio_max_attempts: int | None = Field(default=None, ge=1, le=20)
    exam_max_attempts: int | None = Field(default=None, ge=1, le=10)
    recovery_max_attempts: int | None = Field(default=None, ge=1, le=10)
    portfolio_weight: float | None = Field(default=None, ge=0, le=100)
    exam_weight: float | None = Field(default=None, ge=0, le=100)
    pass_score: float | None = Field(default=None, ge=0, le=100)
    ce_pass_score: float | None = Field(default=None, ge=0, le=100)
    ce_pass_percent: int | None = Field(default=None, ge=0, le=100)
    require_both_instruments: bool | None = None
    exam_enabled: bool | None = None
    exam_questions_per_ce: int | None = Field(default=None, ge=1, le=20)
    exam_minutes: int | None = Field(default=None, ge=1, le=300)
    exam_integrity_enabled: bool | None = None
    exam_fullscreen_required: bool | None = None
    exam_incident_limit: int | None = Field(default=None, ge=0, le=20)
    exam_incident_policy: str | None = Field(
        default=None,
        pattern=r"^(submit|warn|log)$",
    )


class PrivateKeyItemIn(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    ce: str | None = Field(default=None, max_length=40)
    kind: str | None = Field(default=None, max_length=40)
    answer: object
    public_hash: str | None = Field(default=None, max_length=64)
    feedback: str = Field(default="", max_length=5000)


class PrivateKeysIn(BaseModel):
    items: list[PrivateKeyItemIn]
    strict: bool = True
    source: str = Field(default="private-bank", max_length=80)


def _load_private_keys(
    db: Session,
    module_id: int,
    payload: PrivateKeysIn,
) -> dict:
    module_items = db.execute(
        select(AssessmentItem, AssessmentCriterion)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .join(
            LearningResult,
            LearningResult.id == AssessmentCriterion.learning_result_id,
        )
        .where(
            LearningResult.module_id == module_id,
            AssessmentItem.active.is_(True),
            AssessmentItem.evaluable.is_(True),
        )
    ).all()
    by_key = {item.item_key: (item, criterion) for item, criterion in module_items}
    supplied = {entry.id for entry in payload.items}
    expected = set(by_key)
    if payload.strict and supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "El banco privado no coincide exactamente con las actividades públicas",
                "expected": len(expected),
                "supplied": len(supplied),
                "missing": missing[:20],
                "extra": extra[:20],
            },
        )

    imported = 0
    for entry in payload.items:
        pair = by_key.get(entry.id)
        if not pair:
            raise HTTPException(status_code=400, detail=f"Actividad desconocida: {entry.id}")
        item, criterion = pair
        if entry.ce and entry.ce != criterion.code:
            raise HTTPException(status_code=400, detail=f"CE no coincide para {entry.id}")
        if entry.kind and entry.kind != item.item_type:
            raise HTTPException(status_code=400, detail=f"Tipo no coincide para {entry.id}")
        expected_hash = item.public_hash
        supplied_hash = entry.public_hash or expected_hash
        if expected_hash and supplied_hash != expected_hash:
            raise HTTPException(
                status_code=409,
                detail=f"El banco público cambió desde la generación de la clave: {entry.id}",
            )
        key = db.scalar(select(AssessmentKey).where(AssessmentKey.item_id == item.id))
        if not key:
            key = AssessmentKey(item_id=item.id)
            db.add(key)
        key.answer_json = {"value": entry.answer}
        key.feedback = entry.feedback
        key.public_hash = expected_hash
        key.source = payload.source
        key.active = True
        key.updated_at = _now()
        imported += 1
    db.commit()
    return {"module_id": module_id, "items": imported, "strict": payload.strict}


@router.get("/course-modules/{course_module_id}/config")
def get_evaluation_config(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    course_module = _teacher_course_module(
        db, course_module_id, int(session["sub"])
    )
    latest = db.scalar(
        select(EvaluationConfig)
        .where(
            EvaluationConfig.course_module_id == course_module_id,
            EvaluationConfig.active.is_(True),
        )
        .order_by(EvaluationConfig.version.desc())
    )
    return {
        "course_module_id": course_module_id,
        "version": latest.version if latest else 0,
        "config": _effective_config(db, course_module),
        "frozen_at": latest.frozen_at if latest else None,
    }


@router.put("/course-modules/{course_module_id}/config")
def put_evaluation_config(
    course_module_id: int,
    payload: EvaluationConfigIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    course_module = _teacher_course_module(
        db, course_module_id, int(session["sub"])
    )
    current = _effective_config(db, course_module)
    changes = payload.model_dump(exclude_unset=True)
    current.update({k: v for k, v in changes.items() if v is not None})

    if abs(
        float(current.get("portfolio_weight", 0))
        + float(current.get("exam_weight", 0))
        - 100.0
    ) > 0.001:
        raise HTTPException(
            status_code=400,
            detail="Los pesos de Portafolio y examen deben sumar 100",
        )

    previous = list(
        db.scalars(
            select(EvaluationConfig).where(
                EvaluationConfig.course_module_id == course_module_id,
                EvaluationConfig.active.is_(True),
            )
        )
    )
    next_version = max([row.version for row in previous], default=0) + 1
    for row in previous:
        row.active = False
    snapshot = EvaluationConfig(
        course_module_id=course_module_id,
        version=next_version,
        config_json=current,
        active=True,
    )
    db.add(snapshot)
    db.commit()
    return {
        "course_module_id": course_module_id,
        "version": next_version,
        "config": current,
    }


@router.get("/course-modules/{course_module_id}/structure")
def evaluation_structure(
    course_module_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    course_module, membership = _course_module_access(db, course_module_id, user_id)
    config = _effective_config(db, course_module)
    lrs = list(
        db.scalars(
            select(LearningResult)
            .where(
                LearningResult.module_id == course_module.module_id,
                LearningResult.active.is_(True),
            )
            .order_by(LearningResult.position, LearningResult.id)
        )
    )
    data = []
    for lr in lrs:
        criteria = list(
            db.scalars(
                select(AssessmentCriterion)
                .where(
                    AssessmentCriterion.learning_result_id == lr.id,
                    AssessmentCriterion.active.is_(True),
                )
                .order_by(AssessmentCriterion.position, AssessmentCriterion.id)
            )
        )
        ce_rows = []
        for criterion in criteria:
            items = list(
                db.scalars(
                    select(AssessmentItem)
                    .where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.active.is_(True),
                    )
                    .order_by(AssessmentItem.position, AssessmentItem.id)
                )
            )
            ce_rows.append(
                {
                    "id": criterion.id,
                    "code": criterion.code,
                    "title": criterion.title,
                    "description": criterion.description,
                    "pass_score": criterion.pass_score,
                    "items": [
                        {
                            "id": item.id,
                            "key": item.item_key,
                            "instrument": item.instrument,
                            "type": item.item_type,
                            "prompt": item.prompt,
                            "options": item.options_json or [],
                            "pairs": (item.metadata_json or {}).get("pairs") or [],
                            "evaluable": item.evaluable,
                            "max_attempts": item.max_attempts,
                        }
                        for item in items
                    ],
                }
            )
        data.append(
            {
                "id": lr.id,
                "code": lr.code,
                "title": lr.title,
                "description": lr.description,
                "position": lr.position,
                "criteria": ce_rows,
            }
        )
    return {
        "course_module_id": course_module.id,
        "module_id": course_module.module_id,
        "role": membership.role,
        "config": config,
        "learning_results": data,
    }


@router.get("/course-modules/{course_module_id}/readiness")
def evaluation_readiness(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    course_module = _teacher_course_module(db, course_module_id, user_id)
    items = db.execute(
        select(AssessmentItem, AssessmentKey)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .join(
            LearningResult,
            LearningResult.id == AssessmentCriterion.learning_result_id,
        )
        .outerjoin(AssessmentKey, AssessmentKey.item_id == AssessmentItem.id)
        .where(
            LearningResult.module_id == course_module.module_id,
            AssessmentItem.active.is_(True),
            AssessmentItem.evaluable.is_(True),
        )
    ).all()
    missing = [item.item_key for item, key in items if not key or not key.active]
    semantic = [
        item.item_key for item, _ in items if item.item_type in SEMANTIC_KINDS
    ]
    ai = resolve_ai_for_course_module(db, course_module_id)
    return {
        "course_module_id": course_module_id,
        "items_total": len(items),
        "keys_loaded": len(items) - len(missing),
        "missing_keys": missing,
        "semantic_items": len(semantic),
        "ai_enabled": bool(ai),
        "ai_teacher_user_id": ai.get("teacher_user_id") if ai else None,
        "ready_for_objective_grading": not missing,
        "config": _effective_config(db, course_module),
    }


@router.post("/course-modules/{course_module_id}/items/{item_id}/attempts")
def start_attempt(
    course_module_id: int,
    item_id: int,
    payload: StartAttemptIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    course_module, _ = _course_module_access(db, course_module_id, user_id)
    item, criterion, lr = _item_belongs_to_module(
        db, item_id, course_module.module_id
    )
    active = db.scalar(
        select(AssessmentAttempt)
        .where(
            AssessmentAttempt.course_module_id == course_module_id,
            AssessmentAttempt.user_id == user_id,
            AssessmentAttempt.item_id == item_id,
            AssessmentAttempt.status == "started",
        )
        .order_by(AssessmentAttempt.attempt_no.desc())
    )
    if active:
        return {
            "attempt_id": active.id,
            "attempt_no": active.attempt_no,
            "resumed": True,
            "item_id": item.id,
            "criterion": criterion.code,
            "learning_result": lr.code,
        }

    used = db.scalar(
        select(func.count(AssessmentAttempt.id)).where(
            AssessmentAttempt.course_module_id == course_module_id,
            AssessmentAttempt.user_id == user_id,
            AssessmentAttempt.item_id == item_id,
        )
    ) or 0
    limit = int(item.max_attempts or 1)
    if used >= limit:
        raise HTTPException(status_code=409, detail="No quedan intentos disponibles")

    attempt = AssessmentAttempt(
        course_module_id=course_module_id,
        user_id=user_id,
        item_id=item_id,
        attempt_no=used + 1,
        status="started",
        metadata_json=payload.metadata or {},
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return {
        "attempt_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "resumed": False,
        "max_attempts": limit,
        "item_id": item.id,
        "criterion": criterion.code,
        "learning_result": lr.code,
    }


@router.post("/attempts/{attempt_id}/submit")
async def submit_attempt(
    attempt_id: int,
    payload: SubmitAttemptIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    attempt = db.get(AssessmentAttempt, attempt_id)
    if not attempt or attempt.user_id != user_id:
        raise HTTPException(status_code=404, detail="Intento no encontrado")
    if attempt.status != "started":
        raise HTTPException(status_code=409, detail="Este intento ya no está abierto")

    course_module, _ = _course_module_access(db, attempt.course_module_id, user_id)
    item, criterion, lr = _item_belongs_to_module(
        db, attempt.item_id, course_module.module_id
    )
    key = db.scalar(
        select(AssessmentKey).where(
            AssessmentKey.item_id == item.id,
            AssessmentKey.active.is_(True),
        )
    )
    attempt.response_json = {"value": payload.response}
    attempt.metadata_json = {**(attempt.metadata_json or {}), **(payload.metadata or {})}
    attempt.submitted_at = _now()
    attempt.status = "submitted"

    score: float | None = None
    correct: bool | None = None
    pending_review = False
    method = "teacher"
    feedback = ""

    expected = _unwrap_answer(key)
    if key and key.public_hash and item.public_hash and key.public_hash != item.public_hash:
        raise HTTPException(
            status_code=409,
            detail="La clave privada ya no corresponde al banco público actual",
        )

    local_score = _objective_score(item.item_type, payload.response, expected) if key else None
    if local_score is not None:
        score = float(local_score)
        correct = score >= float(criterion.pass_score)
        method = "deterministic"
        feedback = key.feedback if key else ""
    elif item.item_type in SEMANTIC_KINDS and key:
        ai = resolve_ai_for_course_module(db, course_module.id)
        if ai and item.item_type in set(ai.get("allowed_kinds") or []):
            try:
                proposal = await grade_with_ai(
                    ai,
                    response_value=payload.response,
                    reference_answer=expected,
                    context={
                        "learning_result": lr.code,
                        "criterion": criterion.code,
                        "criterion_title": criterion.title,
                        "item_key": item.item_key,
                        "item_type": item.item_type,
                        "prompt": item.prompt,
                    },
                    rubric=(item.metadata_json or {}).get("rubric") or ai.get("default_rubric") or "",
                )
                auto_accept = bool(ai.get("auto_review")) and (
                    proposal["confidence"] >= float(ai.get("confidence_threshold", 0.75))
                )
                review = _upsert_review(
                    db,
                    attempt,
                    ai_teacher_user_id=ai.get("teacher_user_id"),
                    source="ai",
                    proposed_score=proposal["score"],
                    confidence=proposal["confidence"],
                    verdict=proposal["verdict"],
                    feedback=proposal["feedback"],
                    breakdown=proposal["breakdown"],
                    status="accepted" if auto_accept else "pending",
                )
                if auto_accept:
                    score = proposal["score"]
                    correct = score >= float(criterion.pass_score)
                    pending_review = False
                    method = "ai-auto"
                    feedback = proposal["feedback"]
                    review.teacher_score = score
                    review.teacher_feedback = "Aceptación automática por umbral de confianza."
                    review.reviewed_at = _now()
                else:
                    pending_review = True
                    method = "ai-proposal"
                    feedback = proposal["feedback"]
            except RuntimeError as exc:
                pending_review = True
                method = "teacher"
                feedback = "La IA no pudo proponer una corrección. Pendiente de revisión docente."
                _upsert_review(
                    db,
                    attempt,
                    ai_teacher_user_id=ai.get("teacher_user_id"),
                    source="ai-error",
                    confidence=0,
                    verdict="error",
                    feedback=str(exc)[:1200],
                    status="pending",
                )
        else:
            pending_review = True
            feedback = "Pendiente de revisión docente."
            _upsert_review(
                db,
                attempt,
                ai_teacher_user_id=None,
                source="teacher",
                feedback=feedback,
                status="pending",
            )
    else:
        pending_review = True
        feedback = (
            "No hay clave privada cargada para esta actividad. "
            "La respuesta queda guardada y pendiente de revisión docente."
        )
        _upsert_review(
            db,
            attempt,
            ai_teacher_user_id=None,
            source="teacher",
            feedback=feedback,
            status="pending",
        )

    attempt.score = score
    attempt.correct = correct
    attempt.pending_review = pending_review
    attempt.metadata_json = {
        **(attempt.metadata_json or {}),
        "grading_method": method,
        "feedback": feedback,
    }
    db.flush()
    progress = recompute_learning_result(
        db,
        course_module.id,
        user_id,
        lr.id,
    )
    db.commit()
    return {
        "attempt_id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "score": attempt.score,
        "correct": attempt.correct,
        "pending_review": attempt.pending_review,
        "grading_method": method,
        "feedback": feedback,
        "progress": progress,
    }


@router.get("/course-modules/{course_module_id}/my-results")
def my_results(
    course_module_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    course_module, _ = _course_module_access(db, course_module_id, user_id)
    lrs = list(
        db.scalars(
            select(LearningResult)
            .where(
                LearningResult.module_id == course_module.module_id,
                LearningResult.active.is_(True),
            )
            .order_by(LearningResult.position, LearningResult.id)
        )
    )
    results = []
    for lr in lrs:
        results.append(recompute_learning_result(db, course_module_id, user_id, lr.id))
    db.commit()
    return results


@router.get("/course-modules/{course_module_id}/gradebook")
def gradebook(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    course_module = _teacher_course_module(db, course_module_id, teacher_id)
    students = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.course_id == course_module.course_id,
            Membership.active.is_(True),
            Membership.role == "student",
            User.active.is_(True),
        )
        .order_by(User.display_name)
    ).all()
    lrs = list(
        db.scalars(
            select(LearningResult)
            .where(
                LearningResult.module_id == course_module.module_id,
                LearningResult.active.is_(True),
            )
            .order_by(LearningResult.position, LearningResult.id)
        )
    )
    pending_counts = dict(
        db.execute(
            select(
                AssessmentAttempt.user_id,
                func.count(AssessmentReview.id),
            )
            .join(
                AssessmentReview,
                AssessmentReview.attempt_id == AssessmentAttempt.id,
            )
            .where(
                AssessmentAttempt.course_module_id == course_module_id,
                AssessmentReview.status == "pending",
            )
            .group_by(AssessmentAttempt.user_id)
        ).all()
    )
    rows = []
    for _, user in students:
        ra_results = [
            recompute_learning_result(
                db, course_module_id, user.id, lr.id
            )
            for lr in lrs
        ]
        rows.append(
            {
                "user_id": user.id,
                "display_name": user.display_name,
                "email": user.email,
                "pending_reviews": int(pending_counts.get(user.id, 0)),
                "learning_results": ra_results,
            }
        )
    db.commit()
    return {
        "course_module_id": course_module_id,
        "module_id": course_module.module_id,
        "students": rows,
    }


@router.get("/course-modules/{course_module_id}/reviews")
def review_queue(
    course_module_id: int,
    status: str = "pending",
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    teacher_id = int(session["sub"])
    _teacher_course_module(db, course_module_id, teacher_id)
    stmt = (
        select(
            AssessmentReview,
            AssessmentAttempt,
            AssessmentItem,
            AssessmentCriterion,
            LearningResult,
            User,
        )
        .join(
            AssessmentAttempt,
            AssessmentAttempt.id == AssessmentReview.attempt_id,
        )
        .join(AssessmentItem, AssessmentItem.id == AssessmentAttempt.item_id)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .join(
            LearningResult,
            LearningResult.id == AssessmentCriterion.learning_result_id,
        )
        .join(User, User.id == AssessmentAttempt.user_id)
        .where(AssessmentAttempt.course_module_id == course_module_id)
        .order_by(AssessmentReview.created_at.desc(), AssessmentReview.id.desc())
    )
    if status != "all":
        stmt = stmt.where(AssessmentReview.status == status)
    rows = db.execute(stmt).all()
    return [
        _review_dict(review, attempt, item, criterion, lr, user)
        for review, attempt, item, criterion, lr, user in rows
    ]


@router.put("/reviews/{review_id}")
def decide_review(
    review_id: int,
    payload: ReviewDecisionIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    review = db.get(AssessmentReview, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Revisión no encontrada")
    attempt = db.get(AssessmentAttempt, review.attempt_id)
    if not attempt:
        raise HTTPException(status_code=404, detail="Intento no encontrado")
    course_module = _teacher_course_module(db, attempt.course_module_id, teacher_id)
    item, criterion, lr = _item_belongs_to_module(
        db, attempt.item_id, course_module.module_id
    )

    final_score = float(payload.score) if payload.status == "accepted" else 0.0
    attempt.score = final_score
    attempt.correct = final_score >= float(criterion.pass_score)
    attempt.pending_review = False
    attempt.metadata_json = {
        **(attempt.metadata_json or {}),
        "grading_method": "teacher-review",
        "teacher_feedback": payload.feedback,
        "review_id": review.id,
    }
    review.status = payload.status
    review.reviewed_by_user_id = teacher_id
    review.teacher_score = final_score
    review.teacher_feedback = payload.feedback
    review.reviewed_at = _now()

    progress = recompute_learning_result(
        db,
        attempt.course_module_id,
        attempt.user_id,
        lr.id,
    )
    db.commit()
    return {
        "ok": True,
        "review_id": review.id,
        "score": final_score,
        "correct": attempt.correct,
        "status": review.status,
        "progress": progress,
    }


@router.put("/modules/{module_id}/private-keys")
def teacher_private_keys(
    module_id: int,
    payload: PrivateKeysIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    _module_editor(db, module_id, int(session["sub"]))
    if not db.get(Module, module_id):
        raise HTTPException(status_code=404, detail="Módulo no encontrado")
    return _load_private_keys(db, module_id, payload)


@router.put(
    "/admin/modules/{module_id}/private-keys",
    dependencies=[Depends(require_admin)],
)
def admin_private_keys(
    module_id: int,
    payload: PrivateKeysIn,
    db: Session = Depends(get_db),
) -> dict:
    if not db.get(Module, module_id):
        raise HTTPException(status_code=404, detail="Módulo no encontrado")
    return _load_private_keys(db, module_id, payload)
