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
    ContentExemption,
    LearnerContentException,
    ContentReleaseRule,
    CourseModule,
    CourseModuleAIConfig,
    AssessmentAttempt,
    AssessmentCriterion,
    AssessmentItem,
    AssessmentKey,
    AssessmentReview,
    LearningResult,
    LTIResourceLink,
    GradeRecord,
    ExternalIdentity,
    EvaluationResult,
    LTIDeepLinkRequest,
    LTIPlatform,
    Membership,
    Module,
    ModulePermission,
    RecoveryPlan,
    ScormRegistration,
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
        assert provisioned.json()["snapshot_path"].startswith("guided-config/nominasol2026/")
        snapshot = STORAGE / provisioned.json()["snapshot_path"]
        assert (snapshot / "guided.json").is_file()
        assert (snapshot / "scenario.json").is_file()
        assert (snapshot / "support.json").is_file()
        assert (snapshot / "teacher-guide.json").is_file()
        assert (snapshot / "audit-rules.json").is_file()
        assert len(provisioned.json()["config_hashes"]) == 5

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
        assert payload["project"]["version"] == "2026.8"
        assert payload["scenario"]["company"]["legal_name"] == "ATLÁNTICO GESTIÓN INTEGRAL, S.L."
        assert len(payload["scenario"]["workers"]) == 8
        m05 = next(m for m in payload["project"]["milestones"] if m["key"] == "M05")
        m06 = next(m for m in payload["project"]["milestones"] if m["key"] == "M06")
        m14 = next(m for m in payload["project"]["milestones"] if m["key"] == "M14")
        assert m05["documents"]
        assert m05["support"]
        assert payload["project"]["common_support"]
        assert len(payload["project"]["glossary"]) >= 15
        assert payload["project"]["support_version"] == "2026.4"
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

        snapshot_source_path = Path(__file__).resolve().parents[2] / "modules" / "nominasol2026" / "guided.json"
        snapshot_source_original = snapshot_source_path.read_text(encoding="utf-8")
        try:
            changed_source = __import__("json").loads(snapshot_source_original)
            changed_source["version"] = "SOURCE-MODIFIED-AFTER-PROVISION"
            snapshot_source_path.write_text(
                __import__("json").dumps(changed_source, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            pinned = client.get(
                f"/runtime-api/guided/registrations/{registration_id}/project",
                headers=auth,
            )
            assert pinned.status_code == 200
            assert pinned.json()["project"]["version"] == "2026.8"
        finally:
            snapshot_source_path.write_text(snapshot_source_original, encoding="utf-8")

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
        assert len(uploaded.json()["evidence"]["sha256"]) == 64
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

        dossier = client.get(
            f"/api/guided/registrations/{registration_id}/dossier.zip"
        )
        assert dossier.status_code == 200, dossier.text
        assert dossier.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(dossier.content)) as archive:
            names = set(archive.namelist())
            assert "00_INDICE.html" in names
            assert "00_DATOS_EMPRESA.json" in names
            assert "00_VARIANTES.json" in names
            assert "LEEME.txt" in names
            assert any(name.startswith("M05/") for name in names)
            index_html = archive.read("00_INDICE.html").decode("utf-8")
            assert "Alumno NOMINASOL" in index_html
            variant_key = progress.json()[0]["variants"]["M05"]
            assert variant_key in index_html
            variants = __import__("json").loads(
                archive.read("00_VARIANTES.json").decode("utf-8")
            )
            assert variants["M05"]["key"] == variant_key
            assert "audit-rules" not in "\n".join(names).lower()
            assert "hidden_audit" not in index_html

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


def test_evaluation_objective_and_teacher_review_flow():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("evaluation-flow")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={
                    "evaluation": {
                        "portfolio_weight": 40,
                        "exam_weight": 60,
                        "pass_score": 50,
                        "ce_pass_score": 50,
                        "ce_pass_percent": 80,
                        "exam_enabled": False,
                    }
                },
                active=True,
            )
            student = User(display_name="Alumno evaluación", email="eval-student@example.test")
            db.add_all([course_module, student])
            db.flush()
            db.add(
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=[],
                    active=True,
                )
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA1",
                title="RA de prueba",
                position=1,
                active=True,
            )
            db.add(lr)
            db.flush()
            criterion = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="Criterio de prueba",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(criterion)
            db.flush()
            objective = AssessmentItem(
                criterion_id=criterion.id,
                instrument="portfolio",
                item_key="OBJ-1",
                item_type="choice",
                prompt="Selecciona la respuesta correcta",
                options_json=["A", "B", "C"],
                public_hash="a" * 64,
                max_attempts=2,
                position=1,
                active=True,
            )
            semantic = AssessmentItem(
                criterion_id=criterion.id,
                instrument="portfolio",
                item_key="SEM-1",
                item_type="case",
                prompt="Razona el supuesto",
                options_json=[],
                public_hash="b" * 64,
                max_attempts=2,
                position=2,
                active=True,
            )
            db.add_all([objective, semantic])
            db.flush()
            db.add_all([
                AssessmentKey(
                    item_id=objective.id,
                    answer_json={"value": "B"},
                    public_hash=objective.public_hash,
                    source="test",
                    active=True,
                ),
                AssessmentKey(
                    item_id=semantic.id,
                    answer_json={"value": "Respuesta de referencia razonada"},
                    public_hash=semantic.public_hash,
                    source="test",
                    active=True,
                ),
            ])
            db.commit()
            db.refresh(course_module)
            db.refresh(student)
            db.refresh(objective)
            db.refresh(semantic)
            course_module_id = course_module.id
            student_id = student.id
            objective_id = objective.id
            semantic_id = semantic.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(student_id, course_id, role="student"))

        structure = client.get(
            f"/api/evaluation/course-modules/{course_module_id}/structure"
        )
        assert structure.status_code == 200, structure.text
        assert structure.json()["learning_results"][0]["criteria"][0]["items"][0]["key"] == "OBJ-1"

        start_objective = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/items/{objective_id}/attempts",
            json={"metadata": {}},
        )
        assert start_objective.status_code == 200, start_objective.text
        submitted_objective = client.post(
            f"/api/evaluation/attempts/{start_objective.json()['attempt_id']}/submit",
            json={"response": "B", "metadata": {}},
        )
        assert submitted_objective.status_code == 200, submitted_objective.text
        assert submitted_objective.json()["score"] == 100.0
        assert submitted_objective.json()["pending_review"] is False
        assert submitted_objective.json()["grading_method"] == "deterministic"

        start_semantic = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/items/{semantic_id}/attempts",
            json={"metadata": {}},
        )
        assert start_semantic.status_code == 200
        submitted_semantic = client.post(
            f"/api/evaluation/attempts/{start_semantic.json()['attempt_id']}/submit",
            json={"response": "Una respuesta distinta que requiere valoración", "metadata": {}},
        )
        assert submitted_semantic.status_code == 200, submitted_semantic.text
        assert submitted_semantic.json()["score"] is None
        assert submitted_semantic.json()["pending_review"] is True
        assert submitted_semantic.json()["grading_method"] == "teacher"

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        queue = client.get(
            f"/api/evaluation/course-modules/{course_module_id}/reviews"
        )
        assert queue.status_code == 200, queue.text
        assert len(queue.json()) == 1
        review_id = queue.json()[0]["review_id"]
        assert queue.json()[0]["student"]["id"] == student_id
        assert queue.json()[0]["item"]["item_key"] == "SEM-1"

        decided = client.put(
            f"/api/evaluation/reviews/{review_id}",
            json={
                "score": 80,
                "feedback": "Respuesta válida con margen de mejora.",
                "status": "accepted",
            },
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["score"] == 80.0
        assert decided.json()["progress"]["portfolio_score"] == 90.0
        assert decided.json()["progress"]["criteria_passed"] == 0
        assert decided.json()["progress"]["portfolio_criteria_passed"] == 1
        assert decided.json()["progress"]["status"] == "portfolio-progress"

        with SessionLocal() as db:
            semantic_attempt = db.scalar(
                __import__("sqlalchemy").select(AssessmentAttempt).where(
                    AssessmentAttempt.user_id == student_id,
                    AssessmentAttempt.item_id == semantic_id,
                )
            )
            assert semantic_attempt is not None
            assert semantic_attempt.pending_review is False
            assert semantic_attempt.score == 80.0
            review = db.scalar(
                __import__("sqlalchemy").select(AssessmentReview).where(
                    AssessmentReview.attempt_id == semantic_attempt.id
                )
            )
            assert review is not None
            assert review.status == "accepted"
            assert review.reviewed_by_user_id == teacher_id


def test_evaluation_uses_personal_ai_without_sending_student_identity(monkeypatch):
    import app.evaluation as evaluation_module

    captured = {}

    async def fake_grade(ai_config, *, response_value, reference_answer, context, rubric=""):
        captured["response_value"] = response_value
        captured["reference_answer"] = reference_answer
        captured["context"] = context
        captured["rubric"] = rubric
        return {
            "score": 86.0,
            "confidence": 0.94,
            "verdict": "correct",
            "feedback": "Buena argumentación.",
            "breakdown": [],
        }

    monkeypatch.setattr(evaluation_module, "grade_with_ai", fake_grade)

    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("evaluation-ai")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={"evaluation": {"exam_enabled": False}},
                active=True,
            )
            student = User(
                display_name="Nombre que no debe salir",
                email="privacy-student@example.test",
            )
            db.add_all([course_module, student])
            db.flush()
            db.add(
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=[],
                    active=True,
                )
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA1",
                title="RA IA",
                position=1,
                active=True,
            )
            db.add(lr)
            db.flush()
            criterion = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="CE IA",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(criterion)
            db.flush()
            item = AssessmentItem(
                criterion_id=criterion.id,
                instrument="portfolio",
                item_key="AI-CASE-1",
                item_type="case",
                prompt="Explica el procedimiento aplicable.",
                public_hash="c" * 64,
                max_attempts=2,
                position=1,
                active=True,
            )
            db.add(item)
            db.flush()
            db.add(
                AssessmentKey(
                    item_id=item.id,
                    answer_json={"value": "Referencia técnica"},
                    public_hash=item.public_hash,
                    source="test",
                    active=True,
                )
            )
            db.commit()
            db.refresh(course_module)
            db.refresh(student)
            db.refresh(item)
            course_module_id = course_module.id
            student_id = student.id
            item_id = item.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        saved = client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "provider": "openai-compatible",
                "base_url": "https://api.example.test/v1",
                "model": "modelo-privado",
                "api_key": "teacher-private-key",
                "confidence_threshold": 0.8,
                "auto_kinds": ["case"],
                "default_rubric": "Valora precisión técnica.",
            },
        )
        assert saved.status_code == 200
        binding = client.put(
            f"/api/ai/course-modules/{course_module_id}",
            json={
                "enabled": True,
                "auto_review": True,
                "allowed_kinds": ["case"],
            },
        )
        assert binding.status_code == 200

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(student_id, course_id, role="student"))
        started = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/items/{item_id}/attempts",
            json={"metadata": {}},
        )
        assert started.status_code == 200
        submitted = client.post(
            f"/api/evaluation/attempts/{started.json()['attempt_id']}/submit",
            json={"response": "Mi razonamiento técnico", "metadata": {}},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["score"] == 86.0
        assert submitted.json()["pending_review"] is False
        assert submitted.json()["grading_method"] == "ai-auto"

        assert captured["response_value"] == "Mi razonamiento técnico"
        assert captured["reference_answer"] == "Referencia técnica"
        assert captured["context"]["criterion"] == "1.a"
        serialized = __import__("json").dumps(captured, ensure_ascii=False)
        assert "Nombre que no debe salir" not in serialized
        assert "privacy-student@example.test" not in serialized
        assert str(student_id) not in serialized


def test_exam_combines_40_60_and_creates_recovery_without_exposing_answers():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("exam-flow")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            student = User(display_name="Alumno examen", email="exam-student@example.test")
            db.add_all([course_module, student])
            db.flush()
            db.add(
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=[],
                    active=True,
                )
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA1",
                title="RA examen",
                position=1,
                active=True,
            )
            db.add(lr)
            db.flush()
            criterion = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="CE examen",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(criterion)
            db.flush()
            portfolio_item = AssessmentItem(
                criterion_id=criterion.id,
                instrument="portfolio",
                item_key="PORT-1",
                item_type="choice",
                prompt="Actividad de Portafolio",
                options_json=["A", "B"],
                public_hash="d" * 64,
                max_attempts=2,
                position=1,
                active=True,
            )
            db.add(portfolio_item)
            db.flush()
            db.add(
                AssessmentKey(
                    item_id=portfolio_item.id,
                    answer_json={"value": "B"},
                    public_hash=portfolio_item.public_hash,
                    source="test",
                    active=True,
                )
            )
            db.commit()
            db.refresh(course_module)
            db.refresh(student)
            db.refresh(lr)
            db.refresh(portfolio_item)
            course_module_id = course_module.id
            student_id = student.id
            lr_id = lr.id
            portfolio_item_id = portfolio_item.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        cfg = client.put(
            f"/api/evaluation/course-modules/{course_module_id}/config",
            json={
                "portfolio_weight": 40,
                "exam_weight": 60,
                "pass_score": 50,
                "ce_pass_score": 50,
                "ce_pass_percent": 80,
                "exam_enabled": True,
                "exam_questions_per_ce": 1,
                "exam_minutes": 45,
                "exam_max_attempts": 1,
                "exam_integrity_enabled": True,
                "exam_fullscreen_required": True,
                "exam_incident_limit": 3,
                "exam_incident_policy": "submit",
            },
        )
        assert cfg.status_code == 200, cfg.text

        bank = client.put(
            f"/api/evaluation/modules/{module_id}/learning-results/{lr_id}/exam-bank",
            json={
                "source": "test-private-bank",
                "questions": [
                    {
                        "id": "EX-1",
                        "ce": "1.a",
                        "q": "¿Cuál es la respuesta correcta?",
                        "options": ["Incorrecta", "Correcta"],
                        "answer": 1,
                        "type": "choice",
                    }
                ],
            },
        )
        assert bank.status_code == 200, bank.text
        assert bank.json()["questions"] == 1

        public_structure = client.get(
            f"/api/evaluation/course-modules/{course_module_id}/structure"
        )
        assert public_structure.status_code == 200
        public_items = [
            item
            for ra in public_structure.json()["learning_results"]
            for ce in ra["criteria"]
            for item in ce["items"]
        ]
        assert all(item["instrument"] != "exam" for item in public_items)
        assert all(item["key"] != "EX-1" for item in public_items)

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(student_id, course_id, role="student"))
        pstart = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/items/{portfolio_item_id}/attempts",
            json={"metadata": {}},
        )
        assert pstart.status_code == 200
        psubmit = client.post(
            f"/api/evaluation/attempts/{pstart.json()['attempt_id']}/submit",
            json={"response": "B", "metadata": {}},
        )
        assert psubmit.status_code == 200
        assert psubmit.json()["score"] == 100.0
        assert psubmit.json()["progress"]["status"] == "exam-pending"

        exam = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/learning-results/{lr_id}/exam/start"
        )
        assert exam.status_code == 200, exam.text
        exam_payload = exam.json()
        assert exam_payload["resumed"] is False
        assert len(exam_payload["questions"]) == 1
        serialized_public = __import__("json").dumps(exam_payload, ensure_ascii=False)
        assert '"correct"' not in serialized_public
        assert exam_payload["config"]["exam_fullscreen_required"] is True

        event = client.post(
            f"/api/evaluation/exam-sessions/{exam_payload['exam_session_id']}/events",
            json={"event": "fullscreen-exit", "detail": {"reason": "test"}},
        )
        assert event.status_code == 200
        assert event.json()["incidents"] == 1
        assert event.json()["force_submit"] is False

        question = exam_payload["questions"][0]
        # Tras barajar opciones, cualquier índice válido podría ser el correcto.
        # Usamos un índice fuera del rango para garantizar una respuesta incorrecta
        # sin conocer ni exponer la clave privada.
        wrong_answer = len(question["options"])
        saved = client.put(
            f"/api/evaluation/exam-sessions/{exam_payload['exam_session_id']}/draft",
            json={"answers": {str(question["id"]): wrong_answer}},
        )
        assert saved.status_code == 200

        submitted = client.post(
            f"/api/evaluation/exam-sessions/{exam_payload['exam_session_id']}/submit",
            json={
                "answers": {str(question["id"]): wrong_answer},
                "timeout": False,
            },
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["score"] == 0.0
        progress = submitted.json()["progress"]
        assert progress["portfolio_score"] == 100.0
        assert progress["exam_score"] == 0.0
        assert progress["final_score"] == 40.0
        assert progress["criteria_passed"] == 0
        assert progress["passed"] is False
        assert progress["status"] == "recovery-required"
        assert progress["recovery"] == ["1.a"]

        with SessionLocal() as db:
            plan = db.scalar(
                __import__("sqlalchemy").select(RecoveryPlan).where(
                    RecoveryPlan.course_module_id == course_module_id,
                    RecoveryPlan.user_id == student_id,
                    RecoveryPlan.learning_result_id == lr_id,
                )
            )
            assert plan is not None
            assert plan.criteria_json == ["1.a"]
            assert plan.status == "pending"


def test_recovery_changes_ce_status_without_rewriting_original_40_60_grade():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("recovery-flow")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            student = User(
                display_name="Alumno recuperación",
                email="recovery-student@example.test",
            )
            db.add_all([course_module, student])
            db.flush()
            db.add(
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=[],
                    active=True,
                )
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA1",
                title="RA recuperación",
                position=1,
                active=True,
            )
            db.add(lr)
            db.flush()
            ce_a = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="CE A",
                position=1,
                pass_score=50,
                active=True,
            )
            ce_b = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.b",
                title="CE B",
                position=2,
                pass_score=50,
                active=True,
            )
            db.add_all([ce_a, ce_b])
            db.flush()
            p_a = AssessmentItem(
                criterion_id=ce_a.id,
                instrument="portfolio",
                item_key="P-A",
                item_type="choice",
                prompt="Portfolio A",
                options_json=["No", "Sí"],
                public_hash="e" * 64,
                max_attempts=2,
                position=1,
                active=True,
            )
            p_b = AssessmentItem(
                criterion_id=ce_b.id,
                instrument="portfolio",
                item_key="P-B",
                item_type="choice",
                prompt="Portfolio B",
                options_json=["No", "Sí"],
                public_hash="f" * 64,
                max_attempts=2,
                position=1,
                active=True,
            )
            db.add_all([p_a, p_b])
            db.flush()
            db.add_all([
                AssessmentKey(
                    item_id=p_a.id,
                    answer_json={"value": 1},
                    public_hash=p_a.public_hash,
                    source="test",
                    active=True,
                ),
                AssessmentKey(
                    item_id=p_b.id,
                    answer_json={"value": 1},
                    public_hash=p_b.public_hash,
                    source="test",
                    active=True,
                ),
            ])
            db.commit()
            db.refresh(course_module)
            db.refresh(student)
            db.refresh(lr)
            db.refresh(p_a)
            db.refresh(p_b)
            course_module_id = course_module.id
            student_id = student.id
            lr_id = lr.id
            p_a_id = p_a.id
            p_b_id = p_b.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        cfg = client.put(
            f"/api/evaluation/course-modules/{course_module_id}/config",
            json={
                "portfolio_weight": 40,
                "exam_weight": 60,
                "pass_score": 50,
                "ce_pass_score": 50,
                "ce_pass_percent": 80,
                "exam_enabled": True,
                "exam_questions_per_ce": 1,
                "exam_minutes": 45,
                "exam_max_attempts": 1,
                "recovery_max_attempts": 1,
            },
        )
        assert cfg.status_code == 200, cfg.text

        exam_bank = client.put(
            f"/api/evaluation/modules/{module_id}/learning-results/{lr_id}/exam-bank",
            json={
                "questions": [
                    {
                        "id": "EX-A",
                        "ce": "1.a",
                        "q": "Pregunta A",
                        "options": ["Incorrecta A", "Correcta A"],
                        "answer": 1,
                        "type": "choice",
                    },
                    {
                        "id": "EX-B",
                        "ce": "1.b",
                        "q": "Pregunta B",
                        "options": ["Incorrecta B", "Correcta B"],
                        "answer": 1,
                        "type": "choice",
                    },
                ]
            },
        )
        assert exam_bank.status_code == 200, exam_bank.text

        recovery_bank = client.put(
            f"/api/evaluation/modules/{module_id}/learning-results/{lr_id}/recovery-bank",
            json={
                "items": [
                    {
                        "id": "REC-B",
                        "ce": "1.b",
                        "kind": "choice",
                        "prompt": "Recupera el CE B",
                        "options": ["Incorrecta", "Correcta"],
                        "answer": 1,
                        "feedback": "Revisa el criterio B.",
                    }
                ]
            },
        )
        assert recovery_bank.status_code == 200, recovery_bank.text

        client.cookies.clear()
        client.cookies.set(
            "lms_session",
            session_cookie(student_id, course_id, role="student"),
        )
        for item_id in (p_a_id, p_b_id):
            started = client.post(
                f"/api/evaluation/course-modules/{course_module_id}/items/{item_id}/attempts",
                json={"metadata": {}},
            )
            assert started.status_code == 200
            submitted = client.post(
                f"/api/evaluation/attempts/{started.json()['attempt_id']}/submit",
                json={"response": 1, "metadata": {}},
            )
            assert submitted.status_code == 200
            assert submitted.json()["score"] == 100.0

        exam = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/learning-results/{lr_id}/exam/start"
        )
        assert exam.status_code == 200, exam.text
        exam_payload = exam.json()
        answers = {}
        for question in exam_payload["questions"]:
            if question["ce"] == "1.a":
                answers[str(question["id"])] = question["options"].index("Correcta A")
            else:
                answers[str(question["id"])] = question["options"].index("Incorrecta B")

        exam_submit = client.post(
            f"/api/evaluation/exam-sessions/{exam_payload['exam_session_id']}/submit",
            json={"answers": answers, "timeout": False},
        )
        assert exam_submit.status_code == 200, exam_submit.text
        before = exam_submit.json()["progress"]
        assert before["portfolio_score"] == 100.0
        assert before["exam_score"] == 50.0
        assert before["final_score"] == 70.0
        assert before["criteria_passed"] == 1
        assert before["criteria_needed"] == 2
        assert before["passed"] is False
        assert before["status"] == "recovery-required"
        assert before["recovery"] == ["1.b"]

        recovery = client.get(
            f"/api/evaluation/course-modules/{course_module_id}/learning-results/{lr_id}/recovery"
        )
        assert recovery.status_code == 200, recovery.text
        recovery_payload = recovery.json()
        assert recovery_payload["criteria"] == ["1.b"]
        assert len(recovery_payload["items"]) == 1
        recovery_item = recovery_payload["items"][0]
        assert recovery_item["key"] == "REC-B"
        assert "answer" not in recovery_item

        rec_start = client.post(
            f"/api/evaluation/course-modules/{course_module_id}/items/{recovery_item['id']}/attempts",
            json={"metadata": {"source": "recovery"}},
        )
        assert rec_start.status_code == 200
        rec_submit = client.post(
            f"/api/evaluation/attempts/{rec_start.json()['attempt_id']}/submit",
            json={"response": 1, "metadata": {"source": "recovery"}},
        )
        assert rec_submit.status_code == 200, rec_submit.text
        after = rec_submit.json()["progress"]
        assert after["final_score"] == 70.0
        assert after["criteria_passed_original"] == 1
        assert after["criteria_passed"] == 2
        assert after["criteria"]["1.b"]["recovered"] is True
        assert after["recovery"] == []
        assert after["passed"] is True
        assert after["status"] == "passed"

        with SessionLocal() as db:
            plan = db.scalar(
                __import__("sqlalchemy").select(RecoveryPlan).where(
                    RecoveryPlan.course_module_id == course_module_id,
                    RecoveryPlan.user_id == student_id,
                    RecoveryPlan.learning_result_id == lr_id,
                )
            )
            assert plan is not None
            assert plan.criteria_json == []
            assert plan.status == "passed"


