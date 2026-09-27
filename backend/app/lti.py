from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import secrets

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .lti_keys import public_jwk
from .models import (
    Course,
    ExternalIdentity,
    LTIPlatform,
    LTIDeployment,
    LTIResourceLink,
    LTIState,
    Membership,
    User,
)
from .security import create_session_token, require_admin
from .settings import get_settings


router = APIRouter()
settings = get_settings()

CLAIM_DEPLOYMENT = "https://purl.imsglobal.org/spec/lti/claim/deployment_id"
CLAIM_ROLES = "https://purl.imsglobal.org/spec/lti/claim/roles"
CLAIM_CONTEXT = "https://purl.imsglobal.org/spec/lti/claim/context"
CLAIM_RESOURCE = "https://purl.imsglobal.org/spec/lti/claim/resource_link"
CLAIM_AGS = "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint"
CLAIM_NRPS = "https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice"


class PlatformIn(BaseModel):
    name: str
    issuer: str
    client_id: str
    auth_url: str
    token_url: str
    jwks_url: str
    deployment_ids: list[str] = []


def _role_from_lti(roles: list[str]) -> str:
    joined = " ".join(roles).lower()
    if "administrator" in joined:
        return "admin"
    if "instructor" in joined or "teachingassistant" in joined or "mentor" in joined:
        return "teacher"
    return "student"


async def _params(request: Request) -> dict:
    if request.method == "POST":
        form = await request.form()
        return dict(form)
    return dict(request.query_params)


@router.get("/lti/jwks")
def jwks() -> dict:
    return {"keys": [public_jwk()]}


@router.get("/api/admin/lti/tool-config", dependencies=[Depends(require_admin)])
def tool_config() -> dict:
    return {
        "tool_url": f"{settings.base_url}/lti/launch",
        "initiate_login_url": f"{settings.base_url}/lti/login",
        "jwks_url": f"{settings.base_url}/lti/jwks",
        "redirect_uris": [
            f"{settings.base_url}/lti/launch",
            f"{settings.base_url}/lti/deep-link",
        ],
        "deep_linking_url": f"{settings.base_url}/lti/deep-link",
    }


