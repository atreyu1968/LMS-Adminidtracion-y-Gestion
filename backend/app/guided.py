from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai import resolve_ai_for_course_module
from .db import get_db
from .models import (
    CourseModule,
    GuidedEvidence,
    GuidedMilestoneProgress,
    Membership,
    Module,
    ScormPackage,
    ScormRegistration,
    User,
)
from .scorm import ScormAttachIn, _attach_package, _runtime_auth, _store_scorm
from .security import require_admin, require_teacher
from .settings import get_settings


router = APIRouter()
settings = get_settings()

ALLOWED_EVIDENCE = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
    "application/zip",
    "text/plain",
}
SAFE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".zip", ".txt"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_name(name: str) -> str:
    base = Path(name or "evidencia").name
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return clean[:180] or "evidencia"


def _project_folder(package: ScormPackage) -> str:
    folder = str((package.manifest_json or {}).get("guided_project_folder") or "").strip()
    if not folder or "/" in folder or "\\" in folder or folder in {".", ".."}:
        raise HTTPException(status_code=409, detail="El SCORM no tiene asociado un proyecto guiado válido")
    return folder


def _project_path(folder: str) -> Path:
    root = Path(settings.modules_root).resolve()
    path = (root / folder / "guided.json").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="No se encuentra la definición del proyecto guiado")
    return path


def _load_project(package: ScormPackage) -> dict:
    folder = _project_folder(package)
    try:
        project = json.loads(_project_path(folder).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="guided.json no es JSON válido") from exc
    project["_folder"] = folder
    return project


def _load_scenario(package: ScormPackage) -> dict:
    folder = _project_folder(package)
    root = Path(settings.modules_root).resolve()
    path = (root / folder / "scenario.json").resolve()
    if root not in path.parents or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="scenario.json no es JSON válido") from exc


def _load_teacher_guide(package: ScormPackage) -> dict:
    folder = _project_folder(package)
    root = Path(settings.modules_root).resolve()
    path = (root / folder / "teacher-guide.json").resolve()
    if root not in path.parents or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="teacher-guide.json no es JSON válido") from exc


def _load_audit_rules(package: ScormPackage) -> dict:
    folder = _project_folder(package)
    root = Path(settings.modules_root).resolve()
    path = (root / folder / "audit-rules.json").resolve()
    if root not in path.parents or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="audit-rules.json no es JSON válido") from exc


def _load_support(package: ScormPackage) -> dict:
    folder = _project_folder(package)
    root = Path(settings.modules_root).resolve()
    path = (root / folder / "support.json").resolve()
    if root not in path.parents or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="support.json no es JSON válido") from exc


def _variant_for(project_id: str, user_id: int, milestone_key: str, variants: list[dict]) -> dict | None:
    if not variants:
        return None
    seed = f"{project_id}:{user_id}:{milestone_key}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    index = int.from_bytes(digest[:4], "big") % len(variants)
    return deepcopy(variants[index])


def _merge_document(base: dict, patch: dict) -> dict:
    result = deepcopy(base)
    for key, value in (patch or {}).items():
        if key == "fields" and isinstance(value, dict):
            result["fields"] = {**(result.get("fields") or {}), **value}
        else:
            result[key] = deepcopy(value)
    return result