def test_private_bank_bundle_imports_portfolio_exam_recovery_and_keeps_them_secret():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("private-bundle")
        with SessionLocal() as db:
            module = db.get(Module, module_id)
            module.metadata_json = {
                "evaluation_defaults": {
                    "exam_questions_per_ce": 1,
                    "recovery_items_per_ce": 1,
                    "exam_enabled": True,
                }
            }
            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA1",
                title="RA privada",
                position=1,
                metadata_json={"legacy_course_id": "LEGACY_RA1"},
                active=True,
            )
            db.add_all([course_module, lr])
            db.flush()
            ce = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="CE privado",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(ce)
            db.flush()
            public = AssessmentItem(
                criterion_id=ce.id,
                instrument="portfolio",
                item_key="P1",
                item_type="choice",
                prompt="Pregunta pública",
                options_json=["A", "B"],
                public_hash="9" * 64,
                evaluable=True,
                max_attempts=2,
                position=1,
                active=True,
            )
            db.add(public)
            db.commit()
            db.refresh(course_module)
            course_module_id = course_module.id

        portfolio = {
            "course_id": "LEGACY_RA1",
            "kind": "portfolio",
            "items": [
                {
                    "id": "P1",
                    "ce": "1.a",
                    "kind": "choice",
                    "answer": 1,
                }
            ],
        }
        exam = {
            "course_id": "LEGACY_RA1",
            "kind": "exam",
            "questions": [
                {
                    "id": "E1",
                    "ce": "1.a",
                    "q": "Pregunta privada",
                    "options": ["A", "B"],
                    "answer": 1,
                    "type": "choice",
                }
            ],
        }
        recovery = {
            "course_id": "LEGACY_RA1",
            "kind": "recovery",
            "items": [
                {
                    "id": "R1",
                    "ce": "1.a",
                    "kind": "choice",
                    "prompt": "Recuperación privada",
                    "options": ["A", "B"],
                    "answer": 1,
                    "feedback": "Revisar el CE.",
                }
            ],
        }
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "ra1_portfolio.json",
                __import__("json").dumps(portfolio, ensure_ascii=False),
            )
            archive.writestr(
                "ra1_exam.json",
                __import__("json").dumps(exam, ensure_ascii=False),
            )
            archive.writestr(
                "ra1_recovery.json",
                __import__("json").dumps(recovery, ensure_ascii=False),
            )

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        uploaded = client.post(
            f"/api/evaluation/modules/{module_id}/private-bank-bundle",
            files={"file": ("private-banks.zip", bundle.getvalue(), "application/zip")},
        )
        assert uploaded.status_code == 200, uploaded.text
        imported = uploaded.json()["imported"]
        assert imported == {"portfolio": 1, "exam": 1, "recovery": 1}
        coverage = uploaded.json()["coverage"]["RA1"]
        assert coverage["exam"]["insufficient"] == {}
        assert coverage["recovery"]["insufficient"] == {}

        readiness = client.get(
            f"/api/evaluation/course-modules/{course_module_id}/readiness"
        )
        assert readiness.status_code == 200, readiness.text
        state = readiness.json()
        assert state["portfolio_ready"] is True
        assert state["exam_ready"] is True
        assert state["recovery_ready"] is True
        assert state["ready_for_evaluation"] is True
        assert state["can_manage_private_banks"] is True

        public_structure = client.get(
            f"/api/evaluation/course-modules/{course_module_id}/structure"
        )
        assert public_structure.status_code == 200
        public_items = [
            item
            for ra in public_structure.json()["learning_results"]
            for criterion in ra["criteria"]
            for item in criterion["items"]
        ]
        assert [item["key"] for item in public_items] == ["P1"]
        serialized = __import__("json").dumps(
            public_structure.json(), ensure_ascii=False
        )
        assert "Pregunta privada" not in serialized
        assert "Recuperación privada" not in serialized


