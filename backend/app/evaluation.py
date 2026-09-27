from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import random
import zipfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


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
        "recovery_items_per_ce": int(defaults.get("recovery_items_per_ce", 2)),
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


def _objective_score(
    kind: str,
    given: object,
    expected: object,
    options: list | None = None,
) -> float | None:
    if kind == "multi":
        if not isinstance(given, list) or not isinstance(expected, list):
            return 0.0
        return 100.0 if sorted(map(str, given)) == sorted(map(str, expected)) else 0.0
    if kind == "order":
        if not isinstance(given, list) or not isinstance(expected, list):
            return 0.0
        if given == expected:
            return 100.0
        values = list(options or [])
        try:
            if values and all(isinstance(v, int) and not isinstance(v, bool) for v in given):
                given_as_values = [values[int(index)] for index in given]
                if given_as_values == expected:
                    return 100.0
            if values and all(isinstance(v, int) and not isinstance(v, bool) for v in expected):
                given_as_indices = [values.index(value) for value in given]
                if given_as_indices == expected:
                    return 100.0
        except (IndexError, ValueError, TypeError):
            pass
        return 0.0
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
    exam_by_ce: dict[str, dict] = {}
    if latest_exam:
        exam_payload = dict(latest_exam.response_json or {})
        exam_by_ce = dict(exam_payload.get("by_ce") or {})
        snapshot = exam_payload.get("config")
        if isinstance(snapshot, dict):
            # La configuración usada al empezar el examen manda para ese intento:
            # cambios docentes posteriores no reescriben resultados históricos.
            config = {**config, **snapshot}

    existing_plan = db.scalar(
        select(RecoveryPlan).where(
            RecoveryPlan.course_module_id == course_module_id,
            RecoveryPlan.user_id == user_id,
            RecoveryPlan.learning_result_id == learning_result_id,
        )
    )

    details: dict[str, dict] = {}
    portfolio_values: list[float] = []
    exam_values: list[float] = []
    final_values: list[float] = []
    original_passed_count = 0
    effective_passed_count = 0
    portfolio_passed_count = 0
    all_portfolio_complete = True
    all_exam_complete = bool(latest_exam)

    pw = float(config["portfolio_weight"]) / 100.0
    ew = float(config["exam_weight"]) / 100.0

    for criterion in criteria:
        portfolio_items = list(
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
        portfolio_attempts = _latest_attempts_for_items(
            db,
            course_module_id,
            user_id,
            [item.id for item in portfolio_items],
        )
        portfolio_scored = [
            float(portfolio_attempts[item.id].score)
            for item in portfolio_items
            if item.id in portfolio_attempts
            and portfolio_attempts[item.id].score is not None
        ]
        portfolio_pending = sum(
            1
            for item in portfolio_items
            if item.id in portfolio_attempts
            and portfolio_attempts[item.id].pending_review
        )
        portfolio_complete = (
            bool(portfolio_items)
            and len(portfolio_scored) == len(portfolio_items)
            and portfolio_pending == 0
        )
        all_portfolio_complete = all_portfolio_complete and portfolio_complete
        portfolio_ce = (
            round(sum(portfolio_scored) / len(portfolio_scored), 2)
            if portfolio_scored
            else 0.0
        )
        portfolio_values.append(portfolio_ce)
        portfolio_passed = (
            portfolio_complete
            and portfolio_ce >= float(criterion.pass_score)
        )
        if portfolio_passed:
            portfolio_passed_count += 1

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
        original_passed = False
        if config["exam_enabled"] and portfolio_complete and exam_complete:
            final_ce = round(portfolio_ce * pw + float(exam_ce) * ew, 2)
            original_passed = final_ce >= float(config["ce_pass_score"])
            final_values.append(final_ce)
        if original_passed:
            original_passed_count += 1

        recovery_items = list(
            db.scalars(
                select(AssessmentItem)
                .where(
                    AssessmentItem.criterion_id == criterion.id,
                    AssessmentItem.instrument == "recovery",
                    AssessmentItem.active.is_(True),
                    AssessmentItem.evaluable.is_(True),
                )
                .order_by(AssessmentItem.position, AssessmentItem.id)
            )
        )
        recovery_attempts = _latest_attempts_for_items(
            db,
            course_module_id,
            user_id,
            [item.id for item in recovery_items],
        )
        recovery_scored = [
            float(recovery_attempts[item.id].score)
            for item in recovery_items
            if item.id in recovery_attempts
            and recovery_attempts[item.id].score is not None
        ]
        recovery_pending = sum(
            1
            for item in recovery_items
            if item.id in recovery_attempts
            and recovery_attempts[item.id].pending_review
        )
        recovery_complete = (
            bool(recovery_items)
            and len(recovery_scored) == len(recovery_items)
            and recovery_pending == 0
        )
        recovery_score = (
            round(sum(recovery_scored) / len(recovery_scored), 2)
            if recovery_scored
            else None
        )
        recovered = (
            not original_passed
            and recovery_complete
            and float(recovery_score or 0) >= float(config["ce_pass_score"])
        )
        effective_passed = original_passed or recovered
        if effective_passed:
            effective_passed_count += 1

        details[criterion.code] = {
            "criterion_id": criterion.id,
            "portfolio": portfolio_ce,
            "portfolio_items_total": len(portfolio_items),
            "portfolio_items_scored": len(portfolio_scored),
            "pending_review": portfolio_pending,
            "portfolio_complete": portfolio_complete,
            "portfolio_passed": portfolio_passed,
            "exam": exam_ce,
            "exam_questions": exam_n,
            "final": final_ce,
            "passed_original": original_passed,
            "recovery": recovery_score,
            "recovery_items_total": len(recovery_items),
            "recovery_items_scored": len(recovery_scored),
            "recovery_pending_review": recovery_pending,
            "recovery_complete": recovery_complete,
            "recovered": recovered,
            "passed": effective_passed,
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
    needed = (
        math.ceil(criteria_total * int(config["ce_pass_percent"]) / 100)
        if criteria_total
        else 0
    )

    final_score = None
    ra_passed = False
    remaining_recovery: list[str] = []

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
        remaining_recovery = [
            code for code, data in details.items() if not data.get("passed")
        ]
        ra_passed = (
            final_score >= float(config["pass_score"])
            and effective_passed_count >= needed
            and both_ok
        )
        if ra_passed:
            final_state = "passed"
        elif remaining_recovery:
            final_state = "recovery-required"
        else:
            # Todos los CE están superados/recuperados pero la nota numérica global
            # o el requisito de ambos instrumentos impide superar el RA.
            final_state = "not-passed"

    recovery_plan = existing_plan
    if final_score is not None and not ra_passed:
        if not recovery_plan:
            recovery_plan = RecoveryPlan(
                course_module_id=course_module_id,
                user_id=user_id,
                learning_result_id=learning_result_id,
            )
            db.add(recovery_plan)
        recovery_plan.criteria_json = remaining_recovery
        recovery_plan.status = (
            "pending" if remaining_recovery else "completed-no-pass"
        )
        recovery_plan.updated_at = _now()
    elif recovery_plan and ra_passed:
        recovery_plan.criteria_json = []
        recovery_plan.status = "passed"
        recovery_plan.updated_at = _now()

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
    result.criteria_passed = effective_passed_count
    result.criteria_total = criteria_total
    result.passed = ra_passed
    result.details_json = {
        "status": final_state,
        "criteria_needed": needed,
        "criteria_passed_original": original_passed_count,
        "portfolio_criteria_passed": portfolio_passed_count,
        "criteria": details,
        "recovery": remaining_recovery,
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
        "criteria_passed": effective_passed_count,
        "criteria_passed_original": original_passed_count,
        "criteria_total": criteria_total,
        "criteria_needed": needed,
        "portfolio_criteria_passed": portfolio_passed_count,
        "passed": ra_passed,
        "status": final_state,
        "recovery": remaining_recovery,
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


class RecoveryQuestionIn(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    ce: str = Field(min_length=2, max_length=40)
    kind: str = Field(
        default="choice",
        pattern=r"^(choice|tf|multi|order|match|free|text|case|calculation)$",
    )
    prompt: str = Field(min_length=1, max_length=10000)
    options: list[object] = []
    answer: object
    feedback: str = Field(default="", max_length=5000)
    pairs: list[object] = []


class RecoveryBankIn(BaseModel):
    items: list[RecoveryQuestionIn]
    source: str = Field(default="private-recovery-bank", max_length=80)


def _load_recovery_bank(
    db: Session,
    module_id: int,
    learning_result_id: int,
    payload: RecoveryBankIn,
) -> dict:
    lr = db.get(LearningResult, learning_result_id)
    if not lr or lr.module_id != module_id or not lr.active:
        raise HTTPException(status_code=404, detail="RA no encontrado en el módulo")

    criteria = list(
        db.scalars(
            select(AssessmentCriterion).where(
                AssessmentCriterion.learning_result_id == learning_result_id,
                AssessmentCriterion.active.is_(True),
            )
        )
    )
    by_code = {criterion.code: criterion for criterion in criteria}
    supplied: set[tuple[int, str]] = set()
    imported = 0

    for position, question in enumerate(payload.items, start=1):
        criterion = by_code.get(question.ce)
        if not criterion:
            raise HTTPException(
                status_code=400,
                detail=f"El CE {question.ce} no pertenece a {lr.code}",
            )
        supplied.add((criterion.id, question.id))
        item = db.scalar(
            select(AssessmentItem).where(
                AssessmentItem.criterion_id == criterion.id,
                AssessmentItem.instrument == "recovery",
                AssessmentItem.item_key == question.id,
            )
        )
        if not item:
            item = AssessmentItem(
                criterion_id=criterion.id,
                instrument="recovery",
                item_key=question.id,
                item_type=question.kind,
                prompt=question.prompt,
                options_json=question.options or [],
                evaluable=True,
                max_attempts=1,
                position=position,
                metadata_json={
                    "source": payload.source,
                    "pairs": question.pairs or [],
                },
                active=True,
            )
            db.add(item)
            db.flush()
        item.item_type = question.kind
        item.prompt = question.prompt
        item.options_json = question.options or []
        item.evaluable = True
        item.max_attempts = 1
        item.position = position
        item.metadata_json = {
            "source": payload.source,
            "pairs": question.pairs or [],
        }
        item.active = True

        key = db.scalar(
            select(AssessmentKey).where(AssessmentKey.item_id == item.id)
        )
        if not key:
            key = AssessmentKey(item_id=item.id)
            db.add(key)
        key.answer_json = {"value": question.answer}
        key.feedback = question.feedback
        key.public_hash = None
        key.source = payload.source
        key.active = True
        key.updated_at = _now()
        imported += 1

    existing = db.execute(
        select(AssessmentItem, AssessmentCriterion)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .where(
            AssessmentCriterion.learning_result_id == learning_result_id,
            AssessmentItem.instrument == "recovery",
            AssessmentItem.active.is_(True),
        )
    ).all()
    deactivated = 0
    for item, criterion in existing:
        if (criterion.id, item.item_key) not in supplied:
            item.active = False
            key = db.scalar(
                select(AssessmentKey).where(AssessmentKey.item_id == item.id)
            )
            if key:
                key.active = False
            deactivated += 1

    db.commit()
    return {
        "module_id": module_id,
        "learning_result_id": learning_result_id,
        "items": imported,
        "deactivated": deactivated,
    }


class ExamQuestionIn(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    ce: str = Field(min_length=2, max_length=40)
    q: str = Field(min_length=1, max_length=10000)
    options: list[object] = []
    answer: object
    type: str = Field(default="choice", pattern=r"^(choice|tf|multi)$")
    feedback: str = Field(default="", max_length=5000)


class ExamBankIn(BaseModel):
    questions: list[ExamQuestionIn]
    source: str = Field(default="private-exam-bank", max_length=80)


class ExamDraftIn(BaseModel):
    answers: dict[str, object] = {}


class ExamEventIn(BaseModel):
    event: str = Field(min_length=1, max_length=80)
    detail: dict = {}


class ExamSubmitIn(BaseModel):
    answers: dict[str, object] = {}
    timeout: bool = False


def _load_exam_bank(
    db: Session,
    module_id: int,
    learning_result_id: int,
    payload: ExamBankIn,
) -> dict:
    lr = db.get(LearningResult, learning_result_id)
    if not lr or lr.module_id != module_id or not lr.active:
        raise HTTPException(status_code=404, detail="RA no encontrado en el módulo")

    criteria = list(
        db.scalars(
            select(AssessmentCriterion).where(
                AssessmentCriterion.learning_result_id == learning_result_id,
                AssessmentCriterion.active.is_(True),
            )
        )
    )
    by_code = {criterion.code: criterion for criterion in criteria}
    supplied_keys = set()
    imported = 0

    for position, question in enumerate(payload.questions, start=1):
        criterion = by_code.get(question.ce)
        if not criterion:
            raise HTTPException(
                status_code=400,
                detail=f"El CE {question.ce} no pertenece a {lr.code}",
            )
        if question.type == "choice":
            if (
                not isinstance(question.answer, int)
                or isinstance(question.answer, bool)
                or question.answer < 0
                or question.answer >= len(question.options)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Respuesta choice no válida: {question.id}",
                )
        elif question.type == "tf":
            if not isinstance(question.answer, bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"Respuesta V/F no válida: {question.id}",
                )
        elif question.type == "multi":
            if (
                not isinstance(question.answer, list)
                or not question.answer
                or any(
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or index < 0
                    or index >= len(question.options)
                    for index in question.answer
                )
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Respuesta múltiple no válida: {question.id}",
                )

        supplied_keys.add((criterion.id, question.id))
        item = db.scalar(
            select(AssessmentItem).where(
                AssessmentItem.criterion_id == criterion.id,
                AssessmentItem.instrument == "exam",
                AssessmentItem.item_key == question.id,
            )
        )
        if not item:
            item = AssessmentItem(
                criterion_id=criterion.id,
                instrument="exam",
                item_key=question.id,
                item_type=question.type,
                prompt=question.q,
                options_json=question.options or [],
                evaluable=True,
                max_attempts=1,
                position=position,
                metadata_json={"source": payload.source},
                active=True,
            )
            db.add(item)
            db.flush()
        item.item_type = question.type
        item.prompt = question.q
        item.options_json = question.options or []
        item.public_hash = None
        item.evaluable = True
        item.max_attempts = 1
        item.position = position
        item.metadata_json = {"source": payload.source}
        item.active = True

        key = db.scalar(select(AssessmentKey).where(AssessmentKey.item_id == item.id))
        if not key:
            key = AssessmentKey(item_id=item.id)
            db.add(key)
        key.answer_json = {"value": question.answer}
        key.feedback = question.feedback
        key.public_hash = None
        key.source = payload.source
        key.active = True
        key.updated_at = _now()
        imported += 1

    existing = db.execute(
        select(AssessmentItem, AssessmentCriterion)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .where(
            AssessmentCriterion.learning_result_id == learning_result_id,
            AssessmentItem.instrument == "exam",
            AssessmentItem.active.is_(True),
        )
    ).all()
    deactivated = 0
    for item, criterion in existing:
        if (criterion.id, item.item_key) not in supplied_keys:
            item.active = False
            key = db.scalar(
                select(AssessmentKey).where(AssessmentKey.item_id == item.id)
            )
            if key:
                key.active = False
            deactivated += 1

    db.commit()
    return {
        "module_id": module_id,
        "learning_result_id": learning_result_id,
        "questions": imported,
        "deactivated": deactivated,
    }


def _load_private_keys(
    db: Session,
    module_id: int,
    payload: PrivateKeysIn,
    learning_result_id: int | None = None,
) -> dict:
    stmt = (
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
            AssessmentItem.instrument == "portfolio",
            AssessmentItem.active.is_(True),
            AssessmentItem.evaluable.is_(True),
        )
    )
    if learning_result_id is not None:
        stmt = stmt.where(LearningResult.id == learning_result_id)
    module_items = db.execute(stmt).all()
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
                        AssessmentItem.instrument.in_(["portfolio", "practice"]),
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
    config = _effective_config(db, course_module)
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == course_module.module_id,
            ModulePermission.user_id == user_id,
        )
    )
    can_manage_private_banks = bool(
        permission and permission.permission in {"owner", "editor"}
    )

    portfolio_rows = db.execute(
        select(
            AssessmentItem,
            AssessmentKey,
            AssessmentCriterion,
            LearningResult,
        )
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
            AssessmentItem.instrument == "portfolio",
            AssessmentItem.active.is_(True),
            AssessmentItem.evaluable.is_(True),
        )
    ).all()
    missing = [
        item.item_key
        for item, key, _, _ in portfolio_rows
        if not key or not key.active
    ]
    semantic = [
        item.item_key
        for item, _, _, _ in portfolio_rows
        if item.item_type in SEMANTIC_KINDS
    ]

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
    coverage = {}
    exam_min = int(config["exam_questions_per_ce"])
    recovery_min = int(config["recovery_items_per_ce"])
    exam_ready = True
    recovery_ready = True

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
        ce_data = {}
        for criterion in criteria:
            portfolio_count = int(
                db.scalar(
                    select(func.count(AssessmentItem.id)).where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.instrument == "portfolio",
                        AssessmentItem.active.is_(True),
                    )
                )
                or 0
            )
            portfolio_keys = int(
                db.scalar(
                    select(func.count(AssessmentKey.id))
                    .join(
                        AssessmentItem,
                        AssessmentItem.id == AssessmentKey.item_id,
                    )
                    .where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.instrument == "portfolio",
                        AssessmentItem.active.is_(True),
                        AssessmentKey.active.is_(True),
                    )
                )
                or 0
            )
            exam_count = int(
                db.scalar(
                    select(func.count(AssessmentItem.id)).where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.instrument == "exam",
                        AssessmentItem.active.is_(True),
                    )
                )
                or 0
            )
            recovery_count = int(
                db.scalar(
                    select(func.count(AssessmentItem.id)).where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.instrument == "recovery",
                        AssessmentItem.active.is_(True),
                    )
                )
                or 0
            )
            ce_exam_ready = exam_count >= exam_min
            ce_recovery_ready = recovery_count >= recovery_min
            exam_ready = exam_ready and ce_exam_ready
            recovery_ready = recovery_ready and ce_recovery_ready
            ce_data[criterion.code] = {
                "portfolio_items": portfolio_count,
                "portfolio_keys": portfolio_keys,
                "portfolio_ready": (
                    portfolio_count > 0 and portfolio_keys == portfolio_count
                ),
                "exam_questions": exam_count,
                "exam_required": exam_min,
                "exam_ready": ce_exam_ready,
                "recovery_items": recovery_count,
                "recovery_required": recovery_min,
                "recovery_ready": ce_recovery_ready,
            }
        coverage[lr.code] = ce_data

    ai = resolve_ai_for_course_module(db, course_module_id)
    portfolio_ready = not missing and bool(portfolio_rows)
    return {
        "course_module_id": course_module_id,
        "module_id": course_module.module_id,
        "items_total": len(portfolio_rows),
        "keys_loaded": len(portfolio_rows) - len(missing),
        "missing_keys": missing,
        "semantic_items": len(semantic),
        "portfolio_ready": portfolio_ready,
        "exam_ready": exam_ready and bool(lrs),
        "recovery_ready": recovery_ready and bool(lrs),
        "ready_for_evaluation": (
            portfolio_ready
            and (not config["exam_enabled"] or exam_ready)
            and recovery_ready
        ),
        "ai_enabled": bool(ai),
        "ai_teacher_user_id": ai.get("teacher_user_id") if ai else None,
        "can_manage_private_banks": can_manage_private_banks,
        "coverage": coverage,
        "config": config,
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
    config = _effective_config(db, course_module)
    configured_limits = {
        "practice": config.get("practice_max_attempts"),
        "portfolio": config.get("portfolio_max_attempts"),
        "recovery": config.get("recovery_max_attempts"),
    }
    configured = configured_limits.get(item.instrument)
    limit = int(configured if configured is not None else (item.max_attempts or 1))
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

    local_score = (
        _objective_score(
            item.item_type,
            payload.response,
            expected,
            item.options_json or [],
        )
        if key
        else None
    )
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


async def _read_private_bank_bundle(file: UploadFile) -> list[dict]:
    raw = await file.read()
    if len(raw) > 32 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El paquete privado supera 32 MB")
    documents: list[dict] = []
    filename = (file.filename or "").lower()
    try:
        if filename.endswith(".json"):
            parsed = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=400, detail="El JSON privado debe ser un objeto")
            documents.append(parsed)
        else:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = [
                    info for info in archive.infolist()
                    if not info.is_dir() and info.filename.lower().endswith(".json")
                ]
                if not infos or len(infos) > 100:
                    raise HTTPException(
                        status_code=400,
                        detail="El ZIP debe contener entre 1 y 100 JSON privados",
                    )
                total = sum(info.file_size for info in infos)
                if total > 64 * 1024 * 1024:
                    raise HTTPException(status_code=400, detail="El ZIP privado expande demasiado")
                for info in infos:
                    if info.filename.startswith("/") or ".." in info.filename.replace("\\", "/").split("/"):
                        raise HTTPException(status_code=400, detail="Ruta insegura en el ZIP privado")
                    parsed = json.loads(archive.read(info).decode("utf-8-sig"))
                    if not isinstance(parsed, dict):
                        raise HTTPException(
                            status_code=400,
                            detail=f"{info.filename}: el JSON debe ser un objeto",
                        )
                    documents.append(parsed)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="ZIP privado no válido") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="JSON privado no válido") from exc
    return documents


