from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
import hashlib
import io
import json
import mimetypes
import re
import shutil
import tempfile
import uuid
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .db import get_db
from .models import (
    MediaAsset,
    ModuleScormPackage,
    ScormDraft,
    ScormPackage,
    ScormRegistration,
)
from .scorm import _manifest_info, _package_dict, _safe_member
from .security import require_teacher
from .settings import get_settings


router = APIRouter()
settings = get_settings()

TEXT_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".xml", ".txt",
    ".md", ".svg", ".csv", ".vtt", ".srt",
}
PREVIEW_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".xml", ".txt",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac",
    ".mp4", ".webm", ".ogv", ".mov", ".pdf", ".vtt", ".srt",
}


class DraftFileIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str


class DraftMetadataIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    visibility: str | None = Field(default=None, pattern=r"^(private|shared)$")


class InsertMediaIn(BaseModel):
    target_path: str = Field(min_length=1, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _package_root(package: ScormPackage) -> int:
    return int(package.lineage_root_id or package.id)


def _owner_package(db: Session, package_id: int, user_id: int) -> ScormPackage:
    package = db.get(ScormPackage, package_id)
    if not package or not package.active:
        raise HTTPException(status_code=404, detail="SCORM no encontrado")
    if package.owner_user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Para editar un SCORM compartido crea primero una copia editable",
        )
    return package


def _readable_package(db: Session, package_id: int, user_id: int) -> ScormPackage:
    package = db.get(ScormPackage, package_id)
    if not package or not package.active:
        raise HTTPException(status_code=404, detail="SCORM no encontrado")
    if package.owner_user_id != user_id and package.visibility != "shared":
        raise HTTPException(status_code=403, detail="No tienes acceso a este SCORM")
    return package


def _draft(db: Session, draft_id: str, user_id: int) -> ScormDraft:
    draft = db.get(ScormDraft, draft_id)
    if not draft or draft.owner_user_id != user_id:
        raise HTTPException(status_code=404, detail="Borrador SCORM no encontrado")
    return draft


def _draft_root(draft: ScormDraft) -> Path:
    root = (Path(settings.storage_root) / "scorm-drafts" / draft.working_path).resolve()
    base = (Path(settings.storage_root) / "scorm-drafts").resolve()
    if root != base and base not in root.parents:
        raise HTTPException(status_code=500, detail="Ruta de borrador inválida")
    return root


def _package_dir(package: ScormPackage) -> Path:
    root = (Path(settings.storage_root) / "scorm" / package.storage_path).resolve()
    base = (Path(settings.storage_root) / "scorm").resolve()
    if root != base and base not in root.parents:
        raise HTTPException(status_code=500, detail="Ruta SCORM inválida")
    return root


def _safe_relative(raw: str) -> Path:
    posix = _safe_member(raw)
    path = Path(*posix.parts)
    if not path.parts:
        raise HTTPException(status_code=400, detail="Ruta vacía")
    return path


def _inside(root: Path, raw: str) -> Path:
    rel = _safe_relative(raw)
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=400, detail="Ruta no permitida")
    return target


def _tree(root: Path) -> list[dict]:
    items: list[dict] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda p: (p.is_file(), p.as_posix().lower())):
        if path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        total += 1
        if total > 6000:
            raise HTTPException(status_code=400, detail="El SCORM contiene demasiados elementos")
        if path.is_dir():
            items.append({"path": rel, "type": "folder"})
        elif path.is_file():
            ext = path.suffix.lower()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            items.append({
                "path": rel,
                "type": "file",
                "size": path.stat().st_size,
                "mime_type": mime,
                "editable": ext in TEXT_EXTENSIONS,
                "previewable": ext in PREVIEW_EXTENSIONS,
            })
    return items


def _hash_directory(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def _validate_working_tree(root: Path) -> tuple[str, str, str, dict]:
    manifest_path = root / "imsmanifest.xml"
    if not manifest_path.is_file():
        raise HTTPException(status_code=400, detail="El borrador no contiene imsmanifest.xml")
    title, entrypoint, standard, manifest = _manifest_info(manifest_path.read_bytes())
    entry_path = _inside(root, urlsplit(entrypoint).path)
    if not entry_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"El recurso de lanzamiento no existe: {entrypoint}",
        )
    return title, entrypoint, standard, manifest


