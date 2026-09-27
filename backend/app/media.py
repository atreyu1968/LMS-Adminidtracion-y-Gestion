from __future__ import annotations

from pathlib import Path
import hashlib
import mimetypes
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import MediaAsset
from .security import require_teacher
from .settings import get_settings


router = APIRouter(prefix="/api/media-library")
settings = get_settings()


class MediaMetadataIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    visibility: str | None = Field(default=None, pattern=r"^(private|shared)$")


def _safe_filename(name: str | None) -> str:
    raw = Path(name or "recurso").name.strip()
    raw = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw)
    return raw or "recurso"


def _media_type(mime_type: str, filename: str) -> str:
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return "document"
    raise HTTPException(
        status_code=415,
        detail="Solo se admiten vídeo, audio, imágenes y PDF en la biblioteca multimedia",
    )


def _asset_dict(asset: MediaAsset) -> dict:
    return {
        "id": asset.id,
        "owner_user_id": asset.owner_user_id,
        "title": asset.title,
        "description": asset.description,
        "original_filename": asset.original_filename,
        "media_type": asset.media_type,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "sha256": asset.sha256,
        "visibility": asset.visibility,
        "uploaded_at": asset.uploaded_at,
        "url": f"{settings.base_url}/api/media-library/{asset.id}/content",
    }


@router.post("")
async def upload_media(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    description: str = Form(default=""),
    visibility: str = Form(default="private"),
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    if visibility not in {"private", "shared"}:
        raise HTTPException(status_code=400, detail="Visibilidad no válida")

    user_id = int(session["sub"])
    filename = _safe_filename(file.filename)
    guessed = mimetypes.guess_type(filename)[0]
    mime_type = (file.content_type or guessed or "application/octet-stream").lower()
    media_type = _media_type(mime_type, filename)

    max_bytes = settings.max_media_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    total = 0

    incoming = Path(settings.storage_root) / "media" / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temp_path = incoming / f"{user_id}-{hashlib.sha1((filename + str(id(file))).encode()).hexdigest()}.part"

    try:
        with temp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="El archivo multimedia supera el límite")
                digest.update(chunk)
                out.write(chunk)

        sha = digest.hexdigest()
        existing = db.scalar(
            select(MediaAsset).where(
                MediaAsset.owner_user_id == user_id,
                MediaAsset.sha256 == sha,
                MediaAsset.active.is_(True),
            )
        )
        if existing:
            if title and title.strip():
                existing.title = title.strip()
            if description:
                existing.description = description
            existing.visibility = visibility
            db.commit()
            return _asset_dict(existing)

        ext = Path(filename).suffix.lower()
        relative = Path(f"user-{user_id}") / sha[:2] / f"{sha}{ext}"
        destination = Path(settings.storage_root) / "media" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path.replace(destination)

        asset = MediaAsset(
            owner_user_id=user_id,
            title=(title or "").strip() or Path(filename).stem,
            description=description,
            original_filename=filename,
            media_type=media_type,
            mime_type=mime_type,
            storage_path=relative.as_posix(),
            sha256=sha,
            size_bytes=total,
            visibility=visibility,
            active=True,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return _asset_dict(asset)
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("")
def list_media(
    scope: str = "mine",
    media_type: str | None = None,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    stmt = select(MediaAsset).where(MediaAsset.active.is_(True))

    if scope == "mine":
        stmt = stmt.where(MediaAsset.owner_user_id == user_id)
    elif scope == "shared":
        stmt = stmt.where(
            MediaAsset.visibility == "shared",
            MediaAsset.owner_user_id != user_id,
        )
    elif scope == "all":
        stmt = stmt.where(
            or_(MediaAsset.owner_user_id == user_id, MediaAsset.visibility == "shared")
        )
    else:
        raise HTTPException(status_code=400, detail="scope debe ser mine, shared o all")

    if media_type:
        if media_type not in {"video", "audio", "image", "document"}:
            raise HTTPException(status_code=400, detail="Tipo multimedia no válido")
        stmt = stmt.where(MediaAsset.media_type == media_type)

    rows = list(db.scalars(stmt.order_by(MediaAsset.uploaded_at.desc(), MediaAsset.id.desc())))
    return [_asset_dict(row) for row in rows]




@router.get("/{asset_id}/content")
def media_content(
    asset_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user_id = int(session["sub"])
    asset = db.get(MediaAsset, asset_id)
    if not asset or not asset.active:
        raise HTTPException(status_code=404, detail="Recurso multimedia no encontrado")
    if asset.owner_user_id != user_id and asset.visibility != "shared":
        raise HTTPException(status_code=403, detail="No tienes acceso a este recurso multimedia")

    root = (Path(settings.storage_root) / "media").resolve()
    target = (root / asset.storage_path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Fichero multimedia no disponible")

    return FileResponse(
        target,
        media_type=asset.mime_type,
        filename=asset.original_filename,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Accept-Ranges": "bytes",
        },
    )


@router.patch("/{asset_id}")
def update_media(
    asset_id: int,
    payload: MediaMetadataIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    asset = db.get(MediaAsset, asset_id)
    if not asset or not asset.active:
        raise HTTPException(status_code=404, detail="Recurso multimedia no encontrado")
    if asset.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede editar este recurso")

    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        asset.title = changes["title"]
    if "description" in changes:
        asset.description = changes["description"] or ""
    if "visibility" in changes:
        asset.visibility = changes["visibility"]
    db.commit()
    return _asset_dict(asset)


@router.delete("/{asset_id}")
def delete_media(
    asset_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    asset = db.get(MediaAsset, asset_id)
    if not asset or not asset.active:
        raise HTTPException(status_code=404, detail="Recurso multimedia no encontrado")
    if asset.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede borrar este recurso")
    asset.active = False
    db.commit()
    return {"ok": True}