def _lr_lookup(db: Session, module_id: int) -> dict[str, LearningResult]:
    rows = list(
        db.scalars(
            select(LearningResult).where(
                LearningResult.module_id == module_id,
                LearningResult.active.is_(True),
            )
        )
    )
    lookup: dict[str, LearningResult] = {}
    for lr in rows:
        lookup[lr.code] = lr
        legacy = str((lr.metadata_json or {}).get("legacy_course_id") or "").strip()
        if legacy:
            lookup[legacy] = lr
    return lookup


def _validate_bank_coverage(
    db: Session,
    lr: LearningResult,
    *,
    exam_min_per_ce: int,
    recovery_min_per_ce: int,
) -> dict:
    criteria = list(
        db.scalars(
            select(AssessmentCriterion).where(
                AssessmentCriterion.learning_result_id == lr.id,
                AssessmentCriterion.active.is_(True),
            )
        )
    )
    summary = {}
    for instrument, minimum in (
        ("exam", exam_min_per_ce),
        ("recovery", recovery_min_per_ce),
    ):
        counts = {}
        for criterion in criteria:
            counts[criterion.code] = int(
                db.scalar(
                    select(func.count(AssessmentItem.id)).where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.instrument == instrument,
                        AssessmentItem.active.is_(True),
                        AssessmentItem.evaluable.is_(True),
                    )
                )
                or 0
            )
        insufficient = {
            code: count for code, count in counts.items() if count < minimum
        }
        summary[instrument] = {
            "minimum_per_ce": minimum,
            "counts": counts,
            "insufficient": insufficient,
        }
    return summary


