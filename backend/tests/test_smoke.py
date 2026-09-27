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
os.environ["LMS_AI_ENCRYPTION_SECRET"] = "test-ai-encryption-secret"
os.environ["LMS_ADMIN_TOKEN"] = "test-admin"
os.environ["LMS_LTI_PRIVATE_KEY_PATH"] = "/tmp/lms-test-lti-private.pem"
os.environ["LMS_PUBLIC_BASE_URL"] = "https://lms.example.test"
os.environ["LMS_STORAGE_ROOT"] = str(STORAGE)

import jwt
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.lti import CLAIM_DL_CONTENT_ITEMS, CLAIM_MESSAGE_TYPE, _upsert_identity
from app.main import app
from app.models import (
    Course,
    CourseModule,
    CourseModuleAIConfig,
    AssessmentCriterion,
    AssessmentItem,
    LearningResult,
    LTIDeepLinkRequest,
    LTIPlatform,
    Membership,
    Module,
    ModulePermission,
    TeacherAISettings,
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



def minimal_scorm_2004_zip() -> bytes:
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="TEST2004"
 xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"
 xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_v1p3"
 xmlns:imsss="http://www.imsglobal.org/xsd/imsss">
 <metadata>
   <schema>ADL SCORM</schema>
   <schemaversion>2004 4th Edition</schemaversion>
 </metadata>
 <organizations default="ORG">
   <organization identifier="ORG">
     <title>SCORM 2004 de prueba</title>
     <item identifier="I1" identifierref="R1"><title>Lección 2004</title></item>
   </organization>
 </organizations>
 <resources>
   <resource identifier="R1" type="webcontent" adlcp:scormType="sco" href="index.html">
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
            "var api=window.parent.API_1484_11;api.Initialize('');"
            "api.SetValue('cmi.completion_status','completed');"
            "api.SetValue('cmi.success_status','passed');"
            "api.SetValue('cmi.score.raw','92');api.Commit('');"
            "</script>SCORM 2004</body></html>",
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


def test_teacher_private_scorm_isolation_and_sharing():
    with TestClient(app) as client:
        teacher_a, course_a, module_a = teacher_fixture("teacher-a-library")
        teacher_b, course_b, module_b = teacher_fixture("teacher-b-library")

        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        upload = client.post(
            "/api/scorm-library",
            data={"visibility": "private", "title": "SCORM privado A"},
            files={"file": ("private-a.zip", minimal_scorm_zip(), "application/zip")},
        )
        assert upload.status_code == 200, upload.text
        package_id = upload.json()["id"]
        assert upload.json()["owner_user_id"] == teacher_a
        assert upload.json()["visibility"] == "private"

        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        forbidden = client.post(
            f"/api/modules/{module_b}/scorm-packages/{package_id}",
            json={"position": 0, "required": True, "weight": 1, "settings": {}},
        )
        assert forbidden.status_code == 403

        mine_b = client.get("/api/scorm-library?scope=mine")
        assert mine_b.status_code == 200
        assert all(item["id"] != package_id for item in mine_b.json())

        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        shared = client.patch(
            f"/api/scorm-library/{package_id}",
            json={"visibility": "shared"},
        )
        assert shared.status_code == 200
        assert shared.json()["visibility"] == "shared"

        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        shared_list = client.get("/api/scorm-library?scope=shared")
        assert shared_list.status_code == 200
        assert any(item["id"] == package_id for item in shared_list.json())

        attached = client.post(
            f"/api/modules/{module_b}/scorm-packages/{package_id}",
            json={"position": 1, "required": True, "weight": 1, "settings": {}},
        )
        assert attached.status_code == 200, attached.text
        assert attached.json()["module_id"] == module_b


def test_teacher_groups_are_isolated_until_owner_adds_coteacher():
    with TestClient(app) as client:
        teacher_a, course_a, _ = teacher_fixture("group-owner-a")
        teacher_b, course_b, _ = teacher_fixture("group-coteacher-b")
        teacher_c, _, _ = teacher_fixture("group-third-c")

        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        created = client.post(
            "/api/groups",
            json={
                "title": "1 GA A",
                "label": "1GA-A",
                "academic_year": "2026/2027",
                "description": "Grupo local de prueba",
                "settings": {"evaluation": "RA"},
            },
        )
        assert created.status_code == 200, created.text
        group_id = created.json()["id"]
        assert created.json()["source_type"] == "local"
        assert created.json()["owned"] is True
        assert created.json()["join_code"]

        roster = io.BytesIO(
            "nombre,email,rol\nAlumno Uno,alumno1@example.test,student\nAlumno Dos,alumno2@example.test,student\n".encode()
        )
        imported = client.post(
            f"/api/groups/{group_id}/members/import-csv",
            files={"file": ("roster.csv", roster.getvalue(), "text/csv")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 2

        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        blocked = client.get(f"/api/groups/{group_id}")
        assert blocked.status_code == 403

        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        add_coteacher = client.post(
            f"/api/groups/{group_id}/members",
            json={"user_id": teacher_b, "role": "teacher"},
        )
        assert add_coteacher.status_code == 200, add_coteacher.text

        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        allowed = client.get(f"/api/groups/{group_id}")
        assert allowed.status_code == 200
        assert allowed.json()["owned"] is False

        updated = client.patch(
            f"/api/groups/{group_id}",
            json={"description": "Actualizado por profesor colaborador"},
        )
        assert updated.status_code == 200
        assert updated.json()["description"] == "Actualizado por profesor colaborador"

        cannot_add_teacher = client.post(
            f"/api/groups/{group_id}/members",
            json={"user_id": teacher_c, "role": "teacher"},
        )
        assert cannot_add_teacher.status_code == 403

        activated = client.post(f"/api/groups/{group_id}/activate")
        assert activated.status_code == 200
        activated_token = activated.cookies.get("lms_session")
        assert activated_token
        activated_claims = jwt.decode(
            activated_token,
            "test-session-secret",
            algorithms=["HS256"],
        )
        assert activated_claims["course_id"] == group_id

        client.cookies.clear()
        client.cookies.set("lms_session", activated_token)
        me_response = client.get("/api/me")
        assert me_response.status_code == 200
        assert me_response.json()["course"]["id"] == group_id


def test_scorm_2004_detection_launch_and_runtime_persistence():
    with TestClient(app) as client:
        user_id, course_id, module_id = teacher_fixture("scorm-2004-module")
        with SessionLocal() as db:
            course_module = CourseModule(course_id=course_id, module_id=module_id)
            db.add(course_module)
            db.commit()
            db.refresh(course_module)
            course_module_id = course_module.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(user_id, course_id))
        upload = client.post(
            f"/api/modules/{module_id}/scorm-packages",
            files={"file": ("sample-2004.zip", minimal_scorm_2004_zip(), "application/zip")},
        )
        assert upload.status_code == 200, upload.text
        package = upload.json()
        assert package["standard"] == "SCORM_2004"
        assert package["title"] == "SCORM 2004 de prueba"

        launch = client.post(
            f"/api/course-modules/{course_module_id}/scorm/{package['id']}/launch"
        )
        assert launch.status_code == 200, launch.text
        query = parse_qs(urlparse(launch.json()["url"]).query)
        registration_id = int(query["lms_registration"][0])
        token = query["lms_token"][0]

        state = client.get(
            f"/runtime-api/scorm/registrations/{registration_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert state.status_code == 200
        assert state.json()["standard"] == "SCORM_2004"
        assert state.json()["cmi"]["cmi.completion_status"] == "not attempted"
        assert state.json()["cmi"]["cmi.learner_name"] == "Docente de prueba"

        commit = client.put(
            f"/runtime-api/scorm/registrations/{registration_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "cmi": {
                    "cmi.completion_status": "completed",
                    "cmi.success_status": "passed",
                    "cmi.score.raw": "92",
                    "cmi.score.min": "0",
                    "cmi.score.max": "100",
                    "cmi.location": "tema-2",
                    "cmi.suspend_data": "estado-2004",
                }
            },
        )
        assert commit.status_code == 200
        assert commit.json()["standard"] == "SCORM_2004"
        assert commit.json()["lesson_status"] == "completed"
        assert commit.json()["score_raw"] == 92.0

        restored = client.get(
            f"/runtime-api/scorm/registrations/{registration_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert restored.json()["cmi"]["cmi.location"] == "tema-2"
        assert restored.json()["cmi"]["cmi.suspend_data"] == "estado-2004"


def test_group_self_enrolment_is_opt_in_and_module_catalog_is_scoped():
    with TestClient(app) as client:
        teacher_id, teacher_course_id, teacher_module_id = teacher_fixture("self-enrol-teacher")
        student_id, student_course_id, student_module_id = teacher_fixture("self-enrol-student")
        # Convert the second synthetic teacher into a student context for this test.
        student_token = session_cookie(student_id, student_course_id, role="student")

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, teacher_course_id))
        created = client.post(
            "/api/groups",
            json={
                "title": "Grupo autoinscripción",
                "settings": {"allow_self_enrol": False},
            },
        )
        assert created.status_code == 200
        group_id = created.json()["id"]
        join_code = created.json()["join_code"]

        client.cookies.clear()
        client.cookies.set("lms_session", student_token)
        denied = client.post("/api/groups/join", json={"code": join_code})
        assert denied.status_code == 403

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, teacher_course_id))
        enabled = client.patch(
            f"/api/groups/{group_id}",
            json={
                "settings": {
                    "allow_self_enrol": True,
                    "show_scores": True,
                    "max_attempts_default": 2,
                }
            },
        )
        assert enabled.status_code == 200

        client.cookies.clear()
        client.cookies.set("lms_session", student_token)
        joined = client.post("/api/groups/join", json={"code": join_code})
        assert joined.status_code == 200
        assert joined.json()["group_id"] == group_id
        assert joined.json()["role"] == "student"

        # A student must not see unrelated modules from other teachers.
        catalog = client.get("/api/modules")
        assert catalog.status_code == 200
        visible_ids = {item["id"] for item in catalog.json()}
        assert teacher_module_id not in visible_ids
        assert student_module_id not in visible_ids