def test_ags_sync_sends_only_definitive_ra_grades(monkeypatch):
    import app.integrations as integrations_module

    sent = []

    async def fake_post_score(
        platform,
        lineitem_url,
        lti_user_id,
        score_given,
        score_maximum=100.0,
        comment=None,
    ):
        sent.append(
            {
                "platform_id": platform.id,
                "lineitem_url": lineitem_url,
                "lti_user_id": lti_user_id,
                "score_given": score_given,
                "score_maximum": score_maximum,
                "comment": comment,
            }
        )
        return {"status_code": 204, "endpoint": lineitem_url.rstrip("/") + "/scores"}

    monkeypatch.setattr(integrations_module, "post_score", fake_post_score)

    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("ags-ra-sync")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course.source_type = "lti"

            course_module = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            student_done = User(
                display_name="Alumno definitivo",
                email="done@example.test",
            )
            student_pending = User(
                display_name="Alumno pendiente",
                email="pending@example.test",
            )
            platform = LTIPlatform(
                name="CAMPUS test",
                issuer="https://campus.example.test",
                client_id="campus-client",
                auth_url="https://campus.example.test/auth",
                token_url="https://campus.example.test/token",
                jwks_url="https://campus.example.test/jwks",
                active=True,
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA1",
                title="RA para AGS",
                position=1,
                active=True,
            )
            db.add_all([course_module, student_done, student_pending, platform, lr])
            db.flush()

            db.add_all([
                Membership(
                    course_id=course_id,
                    user_id=student_done.id,
                    role="student",
                    lti_roles=["Learner"],
                    active=True,
                ),
                Membership(
                    course_id=course_id,
                    user_id=student_pending.id,
                    role="student",
                    lti_roles=["Learner"],
                    active=True,
                ),
                ExternalIdentity(
                    user_id=student_done.id,
                    issuer=platform.issuer,
                    subject="campus-student-done",
                    client_id=platform.client_id,
                ),
                ExternalIdentity(
                    user_id=student_pending.id,
                    issuer=platform.issuer,
                    subject="campus-student-pending",
                    client_id=platform.client_id,
                ),
            ])
            db.flush()

            link = LTIResourceLink(
                platform_id=platform.id,
                deployment_id="deployment-1",
                resource_link_id="resource-ra1",
                course_id=course_id,
                course_module_id=course_module.id,
                learning_result_id=lr.id,
                lineitem_url="https://campus.example.test/lineitems/ra1",
                scopes=[],
            )
            db.add(link)
            db.flush()

            db.add_all([
                EvaluationResult(
                    course_module_id=course_module.id,
                    user_id=student_done.id,
                    learning_result_id=lr.id,
                    portfolio_score=80,
                    exam_score=70,
                    final_score=74,
                    criteria_passed=8,
                    criteria_total=9,
                    passed=True,
                    details_json={"status": "passed"},
                ),
                EvaluationResult(
                    course_module_id=course_module.id,
                    user_id=student_pending.id,
                    learning_result_id=lr.id,
                    portfolio_score=90,
                    exam_score=None,
                    final_score=None,
                    criteria_passed=0,
                    criteria_total=9,
                    passed=False,
                    details_json={"status": "exam-pending"},
                ),
            ])
            db.commit()
            db.refresh(course_module)
            db.refresh(lr)
            db.refresh(student_done)
            db.refresh(student_pending)
            course_module_id = course_module.id
            lr_id = lr.id
            done_id = student_done.id
            pending_id = student_pending.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        response = client.post(
            f"/api/lti/course-modules/{course_module_id}/learning-results/{lr_id}/sync-grades"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["sent"] == 1
        assert data["pending"] == 1
        assert data["missing_identity"] == 0
        assert len(sent) == 1
        assert sent[0]["lti_user_id"] == "campus-student-done"
        assert sent[0]["score_given"] == 74.0
        assert sent[0]["score_maximum"] == 100.0
        assert "RA1" in sent[0]["comment"]

        with SessionLocal() as db:
            records = list(
                db.scalars(
                    __import__("sqlalchemy").select(GradeRecord)
                )
            )
            assert len(records) == 1
            assert records[0].user_id == done_id
            assert records[0].score_given == 74.0
            assert records[0].grading_progress == "FullyGraded"
            assert all(record.user_id != pending_id for record in records)


def test_teacher_dashboard_aggregates_pending_work_across_course_module():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("dashboard-activity")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            course.source_type = "lti"

            student = User(
                display_name="Alumno Dashboard",
                email="dashboard-student@example.test",
            )
            cm = CourseModule(
                course_id=course_id,
                module_id=module_id,
                settings_json={},
                active=True,
            )
            lr = LearningResult(
                module_id=module_id,
                code="RA-DASH",
                title="RA Dashboard",
                position=1,
                active=True,
            )
            db.add_all([student, cm, lr])
            db.flush()
            ce = AssessmentCriterion(
                learning_result_id=lr.id,
                code="D.a",
                title="Criterio dashboard",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(ce)
            db.flush()
            item = AssessmentItem(
                criterion_id=ce.id,
                instrument="portfolio",
                item_key="dash-free",
                item_type="free",
                prompt="Explica el procedimiento.",
                evaluable=True,
                max_attempts=2,
                active=True,
            )
            db.add(item)
            db.flush()
            attempt = AssessmentAttempt(
                course_module_id=cm.id,
                user_id=student.id,
                item_id=item.id,
                attempt_no=1,
                status="submitted",
                response_json={"value": "Respuesta"},
                pending_review=True,
                submitted_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
            db.flush()
            db.add_all([
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=["Learner"],
                    active=True,
                ),
                AssessmentReview(
                    attempt_id=attempt.id,
                    source="teacher",
                    status="pending",
                    feedback="Pendiente",
                ),
                RecoveryPlan(
                    course_module_id=cm.id,
                    user_id=student.id,
                    learning_result_id=lr.id,
                    criteria_json=["D.a"],
                    status="pending",
                ),
                ScormRegistration(
                    course_module_id=cm.id,
                    package_id=1,
                    user_id=student.id,
                    lesson_status="incomplete",
                    lesson_location="tema-1",
                    suspend_data="",
                    cmi_json={},
                ),
            ])

            platform = LTIPlatform(
                name="CAMPUS Dashboard",
                issuer="https://campus-dashboard.example.test",
                client_id="dashboard-client",
                auth_url="https://campus-dashboard.example.test/auth",
                token_url="https://campus-dashboard.example.test/token",
                jwks_url="https://campus-dashboard.example.test/jwks",
                active=True,
            )
            db.add(platform)
            db.flush()
            link = LTIResourceLink(
                platform_id=platform.id,
                deployment_id="dash-deployment",
                resource_link_id="dash-ra",
                course_id=course_id,
                course_module_id=cm.id,
                learning_result_id=lr.id,
                lineitem_url="https://campus-dashboard.example.test/lineitem/ra",
                scopes=[],
            )
            db.add(link)
            db.add(
                EvaluationResult(
                    course_module_id=cm.id,
                    user_id=student.id,
                    learning_result_id=lr.id,
                    portfolio_score=80,
                    exam_score=70,
                    final_score=74,
                    criteria_passed=1,
                    criteria_total=1,
                    passed=True,
                    details_json={"status": "passed"},
                )
            )
            db.commit()

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        response = client.get("/api/dashboard/teacher")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["summary"]["groups"] >= 1
        assert data["summary"]["students"] >= 1
        assert data["summary"]["pending_reviews"] >= 1
        assert data["summary"]["recoveries"] >= 1
        assert data["summary"]["campus_pending"] >= 1
        assert data["summary"]["scorm_in_progress"] >= 1
        kinds = {item["kind"] for item in data["items"]}
        assert {"review", "recovery", "campus", "scorm"}.issubset(kinds)
        review = next(item for item in data["items"] if item["kind"] == "review")
        assert "tab=reviews" in review["href"]


def test_teacher_student_detail_and_progress_matrix():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("detail-progress")
        with SessionLocal() as db:
            student = User(display_name="Alumno Detalle", email="detalle@example.test")
            cm = CourseModule(course_id=course_id, module_id=module_id, settings_json={}, active=True)
            lr = LearningResult(module_id=module_id, code="RA1", title="RA detalle", position=1, active=True)
            db.add_all([student, cm, lr])
            db.flush()
            ce = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="CE detalle",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(ce)
            db.flush()
            item = AssessmentItem(
                criterion_id=ce.id,
                instrument="portfolio",
                item_key="detalle-item",
                item_type="free",
                prompt="Explica.",
                evaluable=True,
                max_attempts=2,
                active=True,
            )
            db.add(item)
            db.flush()
            attempt = AssessmentAttempt(
                course_module_id=cm.id,
                user_id=student.id,
                item_id=item.id,
                attempt_no=1,
                status="submitted",
                response_json={"value": "Respuesta del alumno"},
                score=80,
                correct=True,
                pending_review=False,
                submitted_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
            db.add(
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=["Learner"],
                    active=True,
                )
            )
            db.commit()
            db.refresh(cm)
            db.refresh(student)
            cmid = cm.id
            sid = student.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        detail = client.get(f"/api/evaluation/course-modules/{cmid}/students/{sid}/detail")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["student"]["display_name"] == "Alumno Detalle"
        assert len(payload["attempts"]) == 1
        assert payload["attempts"][0]["item"]["key"] == "detalle-item"
        assert payload["attempts"][0]["response"] == "Respuesta del alumno"
        assert any(event["kind"] == "assessment" for event in payload["timeline"])

        matrix = client.get(f"/api/evaluation/course-modules/{cmid}/progress-matrix")
        assert matrix.status_code == 200, matrix.text
        row = next(x for x in matrix.json()["students"] if x["user_id"] == sid)
        cell = row["learning_results"][0]
        assert cell["state"] in {"in_progress", "completed"}
        assert cell["completed_items"] >= 1
        assert cell["progress_percent"] > 0


def test_student_overview_exposes_progress_next_action_and_timeline():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("student-overview")
        with SessionLocal() as db:
            student = User(display_name="Alumno Overview", email="overview@example.test")
            cm = CourseModule(course_id=course_id, module_id=module_id, settings_json={}, active=True)
            lr = LearningResult(module_id=module_id, code="RA1", title="RA Overview", position=1, active=True)
            db.add_all([student, cm, lr])
            db.flush()
            ce = AssessmentCriterion(
                learning_result_id=lr.id,
                code="1.a",
                title="CE Overview",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(ce)
            db.flush()
            item = AssessmentItem(
                criterion_id=ce.id,
                instrument="portfolio",
                item_key="overview-item",
                item_type="choice",
                prompt="Selecciona",
                options_json=["A", "B"],
                evaluable=True,
                max_attempts=2,
                active=True,
            )
            db.add(item)
            db.flush()
            db.add_all([
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=["Learner"],
                    active=True,
                ),
                AssessmentAttempt(
                    course_module_id=cm.id,
                    user_id=student.id,
                    item_id=item.id,
                    attempt_no=1,
                    status="submitted",
                    response_json={"value": 0},
                    score=100,
                    correct=True,
                    pending_review=False,
                    submitted_at=datetime.now(timezone.utc),
                ),
            ])
            db.commit()
            db.refresh(cm)
            db.refresh(student)
            cmid, sid = cm.id, student.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(sid, course_id, role="student"))
        response = client.get(f"/api/evaluation/course-modules/{cmid}/my-overview")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total_learning_results"] == 1
        assert data["progress_percent"] > 0
        assert data["learning_results"][0]["state"] in {"in_progress", "completed"}
        assert data["learning_results"][0]["completed_items"] >= 1
        assert any(event["kind"] == "assessment" for event in data["timeline"])


def test_adaptive_release_exception_and_exemption_are_effective():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("adaptive-release")
        with SessionLocal() as db:
            student = User(display_name="Alumno Adaptativo", email="adaptive@example.test")
            cm = CourseModule(course_id=course_id, module_id=module_id, settings_json={}, active=True)
            lr = LearningResult(module_id=module_id, code="RA1", title="RA Adaptativo", position=1, active=True)
            db.add_all([student, cm, lr])
            db.flush()
            ce1 = AssessmentCriterion(
                learning_result_id=lr.id, code="1.a", title="CE 1.a",
                position=1, pass_score=50, active=True,
            )
            ce2 = AssessmentCriterion(
                learning_result_id=lr.id, code="1.b", title="CE 1.b",
                position=2, pass_score=50, active=True,
            )
            db.add_all([ce1, ce2])
            db.flush()
            item1 = AssessmentItem(
                criterion_id=ce1.id, instrument="portfolio",
                item_key="adaptive-1", item_type="choice",
                prompt="Primera", options_json=["A", "B"],
                evaluable=True, max_attempts=1, active=True,
            )
            item2 = AssessmentItem(
                criterion_id=ce2.id, instrument="portfolio",
                item_key="adaptive-2", item_type="choice",
                prompt="Segunda", options_json=["A", "B"],
                evaluable=True, max_attempts=1, active=True,
            )
            db.add_all([item1, item2])
            db.flush()
            db.add_all([
                AssessmentKey(item_id=item1.id, answer_json={"value": 0}, active=True),
                AssessmentKey(item_id=item2.id, answer_json={"value": 0}, active=True),
                Membership(
                    course_id=course_id, user_id=student.id, role="student",
                    lti_roles=["Learner"], active=True,
                ),
            ])
            db.commit()
            db.refresh(cm); db.refresh(student); db.refresh(item1); db.refresh(item2); db.refresh(ce2)
            cmid, sid, i1, i2, ce2id = cm.id, student.id, item1.id, item2.id, ce2.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        rule = client.put(
            f"/api/learning/course-modules/{cmid}/rules/item/{i2}",
            json={
                "requirements": [
                    {"type": "item", "key": str(i1), "completion": True, "min_score": 50}
                ],
                "audience": {},
            },
        )
        assert rule.status_code == 200, rule.text
        exception = client.put(
            f"/api/learning/course-modules/{cmid}/students/{sid}/exceptions/item/{i1}",
            json={"extra_attempts": 1, "extra_time_minutes": 0, "notes": "Segundo intento"},
        )
        assert exception.status_code == 200, exception.text
        exemption = client.put(
            f"/api/learning/course-modules/{cmid}/students/{sid}/exemptions/criterion/{ce2id}",
            json={"reason": "CE exento en esta convocatoria"},
        )
        assert exemption.status_code == 200, exemption.text

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(sid, course_id, role="student"))
        blocked = client.post(
            f"/api/evaluation/course-modules/{cmid}/items/{i2}/attempts",
            json={"metadata": {}},
        )
        assert blocked.status_code == 409  # CE 1.b is exempt, so it cannot be attempted.

        # Remove the CE exemption temporarily to validate the prerequisite.
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        removed = client.delete(
            f"/api/learning/course-modules/{cmid}/students/{sid}/exemptions/criterion/{ce2id}"
        )
        assert removed.status_code == 200

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(sid, course_id, role="student"))
        blocked_by_rule = client.post(
            f"/api/evaluation/course-modules/{cmid}/items/{i2}/attempts",
            json={"metadata": {}},
        )
        assert blocked_by_rule.status_code == 403

        first = client.post(
            f"/api/evaluation/course-modules/{cmid}/items/{i1}/attempts",
            json={"metadata": {}},
        )
        assert first.status_code == 200, first.text
        submitted = client.post(
            f"/api/evaluation/attempts/{first.json()['attempt_id']}/submit",
            json={"response": 0, "metadata": {}},
        )
        assert submitted.status_code == 200
        assert submitted.json()["score"] == 100.0

        now_allowed = client.post(
            f"/api/evaluation/course-modules/{cmid}/items/{i2}/attempts",
            json={"metadata": {}},
        )
        assert now_allowed.status_code == 200, now_allowed.text

        # The personal exception adds a second attempt to item 1.
        second = client.post(
            f"/api/evaluation/course-modules/{cmid}/items/{i1}/attempts",
            json={"metadata": {}},
        )
        assert second.status_code == 200, second.text
        assert second.json()["attempt_no"] == 2
        assert second.json()["max_attempts"] == 3

        # Reapply the CE exemption and verify that it is removed from the RA denominator.
        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        reapplied = client.put(
            f"/api/learning/course-modules/{cmid}/students/{sid}/exemptions/criterion/{ce2id}",
            json={"reason": "CE exento"},
        )
        assert reapplied.status_code == 200

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(sid, course_id, role="student"))
        results = client.get(f"/api/evaluation/course-modules/{cmid}/my-results")
        assert results.status_code == 200, results.text
        ra = results.json()[0]
        assert ra["criteria_total"] == 1
        assert ra["criteria"]["1.b"]["exempt"] is True


