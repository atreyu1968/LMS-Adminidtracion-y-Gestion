from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    AssessmentAttempt,
    AssessmentCriterion,
    AssessmentItem,
    AssessmentReview,
    ContentReleaseRule,
    Course,
    CourseModule,
    ExamSession,
    LearningResult,
    Membership,
    Module,
    ModuleScormPackage,
    RecoveryPlan,
    ScormPackage,
    ScormRegistration,
    User,
    UserPreference,
)
from .security import read_session, require_teacher


router = APIRouter(prefix="/api/experience")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _teacher_course_module(db: Session, course_module_id: int, user_id: int) -> CourseModule:
    cm = db.get(CourseModule, course_module_id)
    if not cm or not cm.active:
        raise HTTPException(status_code=404, detail="Módulo del grupo no encontrado")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == cm.course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
            Membership.role.in_(["teacher", "admin"]),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="Se requiere rol docente")
    return cm


def _user_course_modules(db: Session, user_id: int) -> list[CourseModule]:
    course_ids = list(
        db.scalars(
            select(Membership.course_id).where(
                Membership.user_id == user_id,
                Membership.active.is_(True),
            )
        )
    )
    if not course_ids:
        return []
    return list(
        db.scalars(
            select(CourseModule).where(
                CourseModule.course_id.in_(course_ids),
                CourseModule.active.is_(True),
            )
        )
    )


def _pref(db: Session, user_id: int, key: str) -> UserPreference | None:
    return db.scalar(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.pref_key == key,
        )
    )


def _save_pref(db: Session, user_id: int, key: str, value: dict) -> UserPreference:
    row = _pref(db, user_id, key)
    if not row:
        row = UserPreference(user_id=user_id, pref_key=key)
        db.add(row)
    row.value_json = value
    row.updated_at = _now()
    db.commit()
    return row


class RecentIn(BaseModel):
    kind: str = Field(pattern=r"^(group|module|course_module|page)$")
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    href: str = Field(min_length=1, max_length=1500)


class FavoriteIn(BaseModel):
    kind: str = Field(pattern=r"^(group|module)$")
    id: int