def test_lti_first_launch_reuses_provisional_roster_user_by_email():
    with TestClient(app):
        with SessionLocal() as db:
            provisional = User(
                display_name="Nombre provisional",
                email="alumno.provisional@example.test",
                active=True,
            )
            platform = LTIPlatform(
                name="Moodle provisional",
                issuer="https://moodle.provisional.example.test",
                client_id="provisional-client",
                auth_url="https://moodle.provisional.example.test/auth",
                token_url="https://moodle.provisional.example.test/token",
                jwks_url="https://moodle.provisional.example.test/jwks",
            )
            db.add_all([provisional, platform])
            db.commit()
            db.refresh(provisional)
            db.refresh(platform)
            provisional_id = provisional.id

            user = _upsert_identity(
                db,
                platform,
                {
                    "sub": "campus-user-123",
                    "name": "Nombre oficial CAMPUS",
                    "email": "ALUMNO.PROVISIONAL@example.test",
                },
            )
            db.commit()

            assert user.id == provisional_id
            assert user.display_name == "Nombre oficial CAMPUS"
            identity = db.scalar(
                __import__("sqlalchemy").select(
                    __import__("app.models", fromlist=["ExternalIdentity"]).ExternalIdentity
                ).where(
                    __import__("app.models", fromlist=["ExternalIdentity"]).ExternalIdentity.user_id
                    == provisional_id
                )
            )
            assert identity is not None
            assert identity.subject == "campus-user-123"


