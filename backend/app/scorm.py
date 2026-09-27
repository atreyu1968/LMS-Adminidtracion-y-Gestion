from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
import hashlib
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    CourseModule,
    Membership,
    Module,
    ModulePermission,
    ScormPackage,
    ScormRegistration,
    User,
)
from .security import create_scorm_token, read_scorm_token, read_session, require_teacher
from .settings import get_settings


router = APIRouter()
settings = get_settings()


class ScormCommitIn(BaseModel):
    cmi: dict[str, object]


def _module_editor(db: Session, module_id: int, user_id: int) -> None:
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if not permission or permission.permission not in {"owner", "editor"}:
        raise HTTPException(status_code=403, detail="Module edit permission required")


def _membership(db: Session, course_id: int, user_id: int) -> Membership:
    row = db.scalar(
        select(Membership).where(
            Membership.course_id == course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not row:
        raise HTTPException(status_code=403, detail="Not enrolled in this course")
    return row


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise HTTPException(status_code=400, detail="SCORM ZIP contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="SCORM ZIP contains an unsafe path")
    return path


def _manifest_info(data: bytes) -> tuple[str, str, dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail="Invalid imsmanifest.xml") from exc

    title = "Paquete SCORM"
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "title" and (element.text or "").strip():
            title = (element.text or "").strip()
            break

    resources = [x for x in root.iter() if x.tag.rsplit("}", 1)[-1] == "resource"]
    selected = None
    for resource in resources:
        attrs = {k.rsplit("}", 1)[-1].lower(): v for k, v in resource.attrib.items()}
        if attrs.get("scormtype", "").lower() == "sco" and resource.attrib.get("href"):
            selected = resource
            break
    if selected is None:
        selected = next((x for x in resources if x.attrib.get("href")), None)
    if selected is None:
        raise HTTPException(status_code=400, detail="SCORM manifest has no launchable resource")

    href = str(selected.attrib["href"]).strip()
    split = urlsplit(href)
    if split.scheme or split.netloc:
        raise HTTPException(status_code=400, detail="External SCORM entrypoints are not accepted")
    path = _safe_member(split.path)
    if not str(path):
        raise HTTPException(status_code=400, detail="SCORM entrypoint is empty")

    manifest = {
        "identifier": root.attrib.get("identifier"),
        "title": title,
        "entrypoint": href,
    }
    return title, href, manifest


def _inject_bridge(entry: Path) -> None:
    if entry.suffix.lower() not in {".html", ".htm"}:
        return
    raw = entry.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    marker = "/runtime/scorm-bridge.js"
    if marker in text:
        return
    script = '<script src="/runtime/scorm-bridge.js"></script>'
    lower = text.lower()
    index = lower.find("</head>")
    if index >= 0:
        text = text[:index] + script + text[index:]
    else:
        text = script + text
    entry.write_text(text, encoding="utf-8")


@router.post("/api/modules/{module_id}/scorm-packages")
async def upload_scorm(
    module_id: int,
    file: UploadFile = File(...),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    module = db.get(Module, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    _module_editor(db, module_id, user_id)

    max_bytes = settings.max_scorm_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    total = 0
    tmp = tempfile.NamedTemporaryFile(prefix="lms-scorm-", suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="SCORM package exceeds upload limit")
            digest.update(chunk)
            tmp.write(chunk)
        tmp.close()

        with zipfile.ZipFile(tmp_path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 5000:
                raise HTTPException(status_code=400, detail="SCORM ZIP has an invalid number of files")
            uncompressed = 0
            names: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                path = _safe_member(info.filename)
                uncompressed += info.file_size
                if uncompressed > max(max_bytes * 8, 1024 * 1024 * 1024):
                    raise HTTPException(status_code=400, detail="SCORM ZIP expands beyond the safety limit")
                if not info.is_dir():
                    names[path.as_posix()] = info

            manifest_info = names.get("imsmanifest.xml")
            if not manifest_info:
                raise HTTPException(status_code=400, detail="imsmanifest.xml must be at the ZIP root")
            title, entrypoint, manifest = _manifest_info(archive.read(manifest_info))
            entry_path = _safe_member(urlsplit(entrypoint).path).as_posix()
            if entry_path not in names:
                raise HTTPException(status_code=400, detail="SCORM entrypoint does not exist in the package")

            sha = digest.hexdigest()
            relative_dir = Path(module.slug) / sha
            final_dir = Path(settings.storage_root) / "scorm" / relative_dir
            if not final_dir.exists():
                final_dir.mkdir(parents=True, exist_ok=True)
                archive.extractall(final_dir)
                _inject_bridge(final_dir / entry_path)

        existing = db.scalar(
            select(ScormPackage).where(
                ScormPackage.module_id == module_id,
                ScormPackage.sha256 == sha,
            )
        )
        if existing:
            return {
                "id": existing.id,
                "title": existing.title,
                "sha256": existing.sha256,
                "entrypoint": existing.entrypoint,
                "deduplicated": True,
            }

        package = ScormPackage(
            module_id=module_id,
            title=title or file.filename or "Paquete SCORM",
            version=sha[:12],
            standard="SCORM_1.2",
            entrypoint=entrypoint,
            storage_path=relative_dir.as_posix(),
            sha256=sha,
            manifest_json=manifest,
        )
        db.add(package)
        db.commit()
        db.refresh(package)
        return {
            "id": package.id,
            "title": package.title,
            "sha256": package.sha256,
            "entrypoint": package.entrypoint,
            "deduplicated": False,
        }
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)


@router.get("/api/modules/{module_id}/scorm-packages")
def list_scorm(
    module_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(ScormPackage)
        .where(ScormPackage.module_id == module_id, ScormPackage.active.is_(True))
        .order_by(ScormPackage.id)
    ).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "version": row.version,
            "standard": row.standard,
            "entrypoint": row.entrypoint,
            "sha256": row.sha256,
        }
        for row in rows
    ]


@router.post("/api/course-modules/{course_module_id}/scorm/{package_id}/launch")
def launch_scorm(
    course_module_id: int,
    package_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    course_module = db.get(CourseModule, course_module_id)
    package = db.get(ScormPackage, package_id)
    if not course_module or not package:
        raise HTTPException(status_code=404, detail="Course module or SCORM package not found")
    if package.module_id != course_module.module_id:
        raise HTTPException(status_code=400, detail="SCORM package does not belong to this module")
    if int(session.get("course_id") or 0) != course_module.course_id:
        raise HTTPException(status_code=403, detail="Course context mismatch")
    _membership(db, course_module.course_id, user_id)

    registration = db.scalar(
        select(ScormRegistration).where(
            ScormRegistration.course_module_id == course_module.id,
            ScormRegistration.package_id == package.id,
            ScormRegistration.user_id == user_id,
        )
    )
    if not registration:
        registration = ScormRegistration(
            course_module_id=course_module.id,
            package_id=package.id,
            user_id=user_id,
        )
        db.add(registration)
        db.commit()
        db.refresh(registration)

    token = create_scorm_token(registration.id, user_id)
    split = urlsplit(package.entrypoint)
    base_path = f"{settings.content_base_url}/{package.storage_path}/{quote(split.path, safe='/%')}"
    query = list(parse_qsl(split.query, keep_blank_values=True))
    query.extend([
        ("lms_registration", str(registration.id)),
        ("lms_token", token),
    ])
    url = urlunsplit(("", "", base_path, urlencode(query), split.fragment))
    return {"registration_id": registration.id, "url": url}


def _runtime_auth(
    registration_id: int,
    authorization: str | None,
    db: Session,
) -> tuple[ScormRegistration, User]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="SCORM bearer token required")
    payload = read_scorm_token(authorization.split(" ", 1)[1].strip())
    if int(payload.get("registration_id") or 0) != registration_id:
        raise HTTPException(status_code=403, detail="SCORM registration mismatch")
    registration = db.get(ScormRegistration, registration_id)
    if not registration or registration.user_id != int(payload["sub"]):
        raise HTTPException(status_code=403, detail="SCORM registration not authorized")
    user = db.get(User, registration.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="SCORM user not found")
    return registration, user


@router.get("/runtime-api/scorm/registrations/{registration_id}")
def runtime_state(
    registration_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    registration, user = _runtime_auth(registration_id, authorization, db)
    cmi = dict(registration.cmi_json or {})
    cmi.update(
        {
            "cmi.core.student_id": str(user.id),
            "cmi.core.student_name": user.display_name,
            "cmi.core.lesson_status": registration.lesson_status,
            "cmi.core.lesson_location": registration.lesson_location,
            "cmi.suspend_data": registration.suspend_data,
        }
    )
    if registration.score_raw is not None:
        cmi["cmi.core.score.raw"] = str(registration.score_raw)
    if registration.score_min is not None:
        cmi["cmi.core.score.min"] = str(registration.score_min)
    if registration.score_max is not None:
        cmi["cmi.core.score.max"] = str(registration.score_max)
    return {"registration_id": registration.id, "cmi": cmi}


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


@router.put("/runtime-api/scorm/registrations/{registration_id}")
def runtime_commit(
    registration_id: int,
    payload: ScormCommitIn,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    registration, _ = _runtime_auth(registration_id, authorization, db)
    cmi = {str(k): v for k, v in payload.cmi.items()}
    registration.cmi_json = cmi
    registration.lesson_status = str(cmi.get("cmi.core.lesson_status") or registration.lesson_status)
    registration.lesson_location = str(cmi.get("cmi.core.lesson_location") or "")
    registration.suspend_data = str(cmi.get("cmi.suspend_data") or "")
    registration.score_raw = _float_or_none(cmi.get("cmi.core.score.raw"))
    registration.score_min = _float_or_none(cmi.get("cmi.core.score.min"))
    registration.score_max = _float_or_none(cmi.get("cmi.core.score.max"))
    registration.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "ok": True,
        "lesson_status": registration.lesson_status,
        "score_raw": registration.score_raw,
    }