def _import_private_documents(
    db: Session,
    module_id: int,
    documents: list[dict],
) -> dict:
    module = db.get(Module, module_id)
    if not module or not module.active:
        raise HTTPException(status_code=404, detail="Módulo no encontrado")
    lookup = _lr_lookup(db, module_id)
    if not lookup:
        raise HTTPException(status_code=409, detail="El módulo no tiene RA definidos")

    seen: set[tuple[int, str]] = set()
    imported = {"portfolio": 0, "exam": 0, "recovery": 0}
    touched: dict[int, LearningResult] = {}

    for document in documents:
        kind = str(document.get("kind") or "").strip().lower()
        reference = str(
            document.get("course_id")
            or document.get("learning_result")
            or document.get("ra")
            or ""
        ).strip()
        lr = lookup.get(reference)
        if not lr:
            raise HTTPException(
                status_code=400,
                detail=f"No se reconoce el RA/curso privado: {reference or '(vacío)'}",
            )
        key = (lr.id, kind)
        if key in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Banco duplicado para {lr.code}/{kind}",
            )
        seen.add(key)
        touched[lr.id] = lr

        if kind == "portfolio":
            entries = []
            for item in document.get("items") or []:
                entries.append(
                    PrivateKeyItemIn(
                        id=item.get("id"),
                        ce=item.get("ce"),
                        kind=item.get("kind"),
                        answer=item.get("answer"),
                        public_hash=item.get("public_hash"),
                        feedback=item.get("feedback") or "",
                    )
                )
            result = _load_private_keys(
                db,
                module_id,
                PrivateKeysIn(
                    items=entries,
                    strict=True,
                    source="private-bundle",
                ),
                learning_result_id=lr.id,
            )
            imported["portfolio"] += int(result["items"])
        elif kind == "exam":
            questions = [
                ExamQuestionIn(
                    id=q.get("id"),
                    ce=q.get("ce"),
                    q=q.get("q"),
                    options=q.get("options") or [],
                    answer=q.get("answer"),
                    type=q.get("type") or "choice",
                    feedback=q.get("feedback") or "",
                )
                for q in document.get("questions") or []
            ]
            result = _load_exam_bank(
                db,
                module_id,
                lr.id,
                ExamBankIn(questions=questions, source="private-bundle"),
            )
            imported["exam"] += int(result["questions"])
        elif kind == "recovery":
            items = [
                RecoveryQuestionIn(
                    id=q.get("id"),
                    ce=q.get("ce"),
                    kind=q.get("kind"),
                    prompt=q.get("prompt"),
                    options=q.get("options") or [],
                    answer=q.get("answer"),
                    feedback=q.get("feedback") or "",
                    pairs=q.get("pairs") or [],
                )
                for q in document.get("items") or []
            ]
            result = _load_recovery_bank(
                db,
                module_id,
                lr.id,
                RecoveryBankIn(items=items, source="private-bundle"),
            )
            imported["recovery"] += int(result["items"])
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Tipo de banco privado no reconocido: {kind or '(vacío)'}",
            )

    defaults = dict((module.metadata_json or {}).get("evaluation_defaults") or {})
    exam_min = int(defaults.get("exam_questions_per_ce", 3))
    recovery_min = int(defaults.get("recovery_items_per_ce", 2))
    coverage = {
        lr.code: _validate_bank_coverage(
            db,
            lr,
            exam_min_per_ce=exam_min,
            recovery_min_per_ce=recovery_min,
        )
        for lr in touched.values()
    }
    db.commit()
    return {
        "module_id": module_id,
        "documents": len(documents),
        "imported": imported,
        "coverage": coverage,
    }


