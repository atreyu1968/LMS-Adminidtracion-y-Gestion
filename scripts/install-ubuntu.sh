#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PUBLIC_URL="${LMS_PUBLIC_BASE_URL:-}"
CLOUDFLARE_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"
WITH_CLOUDFLARE=0
PROVISION=1

usage() {
  cat <<'EOF'
Uso:
  sudo ./scripts/install-ubuntu.sh --url https://lms.ejemplo.es [--cloudflare-token TOKEN] [--no-provision]

Opciones:
  --url URL                URL pública HTTPS del LMS.
  --cloudflare-token TOKEN Token de un túnel Cloudflare ya creado.
  --no-provision           No aprovisionar GTH/NOMINASOL tras arrancar.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) PUBLIC_URL="$2"; shift 2 ;;
    --cloudflare-token) CLOUDFLARE_TOKEN="$2"; WITH_CLOUDFLARE=1; shift 2 ;;
    --no-provision) PROVISION=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Ejecuta este instalador con sudo/root." >&2
  exit 1
fi

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Debes indicar --url https://tu-dominio" >&2
  exit 2
fi
if [[ "$PUBLIC_URL" != https://* && "$PUBLIC_URL" != http://localhost* ]]; then
  echo "En producción la URL debe ser HTTPS." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl openssl git

if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y docker.io
fi
systemctl enable --now docker

if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
fi

rand_hex() { openssl rand -hex 32; }

if [[ ! -f .env ]]; then
  SESSION_SECRET="$(rand_hex)"
  ADMIN_TOKEN="$(rand_hex)"
  AI_SECRET="$(rand_hex)"
  DB_PASSWORD="$(rand_hex)"
  cat > .env <<EOF
LMS_DATABASE_URL=postgresql+psycopg://lms:${DB_PASSWORD}@db:5432/lms
LMS_PUBLIC_BASE_URL=${PUBLIC_URL}
LMS_AI_ENCRYPTION_SECRET=${AI_SECRET}
LMS_SESSION_SECRET=${SESSION_SECRET}
LMS_ADMIN_TOKEN=${ADMIN_TOKEN}
LMS_LTI_PRIVATE_KEY_PATH=/data/keys/lti-private.pem
LMS_MODULES_ROOT=/modules
LMS_STORAGE_ROOT=/data
LMS_SCORM_CONTENT_BASE_URL=${PUBLIC_URL}/scorm-content
LMS_MAX_SCORM_UPLOAD_MB=512
LMS_MAX_MEDIA_UPLOAD_MB=2048
LMS_MAX_EVIDENCE_UPLOAD_MB=256
LMS_CORS_ORIGINS=${PUBLIC_URL}
POSTGRES_DB=lms
POSTGRES_USER=lms
POSTGRES_PASSWORD=${DB_PASSWORD}
EOF
  chmod 600 .env
else
  echo ".env ya existe; se conserva sin sobrescribir."
fi

if [[ -n "$CLOUDFLARE_TOKEN" ]]; then
  if grep -q '^CLOUDFLARE_TUNNEL_TOKEN=' .env; then
    sed -i "s#^CLOUDFLARE_TUNNEL_TOKEN=.*#CLOUDFLARE_TUNNEL_TOKEN=${CLOUDFLARE_TOKEN}#" .env
  else
    printf '\nCLOUDFLARE_TUNNEL_TOKEN=%s\n' "$CLOUDFLARE_TOKEN" >> .env
  fi
  chmod 600 .env
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE=(docker compose -f docker-compose.yml)
if [[ $WITH_CLOUDFLARE -eq 1 || -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  COMPOSE+=(-f docker-compose.cloudflare.yml)
fi

"${COMPOSE[@]}" pull --ignore-buildable || true
"${COMPOSE[@]}" up -d --build

echo "Esperando a que el LMS responda..."
READY=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done
if [[ $READY -ne 1 ]]; then
  echo "El LMS no respondió a tiempo." >&2
  "${COMPOSE[@]}" ps >&2 || true
  "${COMPOSE[@]}" logs --tail=120 api web >&2 || true
  exit 1
fi

if [[ $PROVISION -eq 1 ]]; then
  echo "Aprovisionando catálogo público..."
  curl -fsS -X POST -H "X-Admin-Token: ${LMS_ADMIN_TOKEN}"     http://127.0.0.1:8080/api/admin/catalog/gth0652/provision >/dev/null     || echo "Aviso: no se pudo aprovisionar GTH automáticamente; puede repetirse desde administración."
  curl -fsS -X POST -H "X-Admin-Token: ${LMS_ADMIN_TOKEN}"     http://127.0.0.1:8080/api/admin/catalog/nominasol2026/provision >/dev/null     || echo "Aviso: no se pudo aprovisionar NOMINASOL automáticamente."
fi

bash ./scripts/verify-installation.sh

echo
echo "Instalación completada."
echo "URL pública configurada: ${LMS_PUBLIC_BASE_URL}"
if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  echo "Cloudflare Tunnel está habilitado. En Cloudflare, el hostname debe apuntar al servicio http://web:8080."
fi
echo "Conserva .env con permisos restringidos; contiene secretos."
