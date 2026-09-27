import io
import os
import re
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DB_PATH = Path("/tmp/lms-test.db")
STORAGE = Path("/tmp/lms-test-storage")
DB_PATH.unlink(missing_ok=True)
shutil.rmtree(STORAGE, ignore_errors=True)

os.environ["LMS_DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["LMS_SESSION_SECRET"] = "test-session-secret"
os.environ["LMS_ADMIN_TOKEN"] = "test-admin"
os.environ["LMS_LTI_PRIVATE_KEY_PATH"] = "/tmp/lms-test-lti-private.pem"
os.environ["LMS_PUBLIC_BASE_URL"] = "https://lms.example.test"
os.environ["LMS_STORAGE_ROOT"] = str(STORAGE)

import jwt
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.lti import CLAIM_DL_CONTENT_ITEMS, CLAIM_MESSAGE_TYPE
from app.main import app
from app.models import (
    Course,
    CourseModule,
    LTIDeepLinkRequest,
    LTIPlatform,
    Membership,
    Module,
    ModulePermission,
    User,
)
from app.security import create_session_token


def teacher_fixture(slug: str = "modulo-prueba"):
    with SessionLocal() as db:
        user = User(display_name="Docente de prueba", email="teacher@example.test")
        course = Course(
            platform_issuer="https://moodle.example.test",
            context_id=f"context-{slug}",
            title="Curso de prueba",
        )
        module = Module(
            slug=slug,
            code="TEST",
            title="Módulo de prueba",
            description="Contenido de validación",
        )
        db.add_all([user, course, module])
        db.flush()
        db.add(
            Membership(
                course_id=course.id,
                user_id=user.id,
                role="teacher",
                lti_roles=["Instructor"],
            )
        )
        db.add(ModulePermission(module_id=module.id, user_id=user.id, permission="owner"))
        db.commit()
        return user.id, course.id, module.id


def session_cookie(user_id: int, course_id: int, role: str = "teacher") -> str:
    return create_session_token(user_id, course_id, role)


def minimal_scorm_zip() -> bytes:
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="TEST" xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
 xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2">
 <organizations default="ORG">
   <organization identifier="ORG">
     <title>SCORM de prueba</title>
     <item identifier="I1" identifierref="R1"><title>Lección</title></item>
   </organization>
 </organizations>
 <resources>
   <resource identifier="R1" type="webcontent" adlcp:scormtype="sco" href="index.html">
     <file href="index.html"/>
   </resource>
 </resources>
</manifest>"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", manifest)
        zf.writestr(
            "index.html",
            "<!doctype html><html><body><script>"
            "var api=window.parent.API;api.LMSInitialize('');"
            "api.LMSSetValue('cmi.core.lesson_status','completed');"
            "api.LMSCommit('');"
            "</script>SCORM</body></html>",
        )
    return out.getvalue()


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


def test_deep_link_selection_creates_course_module_and_signed_response():
    with TestClient(app) as client:
        user_id, course_id, module_id = teacher_fixture("deep-link-module")
        with SessionLocal() as db:
            platform = LTIPlatform(
                name="Moodle Deep",
                issuer="https://moodle.deep.example.test",
                client_id="deep-client",
                auth_url="https://moodle.deep.example.test/auth",
                token_url="https://moodle.deep.example.test/token",
                jwks_url="https://moodle.deep.example.test/jwks",
            )
            db.add(platform)
            db.flush()
            request_row = LTIDeepLinkRequest(
                token="deep-request-token",
                platform_id=platform.id,
                deployment_id="deployment-deep",
                course_id=course_id,
                user_id=user_id,
                return_url="https://moodle.deep.example.test/mod/lti/content.php",
                data="opaque-data",
                accepts=["ltiResourceLink"],
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            db.add(request_row)
            db.commit()

        client.cookies.set("lms_session", session_cookie(user_id, course_id))
        response = client.post(
            "/api/lti/deep-link/select",
            json={"request_token": "deep-request-token", "module_id": module_id},
        )
        assert response.status_code == 200
        match = re.search(r'name="JWT" value="([^"]+)"', response.text)
        assert match
        claims = jwt.decode(match.group(1), options={"verify_signature": False})
        assert claims[CLAIM_MESSAGE_TYPE] == "LtiDeepLinkingResponse"
        item = claims[CLAIM_DL_CONTENT_ITEMS][0]
        assert item["type"] == "ltiResourceLink"
        assert item["custom"]["lms_module_slug"] == "deep-link-module"
        assert item["lineItem"]["scoreMaximum"] == 100

        with SessionLocal() as db:
            course_module = db.scalar(
                __import__("sqlalchemy").select(CourseModule).where(
                    CourseModule.course_id == course_id,
                    CourseModule.module_id == module_id,
                )
            )
            assert course_module is not None


def test_scorm_upload_launch_and_runtime_persistence():
    with TestClient(app) as client:
        user_id, course_id, module_id = teacher_fixture("scorm-module")
        with SessionLocal() as db:
            course_module = CourseModule(course_id=course_id, module_id=module_id)
            db.add(course_module)
            db.commit()
            db.refresh(course_module)
            course_module_id = course_module.id

        client.cookies.set("lms_session", session_cookie(user_id, course_id))
        upload = client.post(
            f"/api/modules/{module_id}/scorm-packages",
            files={"file": ("sample.zip", minimal_scorm_zip(), "application/zip")},
        )
        assert upload.status_code == 200, upload.text
        package = upload.json()
        assert package["title"] == "SCORM de prueba"
        assert package["deduplicated"] is False

        launch = client.post(
            f"/api/course-modules/{course_module_id}/scorm/{package['id']}/launch"
        )
        assert launch.status_code == 200, launch.text
        launch_url = launch.json()["url"]
        query = parse_qs(urlparse(launch_url).query)
        registration_id = int(query["lms_registration"][0])
        token = query["lms_token"][0]

        state = client.get(
            f"/runtime-api/scorm/registrations/{registration_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert state.status_code == 200
        assert state.json()["cmi"]["cmi.core.lesson_status"] == "not attempted"

        commit = client.put(
            f"/runtime-api/scorm/registrations/{registration_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "cmi": {
                    "cmi.core.lesson_status": "completed",
                    "cmi.core.score.raw": "88.5",
                    "cmi.core.lesson_location": "pagina-3",
                    "cmi.suspend_data": "estado",
                }
            },
        )
        assert commit.status_code == 200
        assert commit.json()["lesson_status"] == "completed"
        assert commit.json()["score_raw"] == 88.5

        restored = client.get(
            f"/runtime-api/scorm/registrations/{registration_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert restored.json()["cmi"]["cmi.core.lesson_status"] == "completed"
        assert restored.json()["cmi"]["cmi.core.lesson_location"] == "pagina-3"
