from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    LTIPlatform,
    Module,
    ScormPackage,
    TeacherAISettings,
)
from .security import require_admin
from .settings import get_settings
from .version import __version__


router = APIRouter(prefix="/api/admin")
settings = get_settings()


def _check(name: str, ok: bool, detail: str, *, external: bool = False) -> dict:
    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "external": external,
    }


@router.get("/readiness", dependencies=[Depends(require_admin)])
def installation_readiness(db: Session = Depends(get_db)) -> dict:
    checks = []

    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_detail = "Conexión con la base de datos correcta"
    except Exception as exc:
        db_ok = False
        db_detail = f"Error de base de datos: {type(exc).__name__}"
    checks.append(_check("database", db_ok, db_detail))

    storage = Path(settings.storage_root)
    try:
        storage.mkdir(parents=True, exist_ok=True)
        probe = storage / ".lms-readiness-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        storage_ok = True
        storage_detail = f"Almacenamiento escribible: {storage}"
    except Exception as exc:
        storage_ok = False
        storage_detail = f"Almacenamiento no escribible: {type(exc).__name__}"
    checks.append(_check("storage", storage_ok, storage_detail))

    key_path = Path(settings.lti_private_key_path)
    key_ok = key_path.is_file() and key_path.stat().st_size > 100
    checks.append(
        _check(
            "lti_private_key",
            key_ok,
            f"Clave LTI: {key_path}" if key_ok else f"Falta una clave LTI válida en {key_path}",
        )
    )

    weak_session = settings.session_secret in {
        "dev-only-change-me",
        "CHANGE_ME_WITH_A_LONG_RANDOM_SECRET",
        "",
    } or len(settings.session_secret) < 32
    checks.append(
        _check(
            "session_secret",
            not weak_session,
            "Secreto de sesión robusto" if not weak_session else "Cambia LMS_SESSION_SECRET por un valor aleatorio de 32+ caracteres",
        )
    )

    weak_admin = settings.admin_token in {
        "dev-admin-change-me",
        "CHANGE_ME_WITH_A_LONG_RANDOM_ADMIN_TOKEN",
        "",
    } or len(settings.admin_token) < 32
    checks.append(
        _check(
            "admin_token",
            not weak_admin,
            "Token administrativo robusto" if not weak_admin else "Cambia LMS_ADMIN_TOKEN por un valor aleatorio de 32+ caracteres",
        )
    )

    ai_rows = db.scalar(select(func.count(TeacherAISettings.id))) or 0
    ai_secret_ok = bool(settings.ai_encryption_secret and len(settings.ai_encryption_secret) >= 32)
    checks.append(
        _check(
            "ai_encryption",
            ai_secret_ok or ai_rows == 0,
            (
                "Secreto independiente de cifrado de IA configurado"
                if ai_secret_ok
                else (
                    "No hay credenciales de IA almacenadas; configura LMS_AI_ENCRYPTION_SECRET antes de usar IA"
                    if ai_rows == 0
                    else "Hay credenciales de IA y falta un LMS_AI_ENCRYPTION_SECRET robusto"
                )
            ),
        )
    )

    modules_root = Path(settings.modules_root)
    catalog_ok = modules_root.is_dir()
    checks.append(
        _check(
            "catalog",
            catalog_ok,
            f"Catálogo montado en {modules_root}" if catalog_ok else f"No se encuentra el catálogo en {modules_root}",
        )
    )

    packages = db.scalar(
        select(func.count(ScormPackage.id)).where(
            ScormPackage.active.is_(True)
        )
    ) or 0
    checks.append(
        _check(
            "scorm_runtime",
            True,
            f"Runtime activo; {int(packages)} paquete(s) SCORM registrados",
        )
    )

    modules = db.scalar(
        select(func.count(Module.id)).where(Module.active.is_(True))
    ) or 0
    checks.append(
        _check(
            "module_engine",
            True,
            f"{int(modules)} módulo(s) activo(s)",
        )
    )

    platforms = db.scalar(
        select(func.count(LTIPlatform.id)).where(LTIPlatform.active.is_(True))
    ) or 0
    checks.append(
        _check(
            "lti_platform",
            platforms > 0,
            (
                f"{int(platforms)} plataforma(s) LTI registrada(s)"
                if platforms
                else "Todavía no hay una plataforma CAMPUS/Moodle registrada"
            ),
            external=True,
        )
    )

    public_https = settings.base_url.startswith("https://")
    checks.append(
        _check(
            "public_https",
            public_https,
            (
                f"URL pública HTTPS: {settings.base_url}"
                if public_https
                else f"Producción requiere HTTPS; URL actual: {settings.base_url}"
            ),
            external=True,
        )
    )

    internal = [row for row in checks if not row["external"]]
    external = [row for row in checks if row["external"]]
    code_ready = all(row["ok"] for row in internal)
    campus_ready = code_ready and all(row["ok"] for row in external)

    return {
        "version": __version__,
        "ok": code_ready,
        "code_ready": code_ready,
        "campus_ready": campus_ready,
        "checks": checks,
        "summary": {
            "internal_passed": sum(1 for row in internal if row["ok"]),
            "internal_total": len(internal),
            "external_passed": sum(1 for row in external if row["ok"]),
            "external_total": len(external),
        },
        "note": (
            "code_ready valida la instalación bajo control de este repositorio. "
            "campus_ready también exige URL HTTPS y una plataforma LTI registrada; "
            "la autorización institucional de CAMPUS se verifica fuera del LMS."
        ),
    }
