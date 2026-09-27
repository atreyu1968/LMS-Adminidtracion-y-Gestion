from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    AssessmentAttempt,
    AssessmentReview,
    Course,
    CourseModule,
    EvaluationResult,
    GradeRecord,
    LearningResult,
    LTIResourceLink,
    Membership,
    Module,
    RecoveryPlan,
    ScormRegistration,
)
from .security import require_teacher


router = APIRouter(prefix="/api/dashboard")


@router.get("/teacher")
def teacher_dashboard(
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])

    teacher_courses = list(
        db.scalars(
            select(Course.id)
            .join(Membership, Membership.course_id == Course.id)
            .where(
                Membership.user_id == teacher_id,
                Membership.active.is_(True),
                Membership.role.in_(["teacher", "admin"]),
                Course.active.is_(True),
            )
        )
    )
    if not teacher_courses:
        return {
            "summary": {
                "groups": 0,
                "course_modules": 0,
                "students": 0,
                "pending_reviews": 0,
                "recoveries": 0,
                "campus_pending": 0,
                "scorm_in_progress": 0,
            },
            "items": [],
        }

    cms = db.execute(
        select(CourseModule, Course, Module)
        .join(Course, Course.id == CourseModule.course_id)
        .join(Module, Module.id == CourseModule.module_id)
        .where(
            CourseModule.course_id.in_(teacher_courses),
            CourseModule.active.is_(True),
            Course.active.is_(True),
            Module.active.is_(True),
        )
        .order_by(Course.title, Module.title)
    ).all()

    cm_ids = [cm.id for cm, _, _ in cms]
    student_count = db.scalar(
        select(func.count(Membership.id)).where(
            Membership.course_id.in_(teacher_courses),
            Membership.active.is_(True),
            Membership.role == "student",
        )
    ) or 0

    pending_reviews = defaultdict(int)
    if cm_ids:
        for cmid, count in db.execute(
            select(AssessmentAttempt.course_module_id, func.count(AssessmentReview.id))
            .join(AssessmentReview, AssessmentReview.attempt_id == AssessmentAttempt.id)
            .where(
                AssessmentAttempt.course_module_id.in_(cm_ids),
                AssessmentReview.status == "pending",
            )
            .group_by(AssessmentAttempt.course_module_id)
        ):
            pending_reviews[int(cmid)] = int(count)

    recoveries = defaultdict(int)
    if cm_ids:
        for cmid, count in db.execute(
            select(RecoveryPlan.course_module_id, func.count(RecoveryPlan.id))
            .where(
                RecoveryPlan.course_module_id.in_(cm_ids),
                RecoveryPlan.status.in_(["pending", "in_progress"]),
            )
            .group_by(RecoveryPlan.course_module_id)
        ):
            recoveries[int(cmid)] = int(count)

    scorm_progress = defaultdict(int)
    if cm_ids:
        for cmid, count in db.execute(
            select(ScormRegistration.course_module_id, func.count(ScormRegistration.id))
            .where(
                ScormRegistration.course_module_id.in_(cm_ids),
                ScormRegistration.lesson_status.notin_(
                    ["completed", "passed", "failed"]
                ),
                ScormRegistration.lesson_status != "not attempted",
            )
            .group_by(ScormRegistration.course_module_id)
        ):
            scorm_progress[int(cmid)] = int(count)

    campus_pending = defaultdict(int)
    if cm_ids:
        results = db.execute(
            select(EvaluationResult, LearningResult)
            .join(
                LearningResult,
                LearningResult.id == EvaluationResult.learning_result_id,
            )
            .where(
                EvaluationResult.course_module_id.in_(cm_ids),
                EvaluationResult.final_score.is_not(None),
            )
        ).all()
        links = list(
            db.scalars(
                select(LTIResourceLink).where(
                    LTIResourceLink.course_module_id.in_(cm_ids),
                    LTIResourceLink.learning_result_id.is_not(None),
                    LTIResourceLink.lineitem_url.is_not(None),
                )
            )
        )
        link_by_pair = {
            (int(link.course_module_id), int(link.learning_result_id)): link
            for link in links
            if link.course_module_id is not None and link.learning_result_id is not None
        }
        for result, lr in results:
            link = link_by_pair.get((result.course_module_id, lr.id))
            if not link:
                continue
            record = db.scalar(
                select(GradeRecord).where(
                    GradeRecord.resource_link_id == link.id,
                    GradeRecord.user_id == result.user_id,
                )
            )
            if (
                not record
                or record.last_sent_at is None
                or record.score_given is None
                or abs(float(record.score_given) - float(result.final_score)) > 0.001
            ):
                campus_pending[int(result.course_module_id)] += 1

    items = []
    for cm, course, module in cms:
        review_count = pending_reviews.get(cm.id, 0)
        recovery_count = recoveries.get(cm.id, 0)
        campus_count = campus_pending.get(cm.id, 0)
        scorm_count = scorm_progress.get(cm.id, 0)
        if review_count:
            items.append({
                "kind": "review",
                "priority": 1,
                "course_module_id": cm.id,
                "group": course.title,
                "module": module.title,
                "count": review_count,
                "title": f"{review_count} respuestas pendientes de revisar",
                "href": f"/evaluation-teacher.html?course_module={cm.id}&tab=reviews",
            })
        if recovery_count:
            items.append({
                "kind": "recovery",
                "priority": 2,
                "course_module_id": cm.id,
                "group": course.title,
                "module": module.title,
                "count": recovery_count,
                "title": f"{recovery_count} planes de recuperación activos",
                "href": f"/evaluation-teacher.html?course_module={cm.id}&tab=gradebook",
            })
        if campus_count:
            items.append({
                "kind": "campus",
                "priority": 3,
                "course_module_id": cm.id,
                "group": course.title,
                "module": module.title,
                "count": campus_count,
                "title": f"{campus_count} calificaciones definitivas pendientes de CAMPUS",
                "href": f"/evaluation-teacher.html?course_module={cm.id}&tab=gradebook",
            })
        if scorm_count:
            items.append({
                "kind": "scorm",
                "priority": 4,
                "course_module_id": cm.id,
                "group": course.title,
                "module": module.title,
                "count": scorm_count,
                "title": f"{scorm_count} intentos SCORM en curso",
                "href": f"/evaluation-teacher.html?course_module={cm.id}&tab=gradebook",
            })

    items.sort(key=lambda x: (x["priority"], x["group"].lower(), x["module"].lower()))
    return {
        "summary": {
            "groups": len(set(teacher_courses)),
            "course_modules": len(cm_ids),
            "students": int(student_count),
            "pending_reviews": sum(pending_reviews.values()),
            "recoveries": sum(recoveries.values()),
            "campus_pending": sum(campus_pending.values()),
            "scorm_in_progress": sum(scorm_progress.values()),
        },
        "items": items,
    }
