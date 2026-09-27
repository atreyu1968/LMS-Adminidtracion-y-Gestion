#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Falta .env" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

BASE_LOCAL="${LMS_VERIFY_URL:-http://127.0.0.1:8080}"

echo "1/5 Salud API y versión"
HEALTH="$(curl -fsS "${BASE_LOCAL}/api/health")"
printf '%s\n' "$HEALTH" | grep -q '"ok":true'
EXPECTED_VERSION="$(tr -d '[:space:]' < VERSION)"
printf '%s\n' "$HEALTH" | grep -q "\"version\":\"${EXPECTED_VERSION}\""

echo "2/5 JWKS LTI"
curl -fsS "${BASE_LOCAL}/lti/jwks" | grep -q '"keys"'

echo "3/5 Autodiagnóstico"
READINESS="$(curl -fsS -H "X-Admin-Token: ${LMS_ADMIN_TOKEN}" "${BASE_LOCAL}/api/admin/readiness")"
printf '%s\n' "$READINESS"
if ! printf '%s' "$READINESS" | grep -q '"code_ready":true'; then
  echo "La instalación no supera los controles internos." >&2
  exit 1
fi

echo "4/5 Contenedores"
docker compose ps

echo "5/5 Configuración Nginx"
docker compose exec -T web nginx -t

echo "OK: instalación interna operativa."
echo "Nota: campus_ready solo será true cuando exista HTTPS público y una plataforma LTI CAMPUS/Moodle registrada."