def _zip_directory(root: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", allowZip64=True) as archive:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            compression = (
                zipfile.ZIP_STORED
                if path.suffix.lower() in {
                    ".mp4", ".webm", ".mp3", ".ogg", ".m4a", ".jpg",
                    ".jpeg", ".png", ".webp", ".gif", ".zip", ".pdf",
                }
                else zipfile.ZIP_DEFLATED
            )
            archive.write(path, rel, compress_type=compression)


def _download_name(title: str, revision: int) -> str:
    clean = re.sub(r"[^A-Za-z0-9._ -]+", "_", title).strip(" ._") or "scorm"
    return f"{clean}-r{revision}.zip"


@router.post("/api/scorm-library/{package_id}/draft")
def create_draft(
    package_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    package = _owner_package(db, package_id, user_id)

    existing = db.scalar(
        select(ScormDraft)
        .where(
            ScormDraft.base_package_id == package.id,
            ScormDraft.owner_user_id == user_id,
        )
        .order_by(ScormDraft.updated_at.desc())
    )
    if existing and _draft_root(existing).is_dir():
        return {
            "draft_id": existing.id,
            "base_package_id": package.id,
            "revision_number": package.revision_number,
        }

    draft_id = uuid.uuid4().hex
    working_path = f"user-{user_id}/{draft_id}"
    root = Path(settings.storage_root) / "scorm-drafts" / working_path
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_package_dir(package), root)

    draft = ScormDraft(
        id=draft_id,
        base_package_id=package.id,
        owner_user_id=user_id,
        working_path=working_path,
        title=package.title,
        description=package.description,
        visibility=package.visibility,
    )
    db.add(draft)
    db.commit()
    return {
        "draft_id": draft.id,
        "base_package_id": package.id,
        "revision_number": package.revision_number,
    }