def test_scorm_editing_creates_revision_without_changing_existing_student_grade():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("versioned-scorm")
        with SessionLocal() as db:
            course_module = CourseModule(course_id=course_id, module_id=module_id)
            student_a = User(display_name="Alumno A", email="student-a@example.test")
            student_b = User(display_name="Alumno B", email="student-b@example.test")
            db.add_all([course_module, student_a, student_b])
            db.flush()
            db.add_all([
                Membership(course_id=course_id, user_id=student_a.id, role="student", lti_roles=[]),
                Membership(course_id=course_id, user_id=student_b.id, role="student", lti_roles=[]),
            ])
            db.commit()
            db.refresh(course_module)
            db.refresh(student_a)
            db.refresh(student_b)
            course_module_id = course_module.id
            student_a_id = student_a.id
            student_b_id = student_b.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        upload = client.post(
            f"/api/modules/{module_id}/scorm-packages",
            files={"file": ("versioned.zip", minimal_scorm_zip(), "application/zip")},
        )
        assert upload.status_code == 200, upload.text
        revision_1 = upload.json()
        assert revision_1["revision_number"] == 1

        # Alumno A starts revision 1 and receives a grade.
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(student_a_id, course_id, role="student"))
        launch_a1 = client.post(
            f"/api/course-modules/{course_module_id}/scorm/{revision_1['id']}/launch"
        )
        assert launch_a1.status_code == 200, launch_a1.text
        assert launch_a1.json()["package_id"] == revision_1["id"]
        query_a1 = parse_qs(urlparse(launch_a1.json()["url"]).query)
        registration_a = int(query_a1["lms_registration"][0])
        token_a = query_a1["lms_token"][0]

        scored = client.put(
            f"/runtime-api/scorm/registrations/{registration_a}",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "cmi": {
                    "cmi.core.lesson_status": "completed",
                    "cmi.core.score.raw": "73",
                    "cmi.core.score.min": "0",
                    "cmi.core.score.max": "100",
                    "cmi.suspend_data": "estado-revision-1",
                }
            },
        )
        assert scored.status_code == 200
        assert scored.json()["score_raw"] == 73.0

        # Teacher edits content and inserts a multimedia resource.
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        media = client.post(
            "/api/media-library",
            data={"title": "Audio de prueba", "visibility": "private"},
            files={"file": ("audio.mp3", b"ID3-test-audio", "audio/mpeg")},
        )
        assert media.status_code == 200, media.text
        media_id = media.json()["id"]

        draft_response = client.post(f"/api/scorm-library/{revision_1['id']}/draft")
        assert draft_response.status_code == 200, draft_response.text
        draft_id = draft_response.json()["draft_id"]

        original = client.get(
            f"/api/scorm-editor/{draft_id}/file",
            params={"path": "index.html"},
        )
        assert original.status_code == 200
        modified_html = original.json()["content"].replace(
            "SCORM</body>",
            "SCORM revisión 2<audio controls src='media/audio.mp3'></audio></body>",
        )
        saved = client.put(
            f"/api/scorm-editor/{draft_id}/file",
            json={"path": "index.html", "content": modified_html},
        )
        assert saved.status_code == 200

        inserted = client.post(
            f"/api/scorm-editor/{draft_id}/insert-media/{media_id}",
            json={"target_path": "media/audio.mp3"},
        )
        assert inserted.status_code == 200, inserted.text

        published = client.post(f"/api/scorm-editor/{draft_id}/publish")
        assert published.status_code == 200, published.text
        revision_2 = published.json()["package"]
        assert revision_2["revision_number"] == 2
        assert revision_2["id"] != revision_1["id"]
        assert published.json()["preserved_registrations"] >= 1

        # Exported revision is still a portable SCORM ZIP containing both changes.
        exported = client.get(f"/api/scorm-library/{revision_2['id']}/export")
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert "imsmanifest.xml" in archive.namelist()
            assert "index.html" in archive.namelist()
            assert "media/audio.mp3" in archive.namelist()
            assert "SCORM revisión 2" in archive.read("index.html").decode("utf-8")

        # Alumno A remains pinned to revision 1 and keeps the exact saved grade/state.
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(student_a_id, course_id, role="student"))
        relaunch_a = client.post(
            f"/api/course-modules/{course_module_id}/scorm/{revision_2['id']}/launch"
        )
        assert relaunch_a.status_code == 200, relaunch_a.text
        assert relaunch_a.json()["package_id"] == revision_1["id"]
        assert relaunch_a.json()["pinned_to_existing_revision"] is True
        query_a2 = parse_qs(urlparse(relaunch_a.json()["url"]).query)
        token_a2 = query_a2["lms_token"][0]
        state_a = client.get(
            f"/runtime-api/scorm/registrations/{registration_a}",
            headers={"Authorization": f"Bearer {token_a2}"},
        )
        assert state_a.status_code == 200
        assert state_a.json()["cmi"]["cmi.core.score.raw"] == "73.0"
        assert state_a.json()["cmi"]["cmi.suspend_data"] == "estado-revision-1"

        # Alumno B has no prior attempt, so receives revision 2.
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(student_b_id, course_id, role="student"))
        launch_b = client.post(
            f"/api/course-modules/{course_module_id}/scorm/{revision_2['id']}/launch"
        )
        assert launch_b.status_code == 200, launch_b.text
        assert launch_b.json()["package_id"] == revision_2["id"]
        assert launch_b.json()["revision_number"] == 2
        assert launch_b.json()["pinned_to_existing_revision"] is False


