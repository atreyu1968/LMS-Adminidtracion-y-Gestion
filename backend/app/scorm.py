from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode, urlsplit
import hashlib
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    CourseModule,
    Membership,
    Module,
    ModulePermission,
    ModuleScormPackage,
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


class ScormMetadataIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    visibility: str | None = Field(default=None, pattern=r"^(private|shared)$")


class ScormAttachIn(BaseModel):
    position: int = Field(default=0, ge=0)
    required: bool = True
    weight: float = Field(default=1.0, ge=0)
    settings: dict = {}


def _module_permission(db: Session, module_id: int, user_id: int) -> ModulePermission | None:
    return db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )


def _module_editor(db: Session, module_id: int, user_id: int) -> None:
    permission = _module_permission(db, module_id, user_id)
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
        raise HTTPException(status_code=403, detail="Not enrolled in this group")
    return row


def _can_read_module(db: Session, module_id: int, user_id: int, course_id: int | None) -> bool:
    if _module_permission(db, module_id, user_id):
        return True
    if not course_id:
        return False
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_id,
            Membership.user_id == user_id,
            Membership.active.is_(True),
        )
    )
    if not membership:
        return False
    assignment = db.scalar(
        select(CourseModule).where(
            CourseModule.course_id == course_id,
            CourseModule.module_id == module_id,
            CourseModule.active.is_(True),
        )
    )
    return bool(assignment)


def _can_use_package(db: Session, package: ScormPackage, user_id: int) -> bool:
    if package.owner_user_id == user_id:
        return True
    if package.visibility == "shared":
        return True
    if package.module_id and _module_permission(db, package.module_id, user_id):
        return True
    return False


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise HTTPException(status_code=400, detail="SCORM ZIP contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(status_code=400, detail="SCORM ZIP contains an unsafe path")
    return path


def _manifest_info(data: bytes) -> tuple[str, str, str, dict]:
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

    schema_version = ""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() == "schemaversion":
            schema_version = (element.text or "").strip()
            break
    xml_hint = data.decode("utf-8", errors="ignore").lower()
    standard = (
        "SCORM_2004"
        if (
            "adlcp_v1p3" in xml_hint
            or "imsss" in xml_hint
            or "2004" in schema_version.lower()
            or "1.3" in schema_version.lower()
        )
        else "SCORM_1.2"
    )

    manifest = {
        "identifier": root.attrib.get("identifier"),
        "title": title,
        "entrypoint": href,
        "schema_version": schema_version,
        "standard": standard,
    }
    return title, href, standard, manifest


async def _store_scorm(
    *,
    file: UploadFile,
    owner_user_id: int | None,
    db: Session,
    title_override: str | None = None,
    description: str = "",
    visibility: str = "private",
    legacy_module_id: int | None = None,
) -> tuple[ScormPackage, bool]:
    if visibility not in {"private", "shared"}:
        raise HTTPException(status_code=400, detail="Invalid SCORM visibility")

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

        sha = digest.hexdigest()
        existing = db.scalar(
            select(ScormPackage).where(
                ScormPackage.owner_user_id == owner_user_id,
                ScormPackage.sha256 == sha,
                ScormPackage.is_current.is_(True),
            )
        )
        if existing:
            existing.active = True
            if title_override:
                existing.title = title_override
            if description:
                existing.description = description
            existing.visibility = visibility
            db.commit()
            db.refresh(existing)
            return existing, True

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
            manifest_title, entrypoint, standard, manifest = _manifest_info(archive.read(manifest_info))
            entry_path = _safe_member(urlsplit(entrypoint).path).as_posix()
            if entry_path not in names:
                raise HTTPException(status_code=400, detail="SCORM entrypoint does not exist in the package")

            owner_segment = f"user-{owner_user_id}" if owner_user_id is not None else "catalog"
            relative_dir = Path(owner_segment) / sha
            final_dir = Path(settings.storage_root) / "scorm" / relative_dir
            if not final_dir.exists():
                final_dir.mkdir(parents=True, exist_ok=True)
                archive.extractall(final_dir)

        package = ScormPackage(
            module_id=legacy_module_id,
            owner_user_id=owner_user_id,
            title=title_override or manifest_title or file.filename or "Paquete SCORM",
            description=description,
            original_filename=file.filename,
            version=sha[:12],
            standard=standard,
            entrypoint=entrypoint,
            storage_path=relative_dir.as_posix(),
            sha256=sha,
            manifest_json=manifest,
            visibility=visibility,
            active=True,
        )
        db.add(package)
        db.flush()
        package.lineage_root_id = package.id
        db.commit()
        db.refresh(package)
        return package, False
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)


