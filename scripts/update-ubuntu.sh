#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

NO_PULL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-pull) NO_PULL=1; shift ;;
    -h|--help)
      echo "Uso: sudo ./scripts/update-ubuntu.sh [--no-pull]"
      exit 0
      ;;
    *) echo "Opción desconocida: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Ejecuta la actualización con sudo/root." >&2
  exit 1
fi
[[ -f .env ]] || { echo "Falta .env" >&2; exit 1; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="backups/pre-update-${STAMP}"

echo "1/5 Copia previa: $BACKUP_DIR"
bash ./scripts/backup.sh "$BACKUP_DIR"

if [[ $NO_PULL -ne 1 ]]; then
  echo "2/5 Actualizando código"
  git fetch --all --prune
  git pull --ff-only
else
  echo "2/5 Código local conservado (--no-pull)"
fi

echo "3/5 Reconstruyendo servicios"
docker compose pull --ignore-buildable || true
docker compose up -d --build

echo "4/5 Esperando migración y salud"
READY=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done
if [[ $READY -ne 1 ]]; then
  echo "La actualización no ha levantado correctamente." >&2
  docker compose ps >&2 || true
  docker compose logs --tail=160 api web >&2 || true
  echo "Puedes restaurar con:" >&2
  echo "  sudo bash scripts/restore.sh '$BACKUP_DIR' --yes" >&2
  exit 1
fi

echo "5/5 Verificación"
if ! bash ./scripts/verify-installation.sh; then
  echo "La actualización no supera la verificación." >&2
  echo "Puedes restaurar con:" >&2
  echo "  sudo bash scripts/restore.sh '$BACKUP_DIR' --yes" >&2
  exit 1
fi

echo
echo "Actualización completada correctamente."
echo "Copia previa conservada en: $BACKUP_DIR"
