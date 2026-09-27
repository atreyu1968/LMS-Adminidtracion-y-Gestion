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

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${1:-backups/${STAMP}}"
mkdir -p "$DEST"
chmod 700 "$DEST"

echo "Guardando PostgreSQL..."
docker compose exec -T db pg_dump   -U "${POSTGRES_USER:-lms}"   -d "${POSTGRES_DB:-lms}"   --clean --if-exists --no-owner --no-privileges   | gzip -9 > "$DEST/database.sql.gz"

echo "Guardando almacenamiento del LMS..."
docker compose exec -T api tar -C /data -czf - . > "$DEST/storage.tar.gz"

echo "Guardando configuración sensible..."
cp .env "$DEST/env.secret"
chmod 600 "$DEST/env.secret"
cp VERSION "$DEST/VERSION" 2>/dev/null || true

(
  cd "$DEST"
  sha256sum database.sql.gz storage.tar.gz env.secret > SHA256SUMS
)

echo "Copia completada: $DEST"
echo "IMPORTANTE: env.secret contiene credenciales; protege el directorio."