@router.post("/api/admin/lti/platforms", dependencies=[Depends(require_admin)])
def create_platform(payload: PlatformIn, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(
        select(LTIPlatform).where(
            LTIPlatform.issuer == payload.issuer,
            LTIPlatform.client_id == payload.client_id,
        )
    )
    if existing:
        existing.name = payload.name
        existing.auth_url = payload.auth_url
        existing.token_url = payload.token_url
        existing.jwks_url = payload.jwks_url
        platform = existing
    else:
        platform = LTIPlatform(
            name=payload.name,
            issuer=payload.issuer,
            client_id=payload.client_id,
            auth_url=payload.auth_url,
            token_url=payload.token_url,
            jwks_url=payload.jwks_url,
        )
        db.add(platform)
        db.flush()

    for deployment_id in payload.deployment_ids:
        found = db.scalar(
            select(LTIDeployment).where(
                LTIDeployment.platform_id == platform.id,
                LTIDeployment.deployment_id == deployment_id,
            )
        )
        if not found:
            db.add(LTIDeployment(platform_id=platform.id, deployment_id=deployment_id))

    db.commit()
    return {"id": platform.id, "issuer": platform.issuer, "client_id": platform.client_id}


@router.api_route("/lti/login", methods=["GET", "POST"])
async def login(request: Request, db: Session = Depends(get_db)):
    p = await _params(request)
    issuer = p.get("iss")
    client_id = p.get("client_id")
    login_hint = p.get("login_hint")
    target_link_uri = p.get("target_link_uri")
    lti_message_hint = p.get("lti_message_hint")

    if not issuer or not login_hint or not target_link_uri:
        raise HTTPException(status_code=400, detail="Incomplete LTI login initiation")

    stmt = select(LTIPlatform).where(LTIPlatform.issuer == issuer, LTIPlatform.active.is_(True))
    platforms = list(db.scalars(stmt))
    if client_id:
        platforms = [x for x in platforms if x.client_id == client_id]
    if len(platforms) != 1:
        raise HTTPException(status_code=400, detail="LTI platform registration not found or ambiguous")
    platform = platforms[0]

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    db.add(
        LTIState(
            state=state,
            nonce=nonce,
            platform_id=platform.id,
            target_link_uri=target_link_uri,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    )
    db.commit()

    query = {
        "scope": "openid",
        "response_type": "id_token",
        "response_mode": "form_post",
        "prompt": "none",
        "client_id": platform.client_id,
        "redirect_uri": f"{settings.base_url}/lti/launch",
        "login_hint": login_hint,
        "state": state,
        "nonce": nonce,
    }
    if lti_message_hint:
        query["lti_message_hint"] = lti_message_hint
    return RedirectResponse(platform.auth_url + ("&" if "?" in platform.auth_url else "?") + urlencode(query), status_code=302)


async def _decode_id_token(id_token: str, platform: LTIPlatform) -> dict:
    try:
        unverified = jwt.get_unverified_header(id_token)
        kid = unverified.get("kid")
        async with httpx.AsyncClient(timeout=15) as client:
            jwks_response = await client.get(platform.jwks_url)
            jwks_response.raise_for_status()
            keyset = jwks_response.json()
        jwk = next((k for k in keyset.get("keys", []) if k.get("kid") == kid), None)
        if not jwk:
            raise HTTPException(status_code=401, detail="LTI signing key not found")
        key = jwt.PyJWK.from_dict(jwk).key
        return jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=platform.client_id,
            issuer=platform.issuer,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid LTI launch: {type(exc).__name__}") from exc


@router.post("/lti/launch")
async def launch(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    state_value = str(form.get("state") or "")
    id_token = str(form.get("id_token") or "")
    if not state_value or not id_token:
        raise HTTPException(status_code=400, detail="Missing LTI state or id_token")

    state = db.get(LTIState, state_value)
    if not state or state.used or state.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Expired or invalid LTI state")

    platform = db.get(LTIPlatform, state.platform_id)
    if not platform or not platform.active:
        raise HTTPException(status_code=401, detail="LTI platform disabled")

    claims = await _decode_id_token(id_token, platform)
    if claims.get("nonce") != state.nonce:
        raise HTTPException(status_code=401, detail="LTI nonce mismatch")

    deployment_id = claims.get(CLAIM_DEPLOYMENT)
    deployment = db.scalar(
        select(LTIDeployment).where(
            LTIDeployment.platform_id == platform.id,
            LTIDeployment.deployment_id == deployment_id,
            LTIDeployment.active.is_(True),
        )
    )
    if not deployment:
        raise HTTPException(status_code=401, detail="Unknown LTI deployment")

    subject = claims["sub"]
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == platform.issuer,
            ExternalIdentity.subject == subject,
        )
    )
    if identity:
        user = db.get(User, identity.user_id)
        identity.last_seen_at = datetime.now(timezone.utc)
    else:
        user = User(
            display_name=claims.get("name") or claims.get("given_name") or subject,
            email=claims.get("email"),
        )
        db.add(user)
        db.flush()
        identity = ExternalIdentity(
            user_id=user.id,
            issuer=platform.issuer,
            subject=subject,
            client_id=platform.client_id,
        )
        db.add(identity)

    context = claims.get(CLAIM_CONTEXT) or {}
    context_id = str(context.get("id") or "")
    if not context_id:
        raise HTTPException(status_code=400, detail="LTI launch has no course context")

    course = db.scalar(
        select(Course).where(
            Course.platform_issuer == platform.issuer,
            Course.context_id == context_id,
        )
    )
    if not course:
        course = Course(
            organization_id=deployment.organization_id,
            platform_issuer=platform.issuer,
            context_id=context_id,
            title=context.get("title") or context.get("label") or "Curso CAMPUS",
            label=context.get("label"),
        )
        db.add(course)
        db.flush()
    else:
        course.title = context.get("title") or course.title
        course.label = context.get("label") or course.label

    roles = list(claims.get(CLAIM_ROLES) or [])
    local_role = _role_from_lti(roles)
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course.id,
            Membership.user_id == user.id,
        )
    )
    if membership:
        membership.role = local_role
        membership.lti_roles = roles
        membership.active = True
        membership.updated_at = datetime.now(timezone.utc)
    else:
        db.add(
            Membership(
                course_id=course.id,
                user_id=user.id,
                role=local_role,
                lti_roles=roles,
            )
        )

    resource = claims.get(CLAIM_RESOURCE) or {}
    resource_link_id = resource.get("id")
    ags = claims.get(CLAIM_AGS) or {}
    nrps = claims.get(CLAIM_NRPS) or {}

    if resource_link_id:
        link = db.scalar(
            select(LTIResourceLink).where(
                LTIResourceLink.platform_id == platform.id,
                LTIResourceLink.deployment_id == deployment_id,
                LTIResourceLink.resource_link_id == str(resource_link_id),
            )
        )
        if not link:
            link = LTIResourceLink(
                platform_id=platform.id,
                deployment_id=deployment_id,
                resource_link_id=str(resource_link_id),
                course_id=course.id,
            )
            db.add(link)
        link.course_id = course.id
        link.lineitem_url = ags.get("lineitem")
        link.lineitems_url = ags.get("lineitems")
        link.memberships_url = nrps.get("context_memberships_url")
        link.scopes = ags.get("scope") or []
        link.last_launch_at = datetime.now(timezone.utc)

    state.used = True
    db.commit()

    session_token = create_session_token(user.id, course.id, local_role)
    response = RedirectResponse(url=f"{settings.base_url}/?course_id={course.id}", status_code=303)
    response.set_cookie(
        "lms_session",
        session_token,
        httponly=True,
        secure=settings.base_url.startswith("https://"),
        samesite="none" if settings.base_url.startswith("https://") else "lax",
        max_age=12 * 60 * 60,
    )
    return response


@router.post("/lti/deep-link")
async def deep_link_placeholder() -> Response:
    raise HTTPException(
        status_code=501,
        detail="Deep Linking is reserved in the API and will be enabled in the next milestone.",
    )