def test_experience_search_favorites_recent_calendar_notifications_and_signals():
    with TestClient(app) as client:
        teacher_id, course_id, module_id = teacher_fixture("experience-suite")
        with SessionLocal() as db:
            course = db.get(Course, course_id)
            course.owner_user_id = teacher_id
            student = User(display_name="Alumno Experiencia", email="experience@example.test")
            cm = CourseModule(course_id=course_id, module_id=module_id, settings_json={}, active=True)
            lr = LearningResult(
                module_id=module_id,
                code="RA-BUSCA",
                title="Gestión documental searchable",
                description="Contenido para la búsqueda global",
                position=1,
                active=True,
            )
            db.add_all([student, cm, lr])
            db.flush()
            ce = AssessmentCriterion(
                learning_result_id=lr.id,
                code="B.a",
                title="Criterio localizable",
                description="criterio buscable",
                position=1,
                pass_score=50,
                active=True,
            )
            db.add(ce); db.flush()
            item = AssessmentItem(
                criterion_id=ce.id,
                instrument="portfolio",
                item_key="buscar-actividad",
                item_type="free",
                prompt="Describe el documento buscable.",
                evaluable=True,
                max_attempts=2,
                active=True,
            )
            db.add(item); db.flush()
            db.add_all([
                Membership(
                    course_id=course_id,
                    user_id=student.id,
                    role="student",
                    lti_roles=["Learner"],
                    active=True,
                ),
                AssessmentAttempt(
                    course_module_id=cm.id,
                    user_id=student.id,
                    item_id=item.id,
                    attempt_no=1,
                    status="submitted",
                    response_json={"value": "texto"},
                    pending_review=True,
                    submitted_at=datetime.now(timezone.utc) - timedelta(days=8),
                ),
                ContentReleaseRule(
                    course_module_id=cm.id,
                    content_type="item",
                    content_key=str(item.id),
                    open_at=datetime.now(timezone.utc) - timedelta(days=1),
                    close_at=datetime.now(timezone.utc) + timedelta(days=2),
                    requirements_json=[],
                    audience_json={},
                    active=True,
                    created_by_user_id=teacher_id,
                ),
            ])
            db.flush()
            attempt = db.scalar(
                __import__("sqlalchemy").select(AssessmentAttempt).where(
                    AssessmentAttempt.course_module_id == cm.id,
                    AssessmentAttempt.user_id == student.id,
                    AssessmentAttempt.item_id == item.id,
                )
            )
            db.add(
                AssessmentReview(
                    attempt_id=attempt.id,
                    source="teacher",
                    status="pending",
                    feedback="Pendiente",
                )
            )
            db.commit()
            db.refresh(cm); db.refresh(student); db.refresh(lr)
            cmid, sid, lrid = cm.id, student.id, lr.id

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_id, course_id))
        search = client.get("/api/experience/search", params={"q": "buscable"})
        assert search.status_code == 200, search.text
        assert any(row["kind"] in {"learning_result", "activity"} for row in search.json()["results"])

        scoped = client.get(
            "/api/experience/search",
            params={"q": "documento", "course_module_id": cmid},
        )
        assert scoped.status_code == 200
        assert all(row["course_module_id"] == cmid for row in scoped.json()["results"])

        fav = client.post("/api/experience/favorites/toggle", json={"kind": "module", "id": module_id})
        assert fav.status_code == 200
        assert module_id in fav.json()["value"]["modules"]
        favs = client.get("/api/experience/favorites")
        assert module_id in favs.json()["modules"]

        recent = client.post(
            "/api/experience/recent",
            json={
                "kind": "course_module",
                "id": str(cmid),
                "title": "Experiencia reciente",
                "href": f"/evaluation-teacher.html?course_module={cmid}",
            },
        )
        assert recent.status_code == 200
        assert recent.json()["items"][0]["title"] == "Experiencia reciente"

        calendar = client.get("/api/experience/calendar", params={"course_module_id": cmid})
        assert calendar.status_code == 200
        assert {event["kind"] for event in calendar.json()["events"]} == {"opens", "closes"}

        notifications = client.get("/api/experience/notifications")
        assert notifications.status_code == 200
        kinds = {row["kind"] for row in notifications.json()["items"]}
        assert "review" in kinds
        assert "deadline" in kinds

        signals = client.get(f"/api/experience/course-modules/{cmid}/signals")
        assert signals.status_code == 200
        row = next(x for x in signals.json()["students"] if x["user_id"] == sid)
        assert row["submitted_activities"] == 1
        assert any(signal["kind"] == "pending_review" for signal in row["signals"])
        assert "predicciones" in signals.json()["note"]


def test_primary_frontends_include_basic_accessibility_landmarks():
    frontend_root = Path(__file__).resolve().parents[2] / "frontend"
    for filename in ["teacher.html", "evaluation-teacher.html", "evaluation-student.html"]:
        text_content = (frontend_root / filename).read_text(encoding="utf-8")
        assert '<html lang="es">' in text_content
        assert 'name="viewport"' in text_content
        assert "Saltar al contenido" in text_content
        assert 'aria-live="polite"' in text_content
        assert 'id="mainContent"' in text_content