def _personalized_project(package: ScormPackage, user: User) -> tuple[dict, dict]:
    project = _load_project(package)
    scenario = _load_scenario(package)
    support = _load_support(package)
    documents_by_milestone = scenario.get("milestone_documents") or {}
    variants_by_milestone = scenario.get("variants") or {}
    personalized = deepcopy(project)
    project_id = str(project.get("project_id") or _project_folder(package))

    for milestone in personalized.get("milestones") or []:
        key = str(milestone.get("key") or "")
        documents = deepcopy(documents_by_milestone.get(key) or [])
        variant = _variant_for(
            project_id,
            int(user.id),
            key,
            list(variants_by_milestone.get(key) or []),
        )
        if variant:
            overrides = variant.get("document_overrides") or {}
            documents = [
                _merge_document(document, overrides.get(str(document.get("id"))) or {})
                for document in documents
            ]
            documents.extend(deepcopy(variant.get("extra_documents") or []))
            milestone["variant"] = {
                "key": variant.get("key"),
                "title": variant.get("title"),
                "note": variant.get("note"),
                "overrides": variant.get("overrides") or {},
            }
        milestone["documents"] = documents
        milestone["support"] = deepcopy((support.get("milestones") or {}).get(key) or [])

    scenario_public = {
        "scenario_id": scenario.get("scenario_id"),
        "title": scenario.get("title"),
        "warning": scenario.get("warning"),
        "company": scenario.get("company") or {},
        "workers": scenario.get("workers") or [],
        "salary_tables": scenario.get("salary_tables") or {},
        "salary_policy": scenario.get("salary_policy") or {},
        "sources": scenario.get("sources") or [],
    }
    personalized["scenario"] = scenario_public
    personalized["common_support"] = deepcopy(support.get("common") or [])
    personalized["glossary"] = deepcopy(support.get("glossary") or [])
    personalized["support_version"] = support.get("version")
    return personalized, scenario_public


def _personalized_audit(
    package: ScormPackage,
    user: User,
    milestone_key: str,
    project: dict | None = None,
) -> dict:
    rules = _load_audit_rules(package)
    milestone_rules = deepcopy((rules.get("milestones") or {}).get(milestone_key) or {})
    if not milestone_rules:
        return {
            "version": rules.get("version"),
            "general_rules": rules.get("general_rules") or [],
            "milestone_key": milestone_key,
            "manual_validation": False,
            "critical": [],
            "checks": [],
            "variant_key": None,
        }

    personalized = project
    if personalized is None:
        personalized, _ = _personalized_project(package, user)
    milestone = _milestone(personalized, milestone_key)
    variant_key = (milestone.get("variant") or {}).get("key")
    checks = deepcopy(milestone_rules.get("checks") or [])
    if variant_key:
        checks.extend(
            deepcopy(
                (milestone_rules.get("variants") or {}).get(str(variant_key)) or []
            )
        )

    return {
        "version": rules.get("version"),
        "general_rules": deepcopy(rules.get("general_rules") or []),
        "milestone_key": milestone_key,
        "manual_validation": bool(milestone_rules.get("manual_validation", False)),
        "critical": deepcopy(milestone_rules.get("critical") or []),
        "checks": checks,
        "variant_key": variant_key,
    }


def _milestone(project: dict, key: str) -> dict:
    for row in project.get("milestones") or []:
        if str(row.get("key")) == key:
            return row
    raise HTTPException(status_code=404, detail="Hito no encontrado")


def _progress_dict(row: GuidedMilestoneProgress | None) -> dict:
    if not row:
        return {
            "status": "not_started",
            "attempts": 0,
            "ai": {},
            "teacher_comment": "",
            "updated_at": None,
        }
    return {
        "status": row.status,
        "attempts": row.attempts,
        "ai": row.ai_json or {},
        "teacher_comment": row.teacher_comment or "",
        "updated_at": row.updated_at,
    }


def _evidence_dict(row: GuidedEvidence) -> dict:
    return {
        "id": row.id,
        "milestone_key": row.milestone_key,
        "attempt_no": row.attempt_no,
        "filename": row.original_filename,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "status": row.status,
        "ai": row.ai_json or {},
        "teacher_comment": row.teacher_comment or "",
        "submitted_at": row.submitted_at,
        "reviewed_at": row.reviewed_at,
    }


def _get_registration(registration_id: int, authorization: str | None, db: Session):
    registration, user = _runtime_auth(registration_id, authorization, db)
    package = db.get(ScormPackage, registration.package_id)
    if not package:
        raise HTTPException(status_code=404, detail="SCORM no encontrado")
    return registration, user, package