def _attach_package(
    db: Session,
    module_id: int,
    package_id: int,
    payload: ScormAttachIn | None = None,
) -> ModuleScormPackage:
    data = payload or ScormAttachIn()
    row = db.scalar(
        select(ModuleScormPackage).where(
            ModuleScormPackage.module_id == module_id,
            ModuleScormPackage.package_id == package_id,
        )
    )
    if row:
        row.active = True
        row.position = data.position
        row.required = data.required
        row.weight = data.weight
        row.settings_json = data.settings
    else:
        row = ModuleScormPackage(
            module_id=module_id,
            package_id=package_id,
            position=data.position,
            required=data.required,
            weight=data.weight,
            settings_json=data.settings,
            active=True,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/api/scorm-library")
async def upload_to_library(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    description: str = Form(default=""),
    visibility: str = Form(default="private"),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    package, deduplicated = await _store_scorm(
        file=file,
        owner_user_id=user_id,
        db=db,
        title_override=(title or "").strip() or None,
        description=description,
        visibility=visibility,
    )
    return _package_dict(package, deduplicated=deduplicated)


@router.get("/api/scorm-library")
def scorm_library(
    scope: str = "mine",
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    stmt = select(ScormPackage).where(
        ScormPackage.active.is_(True),
        ScormPackage.is_current.is_(True),
    )
    if scope == "mine":
        stmt = stmt.where(ScormPackage.owner_user_id == user_id)
    elif scope == "shared":
        stmt = stmt.where(
            ScormPackage.visibility == "shared",
            ScormPackage.owner_user_id != user_id,
        )
    elif scope == "all":
        stmt = stmt.where(
            or_(
                ScormPackage.owner_user_id == user_id,
                ScormPackage.visibility == "shared",
            )
        )
    else:
        raise HTTPException(status_code=400, detail="scope must be mine, shared or all")
    rows = list(db.scalars(stmt.order_by(ScormPackage.uploaded_at.desc(), ScormPackage.id.desc())))
    return [_package_dict(row) for row in rows]


@router.patch("/api/scorm-library/{package_id}")
def update_library_item(
    package_id: int,
    payload: ScormMetadataIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    package = db.get(ScormPackage, package_id)
    if not package or not package.active:
        raise HTTPException(status_code=404, detail="SCORM package not found")
    if package.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the owner can edit this SCORM")
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        package.title = changes["title"]
    if "description" in changes:
        package.description = changes["description"] or ""
    if "visibility" in changes:
        package.visibility = changes["visibility"]
    db.commit()
    return _package_dict(package)


@router.delete("/api/scorm-library/{package_id}")
def delete_library_item(
    package_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    package = db.get(ScormPackage, package_id)
    if not package or not package.active:
        raise HTTPException(status_code=404, detail="SCORM package not found")
    if package.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the owner can delete this SCORM")
    attached = db.scalar(
        select(ModuleScormPackage.id).where(
            ModuleScormPackage.package_id == package_id,
            ModuleScormPackage.active.is_(True),
        )
    )
    if attached:
        raise HTTPException(
            status_code=409,
            detail="Detach this SCORM from all modules before deleting it",
        )
    package.active = False
    db.commit()
    return {"ok": True}


@router.post("/api/modules/{module_id}/scorm-packages")
async def upload_scorm(
    module_id: int,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    description: str = Form(default=""),
    visibility: str = Form(default="private"),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    module = db.get(Module, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    _module_editor(db, module_id, user_id)

    package, deduplicated = await _store_scorm(
        file=file,
        owner_user_id=user_id,
        db=db,
        title_override=(title or "").strip() or None,
        description=description,
        visibility=visibility,
        legacy_module_id=module_id,
    )
    association = _attach_package(db, module_id, package.id)
    result = _package_dict(package, deduplicated=deduplicated)
    result["module_scorm_id"] = association.id
    return result


@router.post("/api/modules/{module_id}/scorm-packages/{package_id}")
def attach_library_scorm(
    module_id: int,
    package_id: int,
    payload: ScormAttachIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _module_editor(db, module_id, user_id)
    package = db.get(ScormPackage, package_id)
    if not package or not package.active:
        raise HTTPException(status_code=404, detail="SCORM package not found")
    if not _can_use_package(db, package, user_id):
        raise HTTPException(status_code=403, detail="This SCORM is private to another teacher")
    row = _attach_package(db, module_id, package_id, payload)
    return {
        "id": row.id,
        "module_id": row.module_id,
        "package_id": row.package_id,
        "position": row.position,
        "required": row.required,
        "weight": row.weight,
    }


@router.delete("/api/modules/{module_id}/scorm-packages/{package_id}")
def detach_scorm(
    module_id: int,
    package_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    _module_editor(db, module_id, user_id)
    row = db.scalar(
        select(ModuleScormPackage).where(
            ModuleScormPackage.module_id == module_id,
            ModuleScormPackage.package_id == package_id,
            ModuleScormPackage.active.is_(True),
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="SCORM is not attached to this module")
    row.active = False
    db.commit()
    return {"ok": True}


@router.get("/api/modules/{module_id}/scorm-packages")
def list_scorm(
    module_id: int,
    session: dict = Depends(read_session),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    course_id = int(session.get("course_id") or 0) or None
    if not _can_read_module(db, module_id, user_id, course_id):
        raise HTTPException(status_code=403, detail="Module access required")

    rows = db.execute(
        select(ModuleScormPackage, ScormPackage)
        .join(ScormPackage, ScormPackage.id == ModuleScormPackage.package_id)
        .where(
            ModuleScormPackage.module_id == module_id,
            ModuleScormPackage.active.is_(True),
            ScormPackage.active.is_(True),
        )
        .order_by(ModuleScormPackage.position, ModuleScormPackage.id)
    ).all()
    return [
        {
            **_package_dict(package),
            "module_scorm_id": association.id,
            "position": association.position,
            "required": association.required,
            "weight": association.weight,
            "settings": association.settings_json or {},
        }
        for association, package in rows
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
    if not course_module or not package or not package.active:
        raise HTTPException(status_code=404, detail="Group module or SCORM package not found")
    if int(session.get("course_id") or 0) != course_module.course_id:
        raise HTTPException(status_code=403, detail="Group context mismatch")
    _membership(db, course_module.course_id, user_id)

    attached = db.scalar(
        select(ModuleScormPackage).where(
            ModuleScormPackage.module_id == course_module.module_id,
            ModuleScormPackage.package_id == package.id,
            ModuleScormPackage.active.is_(True),
        )
    )
    legacy_match = package.module_id == course_module.module_id
    if not attached and not legacy_match:
        raise HTTPException(status_code=400, detail="SCORM package is not assigned to this module")

    lineage_root = int(package.lineage_root_id or package.id)
    family_ids = list(
        db.scalars(
            select(ScormPackage.id).where(
                ScormPackage.owner_user_id == package.owner_user_id,
                __import__("sqlalchemy").or_(
                    ScormPackage.lineage_root_id == lineage_root,
                    ScormPackage.id == lineage_root,
                ),
            )
        )
    )
    if package.id not in family_ids:
        family_ids.append(package.id)

    registration = db.scalar(
        select(ScormRegistration)
        .where(
            ScormRegistration.course_module_id == course_module.id,
            ScormRegistration.user_id == user_id,
            ScormRegistration.package_id.in_(family_ids),
        )
        .order_by(ScormRegistration.updated_at.desc(), ScormRegistration.id.desc())
    )
    effective_package = package
    pinned_to_existing_revision = False
    if registration:
        previous_package = db.get(ScormPackage, registration.package_id)
        if previous_package:
            effective_package = previous_package
            pinned_to_existing_revision = previous_package.id != package.id
    else:
        registration = ScormRegistration(
            course_module_id=course_module.id,
            package_id=package.id,
            user_id=user_id,
        )
        db.add(registration)
        db.commit()
        db.refresh(registration)

    token = create_scorm_token(registration.id, user_id)
    split = urlsplit(effective_package.entrypoint)
    content_url = f"{settings.content_base_url}/{effective_package.storage_path}/{quote(split.path, safe='/%')}"
    if split.query:
        content_url += "?" + split.query
    if split.fragment:
        content_url += "#" + split.fragment

    content_base = urlsplit(settings.content_base_url)
    content_origin = (
        f"{content_base.scheme}://{content_base.netloc}"
        if content_base.scheme and content_base.netloc
        else settings.base_url
    )
    url = content_origin + "/runtime/scorm-player.html?" + urlencode(
        {
            "launch": content_url,
            "lms_registration": str(registration.id),
            "lms_token": token,
        }
    )
    return {
        "registration_id": registration.id,
        "url": url,
        "package_id": effective_package.id,
        "revision_number": effective_package.revision_number,
        "pinned_to_existing_revision": pinned_to_existing_revision,
    }


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
    package = db.get(ScormPackage, registration.package_id)
    if not package:
        raise HTTPException(status_code=404, detail="SCORM package not found")

    cmi = dict(registration.cmi_json or {})
    if package.standard == "SCORM_2004":
        cmi.update(
            {
                "cmi.learner_id": str(user.id),
                "cmi.learner_name": user.display_name,
                "cmi.completion_status": registration.lesson_status,
                "cmi.location": registration.lesson_location,
                "cmi.suspend_data": registration.suspend_data,
            }
        )
        if registration.score_raw is not None:
            cmi["cmi.score.raw"] = str(registration.score_raw)
        if registration.score_min is not None:
            cmi["cmi.score.min"] = str(registration.score_min)
        if registration.score_max is not None:
            cmi["cmi.score.max"] = str(registration.score_max)
    else:
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
    return {
        "registration_id": registration.id,
        "standard": package.standard,
        "cmi": cmi,
    }


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
    package = db.get(ScormPackage, registration.package_id)
    if not package:
        raise HTTPException(status_code=404, detail="SCORM package not found")

    cmi = {str(k): v for k, v in payload.cmi.items()}
    registration.cmi_json = cmi
    if package.standard == "SCORM_2004":
        registration.lesson_status = str(
            cmi.get("cmi.completion_status")
            or cmi.get("cmi.success_status")
            or registration.lesson_status
        )
        registration.lesson_location = str(cmi.get("cmi.location") or "")
        registration.suspend_data = str(cmi.get("cmi.suspend_data") or "")
        registration.score_raw = _float_or_none(cmi.get("cmi.score.raw"))
        registration.score_min = _float_or_none(cmi.get("cmi.score.min"))
        registration.score_max = _float_or_none(cmi.get("cmi.score.max"))
    else:
        registration.lesson_status = str(
            cmi.get("cmi.core.lesson_status") or registration.lesson_status
        )
        registration.lesson_location = str(cmi.get("cmi.core.lesson_location") or "")
        registration.suspend_data = str(cmi.get("cmi.suspend_data") or "")
        registration.score_raw = _float_or_none(cmi.get("cmi.core.score.raw"))
        registration.score_min = _float_or_none(cmi.get("cmi.core.score.min"))
        registration.score_max = _float_or_none(cmi.get("cmi.core.score.max"))
    registration.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "ok": True,
        "standard": package.standard,
        "lesson_status": registration.lesson_status,
        "score_raw": registration.score_raw,
    }


def _package_dict(package: ScormPackage, deduplicated: bool | None = None) -> dict:
    result = {
        "id": package.id,
        "owner_user_id": package.owner_user_id,
        "title": package.title,
        "description": package.description,
        "original_filename": package.original_filename,
        "version": package.version,
        "revision_number": package.revision_number,
        "lineage_root_id": package.lineage_root_id or package.id,
        "supersedes_id": package.supersedes_id,
        "is_current": package.is_current,
        "lifecycle_status": package.lifecycle_status,
        "standard": package.standard,
        "entrypoint": package.entrypoint,
        "sha256": package.sha256,
        "visibility": package.visibility,
        "uploaded_at": package.uploaded_at,
    }
    if deduplicated is not None:
        result["deduplicated"] = deduplicated
    return result