@router.get("/search")
def search_content(
    q: str = Query(min_length=2, max_length=120),
    course_module_id: int | None = None,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    cms = _user_course_modules(db, user_id)
    if course_module_id is not None:
        cms = [cm for cm in cms if cm.id == course_module_id]
    cm_by_module = {cm.module_id: cm for cm in cms}
    if not cm_by_module:
        return {"query": q, "results": []}
    module_ids = list(cm_by_module)
    needle = f"%{q.lower()}%"
    results = []

    modules = list(
        db.scalars(
            select(Module).where(
                Module.id.in_(module_ids),
                Module.active.is_(True),
                or_(
                    func.lower(Module.title).like(needle),
                    func.lower(Module.description).like(needle),
                    func.lower(func.coalesce(Module.code, "")).like(needle),
                ),
            )
        )
    )
    for module in modules:
        cm = cm_by_module[module.id]
        results.append({
            "kind": "module",
            "title": module.title,
            "subtitle": module.code or "",
            "course_module_id": cm.id,
            "href": f"/evaluation-student.html?course_module={cm.id}",
        })

    lrs = db.execute(
        select(LearningResult, CourseModule)
        .join(CourseModule, CourseModule.module_id == LearningResult.module_id)
        .where(
            CourseModule.id.in_([cm.id for cm in cms]),
            LearningResult.active.is_(True),
            or_(
                func.lower(LearningResult.title).like(needle),
                func.lower(LearningResult.code).like(needle),
                func.lower(LearningResult.description).like(needle),
            ),
        )
    ).all()
    for lr, cm in lrs:
        results.append({
            "kind": "learning_result",
            "title": f"{lr.code} · {lr.title}",
            "subtitle": "Resultado de aprendizaje",
            "course_module_id": cm.id,
            "learning_result_id": lr.id,
            "href": f"/evaluation-student.html?course_module={cm.id}#ra-{lr.id}",
        })

    criteria = db.execute(
        select(AssessmentCriterion, LearningResult, CourseModule)
        .join(LearningResult, LearningResult.id == AssessmentCriterion.learning_result_id)
        .join(CourseModule, CourseModule.module_id == LearningResult.module_id)
        .where(
            CourseModule.id.in_([cm.id for cm in cms]),
            AssessmentCriterion.active.is_(True),
            or_(
                func.lower(AssessmentCriterion.title).like(needle),
                func.lower(AssessmentCriterion.code).like(needle),
                func.lower(AssessmentCriterion.description).like(needle),
            ),
        )
    ).all()
    for criterion, lr, cm in criteria:
        results.append({
            "kind": "criterion",
            "title": f"{criterion.code} · {criterion.title}",
            "subtitle": lr.code,
            "course_module_id": cm.id,
            "learning_result_id": lr.id,
            "href": f"/evaluation-student.html?course_module={cm.id}#ra-{lr.id}",
        })

    items = db.execute(
        select(AssessmentItem, AssessmentCriterion, LearningResult, CourseModule)
        .join(AssessmentCriterion, AssessmentCriterion.id == AssessmentItem.criterion_id)
        .join(LearningResult, LearningResult.id == AssessmentCriterion.learning_result_id)
        .join(CourseModule, CourseModule.module_id == LearningResult.module_id)
        .where(
            CourseModule.id.in_([cm.id for cm in cms]),
            AssessmentItem.active.is_(True),
            or_(
                func.lower(AssessmentItem.item_key).like(needle),
                func.lower(AssessmentItem.prompt).like(needle),
            ),
        )
        .limit(100)
    ).all()
    for item, criterion, lr, cm in items:
        results.append({
            "kind": "activity",
            "title": item.item_key,
            "subtitle": f"{lr.code} · {criterion.code} · {item.prompt[:160]}",
            "course_module_id": cm.id,
            "learning_result_id": lr.id,
            "href": f"/evaluation-student.html?course_module={cm.id}#ra-{lr.id}",
        })

    scorms = db.execute(
        select(ScormPackage, ModuleScormPackage, CourseModule)
        .join(ModuleScormPackage, ModuleScormPackage.package_id == ScormPackage.id)
        .join(CourseModule, CourseModule.module_id == ModuleScormPackage.module_id)
        .where(
            CourseModule.id.in_([cm.id for cm in cms]),
            ModuleScormPackage.active.is_(True),
            ScormPackage.active.is_(True),
            ScormPackage.is_current.is_(True),
            or_(
                func.lower(ScormPackage.title).like(needle),
                func.lower(ScormPackage.description).like(needle),
            ),
        )
        .limit(100)
    ).all()
    for package, _, cm in scorms:
        results.append({
            "kind": "scorm",
            "title": package.title,
            "subtitle": package.standard,
            "course_module_id": cm.id,
            "package_id": package.id,
            "href": f"/?course_module={cm.id}",
        })

    return {"query": q, "results": results[:150]}


@router.post("/recent")
def record_recent(
    payload: RecentIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    row = _pref(db, user_id, "recent")
    items = list((row.value_json or {}).get("items", [])) if row else []
    item = {
        "kind": payload.kind,
        "id": payload.id,
        "title": payload.title,
        "href": payload.href,
        "at": _now().isoformat(),
    }
    items = [
        old for old in items
        if not (old.get("kind") == payload.kind and str(old.get("id")) == payload.id)
    ]
    items.insert(0, item)
    _save_pref(db, user_id, "recent", {"items": items[:20]})
    return {"items": items[:20]}


@router.get("/recent")
def get_recent(
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    row = _pref(db, int(session["sub"]), "recent")
    return row.value_json if row else {"items": []}


@router.post("/favorites/toggle")
def toggle_favorite(
    payload: FavoriteIn,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    row = _pref(db, user_id, "favorites")
    value = dict(row.value_json or {}) if row else {}
    key = "groups" if payload.kind == "group" else "modules"
    ids = [int(x) for x in value.get(key, [])]
    if payload.id in ids:
        ids.remove(payload.id)
        favorite = False
    else:
        ids.append(payload.id)
        favorite = True
    value[key] = sorted(set(ids))
    _save_pref(db, user_id, "favorites", value)
    return {"favorite": favorite, "value": value}


@router.get("/favorites")
def get_favorites(
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    row = _pref(db, int(session["sub"]), "favorites")
    return row.value_json if row else {"groups": [], "modules": []}


@router.get("/calendar")
def calendar(
    course_module_id: int | None = None,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    cms = _user_course_modules(db, user_id)
    if course_module_id is not None:
        cms = [cm for cm in cms if cm.id == course_module_id]
    cm_ids = [cm.id for cm in cms]
    if not cm_ids:
        return {"events": []}
    courses = {c.id: c for c in db.scalars(select(Course).where(Course.id.in_([cm.course_id for cm in cms])))}
    modules = {m.id: m for m in db.scalars(select(Module).where(Module.id.in_([cm.module_id for cm in cms])))}
    cm_map = {cm.id: cm for cm in cms}
    rules = list(
        db.scalars(
            select(ContentReleaseRule).where(
                ContentReleaseRule.course_module_id.in_(cm_ids),
                ContentReleaseRule.active.is_(True),
                or_(
                    ContentReleaseRule.open_at.is_not(None),
                    ContentReleaseRule.close_at.is_not(None),
                ),
            )
        )
    )
    events = []
    for rule in rules:
        cm = cm_map[rule.course_module_id]
        course = courses.get(cm.course_id)
        module = modules.get(cm.module_id)
        label = f"{rule.content_type}:{rule.content_key}"
        if rule.open_at:
            events.append({
                "at": rule.open_at,
                "kind": "opens",
                "title": f"Se abre {label}",
                "group": course.title if course else "",
                "module": module.title if module else "",
                "course_module_id": cm.id,
            })
        if rule.close_at:
            events.append({
                "at": rule.close_at,
                "kind": "closes",
                "title": f"Cierra {label}",
                "group": course.title if course else "",
                "module": module.title if module else "",
                "course_module_id": cm.id,
            })
    events.sort(key=lambda x: x["at"])
    return {"events": events[:500]}


@router.get("/notifications")
def notifications(
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    cms = _user_course_modules(db, user_id)
    cm_ids = [cm.id for cm in cms]
    if not cm_ids:
        return {"items": []}
    role_rows = list(
        db.scalars(
            select(Membership.role).where(
                Membership.user_id == user_id,
                Membership.course_id.in_([cm.course_id for cm in cms]),
                Membership.active.is_(True),
            )
        )
    )
    teacher = any(role in {"teacher", "admin"} for role in role_rows)
    items = []
    if teacher:
        pending = db.execute(
            select(AssessmentAttempt.course_module_id, func.count(AssessmentReview.id))
            .join(AssessmentReview, AssessmentReview.attempt_id == AssessmentAttempt.id)
            .where(
                AssessmentAttempt.course_module_id.in_(cm_ids),
                AssessmentReview.status == "pending",
            )
            .group_by(AssessmentAttempt.course_module_id)
        ).all()
        for cmid, count in pending:
            items.append({
                "kind": "review",
                "title": f"{int(count)} respuestas pendientes de revisión",
                "href": f"/evaluation-teacher.html?course_module={cmid}&tab=reviews",
            })
    else:
        recoveries = db.execute(
            select(RecoveryPlan.course_module_id, func.count(RecoveryPlan.id))
            .where(
                RecoveryPlan.course_module_id.in_(cm_ids),
                RecoveryPlan.user_id == user_id,
                RecoveryPlan.status.in_(["pending", "in_progress"]),
            )
            .group_by(RecoveryPlan.course_module_id)
        ).all()
        for cmid, count in recoveries:
            items.append({
                "kind": "recovery",
                "title": f"Tienes {int(count)} recuperación(es) activa(s)",
                "href": f"/evaluation-student.html?course_module={cmid}",
            })

    soon = _now() + timedelta(days=7)
    rules = list(
        db.scalars(
            select(ContentReleaseRule).where(
                ContentReleaseRule.course_module_id.in_(cm_ids),
                ContentReleaseRule.active.is_(True),
                ContentReleaseRule.close_at.is_not(None),
                ContentReleaseRule.close_at >= _now(),
                ContentReleaseRule.close_at <= soon,
            )
        )
    )
    for rule in rules:
        items.append({
            "kind": "deadline",
            "title": f"Próximo cierre: {rule.content_type}:{rule.content_key}",
            "at": rule.close_at,
            "href": (
                f"/evaluation-teacher.html?course_module={rule.course_module_id}&tab=availability"
                if teacher
                else f"/evaluation-student.html?course_module={rule.course_module_id}"
            ),
        })
    return {"items": items[:100]}


@router.get("/course-modules/{course_module_id}/signals")
def observable_signals(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    cm = _teacher_course_module(db, course_module_id, teacher_id)
    students = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.course_id == cm.course_id,
            Membership.active.is_(True),
            Membership.role == "student",
            User.active.is_(True),
        )
        .order_by(User.display_name)
    ).all()
    now = _now()
    rows = []
    for _, user in students:
        timestamps = []
        attempts = list(
            db.scalars(
                select(AssessmentAttempt).where(
                    AssessmentAttempt.course_module_id == course_module_id,
                    AssessmentAttempt.user_id == user.id,
                )
            )
        )
        timestamps.extend(
            [a.submitted_at or a.started_at for a in attempts if (a.submitted_at or a.started_at)]
        )
        scorms = list(
            db.scalars(
                select(ScormRegistration).where(
                    ScormRegistration.course_module_id == course_module_id,
                    ScormRegistration.user_id == user.id,
                )
            )
        )
        timestamps.extend([r.updated_at for r in scorms if r.updated_at])
        exams = list(
            db.scalars(
                select(ExamSession).where(
                    ExamSession.course_module_id == course_module_id,
                    ExamSession.user_id == user.id,
                )
            )
        )
        timestamps.extend([e.submitted_at or e.started_at for e in exams if (e.submitted_at or e.started_at)])
        last = max(timestamps) if timestamps else None
        last_aware = last if last and last.tzinfo else (last.replace(tzinfo=timezone.utc) if last else None)
        inactive_days = (now - last_aware).days if last_aware else None
        submitted = sum(1 for a in attempts if a.status == "submitted")
        pending = sum(1 for a in attempts if a.pending_review)
        signals = []
        if last is None:
            signals.append({"kind": "no_activity", "label": "Sin actividad registrada"})
        elif inactive_days is not None and inactive_days >= 7:
            signals.append({"kind": "inactive_7d", "label": f"Sin actividad desde hace {inactive_days} días"})
        if pending:
            signals.append({"kind": "pending_review", "label": f"{pending} entrega(s) pendientes de revisión"})
        if submitted == 0 and (scorms or exams):
            signals.append({"kind": "no_portfolio", "label": "Sin entregas de Portafolio"})
        rows.append({
            "user_id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "last_activity": last,
            "submitted_activities": submitted,
            "scorm_registrations": len(scorms),
            "exam_sessions": len(exams),
            "signals": signals,
        })
    return {
        "course_module_id": course_module_id,
        "note": "Señales descriptivas basadas en actividad observable; no son predicciones ni diagnósticos.",
        "students": rows,
    }