def test_media_library_is_private_and_supports_range_playback():
    with TestClient(app) as client:
        teacher_a, course_a, _ = teacher_fixture("media-owner")
        teacher_b, course_b, _ = teacher_fixture("media-other")

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        uploaded = client.post(
            "/api/media-library",
            data={"title": "Vídeo privado", "visibility": "private"},
            files={"file": ("video.mp4", b"0123456789abcdef", "video/mp4")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset = uploaded.json()

        full = client.get(f"/api/media-library/{asset['id']}/content")
        assert full.status_code == 200
        assert full.content == b"0123456789abcdef"

        ranged = client.get(
            f"/api/media-library/{asset['id']}/content",
            headers={"Range": "bytes=2-5"},
        )
        assert ranged.status_code == 206
        assert ranged.content == b"2345"
        assert ranged.headers.get("content-range", "").startswith("bytes 2-5/")

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        forbidden = client.get(f"/api/media-library/{asset['id']}/content")
        assert forbidden.status_code == 403


def test_shared_scorm_revision_does_not_silently_update_other_teacher_module():
    with TestClient(app) as client:
        teacher_a, course_a, module_a = teacher_fixture("shared-owner-revision")
        teacher_b, course_b, module_b = teacher_fixture("shared-consumer-revision")

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        upload = client.post(
            f"/api/modules/{module_a}/scorm-packages",
            data={"visibility": "shared", "title": "SCORM compartido versionado"},
            files={"file": ("shared-versioned.zip", minimal_scorm_zip(), "application/zip")},
        )
        assert upload.status_code == 200, upload.text
        revision_1 = upload.json()

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        attached_b = client.post(
            f"/api/modules/{module_b}/scorm-packages/{revision_1['id']}",
            json={"position": 0, "required": True, "weight": 1, "settings": {}},
        )
        assert attached_b.status_code == 200, attached_b.text

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        draft = client.post(f"/api/scorm-library/{revision_1['id']}/draft")
        assert draft.status_code == 200
        draft_id = draft.json()["draft_id"]
        changed = client.put(
            f"/api/scorm-editor/{draft_id}/file",
            json={
                "path": "index.html",
                "content": "<!doctype html><html><body>Revisión nueva</body></html>",
            },
        )
        assert changed.status_code == 200
        published = client.post(f"/api/scorm-editor/{draft_id}/publish")
        assert published.status_code == 200, published.text
        revision_2 = published.json()["package"]
        assert published.json()["preserved_external_modules"] >= 1

        modules_a = client.get(f"/api/modules/{module_a}/scorm-packages")
        assert modules_a.status_code == 200
        assert [row["id"] for row in modules_a.json()] == [revision_2["id"]]

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        modules_b = client.get(f"/api/modules/{module_b}/scorm-packages")
        assert modules_b.status_code == 200
        assert [row["id"] for row in modules_b.json()] == [revision_1["id"]]

        shared_updates = client.get("/api/scorm-library?scope=shared")
        assert shared_updates.status_code == 200
        assert any(row["id"] == revision_2["id"] for row in shared_updates.json())

        fork = client.post(f"/api/scorm-library/{revision_2['id']}/fork")
        assert fork.status_code == 200
        assert fork.json()["owner_user_id"] == teacher_b
        assert fork.json()["visibility"] == "private"
        assert fork.json()["revision_number"] == 1


def test_export_all_contains_independent_scorm_archives_and_catalog():
    with TestClient(app) as client:
        teacher_id, course_id, _ = teacher_fixture("batch-export")
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))

        one = client.post(
            "/api/scorm-library",
            data={"title": "Paquete uno"},
            files={"file": ("one.zip", minimal_scorm_zip(), "application/zip")},
        )
        assert one.status_code == 200
        two = client.post(
            "/api/scorm-library",
            data={"title": "Paquete dos"},
            files={"file": ("two-2004.zip", minimal_scorm_2004_zip(), "application/zip")},
        )
        assert two.status_code == 200

        exported = client.get("/api/scorm-library/export/all")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as outer:
            names = outer.namelist()
            assert "catalogo.json" in names
            inner_names = [name for name in names if name.startswith("scorm/") and name.endswith(".zip")]
            assert len(inner_names) >= 2
            catalog = __import__("json").loads(outer.read("catalogo.json"))
            assert any(item["title"] == "Paquete uno" for item in catalog)
            assert any(item["standard"] == "SCORM_2004" for item in catalog)

            with zipfile.ZipFile(io.BytesIO(outer.read(inner_names[0]))) as inner:
                assert "imsmanifest.xml" in inner.namelist()


