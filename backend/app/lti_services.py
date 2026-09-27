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
    async with httpx.AsyncClient(timeout=15) as client:
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
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(lineitem_url.rstrip("/") + "/scores", json=body, headers=headers)
        response.raise_for_status()
        return {"status_code": response.status_code}


async def fetch_memberships(platform: LTIPlatform, memberships_url: str) -> dict:
    token = await access_token(platform, [NRPS_SCOPE])
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.ims.lti-nrps.v2.membershipcontainer+json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(memberships_url, headers=headers)
        response.raise_for_status()
        return response.json()
