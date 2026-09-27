from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .lti import _role_from_lti
from .lti_services import fetch_memberships, post_score
from .models import (
    ExternalIdentity,
    GradeRecord,
    LTIPlatform,
    LTIResourceLink,
    Membership,
    User,
)
from .security import require_teacher


router = APIRouter(prefix="/api/lti")


class ScoreIn(BaseModel):
    user_id: int
    score_given: float = Field(ge=0)
    score_maximum: float = Field(default=100.0, gt=0)
    comment: str | None = None


def _teacher_membership(db: Session, course_id: int, user_id: int) -> Membership:
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership or membership.role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Teacher membership required")
    return membership


def _latest_link(
    db: Session,
    course_id: int,
    require_memberships: bool = False,
    resource_link_id: int | None = None,
) -> LTIResourceLink:
    stmt = select(LTIResourceLink).where(LTIResourceLink.course_id == course_id)
    if resource_link_id is not None:
        stmt = stmt.where(LTIResourceLink.id == resource_link_id)
    if require_memberships:
        stmt = stmt.where(LTIResourceLink.memberships_url.is_not(None))
    stmt = stmt.order_by(LTIResourceLink.last_launch_at.desc(), LTIResourceLink.id.desc())
    link = db.scalar(stmt)
    if not link:
        raise HTTPException(status_code=404, detail="No suitable LTI resource link is available")
    return link


@router.post("/courses/{course_id}/sync-memberships")
async def sync_memberships(
    course_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    if int(session.get("course_id") or 0) != course_id:
        raise HTTPException(status_code=403, detail="Course context mismatch")
    _teacher_membership(db, course_id, teacher_id)

    link = _latest_link(db, course_id, require_memberships=True)
    platform = db.get(LTIPlatform, link.platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail="LTI platform not found")

    try:
        payload = await fetch_memberships(platform, str(link.memberships_url))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"NRPS returned HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="NRPS connection failed") from exc

    created_users = 0
    updated_users = 0
    created_memberships = 0
    updated_memberships = 0
    seen_users: set[int] = set()

    for member in payload.get("members", []):
        if not isinstance(member, dict):
            continue
        subject = str(member.get("user_id") or "").strip()
        if not subject:
            continue

        identity = db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == platform.issuer,
                ExternalIdentity.subject == subject,
            )
        )
        if identity:
            user = db.get(User, identity.user_id)
            if not user:
                continue
            updated_users += 1
        else:
            user = User(
                display_name=(
                    member.get("name")
                    or " ".join(
                        x for x in [member.get("given_name"), member.get("family_name")] if x
                    ).strip()
                    or subject
                ),
                email=member.get("email"),
            )
            db.add(user)
            db.flush()
            db.add(
                ExternalIdentity(
                    user_id=user.id,
                    issuer=platform.issuer,
                    subject=subject,
                    client_id=platform.client_id,
                )
            )
            created_users += 1

        if member.get("name"):
            user.display_name = str(member["name"])
        if member.get("email"):
            user.email = str(member["email"])

        roles = list(member.get("roles") or [])
        role = _role_from_lti(roles)
        membership = db.scalar(
            select(Membership).where(
                Membership.course_id == course_id,
                Membership.user_id == user.id,
            )
        )
        if membership:
            membership.role = role
            membership.lti_roles = roles
            membership.active = True
            membership.updated_at = datetime.now(timezone.utc)
            updated_memberships += 1
        else:
            db.add(
                Membership(
                    course_id=course_id,
                    user_id=user.id,
                    role=role,
                    lti_roles=roles,
                    active=True,
                )
            )
            created_memberships += 1
        seen_users.add(user.id)

    db.commit()
    return {
        "course_id": course_id,
        "pages": payload.get("pages", 1),
        "members_received": len(payload.get("members", [])),
        "created_users": created_users,
        "updated_users": updated_users,
        "created_memberships": created_memberships,
        "updated_memberships": updated_memberships,
        "active_users_seen": len(seen_users),
        "note": "La sincronización no desactiva ausentes automáticamente en esta primera versión.",
    }


@router.post("/courses/{course_id}/resource-links/{resource_link_id}/score")
async def send_score(
    course_id: int,
    resource_link_id: int,
    payload: ScoreIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    if int(session.get("course_id") or 0) != course_id:
        raise HTTPException(status_code=403, detail="Course context mismatch")
    _teacher_membership(db, course_id, teacher_id)

    link = _latest_link(db, course_id, resource_link_id=resource_link_id)
    if not link.lineitem_url:
        raise HTTPException(
            status_code=409,
            detail="CAMPUS did not provide an AGS lineitem for this resource",
        )

    student_membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_id,
            Membership.user_id == payload.user_id,
            Membership.active.is_(True),
        )
    )
    if not student_membership:
        raise HTTPException(status_code=404, detail="Student is not enrolled in this course")

    platform = db.get(LTIPlatform, link.platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail="LTI platform not found")

    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.user_id == payload.user_id,
            ExternalIdentity.issuer == platform.issuer,
        )
    )
    if not identity:
        raise HTTPException(status_code=409, detail="Student has no LTI identity for this platform")

    try:
        result = await post_score(
            platform=platform,
            lineitem_url=str(link.lineitem_url),
            lti_user_id=identity.subject,
            score_given=payload.score_given,
            score_maximum=payload.score_maximum,
            comment=payload.comment,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AGS returned HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="AGS connection failed") from exc

    record = db.scalar(
        select(GradeRecord).where(
            GradeRecord.resource_link_id == link.id,
            GradeRecord.user_id == payload.user_id,
        )
    )
    if not record:
        record = GradeRecord(resource_link_id=link.id, user_id=payload.user_id)
        db.add(record)
    record.score_given = payload.score_given
    record.score_maximum = payload.score_maximum
    record.comment = payload.comment
    record.activity_progress = "Completed"
    record.grading_progress = "FullyGraded"
    record.updated_at = datetime.now(timezone.utc)
    record.last_sent_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "ok": True,
        "resource_link_id": link.id,
        "user_id": payload.user_id,
        "score_given": payload.score_given,
        "score_maximum": payload.score_maximum,
        "ags": result,
    }


@router.get("/courses/{course_id}/resource-links")
def resource_links(
    course_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    teacher_id = int(session["sub"])
    if int(session.get("course_id") or 0) != course_id:
        raise HTTPException(status_code=403, detail="Course context mismatch")
    _teacher_membership(db, course_id, teacher_id)
    rows = list(
        db.scalars(
            select(LTIResourceLink)
            .where(LTIResourceLink.course_id == course_id)
            .order_by(LTIResourceLink.last_launch_at.desc())
        )
    )
    return [
        {
            "id": row.id,
            "resource_link_id": row.resource_link_id,
            "course_module_id": row.course_module_id,
            "has_lineitem": bool(row.lineitem_url),
            "has_nrps": bool(row.memberships_url),
            "scopes": row.scopes or [],
            "last_launch_at": row.last_launch_at,
        }
        for row in rows
    ]
