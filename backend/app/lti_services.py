from datetime import datetime, timedelta, timezone
import secrets

import httpx

from .lti_keys import sign_jwt
from .models import LTIPlatform


AGS_SCORE_SCOPE = "https://purl.imsglobal.org/spec/lti-ags/scope/score"
AGS_LINEITEM_SCOPE = "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem"
NRPS_SCOPE = "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly"


async def access_token(platform: LTIPlatform, scopes: list[str]) -> str:
    now = datetime.now(timezone.utc)
    assertion = sign_jwt(
        {
            "iss": platform.client_id,
            "sub": platform.client_id,
            "aud": platform.token_url,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": secrets.token_urlsafe(24),
        }
    )
    payload = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": " ".join(scopes),
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(platform.token_url, data=payload)
        response.raise_for_status()
        return response.json()["access_token"]


async def post_score(
    platform: LTIPlatform,
    lineitem_url: str,
    lti_user_id: str,
    score_given: float,
    score_maximum: float = 100.0,
    comment: str | None = None,
) -> dict:
    token = await access_token(platform, [AGS_SCORE_SCOPE])
    body = {
        "userId": lti_user_id,
        "scoreGiven": score_given,
        "scoreMaximum": score_maximum,
        "activityProgress": "Completed",
        "gradingProgress": "FullyGraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if comment:
        body["comment"] = comment
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.ims.lis.v1.score+json",
    }
    endpoint = lineitem_url.rstrip("/") + "/scores"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(endpoint, json=body, headers=headers)
        response.raise_for_status()
        return {"status_code": response.status_code, "endpoint": endpoint}


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section and "rel=next" not in section:
            continue
        start = section.find("<")
        end = section.find(">", start + 1)
        if start >= 0 and end > start:
            return section[start + 1 : end]
    return None


async def fetch_memberships(platform: LTIPlatform, memberships_url: str) -> dict:
    token = await access_token(platform, [NRPS_SCOPE])
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.ims.lti-nrps.v2.membershipcontainer+json",
    }
    members: list[dict] = []
    context: dict = {}
    url: str | None = memberships_url
    pages = 0

    async with httpx.AsyncClient(timeout=20) as client:
        while url:
            pages += 1
            if pages > 100:
                raise RuntimeError("NRPS pagination exceeded safety limit")
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if not context and isinstance(payload.get("context"), dict):
                context = payload["context"]
            page_members = payload.get("members") or []
            if not isinstance(page_members, list):
                raise RuntimeError("Invalid NRPS response: members is not a list")
            members.extend(page_members)
            url = _next_link(response.headers.get("Link"))

    return {"context": context, "members": members, "pages": pages}