def test_teacher_ai_settings_are_private_encrypted_and_not_shared():
    with TestClient(app) as client:
        teacher_a, course_a, module_a = teacher_fixture("ai-private-a")
        teacher_b, course_b, _ = teacher_fixture("ai-private-b")

        with SessionLocal() as db:
            course = db.get(Course, course_a)
            course.owner_user_id = teacher_a
            db.add(
                Membership(
                    course_id=course_a,
                    user_id=teacher_b,
                    role="teacher",
                    lti_roles=["Instructor"],
                    active=True,
                )
            )
            course_module = CourseModule(
                course_id=course_a,
                module_id=module_a,
                settings_json={},
                active=True,
            )
            db.add(course_module)
            db.commit()
            db.refresh(course_module)
            course_module_id = course_module.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_a, course_a))
        saved_a = client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "provider": "openai-compatible",
                "base_url": "https://api-a.example.test/v1",
                "model": "modelo-a",
                "api_key": "secret-key-teacher-a",
                "confidence_threshold": 0.8,
                "auto_kinds": ["free", "case"],
                "default_rubric": "Rúbrica del profesor A",
            },
        )
        assert saved_a.status_code == 200, saved_a.text
        assert saved_a.json()["api_key_configured"] is True
        assert "secret-key-teacher-a" not in saved_a.text

        bound_a = client.put(
            f"/api/ai/course-modules/{course_module_id}",
            json={
                "enabled": True,
                "auto_review": True,
                "allowed_kinds": ["free", "case"],
            },
        )
        assert bound_a.status_code == 200, bound_a.text
        assert bound_a.json()["teacher_user_id"] == teacher_a
        assert bound_a.json()["is_mine"] is True

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_b, course_a))
        settings_b_before = client.get("/api/ai/settings")
        assert settings_b_before.status_code == 200
        assert settings_b_before.json()["api_key_configured"] is False
        assert settings_b_before.json()["model"] == ""

        saved_b = client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "provider": "openai-compatible",
                "base_url": "https://api-b.example.test/v1",
                "model": "modelo-b",
                "api_key": "secret-key-teacher-b",
                "confidence_threshold": 0.7,
                "auto_kinds": ["text"],
                "default_rubric": "Rúbrica del profesor B",
            },
        )
        assert saved_b.status_code == 200, saved_b.text
        assert "secret-key-teacher-b" not in saved_b.text

        binding_seen_by_b = client.get(f"/api/ai/course-modules/{course_module_id}")
        assert binding_seen_by_b.status_code == 200
        assert binding_seen_by_b.json()["enabled"] is True
        assert binding_seen_by_b.json()["is_mine"] is False

        cannot_replace_owner_binding = client.put(
            f"/api/ai/course-modules/{course_module_id}",
            json={
                "enabled": True,
                "auto_review": True,
                "allowed_kinds": ["text"],
            },
        )
        assert cannot_replace_owner_binding.status_code == 409

        with SessionLocal() as db:
            row_a = db.scalar(
                __import__("sqlalchemy").select(TeacherAISettings).where(
                    TeacherAISettings.user_id == teacher_a
                )
            )
            row_b = db.scalar(
                __import__("sqlalchemy").select(TeacherAISettings).where(
                    TeacherAISettings.user_id == teacher_b
                )
            )
            assert row_a is not None and row_b is not None
            assert row_a.encrypted_api_key != "secret-key-teacher-a"
            assert row_b.encrypted_api_key != "secret-key-teacher-b"
            assert row_a.encrypted_api_key != row_b.encrypted_api_key

            binding = db.scalar(
                __import__("sqlalchemy").select(CourseModuleAIConfig).where(
                    CourseModuleAIConfig.course_module_id == course_module_id
                )
            )
            assert binding is not None
            assert binding.teacher_user_id == teacher_a


