import os
from pathlib import Path

os.environ.setdefault("LMS_DATABASE_URL", "sqlite:///./test-lms.db")
os.environ.setdefault("LMS_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("LMS_ADMIN_TOKEN", "test-admin")
os.environ.setdefault("LMS_LTI_PRIVATE_KEY_PATH", "/tmp/lms-test-lti-private.pem")
os.environ.setdefault("LMS_PUBLIC_BASE_URL", "https://lms.example.test")

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_jwks():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        jwks = client.get("/lti/jwks")
        assert jwks.status_code == 200
        key = jwks.json()["keys"][0]
        assert key["kty"] == "RSA"
        assert key["alg"] == "RS256"
        assert key["kid"]


def test_tool_config_is_admin_only():
    with TestClient(app) as client:
        assert client.get("/api/admin/lti/tool-config").status_code == 401
        result = client.get(
            "/api/admin/lti/tool-config",
            headers={"X-Admin-Token": "test-admin"},
        )
        assert result.status_code == 200
        payload = result.json()
        assert payload["tool_url"] == "https://lms.example.test/lti/launch"
        assert payload["jwks_url"] == "https://lms.example.test/lti/jwks"


def test_platform_registration():
    with TestClient(app) as client:
        result = client.post(
            "/api/admin/lti/platforms",
            headers={"X-Admin-Token": "test-admin"},
            json={
                "name": "Moodle de pruebas",
                "issuer": "https://moodle.example.test",
                "client_id": "client-1",
                "auth_url": "https://moodle.example.test/mod/lti/auth.php",
                "token_url": "https://moodle.example.test/mod/lti/token.php",
                "jwks_url": "https://moodle.example.test/mod/lti/certs.php",
                "deployment_ids": ["deployment-1"],
            },
        )
        assert result.status_code == 200
        assert result.json()["client_id"] == "client-1"