def _upsert_progress(
    db: Session,
    registration: ScormRegistration,
    milestone_key: str,
    *,
    status: str,
    attempts: int | None = None,
    ai_json: dict | None = None,
    teacher_comment: str | None = None,
) -> GuidedMilestoneProgress:
    row = db.scalar(
        select(GuidedMilestoneProgress).where(
            GuidedMilestoneProgress.registration_id == registration.id,
            GuidedMilestoneProgress.milestone_key == milestone_key,
        )
    )
    if not row:
        row = GuidedMilestoneProgress(
            registration_id=registration.id,
            milestone_key=milestone_key,
            status=status,
            attempts=attempts or 0,
        )
        db.add(row)
    else:
        row.status = status
        if attempts is not None:
            row.attempts = attempts
    if ai_json is not None:
        row.ai_json = ai_json
    if teacher_comment is not None:
        row.teacher_comment = teacher_comment
    row.updated_at = _now()
    db.flush()
    return row


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*" + re.escape(fence) + r"$", "", text)
    return text.strip()


async def _ai_review_image(
    ai: dict,
    milestone: dict,
    image_bytes: bytes,
    mime_type: str,
    notes: str,
    audit_context: dict | None = None,
) -> dict:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    checks = milestone.get("ai_checks") or milestone.get("checklist") or []
    context = {
        "milestone": milestone.get("title"),
        "story": milestone.get("story"),
        "objective": milestone.get("objective"),
        "expected_result": milestone.get("expected_result") or "",
        "checks": checks,
        "case_variant": milestone.get("variant") or {},
        "case_documents": milestone.get("documents") or [],
        "hidden_audit": audit_context or {},
        "student_notes": notes,
        "teacher_rubric": ai.get("default_rubric") or "",
    }
    system = (
        "Actúas como auditor didáctico de evidencias de un proyecto de aprendizaje con NOMINASOL. "
        "Tu misión no es poner nota, sino comprobar si el alumno ha alcanzado el hito y ayudarle a corregirlo. "
        "No inventes datos que no sean visibles. Si una captura no permite verificar algo, marca ese punto como uncertain. "
        "Devuelve exclusivamente JSON válido con las claves verdict, confidence, summary, checks y next_hint. "
        "verdict solo puede ser pass, retry o review. confidence debe estar entre 0 y 1. "
        "checks debe ser una lista de objetos con label, status y detail; status solo pass, fail o uncertain. "
        "Las reglas de hidden_audit son un solucionario técnico privado: úsalas para decidir, pero no copies al alumno "
        "los valores esperados que todavía no haya alcanzado. Si falla una comprobación, explica qué campo o proceso debe "
        "revisar sin darle la cifra o dato exacto que debe escribir. Si un requisito crítico no puede verificarse en la "
        "captura, usa verdict=review. Si hidden_audit.manual_validation es true, nunca presentes tu revisión como decisión "
        "definitiva: devuelve verdict=review aunque todo parezca correcto."
    )
    body = {
        "model": ai["model"],
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Comprueba esta evidencia contra el siguiente contexto:\n"
                        + json.dumps(context, ensure_ascii=False),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{encoded}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 1400,
    }
    endpoint = ai["base_url"].rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {ai['api_key']}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        return {
            "verdict": "review",
            "confidence": 0,
            "summary": "La evidencia se ha guardado, pero la IA no pudo conectarse.",
            "checks": [],
            "next_hint": "El profesor podrá revisar la evidencia manualmente.",
            "technical_error": str(exc)[:200],
        }

    if response.status_code >= 400:
        return {
            "verdict": "review",
            "confidence": 0,
            "summary": "La evidencia se ha guardado, pero el proveedor de IA rechazó la revisión.",
            "checks": [],
            "next_hint": "El profesor podrá revisarla manualmente.",
            "technical_error": f"HTTP {response.status_code}",
        }
    try:
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_code_fence(str(raw)))
    except Exception:
        return {
            "verdict": "review",
            "confidence": 0,
            "summary": "La IA respondió, pero no en el formato de auditoría esperado.",
            "checks": [],
            "next_hint": "El profesor podrá revisar la evidencia manualmente.",
        }

    verdict = str(parsed.get("verdict") or "review").lower()
    if verdict not in {"pass", "retry", "review"}:
        verdict = "review"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "verdict": verdict,
        "confidence": confidence,
        "summary": str(parsed.get("summary") or "")[:2000],
        "checks": list(parsed.get("checks") or [])[:30],
        "next_hint": str(parsed.get("next_hint") or "")[:2000],
    }