@router.post("/api/scorm-library/{package_id}/fork")
def fork_package(
    package_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    source = _readable_package(db, package_id, user_id)

    package = ScormPackage(
        module_id=None,
        owner_user_id=user_id,
        title=f"Copia de {source.title}" if source.owner_user_id != user_id else source.title,
        description=source.description,
        original_filename=source.original_filename,
        version=source.version,
        standard=source.standard,
        entrypoint=source.entrypoint,
        storage_path=source.storage_path,
        sha256=source.sha256,
        manifest_json=dict(source.manifest_json or {}),
        visibility="private",
        revision_number=1,
        is_current=True,
        lifecycle_status="published",
        active=True,
    )
    db.add(package)
    db.flush()
    package.lineage_root_id = package.id
    db.commit()
    db.refresh(package)
    return _package_dict(package)


@router.get("/api/scorm-library/{package_id}/history")
def package_history(
    package_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    package = _readable_package(db, package_id, user_id)
    root_id = _package_root(package)
    rows = list(
        db.scalars(
            select(ScormPackage)
            .where(
                ScormPackage.lineage_root_id == root_id,
                ScormPackage.owner_user_id == package.owner_user_id,
            )
            .order_by(ScormPackage.revision_number.desc(), ScormPackage.id.desc())
        )
    )
    if not rows:
        rows = [package]
    return [_package_dict(row) for row in rows]


@router.get("/api/scorm-editor/{draft_id}")
def draft_info(
    draft_id: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    base = db.get(ScormPackage, draft.base_package_id)
    if not base:
        raise HTTPException(status_code=404, detail="Versión base no encontrada")
    root = _draft_root(draft)
    return {
        "draft_id": draft.id,
        "base_package": _package_dict(base),
        "title": draft.title,
        "description": draft.description,
        "visibility": draft.visibility,
        "files": _tree(root),
        "preview_entrypoint": f"/api/scorm-editor/{draft.id}/preview/{base.entrypoint}",
    }


@router.patch("/api/scorm-editor/{draft_id}")
def update_draft_metadata(
    draft_id: str,
    payload: DraftMetadataIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        draft.title = changes["title"]
    if "description" in changes:
        draft.description = changes["description"] or ""
    if "visibility" in changes:
        draft.visibility = changes["visibility"]
    draft.updated_at = _now()
    db.commit()
    return {
        "draft_id": draft.id,
        "title": draft.title,
        "description": draft.description,
        "visibility": draft.visibility,
    }


@router.get("/api/scorm-editor/{draft_id}/file")
def read_draft_file(
    draft_id: str,
    path: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    root = _draft_root(draft)
    target = _inside(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    if target.suffix.lower() not in TEXT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Este archivo no es editable como texto")
    if target.stat().st_size > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="El archivo de texto es demasiado grande para el editor")
    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("latin-1")
        encoding = "latin-1"
    return {"path": path, "content": content, "encoding": encoding}


@router.put("/api/scorm-editor/{draft_id}/file")
def write_draft_file(
    draft_id: str,
    payload: DraftFileIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    root = _draft_root(draft)
    target = _inside(root, payload.path)
    if target.suffix.lower() not in TEXT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Este tipo de archivo no se edita como texto")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.content, encoding="utf-8")
    draft.updated_at = _now()
    db.commit()
    return {"ok": True, "path": payload.path, "size": target.stat().st_size}


@router.post("/api/scorm-editor/{draft_id}/upload")
async def upload_draft_file(
    draft_id: str,
    target_path: str = Form(...),
    file: UploadFile = File(...),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    root = _draft_root(draft)
    target = _inside(root, target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    limit = settings.max_media_upload_mb * 1024 * 1024
    total = 0
    with target.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="El archivo supera el límite configurado")
            out.write(chunk)

    draft.updated_at = _now()
    db.commit()
    return {"ok": True, "path": target_path, "size": total}


@router.delete("/api/scorm-editor/{draft_id}/file")
def delete_draft_file(
    draft_id: str,
    path: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    root = _draft_root(draft)
    target = _inside(root, path)
    if target.name == "imsmanifest.xml":
        raise HTTPException(status_code=409, detail="El manifiesto no puede eliminarse")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    target.unlink()
    draft.updated_at = _now()
    db.commit()
    return {"ok": True}


@router.post("/api/scorm-editor/{draft_id}/insert-media/{asset_id}")
def insert_media(
    draft_id: str,
    asset_id: int,
    payload: InsertMediaIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    asset = db.get(MediaAsset, asset_id)
    if not asset or not asset.active:
        raise HTTPException(status_code=404, detail="Recurso multimedia no encontrado")
    if asset.owner_user_id != user_id and asset.visibility != "shared":
        raise HTTPException(status_code=403, detail="No tienes acceso a este recurso multimedia")

    source = (Path(settings.storage_root) / "media" / asset.storage_path).resolve()
    media_base = (Path(settings.storage_root) / "media").resolve()
    if media_base not in source.parents or not source.is_file():
        raise HTTPException(status_code=404, detail="Fichero multimedia no disponible")

    root = _draft_root(draft)
    target = _inside(root, payload.target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    draft.updated_at = _now()
    db.commit()
    return {"ok": True, "path": payload.target_path, "size": target.stat().st_size}


@router.get("/api/scorm-editor/{draft_id}/preview/{file_path:path}")
def preview_file(
    draft_id: str,
    file_path: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    root = _draft_root(draft)
    target = _inside(root, file_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.post("/api/scorm-editor/{draft_id}/publish")
def publish_draft(
    draft_id: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    base = _owner_package(db, draft.base_package_id, user_id)
    if not base.is_current:
        raise HTTPException(
            status_code=409,
            detail="Este borrador parte de una revisión antigua. Abre el editor desde la revisión actual.",
        )

    root = _draft_root(draft)
    manifest_title, entrypoint, standard, manifest = _validate_working_tree(root)
    sha = _hash_directory(root)

    relative = Path(f"user-{user_id}") / sha
    immutable = Path(settings.storage_root) / "scorm" / relative
    if not immutable.exists():
        immutable.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, immutable)

    new_revision = int(base.revision_number or 1) + 1
    package = ScormPackage(
        module_id=base.module_id,
        owner_user_id=user_id,
        title=draft.title or manifest_title,
        description=draft.description,
        original_filename=base.original_filename,
        version=f"r{new_revision}-{sha[:12]}",
        lineage_root_id=_package_root(base),
        supersedes_id=base.id,
        revision_number=new_revision,
        is_current=True,
        lifecycle_status="published",
        standard=standard,
        entrypoint=entrypoint,
        storage_path=relative.as_posix(),
        sha256=sha,
        manifest_json=manifest,
        visibility=draft.visibility,
        active=True,
    )
    db.add(package)
    db.flush()

    active_links = list(
        db.scalars(
            select(ModuleScormPackage).where(
                ModuleScormPackage.package_id == base.id,
                ModuleScormPackage.active.is_(True),
            )
        )
    )
    for old_link in active_links:
        already = db.scalar(
            select(ModuleScormPackage).where(
                ModuleScormPackage.module_id == old_link.module_id,
                ModuleScormPackage.package_id == package.id,
            )
        )
        if already:
            already.position = old_link.position
            already.required = old_link.required
            already.weight = old_link.weight
            already.settings_json = dict(old_link.settings_json or {})
            already.active = True
        else:
            db.add(
                ModuleScormPackage(
                    module_id=old_link.module_id,
                    package_id=package.id,
                    position=old_link.position,
                    required=old_link.required,
                    weight=old_link.weight,
                    settings_json=dict(old_link.settings_json or {}),
                    active=True,
                )
            )
        old_link.active = False

    base.is_current = False
    base.lifecycle_status = "superseded"
    draft_path = root
    db.delete(draft)
    db.commit()
    db.refresh(package)
    shutil.rmtree(draft_path, ignore_errors=True)

    registrations = db.scalar(
        select(__import__("sqlalchemy").func.count(ScormRegistration.id)).where(
            ScormRegistration.package_id == base.id
        )
    ) or 0
    return {
        "ok": True,
        "package": _package_dict(package),
        "superseded_package_id": base.id,
        "preserved_registrations": registrations,
        "message": (
            "Nueva revisión publicada. Los intentos existentes permanecen vinculados "
            "a la revisión anterior."
        ),
    }


@router.get("/api/scorm-library/{package_id}/export")
def export_package(
    package_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user_id = int(session["sub"])
    package = _readable_package(db, package_id, user_id)
    root = _package_dir(package)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Contenido SCORM no disponible")
    fd, temp_name = tempfile.mkstemp(prefix="lms-scorm-export-", suffix=".zip")
    Path(temp_name).unlink(missing_ok=True)
    _zip_directory(root, Path(temp_name))
    return FileResponse(
        temp_name,
        media_type="application/zip",
        filename=_download_name(package.title, package.revision_number),
        background=BackgroundTask(lambda: Path(temp_name).unlink(missing_ok=True)),
    )


@router.get("/api/scorm-editor/{draft_id}/export")
def export_draft(
    draft_id: str,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user_id = int(session["sub"])
    draft = _draft(db, draft_id, user_id)
    root = _draft_root(draft)
    _validate_working_tree(root)
    base = db.get(ScormPackage, draft.base_package_id)
    revision = int(base.revision_number or 1) + 1 if base else 1
    fd, temp_name = tempfile.mkstemp(prefix="lms-scorm-draft-", suffix=".zip")
    Path(temp_name).unlink(missing_ok=True)
    _zip_directory(root, Path(temp_name))
    return FileResponse(
        temp_name,
        media_type="application/zip",
        filename=_download_name(draft.title, revision),
        background=BackgroundTask(lambda: Path(temp_name).unlink(missing_ok=True)),
    )


@router.get("/api/scorm-library/export-all")
def export_all_packages(
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user_id = int(session["sub"])
    packages = list(
        db.scalars(
            select(ScormPackage)
            .where(
                ScormPackage.owner_user_id == user_id,
                ScormPackage.active.is_(True),
                ScormPackage.is_current.is_(True),
            )
            .order_by(ScormPackage.title)
        )
    )
    if not packages:
        raise HTTPException(status_code=404, detail="No hay SCORM para exportar")

    fd, outer_name = tempfile.mkstemp(prefix="lms-scorm-library-", suffix=".zip")
    Path(outer_name).unlink(missing_ok=True)
    inner_temps: list[Path] = []
    try:
        with zipfile.ZipFile(outer_name, "w", allowZip64=True) as outer:
            catalog = []
            used_names: set[str] = set()
            for index, package in enumerate(packages, start=1):
                root = _package_dir(package)
                if not root.is_dir():
                    continue
                fd2, inner_name = tempfile.mkstemp(prefix="lms-inner-", suffix=".zip")
                Path(inner_name).unlink(missing_ok=True)
                inner = Path(inner_name)
                inner_temps.append(inner)
                _zip_directory(root, inner)
                name = _download_name(package.title, package.revision_number)
                if name in used_names:
                    name = f"{index:03d}-{name}"
                used_names.add(name)
                outer.write(inner, f"scorm/{name}", compress_type=zipfile.ZIP_STORED)
                catalog.append({
                    "id": package.id,
                    "title": package.title,
                    "revision": package.revision_number,
                    "standard": package.standard,
                    "filename": f"scorm/{name}",
                    "sha256": package.sha256,
                })
            outer.writestr(
                "catalogo.json",
                json.dumps(catalog, ensure_ascii=False, indent=2),
            )
    finally:
        for path in inner_temps:
            path.unlink(missing_ok=True)

    return FileResponse(
        outer_name,
        media_type="application/zip",
        filename="biblioteca-scorm.zip",
        background=BackgroundTask(lambda: Path(outer_name).unlink(missing_ok=True)),
    )
