#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker no está instalado." >&2
  exit 1
fi

read -r -p "Nombre del administrador: " ADMIN_NAME
read -r -p "Correo/usuario de acceso: " ADMIN_EMAIL
read -r -s -p "Contraseña (mínimo 10 caracteres): " ADMIN_PASSWORD
echo
read -r -s -p "Repite la contraseña: " ADMIN_PASSWORD_2
echo

if [[ -z "${ADMIN_NAME// }" ]]; then
  echo "ERROR: El nombre no puede estar vacío." >&2
  exit 1
fi
if [[ -z "${ADMIN_EMAIL// }" ]]; then
  echo "ERROR: El correo/usuario no puede estar vacío." >&2
  exit 1
fi
if (( ${#ADMIN_PASSWORD} < 10 )); then
  echo "ERROR: La contraseña debe tener al menos 10 caracteres." >&2
  exit 1
fi
if [[ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD_2" ]]; then
  echo "ERROR: Las contraseñas no coinciden." >&2
  exit 1
fi

docker compose exec -T \
  -e BOOTSTRAP_ADMIN_NAME="$ADMIN_NAME" \
  -e BOOTSTRAP_ADMIN_EMAIL="$ADMIN_EMAIL" \
  -e BOOTSTRAP_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  api python - <<'PY'
import os
from sqlalchemy import func, select

from app.auth import _hash_password
from app.db import SessionLocal
from app.models import Course, LocalCredential, Membership, User

name = os.environ["BOOTSTRAP_ADMIN_NAME"].strip()
email = os.environ["BOOTSTRAP_ADMIN_EMAIL"].strip()
password = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
normalized = email.casefold()

with SessionLocal() as db:
    user = db.scalar(select(User).where(func.lower(User.email) == normalized))
    if user is None:
        user = User(display_name=name, email=email, active=True)
        db.add(user)
        db.flush()
        created_user = True
    else:
        user.display_name = name
        user.email = email
        user.active = True
        created_user = False

    credential = db.scalar(select(LocalCredential).where(LocalCredential.user_id == user.id))
    collision = db.scalar(
        select(LocalCredential).where(
            LocalCredential.login_normalized == normalized,
            LocalCredential.user_id != user.id,
        )
    )
    if collision is not None:
        raise SystemExit("ERROR: ese usuario de acceso ya pertenece a otra cuenta.")

    if credential is None:
        credential = LocalCredential(
            user_id=user.id,
            login_display=email,
            login_normalized=normalized,
            password_hash=_hash_password(password),
        )
        db.add(credential)
    else:
        credential.login_display = email
        credential.login_normalized = normalized
        credential.password_hash = _hash_password(password)
        credential.failed_attempts = 0
        credential.locked_until = None

    course = db.scalar(
        select(Course).where(
            Course.platform_issuer == "local://lms-administracion-gestion",
            Course.context_id == "local:system-admin",
        )
    )
    if course is None:
        course = Course(
            owner_user_id=user.id,
            platform_issuer="local://lms-administracion-gestion",
            context_id="local:system-admin",
            source_type="local",
            title="Administración del LMS",
            label="ADMIN",
            description="Contexto local reservado para la administración del sistema.",
            settings_json={
                "allow_self_enrol": False,
                "show_scores": False,
                "max_attempts_default": 0,
                "system_admin_context": True,
            },
            active=True,
        )
        db.add(course)
        db.flush()
    else:
        course.owner_user_id = user.id
        course.active = True

    membership = db.scalar(
        select(Membership).where(
            Membership.course_id == course.id,
            Membership.user_id == user.id,
        )
    )
    if membership is None:
        membership = Membership(
            course_id=course.id,
            user_id=user.id,
            role="admin",
            lti_roles=[],
            active=True,
        )
        db.add(membership)
    else:
        membership.role = "admin"
        membership.active = True

    db.commit()
    print("OK: administrador " + ("creado" if created_user else "actualizado"))
    print("Usuario ID:", user.id)
    print("Acceso:", email)
    print("Contexto admin ID:", course.id)
PY

unset ADMIN_PASSWORD ADMIN_PASSWORD_2
echo
echo "Acceso directo:"
echo "  /login.html"