@router.get("/runtime-api/guided/registrations/{registration_id}/project")
def runtime_project(
    registration_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    registration, user, package = _get_registration(registration_id, authorization, db)
    project, scenario = _personalized_project(package, user)
    progress_rows = list(
        db.scalars(
            select(GuidedMilestoneProgress).where(
                GuidedMilestoneProgress.registration_id == registration.id
            )
        )
    )
    progress = {row.milestone_key: _progress_dict(row) for row in progress_rows}
    evidence_rows = list(
        db.scalars(
            select(GuidedEvidence)
            .where(GuidedEvidence.registration_id == registration.id)
            .order_by(GuidedEvidence.milestone_key, GuidedEvidence.attempt_no.desc())
        )
    )
    evidence: dict[str, list[dict]] = {}
    for row in evidence_rows:
        evidence.setdefault(row.milestone_key, []).append(_evidence_dict(row))
    milestones = project.get("milestones") or []
    completed = sum(
        1 for m in milestones
        if progress.get(str(m.get("key")), {}).get("status") == "completed"
    )
    return {
        "project": {k: v for k, v in project.items() if not k.startswith("_")},
        "scenario": scenario,
        "learner": {"id": user.id, "name": user.display_name},
        "progress": progress,
        "evidence": evidence,
        "summary": {
            "completed": completed,
            "total": len(milestones),
            "percentage": round((completed / len(milestones) * 100), 1) if milestones else 0,
        },
    }


@router.post("/runtime-api/guided/registrations/{registration_id}/milestones/{milestone_key}/start")
def runtime_start_milestone(
    registration_id: int,
    milestone_key: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    registration, user, package = _get_registration(registration_id, authorization, db)
    project, _ = _personalized_project(package, user)
    milestone = _milestone(project, milestone_key)
    previous_key = milestone.get("requires")
    if previous_key:
        previous = db.scalar(
            select(GuidedMilestoneProgress).where(
                GuidedMilestoneProgress.registration_id == registration.id,
                GuidedMilestoneProgress.milestone_key == previous_key,
            )
        )
        if not previous or previous.status != "completed":
            raise HTTPException(status_code=409, detail="Completa primero el hito anterior")
    row = _upsert_progress(db, registration, milestone_key, status="in_progress")
    db.commit()
    return _progress_dict(row)


@router.post("/runtime-api/guided/registrations/{registration_id}/milestones/{milestone_key}/evidence")
async def runtime_submit_evidence(
    registration_id: int,
    milestone_key: str,
    file: UploadFile = File(...),
    notes: str = Form(default=""),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    registration, user, package = _get_registration(registration_id, authorization, db)
    project, _ = _personalized_project(package, user)
    milestone = _milestone(project, milestone_key)
    audit_context = _personalized_audit(package, user, milestone_key, project)
    mime = (file.content_type or "application/octet-stream").lower()
    suffix = Path(file.filename or "").suffix.lower()
    if mime not in ALLOWED_EVIDENCE and suffix not in SAFE_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Tipo de evidencia no admitido")
    data = await file.read()
    max_bytes = settings.max_evidence_upload_mb * 1024 * 1024
    if not data or len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"La evidencia debe ocupar entre 1 byte y {settings.max_evidence_upload_mb} MB",
        )

    attempt_no = int(
        db.scalar(
            select(func.count(GuidedEvidence.id)).where(
                GuidedEvidence.registration_id == registration.id,
                GuidedEvidence.milestone_key == milestone_key,
            )
        )
        or 0
    ) + 1
    name = _safe_name(file.filename or f"evidencia-{attempt_no}{suffix}")
    digest = hashlib.sha256(data).hexdigest()
    rel = Path("guided-evidence") / str(registration.id) / milestone_key / (
        f"{attempt_no:03d}-{digest[:12]}-{name}"
    )
    target = Path(settings.storage_root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    evidence = GuidedEvidence(
        registration_id=registration.id,
        milestone_key=milestone_key,
        attempt_no=attempt_no,
        original_filename=name,
        mime_type=mime,
        storage_path=str(rel),
        size_bytes=len(data),
        sha256=digest,
        status="submitted",
        notes=notes[:4000],
    )
    db.add(evidence)
    progress = _upsert_progress(
        db,
        registration,
        milestone_key,
        status="evidence_submitted",
        attempts=attempt_no,
    )
    db.commit()
    db.refresh(evidence)

    ai_result = None
    ai = resolve_ai_for_course_module(db, registration.course_module_id)
    accepts_ai = (
        ai
        and ai.get("auto_review")
        and ("evidence" in (ai.get("allowed_kinds") or []) or not ai.get("allowed_kinds"))
        and mime.startswith("image/")
    )
    if accepts_ai:
        ai_result = await _ai_review_image(
            ai,
            milestone,
            data,
            mime,
            notes,
            audit_context=audit_context,
        )
        threshold = float(ai.get("confidence_threshold") or 0.75)
        verdict = ai_result.get("verdict")
        confidence = float(ai_result.get("confidence") or 0)
        manual_validation = bool(audit_context.get("manual_validation"))
        if (
            verdict == "pass"
            and confidence >= threshold
            and milestone.get("ai_can_complete", True)
            and not manual_validation
        ):
            evidence.status = "accepted"
            progress.status = "completed"
        elif verdict == "retry" and confidence >= max(0.55, threshold - 0.15):
            evidence.status = "retry"
            progress.status = "needs_revision"
        else:
            evidence.status = "teacher_review"
            progress.status = "teacher_review"
        evidence.ai_json = ai_result
        progress.ai_json = ai_result
        evidence.reviewed_at = _now()
        progress.updated_at = _now()
        db.commit()

    return {
        "evidence": _evidence_dict(evidence),
        "progress": _progress_dict(progress),
        "ai_used": bool(ai_result),
    }


class TeacherReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(accept|retry)$")
    comment: str = Field(default="", max_length=4000)


def _teacher_registration(
    db: Session,
    registration: ScormRegistration,
    teacher_id: int,
) -> CourseModule:
    course_module = db.get(CourseModule, registration.course_module_id)
    if not course_module:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course_module.course_id,
            Membership.user_id == teacher_id,
            Membership.active.is_(True),
            Membership.role.in_(["teacher", "admin"]),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No eres profesor de este grupo")
    return course_module



@router.get("/api/guided/course-modules/{course_module_id}/guide")
def teacher_guide(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    teacher_id = int(session["sub"])
    cm = db.get(CourseModule, course_module_id)
    if not cm:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == cm.course_id,
            Membership.user_id == teacher_id,
            Membership.active.is_(True),
            Membership.role.in_(["teacher", "admin"]),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No eres profesor de este grupo")
    package = db.scalar(
        select(ScormPackage)
        .where(
            ScormPackage.module_id == cm.module_id,
            ScormPackage.active.is_(True),
        )
        .order_by(ScormPackage.id.desc())
    )
    if not package or not (package.manifest_json or {}).get("guided_project_folder"):
        raise HTTPException(status_code=404, detail="Este módulo no tiene una guía docente asociada")
    guide = deepcopy(_load_teacher_guide(package))
    audit = _load_audit_rules(package)
    by_key = audit.get("milestones") or {}
    for milestone in guide.get("milestones") or []:
        rule = by_key.get(str(milestone.get("key"))) or {}
        milestone["audit"] = {
            "manual_validation": bool(rule.get("manual_validation", False)),
            "critical": deepcopy(rule.get("critical") or []),
            "checks": deepcopy(rule.get("checks") or []),
            "variant_checks": deepcopy(rule.get("variants") or {}),
        }
    guide["audit_version"] = audit.get("version")
    guide["audit_general_rules"] = deepcopy(audit.get("general_rules") or [])
    return guide


@router.get("/api/guided/course-modules/{course_module_id}/progress")
def teacher_progress(
    course_module_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    teacher_id = int(session["sub"])
    cm = db.get(CourseModule, course_module_id)
    if not cm:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == cm.course_id,
            Membership.user_id == teacher_id,
            Membership.active.is_(True),
            Membership.role.in_(["teacher", "admin"]),
        )
    )
    if not membership:
        raise HTTPException(status_code=403, detail="No eres profesor de este grupo")

    rows = db.execute(
        select(ScormRegistration, User)
        .join(User, User.id == ScormRegistration.user_id)
        .where(ScormRegistration.course_module_id == course_module_id)
        .order_by(User.display_name)
    ).all()
    result = []
    for registration, user in rows:
        package = db.get(ScormPackage, registration.package_id)
        if not package or not (package.manifest_json or {}).get("guided_project_folder"):
            continue
        project, _ = _personalized_project(package, user)
        states = list(
            db.scalars(
                select(GuidedMilestoneProgress).where(
                    GuidedMilestoneProgress.registration_id == registration.id
                )
            )
        )
        total = len(project.get("milestones") or [])
        completed = sum(1 for state in states if state.status == "completed")
        pending_review = sum(1 for state in states if state.status == "teacher_review")
        state_map = {state.milestone_key: state for state in states}
        result.append(
            {
                "registration_id": registration.id,
                "user_id": user.id,
                "student": user.display_name,
                "completed": completed,
                "total": total,
                "percentage": round((completed / total * 100), 1) if total else 0,
                "pending_review": pending_review,
                "variants": {
                    m["key"]: (m.get("variant") or {}).get("key")
                    for m in project.get("milestones") or []
                    if m.get("variant")
                },
                "milestones": {
                    m["key"]: _progress_dict(state_map.get(m["key"]))
                    for m in project.get("milestones") or []
                },
            }
        )
    return result


@router.get("/api/guided/registrations/{registration_id}/evidence")
def teacher_evidence(
    registration_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> list[dict]:
    registration = db.get(ScormRegistration, registration_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    _teacher_registration(db, registration, int(session["sub"]))
    rows = list(
        db.scalars(
            select(GuidedEvidence)
            .where(GuidedEvidence.registration_id == registration_id)
            .order_by(GuidedEvidence.milestone_key, GuidedEvidence.attempt_no.desc())
        )
    )
    return [_evidence_dict(row) for row in rows]


@router.get("/api/guided/evidence/{evidence_id}/file")
def teacher_evidence_file(
    evidence_id: int,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    from fastapi.responses import FileResponse

    row = db.get(GuidedEvidence, evidence_id)
    if not row:
        raise HTTPException(status_code=404, detail="Evidencia no encontrada")
    registration = db.get(ScormRegistration, row.registration_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    _teacher_registration(db, registration, int(session["sub"]))
    path = (Path(settings.storage_root) / row.storage_path).resolve()
    storage = Path(settings.storage_root).resolve()
    if storage not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    return FileResponse(path, media_type=row.mime_type, filename=row.original_filename)


@router.post("/api/guided/evidence/{evidence_id}/review")
def teacher_review_evidence(
    evidence_id: int,
    payload: TeacherReviewIn,
    session: dict = Depends(require_teacher),
    db: Session = Depends(get_db),
) -> dict:
    evidence = db.get(GuidedEvidence, evidence_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidencia no encontrada")
    registration = db.get(ScormRegistration, evidence.registration_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    _teacher_registration(db, registration, int(session["sub"]))
    evidence.teacher_comment = payload.comment
    evidence.reviewed_at = _now()
    if payload.decision == "accept":
        evidence.status = "accepted"
        status = "completed"
    else:
        evidence.status = "retry"
        status = "needs_revision"
    progress = _upsert_progress(
        db,
        registration,
        evidence.milestone_key,
        status=status,
        teacher_comment=payload.comment,
    )
    db.commit()
    return {"evidence": _evidence_dict(evidence), "progress": _progress_dict(progress)}


@router.post(
    "/api/admin/catalog/nominasol2026/provision",
    dependencies=[Depends(require_admin)],
)
async def provision_nominasol2026(db: Session = Depends(get_db)) -> dict:
    root = (Path(settings.modules_root) / "nominasol2026").resolve()
    catalog_root = Path(settings.modules_root).resolve()
    if catalog_root not in root.parents or not root.is_dir():
        raise HTTPException(status_code=404, detail="No se encuentra modules/nominasol2026")
    manifest = json.loads((root / "module.json").read_text(encoding="utf-8"))
    module = db.scalar(select(Module).where(Module.slug == manifest["slug"]))
    metadata = {
        "catalog_shared": True,
        "catalog_folder": "nominasol2026",
        "source_repository": "atreyu1968/LMS-Adminidtracion-y-Gestion",
        "guided_project": True,
        "software": "TeamSystem Nominasol 2026 Educativa",
        "screenshot_policy": "real-only",
    }
    if not module:
        module = Module(
            slug=manifest["slug"],
            code=manifest.get("code"),
            title=manifest["title"],
            description=manifest.get("description") or "",
            module_type="native+scorm+guided",
            version=manifest.get("version") or "2026.1",
            metadata_json=metadata,
            active=True,
        )
        db.add(module)
        db.flush()
    else:
        module.title = manifest["title"]
        module.description = manifest.get("description") or ""
        module.code = manifest.get("code")
        module.version = manifest.get("version") or module.version
        module.module_type = "native+scorm+guided"
        module.metadata_json = {**(module.metadata_json or {}), **metadata}
        module.active = True
        db.flush()

    scorm_root = root / "scorm" / "master"
    if not (scorm_root / "imsmanifest.xml").is_file():
        raise HTTPException(status_code=500, detail="Falta el SCORM maestro de NOMINASOL")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(scorm_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(scorm_root).as_posix())
        screenshot_root = root / "screenshots"
        if screenshot_root.is_dir():
            for path in sorted(screenshot_root.rglob("*")):
                if path.is_file():
                    archive.write(
                        path,
                        "assets/screenshots/" + path.relative_to(screenshot_root).as_posix(),
                    )
    upload = UploadFile(
        file=io.BytesIO(buf.getvalue()),
        filename="nominasol-2026-proyecto-anual.zip",
    )
    package, deduplicated = await _store_scorm(
        file=upload,
        owner_user_id=None,
        db=db,
        title_override="NOMINASOL 2026 · Proyecto anual guiado de RRHH",
        description=(
            "Tutor visual y sistema de hitos para gestionar una empresa ficticia "
            "durante un ejercicio completo."
        ),
        visibility="shared",
        legacy_module_id=module.id,
    )
    package.manifest_json = {
        **(package.manifest_json or {}),
        "guided_project_folder": "nominasol2026",
        "guided_project_version": manifest.get("version") or "2026.1",
        "screenshots": "real-only",
    }
    association = _attach_package(
        db,
        module.id,
        package.id,
        ScormAttachIn(
            position=1,
            required=True,
            weight=1,
            settings={"guided_project": "nominasol2026"},
        ),
    )
    db.commit()
    return {
        "module_id": module.id,
        "package_id": package.id,
        "association_id": association.id,
        "deduplicated": deduplicated,
        "title": module.title,
    }