def test_deleting_personal_ai_settings_disables_only_that_teachers_bindings():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("ai-delete")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            db.add(course_module)
            db.commit()
            db.refresh(course_module)
            course_module_id = course_module.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        saved = client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "provider": "openai-compatible",
                "base_url": "https://api.example.test/v1",
                "model": "modelo",
                "api_key": "teacher-secret",
                "auto_kinds": ["free"],
            },
        )
        assert saved.status_code == 200
        binding = client.put(
            f"/api/ai/course-modules/{course_module_id}",
            json={"enabled": True, "auto_review": True, "allowed_kinds": ["free"]},
        )
        assert binding.status_code == 200

        deleted = client.delete("/api/ai/settings")
        assert deleted.status_code == 200
        assert deleted.json()["disabled_bindings"] == 1

        after = client.get(f"/api/ai/course-modules/{course_module_id}")
        assert after.status_code == 200
        assert after.json()["enabled"] is False


def test_gth_catalog_imports_four_ra_thirty_three_criteria_and_public_portfolio():
    with TestClient(app) as client:
        imported = client.post(
            "/api/admin/catalog/gth0652/import-metadata",
            headers={"X-Admin-Token": "test-admin"},
        )
        assert imported.status_code == 200, imported.text
        payload = imported.json()
        assert payload["slug"] == "gth-0652"
        assert payload["learning_results"] == 4
        assert payload["criteria"] == 33
        assert payload["portfolio_items"] == 198
        module_id = payload["module_id"]

        with SessionLocal() as db:
            ra_count = db.scalar(
                __import__("sqlalchemy").select(
                    __import__("sqlalchemy").func.count(LearningResult.id)
                ).where(LearningResult.module_id == module_id)
            )
            ce_count = db.scalar(
                __import__("sqlalchemy").select(
                    __import__("sqlalchemy").func.count(AssessmentCriterion.id)
                )
                .join(LearningResult, LearningResult.id == AssessmentCriterion.learning_result_id)
                .where(LearningResult.module_id == module_id)
            )
            item_count = db.scalar(
                __import__("sqlalchemy").select(
                    __import__("sqlalchemy").func.count(AssessmentItem.id)
                )
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
                )
            )
            assert ra_count == 4
            assert ce_count == 33
            assert item_count == 198
            first = db.scalar(
                __import__("sqlalchemy").select(AssessmentItem)
                .join(
                    AssessmentCriterion,
                    AssessmentCriterion.id == AssessmentItem.criterion_id,
                )
                .join(
                    LearningResult,
                    LearningResult.id == AssessmentCriterion.learning_result_id,
                )
                .where(LearningResult.module_id == module_id)
                .order_by(AssessmentItem.id)
            )
            assert first is not None
            assert first.max_attempts == 2
            assert first.public_hash
            assert first.evaluable is True

        teacher_id, course_id, _ = teacher_fixture("catalog-teacher")
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))

        catalog = client.get("/api/catalog/modules")
        assert catalog.status_code == 200
        gth = next(row for row in catalog.json() if row["id"] == module_id)
        assert gth["installed"] is False

        installed = client.post(f"/api/catalog/modules/{module_id}/install")
        assert installed.status_code == 200
        assert installed.json()["permission"] == "viewer"

        mine = client.get("/api/modules/mine")
        assert mine.status_code == 200
        mine_gth = next(row for row in mine.json() if row["id"] == module_id)
        assert mine_gth["permission"] == "viewer"

        assigned = client.post(
            f"/api/groups/{course_id}/modules/{module_id}",
            json={"settings": {}},
        )
        assert assigned.status_code == 200, assigned.text

        cannot_uninstall_in_use = client.delete(
            f"/api/catalog/modules/{module_id}/install"
        )
        assert cannot_uninstall_in_use.status_code == 409

        removed = client.delete(f"/api/groups/{course_id}/modules/{module_id}")
        assert removed.status_code == 200
        uninstalled = client.delete(f"/api/catalog/modules/{module_id}/install")
        assert uninstalled.status_code == 200