@router.post("/modules/{module_id}/private-bank-bundle")
async def teacher_private_bank_bundle(
    module_id: int,
    file: UploadFile = File(...),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    _module_editor(db, module_id, int(session["sub"]))
    documents = await _read_private_bank_bundle(file)
    return _import_private_documents(db, module_id, documents)


@router.post(
    "/admin/modules/{module_id}/private-bank-bundle",
    dependencies=[Depends(require_admin)],
)
async def admin_private_bank_bundle(
    module_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    documents = await _read_private_bank_bundle(file)
    return _import_private_documents(db, module_id, documents)


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



@router.put("/modules/{module_id}/learning-results/{learning_result_id}/exam-bank")
def teacher_exam_bank(
    module_id: int,
    learning_result_id: int,
    payload: ExamBankIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    _module_editor(db, module_id, int(session["sub"]))
    return _load_exam_bank(db, module_id, learning_result_id, payload)


@router.put(
    "/admin/modules/{module_id}/learning-results/{learning_result_id}/exam-bank",
    dependencies=[Depends(require_admin)],
)
def admin_exam_bank(
    module_id: int,
    learning_result_id: int,
    payload: ExamBankIn,
    db: Session = Depends(get_db),
) -> dict:
    return _load_exam_bank(db, module_id, learning_result_id, payload)


def _exam_public(session: ExamSession) -> dict:
    public_questions = []
    for question in session.question_snapshot_json or []:
        if not isinstance(question, dict):
            continue
        public_questions.append(
            {
                "id": question.get("id"),
                "item_key": question.get("item_key"),
                "ce": question.get("ce"),
                "type": question.get("type"),
                "q": question.get("q"),
                "options": question.get("options") or [],
            }
        )
    payload = session.response_json or {}
    return {
        "exam_session_id": session.id,
        "attempt_no": session.attempt_no,
        "status": session.status,
        "started_at": session.started_at,
        "deadline_at": session.deadline_at,
        "questions": public_questions,
        "saved_answers": payload.get("draft_answers") or {},
        "security_events": len(session.security_events_json or []),
        "config": payload.get("config") or {},
        "resumed": True,
    }


def _exam_items_for_lr(
    db: Session,
    learning_result_id: int,
) -> dict[str, list[tuple[AssessmentItem, AssessmentKey]]]:
    rows = db.execute(
        select(AssessmentItem, AssessmentKey, AssessmentCriterion)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .join(
            AssessmentKey,
            AssessmentKey.item_id == AssessmentItem.id,
        )
        .where(
            AssessmentCriterion.learning_result_id == learning_result_id,
            AssessmentItem.instrument == "exam",
            AssessmentItem.active.is_(True),
            AssessmentItem.evaluable.is_(True),
            AssessmentKey.active.is_(True),
        )
        .order_by(AssessmentCriterion.position, AssessmentItem.position, AssessmentItem.id)
    ).all()
    by_ce: dict[str, list[tuple[AssessmentItem, AssessmentKey]]] = {}
    for item, key, criterion in rows:
        by_ce.setdefault(criterion.code, []).append((item, key))
    return by_ce


@router.post(
    "/course-modules/{course_module_id}/learning-results/{learning_result_id}/exam/start"
)
def start_exam(
    course_module_id: int,
    learning_result_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    course_module, _ = _course_module_access(db, course_module_id, user_id)
    lr = db.get(LearningResult, learning_result_id)
    if not lr or lr.module_id != course_module.module_id or not lr.active:
        raise HTTPException(status_code=404, detail="RA no encontrado")

    config = _effective_config(db, course_module)
    if not config["exam_enabled"]:
        raise HTTPException(status_code=403, detail="El examen no está habilitado")

    active = db.scalar(
        select(ExamSession)
        .where(
            ExamSession.course_module_id == course_module_id,
            ExamSession.user_id == user_id,
            ExamSession.learning_result_id == learning_result_id,
            ExamSession.status == "started",
        )
        .order_by(ExamSession.attempt_no.desc())
    )
    if active:
        return _exam_public(active)

    used = db.scalar(
        select(func.count(ExamSession.id)).where(
            ExamSession.course_module_id == course_module_id,
            ExamSession.user_id == user_id,
            ExamSession.learning_result_id == learning_result_id,
        )
    ) or 0
    if used >= int(config["exam_max_attempts"]):
        raise HTTPException(status_code=409, detail="No quedan intentos de examen")

    bank = _exam_items_for_lr(db, learning_result_id)
    criteria = list(
        db.scalars(
            select(AssessmentCriterion)
            .where(
                AssessmentCriterion.learning_result_id == learning_result_id,
                AssessmentCriterion.active.is_(True),
            )
            .order_by(AssessmentCriterion.position, AssessmentCriterion.id)
        )
    )
    per_ce = int(config["exam_questions_per_ce"])
    shortages = {
        criterion.code: len(bank.get(criterion.code, []))
        for criterion in criteria
        if len(bank.get(criterion.code, [])) < per_ce
    }
    if shortages:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Banco de examen incompleto",
                "required_per_ce": per_ce,
                "available": shortages,
            },
        )

    attempt_no = int(used) + 1
    seed_raw = f"{user_id}|{course_module_id}|{learning_result_id}|{attempt_no}"
    seed = int(hashlib.sha256(seed_raw.encode("utf-8")).hexdigest()[:16], 16)
    rnd = random.Random(seed)
    chosen: list[dict] = []

    for criterion in criteria:
        pool = list(bank.get(criterion.code, []))
        rnd.shuffle(pool)
        for item, key in pool[:per_ce]:
            options = list(item.options_json or [])
            expected = _unwrap_answer(key)
            remapped = expected
            if item.item_type in {"choice", "multi"}:
                indexed = list(enumerate(options))
                rnd.shuffle(indexed)
                options = [value for _, value in indexed]
                new_index = {old: new for new, (old, _) in enumerate(indexed)}
                if item.item_type == "choice":
                    remapped = new_index[int(expected)]
                else:
                    remapped = sorted(new_index[int(index)] for index in expected)
            chosen.append(
                {
                    "id": item.id,
                    "item_key": item.item_key,
                    "ce": criterion.code,
                    "type": item.item_type,
                    "q": item.prompt,
                    "options": options,
                    "correct": remapped,
                }
            )
    rnd.shuffle(chosen)
    started = _now()
    deadline = started + timedelta(minutes=int(config["exam_minutes"]))
    exam = ExamSession(
        course_module_id=course_module_id,
        user_id=user_id,
        learning_result_id=learning_result_id,
        attempt_no=attempt_no,
        status="started",
        question_ids_json=[int(q["id"]) for q in chosen],
        question_snapshot_json=chosen,
        response_json={
            "draft_answers": {},
            "config": config,
        },
        security_events_json=[],
        started_at=started,
        deadline_at=deadline,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    result = _exam_public(exam)
    result["resumed"] = False
    return result


@router.put("/exam-sessions/{exam_session_id}/draft")
def save_exam_draft(
    exam_session_id: int,
    payload: ExamDraftIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    exam = db.get(ExamSession, exam_session_id)
    if not exam or exam.user_id != user_id:
        raise HTTPException(status_code=404, detail="Examen no encontrado")
    if exam.status != "started":
        raise HTTPException(status_code=409, detail="El examen ya no está abierto")
    _course_module_access(db, exam.course_module_id, user_id)
    current = dict(exam.response_json or {})
    current["draft_answers"] = payload.answers or {}
    current["draft_saved_at"] = _now().isoformat()
    exam.response_json = current
    db.commit()
    return {
        "ok": True,
        "exam_session_id": exam.id,
        "saved_at": current["draft_saved_at"],
    }


@router.post("/exam-sessions/{exam_session_id}/events")
def record_exam_event(
    exam_session_id: int,
    payload: ExamEventIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    exam = db.get(ExamSession, exam_session_id)
    if not exam or exam.user_id != user_id:
        raise HTTPException(status_code=404, detail="Examen no encontrado")
    if exam.status != "started":
        raise HTTPException(status_code=409, detail="El examen ya no está abierto")
    course_module, _ = _course_module_access(db, exam.course_module_id, user_id)
    config = dict((exam.response_json or {}).get("config") or _effective_config(db, course_module))

    events = list(exam.security_events_json or [])
    if len(events) < 300:
        events.append(
            {
                "event": payload.event,
                "detail": payload.detail or {},
                "at": _now().isoformat(),
            }
        )
    exam.security_events_json = events

    incident_events = {
        "blur",
        "visibility-hidden",
        "fullscreen-exit",
        "copy",
        "paste",
        "contextmenu",
    }
    incidents = sum(1 for event in events if event.get("event") in incident_events)
    limit = int(config.get("exam_incident_limit", 3))
    force_submit = bool(
        config.get("exam_integrity_enabled", True)
        and config.get("exam_incident_policy", "submit") == "submit"
        and limit >= 0
        and incidents >= limit
    )
    db.commit()
    return {
        "ok": True,
        "incidents": incidents,
        "limit": limit,
        "force_submit": force_submit,
    }


@router.post("/exam-sessions/{exam_session_id}/submit")
def submit_exam(
    exam_session_id: int,
    payload: ExamSubmitIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    exam = db.get(ExamSession, exam_session_id)
    if not exam or exam.user_id != user_id:
        raise HTTPException(status_code=404, detail="Examen no encontrado")
    if exam.status != "started":
        raise HTTPException(status_code=409, detail="El examen ya no está abierto")
    _course_module_access(db, exam.course_module_id, user_id)

    now = _now()
    deadline = _aware_utc(exam.deadline_at)
    if deadline and now > deadline + timedelta(seconds=30) and not payload.timeout:
        raise HTTPException(status_code=410, detail="El tiempo del examen ha finalizado")

    current = dict(exam.response_json or {})
    answers = payload.answers or current.get("draft_answers") or {}
    by_ce: dict[str, dict] = {}
    correct_total = 0
    question_count = 0

    for question in exam.question_snapshot_json or []:
        if not isinstance(question, dict):
            continue
        question_count += 1
        qid = str(question.get("id"))
        given = answers.get(qid)
        if given is None:
            given = answers.get(question.get("item_key"))
        expected = question.get("correct")
        kind = str(question.get("type") or "choice")
        if kind == "multi":
            ok = (
                isinstance(given, list)
                and sorted(map(str, given)) == sorted(map(str, expected or []))
            )
        else:
            ok = given == expected
        correct_total += int(ok)
        ce = str(question.get("ce") or "")
        row = by_ce.setdefault(ce, {"ok": 0, "n": 0})
        row["n"] += 1
        row["ok"] += int(ok)

    score = round(correct_total / max(1, question_count) * 100.0, 2)
    current.update(
        {
            "answers": answers,
            "draft_answers": answers,
            "by_ce": by_ce,
            "answered": len(answers),
            "total": question_count,
            "submitted_at": now.isoformat(),
            "timeout": bool(payload.timeout),
            "integrity": {
                "events": len(exam.security_events_json or []),
            },
        }
    )
    exam.response_json = current
    exam.score = score
    exam.status = "submitted"
    exam.submitted_at = now
    db.flush()

    progress = recompute_learning_result(
        db,
        exam.course_module_id,
        user_id,
        exam.learning_result_id,
    )
    db.commit()
    return {
        "exam_session_id": exam.id,
        "score": score,
        "answered": len(answers),
        "total": question_count,
        "by_ce": by_ce,
        "progress": progress,
    }



@router.put("/modules/{module_id}/learning-results/{learning_result_id}/recovery-bank")
def teacher_recovery_bank(
    module_id: int,
    learning_result_id: int,
    payload: RecoveryBankIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    _module_editor(db, module_id, int(session["sub"]))
    return _load_recovery_bank(db, module_id, learning_result_id, payload)


@router.put(
    "/admin/modules/{module_id}/learning-results/{learning_result_id}/recovery-bank",
    dependencies=[Depends(require_admin)],
)
def admin_recovery_bank(
    module_id: int,
    learning_result_id: int,
    payload: RecoveryBankIn,
    db: Session = Depends(get_db),
) -> dict:
    return _load_recovery_bank(db, module_id, learning_result_id, payload)


@router.get(
    "/course-modules/{course_module_id}/learning-results/{learning_result_id}/recovery"
)
def recovery_for_student(
    course_module_id: int,
    learning_result_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    course_module, _ = _course_module_access(db, course_module_id, user_id)
    lr = db.get(LearningResult, learning_result_id)
    if not lr or lr.module_id != course_module.module_id or not lr.active:
        raise HTTPException(status_code=404, detail="RA no encontrado")

    # Recalcular primero garantiza que el plan refleja el estado actual.
    progress = recompute_learning_result(
        db, course_module_id, user_id, learning_result_id
    )
    plan = db.scalar(
        select(RecoveryPlan).where(
            RecoveryPlan.course_module_id == course_module_id,
            RecoveryPlan.user_id == user_id,
            RecoveryPlan.learning_result_id == learning_result_id,
        )
    )
    criteria_codes = list(plan.criteria_json or []) if plan else []
    if not plan or not criteria_codes:
        db.commit()
        return {
            "learning_result_id": learning_result_id,
            "code": lr.code,
            "status": plan.status if plan else "not-required",
            "criteria": [],
            "items": [],
            "progress": progress,
        }

    rows = db.execute(
        select(AssessmentItem, AssessmentCriterion)
        .join(
            AssessmentCriterion,
            AssessmentCriterion.id == AssessmentItem.criterion_id,
        )
        .where(
            AssessmentCriterion.learning_result_id == learning_result_id,
            AssessmentCriterion.code.in_(criteria_codes),
            AssessmentItem.instrument == "recovery",
            AssessmentItem.active.is_(True),
            AssessmentItem.evaluable.is_(True),
        )
        .order_by(
            AssessmentCriterion.position,
            AssessmentItem.position,
            AssessmentItem.id,
        )
    ).all()
    items = [
        {
            "id": item.id,
            "key": item.item_key,
            "ce": criterion.code,
            "type": item.item_type,
            "prompt": item.prompt,
            "options": item.options_json or [],
            "max_attempts": _effective_config(
                db, course_module
            ).get("recovery_max_attempts", item.max_attempts),
        }
        for item, criterion in rows
    ]
    db.commit()
    return {
        "learning_result_id": learning_result_id,
        "code": lr.code,
        "status": plan.status,
        "criteria": criteria_codes,
        "items": items,
        "progress": progress,
    }
