from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode
import secrets

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .lti_keys import public_jwk, sign_jwt
from .models import (
    Course,
    CourseModule,
    ExternalIdentity,
    LTIDeepLinkRequest,
    LTIPlatform,
    LTIDeployment,
    LTIResourceLink,
    LTIState,
    Membership,
    Module,
    ModulePermission,
    User,
)
from .security import create_session_token, read_session, require_admin, require_teacher
from .settings import get_settings


router = APIRouter()
settings = get_settings()

CLAIM_DEPLOYMENT = "https://purl.imsglobal.org/spec/lti/claim/deployment_id"
CLAIM_ROLES = "https://purl.imsglobal.org/spec/lti/claim/roles"
CLAIM_CONTEXT = "https://purl.imsglobal.org/spec/lti/claim/context"
CLAIM_RESOURCE = "https://purl.imsglobal.org/spec/lti/claim/resource_link"
CLAIM_CUSTOM = "https://purl.imsglobal.org/spec/lti/claim/custom"
CLAIM_MESSAGE_TYPE = "https://purl.imsglobal.org/spec/lti/claim/message_type"
CLAIM_VERSION = "https://purl.imsglobal.org/spec/lti/claim/version"
CLAIM_AGS = "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint"
CLAIM_NRPS = "https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice"
CLAIM_DL_SETTINGS = "https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings"
CLAIM_DL_CONTENT_ITEMS = "https://purl.imsglobal.org/spec/lti-dl/claim/content_items"
CLAIM_DL_DATA = "https://purl.imsglobal.org/spec/lti-dl/claim/data"


class PlatformIn(BaseModel):
    name: str
    issuer: str
    client_id: str
    auth_url: str
    token_url: str
    jwks_url: str
    deployment_ids: list[str] = []


class DeepLinkSelectionIn(BaseModel):
    request_token: str
    module_id: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _role_from_lti(roles: list[str]) -> str:
    joined = " ".join(roles).lower()
    if "administrator" in joined:
        return "admin"
    if "instructor" in joined or "teachingassistant" in joined or "mentor" in joined:
        return "teacher"
    return "student"