def test_nominasol_guided_project_provisions_and_tracks_evidence():
    with TestClient(app) as client:
        provisioned = client.post(
            "/api/admin/catalog/nominasol2026/provision",
            headers={"X-Admin-Token": "test-admin"},
        )
        assert provisioned.status_code == 200, provisioned.text
        module_id = provisioned.json()["module_id"]
        package_id = provisioned.json()["package_id"]

        with SessionLocal() as db:
            teacher = User(display_name="Docente NOMINASOL", email="nominasol-teacher@example.test")
            student = User(display_name="Alumno NOMINASOL", email="nominasol-student@example.test")
            course = Course(
                platform_issuer="local://guided-test",
                context_id="nominasol-guided-course",
                source_type="local",
                title="Proyecto NOMINASOL",
            )
            db.add_all([teacher, student, course])
            db.flush()
            db.add_all(
                [
                    Membership(
                        course_id=course.id,
                        user_id=teacher.id,
                        role="teacher",
                        lti_roles=["Instructor"],
                    ),
                    Membership(
                        course_id=course.id,
                        user_id=student.id,
                        role="student",
                        lti_roles=["Learner"],
                    ),
                    ModulePermission(
                        module_id=module_id,
                        user_id=teacher.id,
                        permission="viewer",
                    ),
                ]
            )
            course_module = CourseModule(
                course_id=course.id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            db.add(course_module)
            db.commit()
            db.refresh(course_module)
            teacher_id = teacher.id
            student_id = student.id
            course_id = course.id
            course_module_id = course_module.id

        client.cookies.clear()
        client.cookies.set(
            "lms_session",
            session_cookie(student_id, course_id, role="student"),
        )
        launch = client.post(
            f"/api/course-modules/{course_module_id}/scorm/{package_id}/launch"
        )
        assert launch.status_code == 200, launch.text
        query = parse_qs(urlparse(launch.json()["url"]).query)
        token = query["lms_token"][0]
        registration_id = int(query["lms_registration"][0])
        auth = {"Authorization": f"Bearer {token}"}

        project = client.get(
            f"/runtime-api/guided/registrations/{registration_id}/project",
            headers=auth,
        )
        assert project.status_code == 200, project.text
        payload = project.json()
        assert payload["project"]["project_id"] == "nominasol-2026-anual"
        assert payload["project"]["version"] == "2026.3"
        assert payload["scenario"]["company"]["legal_name"] == "ATLÁNTICO GESTIÓN INTEGRAL, S.L."
        assert len(payload["scenario"]["workers"]) == 8
        m05 = next(m for m in payload["project"]["milestones"] if m["key"] == "M05")
        m06 = next(m for m in payload["project"]["milestones"] if m["key"] == "M06")
        m14 = next(m for m in payload["project"]["milestones"] if m["key"] == "M14")
        assert m05["documents"]
        assert m05["variant"]["key"] in {"F02-A", "F02-B", "F02-C"}
        assert m06["variant"]["key"] in {"IT-A", "IT-B", "IT-C"}
        assert m14["variant"]["key"] in {"NOV-A", "NOV-B", "NOV-C"}
        assert payload["summary"]["total"] >= 17
        assert payload["summary"]["completed"] == 0

        repeated = client.get(
            f"/runtime-api/guided/registrations/{registration_id}/project",
            headers=auth,
        )
        assert repeated.status_code == 200
        m05_repeated = next(
            m for m in repeated.json()["project"]["milestones"] if m["key"] == "M05"
        )
        assert m05_repeated["variant"]["key"] == m05["variant"]["key"]

        started = client.post(
            f"/runtime-api/guided/registrations/{registration_id}/milestones/M00/start",
            headers=auth,
        )
        assert started.status_code == 200
        assert started.json()["status"] == "in_progress"

        uploaded = client.post(
            f"/runtime-api/guided/registrations/{registration_id}/milestones/M00/evidence",
            headers=auth,
            data={"notes": "Pantalla principal de la versión educativa"},
            files={"file": ("pantalla.png", b"not-a-real-png-but-storage-test", "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["ai_used"] is False
        evidence_id = uploaded.json()["evidence"]["id"]

        blocked = client.post(
            f"/runtime-api/guided/registrations/{registration_id}/milestones/M01/start",
            headers=auth,
        )
        assert blocked.status_code == 409

        client.cookies.clear()
        client.cookies.set(
            "lms_session",
            session_cookie(teacher_id, course_id, role="teacher"),
        )
        reviewed = client.post(
            f"/api/guided/evidence/{evidence_id}/review",
            json={"decision": "accept", "comment": "Evidencia correcta."},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["progress"]["status"] == "completed"

        progress = client.get(
            f"/api/guided/course-modules/{course_module_id}/progress"
        )
        assert progress.status_code == 200, progress.text
        assert progress.json()[0]["completed"] == 1
        assert progress.json()[0]["variants"]["M05"] in {"F02-A", "F02-B", "F02-C"}

        guide = client.get(
            f"/api/guided/course-modules/{course_module_id}/guide"
        )
        assert guide.status_code == 200, guide.text
        assert guide.json()["title"].startswith("Guía docente")
        assert guide.json()["audit_version"] == "2026.3"
        assert len(guide.json()["milestones"]) >= 17
        assert any(row["manual_validation"] for row in guide.json()["milestones"])
        guide_by_key = {row["key"]: row for row in guide.json()["milestones"]}
        assert guide_by_key["M02"]["audit"]["manual_validation"] is True
        assert guide_by_key["M08"]["audit"]["manual_validation"] is True
        assert guide_by_key["M16"]["audit"]["manual_validation"] is True
        assert any(
            check["field"] == "agreement_code"
            for check in guide_by_key["M02"]["audit"]["checks"]
        )
        assert "F02-B" in guide_by_key["M05"]["audit"]["variant_checks"]

        client.cookies.clear()
        client.cookies.set(
            "lms_session",
            session_cookie(student_id, course_id, role="student"),
        )
        next_step = client.post(
            f"/runtime-api/guided/registrations/{registration_id}/milestones/M01/start",
            headers=auth,
        )
        assert next_step.status_code == 200
        assert next_step.json()["status"] == "in_progress"
