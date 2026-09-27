from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    AssessmentCriterion,
    AssessmentItem,
    AssessmentKey,
    CourseModule,
    LearningResult,
    Module,
    ModulePermission,
    ModuleScormPackage,
    ScormPackage,
)
from .scorm import ScormAttachIn, _attach_package, _store_scorm
from .security import require_admin, require_teacher
from .settings import get_settings


router = APIRouter(prefix="/api")
settings = get_settings()


def _catalog_root() -> Path:
    return Path(settings.modules_root).resolve()


def _module_dir(folder: str) -> Path:
    if not folder or "/" in folder or "\\" in folder or folder in {".", ".."}:
        raise HTTPException(status_code=400, detail="Identificador de catálogo no válido")
    root = _catalog_root()
    target = (root / folder).resolve()
    if root not in target.parents or not target.is_dir():
        raise HTTPException(status_code=404, detail="Módulo de catálogo no encontrado")
    return target


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Falta {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"JSON inválido: {path.name}") from exc


def _public_hash(item: dict) -> str:
    payload = {
        "id": item.get("id"),
        "ce": item.get("ce"),
        "kind": item.get("kind"),
        "prompt": item.get("prompt"),
        "options": item.get("options", []),
        "pairs": item.get("pairs", []),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def import_catalog_metadata(db: Session, folder: str) -> dict:
    root = _module_dir(folder)
    manifest = _load_json(root / "module.json")
    structure = _load_json(root / "structure.json")

    module_data = structure.get("module") or {}
    slug = module_data.get("slug") or manifest.get("slug")
    if not slug:
        raise HTTPException(status_code=500, detail="El catálogo no define slug")

    source = manifest.get("source") or {}
    migration = manifest.get("migration") or {}
    module = db.scalar(select(Module).where(Module.slug == slug))
    metadata = {
        **(module.metadata_json if module else {}),
        "catalog_shared": True,
        "catalog_folder": folder,
        "source_repository": source.get("repository"),
        "source_ref": source.get("ref") or migration.get("source_commit"),
        "source_path": source.get("path"),
        "ra_weights_verified": bool(
            (structure.get("evaluation_defaults") or {}).get("ra_weights_verified", False)
        ),
        "evaluation_defaults": structure.get("evaluation_defaults") or {},
    }

    if not module:
        module = Module(
            slug=slug,
            code=module_data.get("code") or manifest.get("code"),
            title=module_data.get("title") or manifest.get("title") or slug,
            description=manifest.get("description")
            or "Módulo oficial importado desde el catálogo del LMS.",
            module_type="native+scorm",
            version=(source.get("ref") or migration.get("source_commit") or "catalog")[:12],
            metadata_json=metadata,
            active=True,
        )
        db.add(module)
        db.flush()
    else:
        module.code = module_data.get("code") or manifest.get("code") or module.code
        module.title = module_data.get("title") or manifest.get("title") or module.title
        module.module_type = "native+scorm"
        module.version = (source.get("ref") or migration.get("source_commit") or module.version)[:12]
        module.metadata_json = metadata
        module.active = True
        db.flush()

    defaults = structure.get("evaluation_defaults") or {}
    portfolio_attempts = int(defaults.get("portfolio_max_attempts", 2))
    learning_results = 0
    criteria_count = 0
    item_count = 0

    for lr_data in structure.get("learning_results") or []:
        lr = db.scalar(
            select(LearningResult).where(
                LearningResult.module_id == module.id,
                LearningResult.code == lr_data["code"],
            )
        )
        if not lr:
            lr = LearningResult(
                module_id=module.id,
                code=lr_data["code"],
                title=lr_data.get("title") or lr_data["code"],
            )
            db.add(lr)
            db.flush()
        lr.title = lr_data.get("title") or lr.code
        lr.description = lr_data.get("description") or ""
        lr.position = int(lr_data.get("position") or 0)
        lr.weight = lr_data.get("weight")
        lr.metadata_json = {
            **(lr.metadata_json or {}),
            "legacy_course_id": lr_data.get("legacy_course_id"),
            "unit": lr_data.get("unit"),
        }
        lr.active = True
        learning_results += 1

        bank_path = root / "public-banks" / f"{lr.code.lower()}_portfolio.json"
        bank = _load_json(bank_path)
        by_ce: dict[str, list[dict]] = {}
        for item in bank.get("items") or []:
            by_ce.setdefault(str(item.get("ce") or ""), []).append(item)

        for criterion_data in lr_data.get("criteria") or []:
            criterion = db.scalar(
                select(AssessmentCriterion).where(
                    AssessmentCriterion.learning_result_id == lr.id,
                    AssessmentCriterion.code == criterion_data["code"],
                )
            )
            if not criterion:
                criterion = AssessmentCriterion(
                    learning_result_id=lr.id,
                    code=criterion_data["code"],
                    title=criterion_data.get("title") or criterion_data["code"],
                )
                db.add(criterion)
                db.flush()
            criterion.title = criterion_data.get("title") or criterion.code
            criterion.description = criterion_data.get("description") or ""
            criterion.position = int(criterion_data.get("position") or 0)
            criterion.pass_score = float(defaults.get("ce_pass_score", 5))
            criterion.weight = criterion_data.get("weight")
            criterion.metadata_json = {
                **(criterion.metadata_json or {}),
                "portfolio_items_expected": criterion_data.get("portfolio_items"),
            }
            criterion.active = True
            criteria_count += 1

            for position, public_item in enumerate(by_ce.get(criterion.code, []), start=1):
                item = db.scalar(
                    select(AssessmentItem).where(
                        AssessmentItem.criterion_id == criterion.id,
                        AssessmentItem.instrument == "portfolio",
                        AssessmentItem.item_key == public_item["id"],
                    )
                )
                if not item:
                    item = AssessmentItem(
                        criterion_id=criterion.id,
                        instrument="portfolio",
                        item_key=public_item["id"],
                    )
                    db.add(item)
                item.item_type = public_item.get("kind") or "choice"
                item.prompt = public_item.get("prompt") or ""
                item.options_json = public_item.get("options") or []
                item.public_hash = _public_hash(public_item)
                item.evaluable = True
                item.max_attempts = portfolio_attempts
                item.position = position
                item.metadata_json = {
                    "pairs": public_item.get("pairs") or [],
                    "source": "public-bank",
                }
                item.active = True
                item_count += 1

    db.commit()
    db.refresh(module)
    return {
        "module_id": module.id,
        "slug": module.slug,
        "title": module.title,
        "learning_results": learning_results,
        "criteria": criteria_count,
        "portfolio_items": item_count,
    }


@router.get("/catalog/modules")
def list_catalog_modules(
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    user_id = int(session["sub"])
    modules = list(db.scalars(select(Module).where(Module.active.is_(True)).order_by(Module.title)))
    result = []
    for module in modules:
        metadata = module.metadata_json or {}
        if not metadata.get("catalog_shared"):
            continue
        permission = db.scalar(
            select(ModulePermission).where(
                ModulePermission.module_id == module.id,
                ModulePermission.user_id == user_id,
            )
        )
        result.append(
            {
                "id": module.id,
                "slug": module.slug,
                "code": module.code,
                "title": module.title,
                "description": module.description,
                "version": module.version,
                "installed": bool(permission),
                "permission": permission.permission if permission else None,
                "metadata": {
                    "source_repository": metadata.get("source_repository"),
                    "source_ref": metadata.get("source_ref"),
                    "ra_weights_verified": metadata.get("ra_weights_verified"),
                },
            }
        )
    return result


def _unique_fork_slug(db: Session, source_slug: str, user_id: int) -> str:
    base = f"{source_slug}-u{user_id}"
    candidates = [base] + [f"{base}-{number}" for number in range(2, 100)]
    for candidate in candidates:
        if not db.scalar(select(Module.id).where(Module.slug == candidate)):
            return candidate
    raise HTTPException(
        status_code=409,
        detail="No se pudo generar un identificador único para la copia",
    )


@router.post("/catalog/modules/{module_id}/fork")
def fork_catalog_module(
    module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    source = db.get(Module, module_id)
    if (
        not source
        or not source.active
        or not (source.metadata_json or {}).get("catalog_shared")
    ):
        raise HTTPException(status_code=404, detail="Módulo de catálogo no encontrado")

    clone = Module(
        slug=_unique_fork_slug(db, source.slug, user_id),
        code=source.code,
        title=f"{source.title} · copia personal",
        description=source.description,
        module_type=source.module_type,
        version=source.version,
        metadata_json={
            **(source.metadata_json or {}),
            "catalog_shared": False,
            "derived_from_module_id": source.id,
            "derived_from_slug": source.slug,
            "personal_copy": True,
        },
        active=True,
    )
    db.add(clone)
    db.flush()
    db.add(
        ModulePermission(
            module_id=clone.id,
            user_id=user_id,
            permission="owner",
        )
    )

    lr_map: dict[int, LearningResult] = {}
    criterion_map: dict[int, AssessmentCriterion] = {}
    item_map: dict[int, AssessmentItem] = {}

    source_lrs = list(
        db.scalars(
            select(LearningResult)
            .where(LearningResult.module_id == source.id)
            .order_by(LearningResult.position, LearningResult.id)
        )
    )
    for old_lr in source_lrs:
        new_lr = LearningResult(
            module_id=clone.id,
            code=old_lr.code,
            title=old_lr.title,
            description=old_lr.description,
            position=old_lr.position,
            weight=old_lr.weight,
            metadata_json=dict(old_lr.metadata_json or {}),
            active=old_lr.active,
        )
        db.add(new_lr)
        db.flush()
        lr_map[old_lr.id] = new_lr

        source_criteria = list(
            db.scalars(
                select(AssessmentCriterion)
                .where(AssessmentCriterion.learning_result_id == old_lr.id)
                .order_by(AssessmentCriterion.position, AssessmentCriterion.id)
            )
        )
        for old_criterion in source_criteria:
            new_criterion = AssessmentCriterion(
                learning_result_id=new_lr.id,
                code=old_criterion.code,
                title=old_criterion.title,
                description=old_criterion.description,
                position=old_criterion.position,
                pass_score=old_criterion.pass_score,
                weight=old_criterion.weight,
                metadata_json=dict(old_criterion.metadata_json or {}),
                active=old_criterion.active,
            )
            db.add(new_criterion)
            db.flush()
            criterion_map[old_criterion.id] = new_criterion

            source_items = list(
                db.scalars(
                    select(AssessmentItem)
                    .where(AssessmentItem.criterion_id == old_criterion.id)
                    .order_by(AssessmentItem.instrument, AssessmentItem.position, AssessmentItem.id)
                )
            )
            for old_item in source_items:
                new_item = AssessmentItem(
                    criterion_id=new_criterion.id,
                    instrument=old_item.instrument,
                    item_key=old_item.item_key,
                    item_type=old_item.item_type,
                    prompt=old_item.prompt,
                    options_json=list(old_item.options_json or []),
                    public_hash=old_item.public_hash,
                    evaluable=old_item.evaluable,
                    max_attempts=old_item.max_attempts,
                    position=old_item.position,
                    metadata_json=dict(old_item.metadata_json or {}),
                    active=old_item.active,
                )
                db.add(new_item)
                db.flush()
                item_map[old_item.id] = new_item

                old_key = db.scalar(
                    select(AssessmentKey).where(
                        AssessmentKey.item_id == old_item.id,
                        AssessmentKey.active.is_(True),
                    )
                )
                if old_key:
                    db.add(
                        AssessmentKey(
                            item_id=new_item.id,
                            answer_json=dict(old_key.answer_json or {}),
                            feedback=old_key.feedback,
                            public_hash=old_key.public_hash,
                            source=f"fork:{source.slug}",
                            active=True,
                        )
                    )

    package_copies = 0
    source_links = db.execute(
        select(ModuleScormPackage, ScormPackage)
        .join(ScormPackage, ScormPackage.id == ModuleScormPackage.package_id)
        .where(
            ModuleScormPackage.module_id == source.id,
            ModuleScormPackage.active.is_(True),
            ScormPackage.active.is_(True),
        )
        .order_by(ModuleScormPackage.position, ModuleScormPackage.id)
    ).all()

    for old_link, old_package in source_links:
        package_copy = ScormPackage(
            module_id=clone.id,
            owner_user_id=user_id,
            title=old_package.title,
            description=old_package.description,
            original_filename=old_package.original_filename,
            version=old_package.version,
            standard=old_package.standard,
            entrypoint=old_package.entrypoint,
            storage_path=old_package.storage_path,
            sha256=old_package.sha256,
            manifest_json={
                **(old_package.manifest_json or {}),
                "derived_from_package_id": old_package.id,
            },
            visibility="private",
            revision_number=1,
            is_current=True,
            lifecycle_status="published",
            active=True,
        )
        db.add(package_copy)
        db.flush()
        package_copy.lineage_root_id = package_copy.id
        db.add(
            ModuleScormPackage(
                module_id=clone.id,
                package_id=package_copy.id,
                position=old_link.position,
                required=old_link.required,
                weight=old_link.weight,
                settings_json=dict(old_link.settings_json or {}),
                active=True,
            )
        )
        package_copies += 1

    db.commit()
    db.refresh(clone)
    return {
        "module_id": clone.id,
        "slug": clone.slug,
        "title": clone.title,
        "permission": "owner",
        "derived_from_module_id": source.id,
        "learning_results": len(lr_map),
        "criteria": len(criterion_map),
        "items": len(item_map),
        "scorm_packages": package_copies,
    }


@router.post("/catalog/modules/{module_id}/install")
def install_catalog_module(
    module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    module = db.get(Module, module_id)
    if not module or not module.active or not (module.metadata_json or {}).get("catalog_shared"):
        raise HTTPException(status_code=404, detail="Módulo de catálogo no encontrado")
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if not permission:
        permission = ModulePermission(
            module_id=module_id,
            user_id=user_id,
            permission="viewer",
        )
        db.add(permission)
        db.commit()
    return {
        "module_id": module_id,
        "title": module.title,
        "permission": permission.permission,
    }


@router.delete("/catalog/modules/{module_id}/install")
def uninstall_catalog_module(
    module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    user_id = int(session["sub"])
    permission = db.scalar(
        select(ModulePermission).where(
            ModulePermission.module_id == module_id,
            ModulePermission.user_id == user_id,
        )
    )
    if not permission:
        return {"ok": True}
    if permission.permission != "viewer":
        raise HTTPException(
            status_code=409,
            detail="Solo se puede retirar desde el catálogo un permiso de consulta",
        )
    assigned = db.scalar(
        select(CourseModule.id)
        .join(Module, Module.id == CourseModule.module_id)
        .where(
            CourseModule.module_id == module_id,
            CourseModule.active.is_(True),
        )
    )
    if assigned:
        raise HTTPException(
            status_code=409,
            detail="Retira el módulo de tus grupos antes de quitarlo del catálogo",
        )
    db.delete(permission)
    db.commit()
    return {"ok": True}


@router.post(
    "/admin/catalog/{folder}/import-metadata",
    dependencies=[Depends(require_admin)],
)
def admin_import_catalog_metadata(
    folder: str,
    db: Session = Depends(get_db),
) -> dict:
    return import_catalog_metadata(db, folder)


def _source_archive_url(manifest: dict) -> str:
    source = manifest.get("source") or {}
    repository = source.get("repository")
    ref = source.get("ref")
    if not repository or not ref:
        raise HTTPException(status_code=500, detail="El manifiesto no define repositorio/ref")
    return f"https://codeload.github.com/{repository}/tar.gz/{ref}"


def _build_scorm_zip_from_tar(tf: tarfile.TarFile, unit: str) -> bytes:
    needle = f"/grh0652/scorm/{unit}/"
    files: list[tuple[str, tarfile.TarInfo]] = []
    for member in tf.getmembers():
        name = "/" + member.name.lstrip("/")
        if not member.isfile() or needle not in name:
            continue
        rel = name.split(needle, 1)[1]
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            continue
        files.append((rel, member))
    if not files:
        raise HTTPException(status_code=502, detail=f"No se encontró {unit} en el repositorio fuente")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, member in sorted(files, key=lambda x: x[0]):
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            zf.writestr(rel, extracted.read())
    return out.getvalue()


@router.post(
    "/admin/catalog/gth0652/provision",
    dependencies=[Depends(require_admin)],
)
async def provision_gth0652(
    db: Session = Depends(get_db),
) -> dict:
    metadata_result = import_catalog_metadata(db, "gth0652")
    module = db.get(Module, metadata_result["module_id"])
    root = _module_dir("gth0652")
    manifest = _load_json(root / "module.json")
    url = _source_archive_url(manifest)

    try:
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            archive_bytes = response.content
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="No se pudo descargar la fuente fijada de GTH",
        ) from exc

    if len(archive_bytes) > 150 * 1024 * 1024:
        raise HTTPException(status_code=502, detail="El archivo fuente de GTH supera el límite")

    titles = {
        1: "GTH · RA1 · Gestión de la contratación laboral",
        2: "GTH · RA2 · Modificación, suspensión y extinción",
        3: "GTH · RA3 · Seguridad Social",
        4: "GTH · RA4 · Retribución, nóminas, cotización e IRPF",
    }
    packages = []
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        for number in range(1, 5):
            zip_bytes = _build_scorm_zip_from_tar(tf, f"ut{number}")
            upload = UploadFile(
                file=io.BytesIO(zip_bytes),
                filename=f"gth0652-ra{number}.zip",
            )
            package, deduplicated = await _store_scorm(
                file=upload,
                owner_user_id=None,
                db=db,
                title_override=titles[number],
                description="Paquete oficial migrado desde CFGSAF.",
                visibility="shared",
                legacy_module_id=module.id,
            )
            package.manifest_json = {
                **(package.manifest_json or {}),
                "catalog": "gth0652",
                "learning_result": f"RA{number}",
                "source_repository": (manifest.get("source") or {}).get("repository"),
                "source_ref": (manifest.get("source") or {}).get("ref"),
            }
            association = _attach_package(
                db,
                module.id,
                package.id,
                ScormAttachIn(
                    position=number,
                    required=True,
                    weight=1,
                    settings={"learning_result": f"RA{number}"},
                ),
            )
            packages.append(
                {
                    "id": package.id,
                    "title": package.title,
                    "standard": package.standard,
                    "sha256": package.sha256,
                    "deduplicated": deduplicated,
                    "association_id": association.id,
                }
            )
    db.commit()
    return {
        **metadata_result,
        "source_ref": (manifest.get("source") or {}).get("ref"),
        "packages": packages,
    }