def _teacher_can_edit_module(db: Session, user_id: int, module_id: int) -> bool:
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    return bool(permission and permission.permission in {"owner", "editor"})


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
        "redirect_uris": [f"{settings.base_url}/lti/launch"],
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
        existing.active = True
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

    stmt = select(LTIPlatform).where(
        LTIPlatform.issuer == issuer,
        LTIPlatform.active.is_(True),
    )
    platforms = list(db.scalars(stmt))
    if client_id:
        platforms = [x for x in platforms if x.client_id == client_id]
    if len(platforms) != 1:
        raise HTTPException(
            status_code=400,
            detail="LTI platform registration not found or ambiguous",
        )
    platform = platforms[0]

    state_value = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    db.add(
        LTIState(
            state=state_value,
            nonce=nonce,
            platform_id=platform.id,
            target_link_uri=target_link_uri,
            expires_at=_utcnow() + timedelta(minutes=10),
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
        "state": state_value,
        "nonce": nonce,
    }
    if lti_message_hint:
        query["lti_message_hint"] = lti_message_hint
    separator = "&" if "?" in platform.auth_url else "?"
    return RedirectResponse(platform.auth_url + separator + urlencode(query), status_code=302)


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
        raise HTTPException(
            status_code=401,
            detail=f"Invalid LTI launch: {type(exc).__name__}",
        ) from exc


def _validate_lti_claims(claims: dict) -> str:
    version = str(claims.get(CLAIM_VERSION) or "")
    if version and version != "1.3.0":
        raise HTTPException(status_code=400, detail="Unsupported LTI version")
    message_type = str(claims.get(CLAIM_MESSAGE_TYPE) or "")
    if message_type not in {"LtiResourceLinkRequest", "LtiDeepLinkingRequest"}:
        raise HTTPException(status_code=400, detail="Unsupported LTI message type")
    return message_type


def _upsert_identity(db: Session, platform: LTIPlatform, claims: dict) -> User:
    subject = str(claims["sub"])
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == platform.issuer,
            ExternalIdentity.subject == subject,
        )
    )
    if identity:
        user = db.get(User, identity.user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Federated identity is invalid")
        identity.last_seen_at = _utcnow()
        if claims.get("name"):
            user.display_name = str(claims["name"])
        if claims.get("email"):
            user.email = str(claims["email"])
        return user

    user = User(
        display_name=claims.get("name") or claims.get("given_name") or subject,
        email=claims.get("email"),
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
    return user


def _upsert_course(
    db: Session,
    platform: LTIPlatform,
    deployment: LTIDeployment,
    claims: dict,
) -> Course:
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
        course.active = True
    return course


def _upsert_membership(db: Session, course: Course, user: User, claims: dict) -> str:
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
        membership.updated_at = _utcnow()
    else:
        db.add(
            Membership(
                course_id=course.id,
                user_id=user.id,
                role=local_role,
                lti_roles=roles,
            )
        )
    return local_role


def _bind_resource_link(
    db: Session,
    platform: LTIPlatform,
    deployment_id: str,
    course: Course,
    claims: dict,
) -> LTIResourceLink | None:
    resource = claims.get(CLAIM_RESOURCE) or {}
    resource_link_id = resource.get("id")
    if not resource_link_id:
        return None

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

    ags = claims.get(CLAIM_AGS) or {}
    nrps = claims.get(CLAIM_NRPS) or {}
    link.course_id = course.id
    link.lineitem_url = ags.get("lineitem")
    link.lineitems_url = ags.get("lineitems")
    link.memberships_url = nrps.get("context_memberships_url")
    link.scopes = ags.get("scope") or []
    link.last_launch_at = _utcnow()

    custom = claims.get(CLAIM_CUSTOM) or {}
    module_slug = str(custom.get("lms_module_slug") or "").strip()
    if module_slug:
        module = db.scalar(select(Module).where(Module.slug == module_slug, Module.active.is_(True)))
        if module:
            course_module = db.scalar(
                select(CourseModule).where(
                    CourseModule.course_id == course.id,
                    CourseModule.module_id == module.id,
                )
            )
            if course_module:
                link.course_module_id = course_module.id
    return link


@router.post("/lti/launch")
async def launch(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    state_value = str(form.get("state") or "")
    id_token = str(form.get("id_token") or "")
    if not state_value or not id_token:
        raise HTTPException(status_code=400, detail="Missing LTI state or id_token")

    state = db.get(LTIState, state_value)
    if not state or state.used or _as_aware(state.expires_at) < _utcnow():
        raise HTTPException(status_code=401, detail="Expired or invalid LTI state")

    platform = db.get(LTIPlatform, state.platform_id)
    if not platform or not platform.active:
        raise HTTPException(status_code=401, detail="LTI platform disabled")

    claims = await _decode_id_token(id_token, platform)
    if claims.get("nonce") != state.nonce:
        raise HTTPException(status_code=401, detail="LTI nonce mismatch")
    message_type = _validate_lti_claims(claims)

    deployment_id = str(claims.get(CLAIM_DEPLOYMENT) or "")
    deployment = db.scalar(
        select(LTIDeployment).where(
            LTIDeployment.platform_id == platform.id,
            LTIDeployment.deployment_id == deployment_id,
            LTIDeployment.active.is_(True),
        )
    )
    if not deployment:
        raise HTTPException(status_code=401, detail="Unknown LTI deployment")

    user = _upsert_identity(db, platform, claims)
    course = _upsert_course(db, platform, deployment, claims)
    local_role = _upsert_membership(db, course, user, claims)

    state.used = True

    if message_type == "LtiDeepLinkingRequest":
        if local_role not in {"teacher", "admin"}:
            raise HTTPException(status_code=403, detail="Deep Linking requires a teacher role")
        dl = claims.get(CLAIM_DL_SETTINGS) or {}
        return_url = str(dl.get("deep_link_return_url") or "")
        if not return_url:
            raise HTTPException(status_code=400, detail="Deep Linking return URL is missing")
        request_token = secrets.token_urlsafe(32)
        db.add(
            LTIDeepLinkRequest(
                token=request_token,
                platform_id=platform.id,
                deployment_id=deployment_id,
                course_id=course.id,
                user_id=user.id,
                return_url=return_url,
                data=dl.get("data"),
                accepts=list(dl.get("accept_types") or []),
                expires_at=_utcnow() + timedelta(minutes=15),
            )
        )
        db.commit()
        session_token = create_session_token(user.id, course.id, local_role)
        response = RedirectResponse(
            url=f"{settings.base_url}/deep-link.html?request={request_token}",
            status_code=303,
        )
        response.set_cookie(
            "lms_session",
            session_token,
            httponly=True,
            secure=settings.base_url.startswith("https://"),
            samesite="none" if settings.base_url.startswith("https://") else "lax",
            max_age=12 * 60 * 60,
        )
        return response

    link = _bind_resource_link(db, platform, deployment_id, course, claims)
    db.commit()

    session_token = create_session_token(user.id, course.id, local_role)
    target = f"{settings.base_url}/?course_id={course.id}"
    if link and link.course_module_id:
        target = f"{settings.base_url}/?course_id={course.id}&course_module_id={link.course_module_id}"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        "lms_session",
        session_token,
        httponly=True,
        secure=settings.base_url.startswith("https://"),
        samesite="none" if settings.base_url.startswith("https://") else "lax",
        max_age=12 * 60 * 60,
    )
    return response


@router.get("/api/lti/deep-link/{request_token}")
def deep_link_options(
    request_token: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    request_row = db.get(LTIDeepLinkRequest, request_token)
    if (
        not request_row
        or request_row.used
        or _as_aware(request_row.expires_at) < _utcnow()
        or request_row.user_id != int(session["sub"])
        or request_row.course_id != int(session.get("course_id") or 0)
    ):
        raise HTTPException(status_code=404, detail="Deep Linking request not found or expired")

    permissions = list(
        db.scalars(
            select(ModulePermission).where(
                ModulePermission.user_id == request_row.user_id,
                ModulePermission.permission.in_(["owner", "editor"]),
            )
        )
    )
    module_ids = [row.module_id for row in permissions]
    modules = (
        list(
            db.scalars(
                select(Module)
                .where(Module.id.in_(module_ids), Module.active.is_(True))
                .order_by(Module.title)
            )
        )
        if module_ids
        else []
    )
    return {
        "request_token": request_row.token,
        "course_id": request_row.course_id,
        "modules": [
            {
                "id": module.id,
                "slug": module.slug,
                "code": module.code,
                "title": module.title,
                "description": module.description,
                "version": module.version,
            }
            for module in modules
        ],
    }


@router.post("/api/lti/deep-link/select", response_class=HTMLResponse)
def deep_link_select(
    payload: DeepLinkSelectionIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    request_row = db.get(LTIDeepLinkRequest, payload.request_token)
    if (
        not request_row
        or request_row.used
        or _as_aware(request_row.expires_at) < _utcnow()
        or request_row.user_id != int(session["sub"])
        or request_row.course_id != int(session.get("course_id") or 0)
    ):
        raise HTTPException(status_code=404, detail="Deep Linking request not found or expired")

    module = db.get(Module, payload.module_id)
    if not module or not module.active:
        raise HTTPException(status_code=404, detail="Module not found")
    if not _teacher_can_edit_module(db, request_row.user_id, module.id):
        raise HTTPException(status_code=403, detail="Module edit permission required")

    course_module = db.scalar(
        select(CourseModule).where(
            CourseModule.course_id == request_row.course_id,
            CourseModule.module_id == module.id,
        )
    )
    if not course_module:
        course_module = CourseModule(course_id=request_row.course_id, module_id=module.id)
        db.add(course_module)
        db.flush()
    else:
        course_module.active = True

    platform = db.get(LTIPlatform, request_row.platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail="LTI platform not found")

    now = _utcnow()
    response_claims = {
        "iss": platform.client_id,
        "aud": platform.issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "nonce": secrets.token_urlsafe(24),
        CLAIM_DEPLOYMENT: request_row.deployment_id,
        CLAIM_MESSAGE_TYPE: "LtiDeepLinkingResponse",
        CLAIM_VERSION: "1.3.0",
        CLAIM_DL_CONTENT_ITEMS: [
            {
                "type": "ltiResourceLink",
                "title": module.title,
                "text": module.description or module.title,
                "url": f"{settings.base_url}/lti/launch",
                "custom": {
                    "lms_module_slug": module.slug,
                    "lms_course_module_id": str(course_module.id),
                },
            }
        ],
    }
    if request_row.data is not None:
        response_claims[CLAIM_DL_DATA] = request_row.data

    token = sign_jwt(response_claims)
    request_row.used = True
    db.commit()

    return HTMLResponse(
        """<!doctype html><html><body>
<form id="lti-return" method="post" action="{action}">
<input type="hidden" name="JWT" value="{jwt}">
<noscript><button type="submit">Volver a CAMPUS</button></noscript>
</form>
<script>document.getElementById("lti-return").submit();</script>
</body></html>""".format(
            action=escape(request_row.return_url, quote=True),
            jwt=escape(token, quote=True),
        )
    )


@router.post("/lti/deep-link")
async def deep_link_entry() -> RedirectResponse:
    return RedirectResponse(url=f"{settings.base_url}/", status_code=303)
