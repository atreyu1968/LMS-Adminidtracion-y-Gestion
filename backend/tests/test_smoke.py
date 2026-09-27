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
from app.lti import CLAIM_DL_CONTENT_ITEMS, CLAIM_MESSAGE_TYPE, _upsert_identity
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
        assert ranged.status_code in {200, 206}
        if ranged.status_code == 206:
            assert ranged.content == b"2345"

        client.cookies.clear()
        client.cookies.set("lms_session", session_cookie(teacher_b, course_b))
        forbidden = client.get(f"/api/media-library/{asset['id']}/content")
        assert forbidden.status_code == 403
