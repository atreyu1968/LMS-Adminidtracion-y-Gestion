#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKUP_DIR=""
ASSUME_YES=0

usage() {
  cat <<'EOF'
Uso:
  sudo ./scripts/restore.sh BACKUP_DIR [--yes]

Restaura:
  - base de datos PostgreSQL;
  - volumen /data;
  - secretos criptográficos esenciales del LMS.

Conserva la URL pública y las credenciales PostgreSQL de la instalación destino.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ -z "$BACKUP_DIR" ]]; then BACKUP_DIR="$1"; shift
      else echo "Argumento no reconocido: $1" >&2; usage; exit 2
      fi
      ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Ejecuta la restauración con sudo/root." >&2
  exit 1
fi
if [[ -z "$BACKUP_DIR" ]]; then
  usage
  exit 2
fi
BACKUP_DIR="$(readlink -f "$BACKUP_DIR")"
for file in database.sql.gz storage.tar.gz env.secret SHA256SUMS; do
  [[ -f "$BACKUP_DIR/$file" ]] || { echo "Falta $file en $BACKUP_DIR" >&2; exit 1; }
done
[[ -f .env ]] || { echo "Falta .env en la instalación destino." >&2; exit 1; }

echo "Verificando integridad..."
(
  cd "$BACKUP_DIR"
  sha256sum -c SHA256SUMS
)

if [[ $ASSUME_YES -ne 1 ]]; then
  echo
  echo "ATENCIÓN: se sustituirán la base de datos y /data de esta instalación."
  read -r -p "Escribe RESTAURAR para continuar: " CONFIRM
  [[ "$CONFIRM" == "RESTAURAR" ]] || { echo "Cancelado."; exit 1; }
fi

# Preserve destination database/network settings but recover encryption/session/admin
# secrets required to read protected data from the backup.
python3 - "$BACKUP_DIR/env.secret" ".env" <<'PY'
from pathlib import Path
import sys

backup_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])

def parse(path):
    result = {}
    order = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            order.append((None, raw))
            continue
        key, value = raw.split("=", 1)
        result[key] = value
        order.append((key, None))
    return result, order

backup, _ = parse(backup_path)
current, order = parse(target_path)
restore_keys = {
    "LMS_AI_ENCRYPTION_SECRET",
    "LMS_SESSION_SECRET",
    "LMS_ADMIN_TOKEN",
}
for key in restore_keys:
    if backup.get(key):
        current[key] = backup[key]

seen = set()
out = []
for key, raw in order:
    if key is None:
        out.append(raw)
        continue
    if key in seen:
        continue
    seen.add(key)
    out.append(f"{key}={current.get(key, '')}")
for key in sorted(current):
    if key not in seen:
        out.append(f"{key}={current[key]}")
target_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
chmod 600 .env

set -a
# shellcheck disable=SC1091
source .env
set +a

echo "Deteniendo servicios de aplicación..."
docker compose stop api web || true

echo "Arrancando PostgreSQL..."
docker compose up -d db
for _ in $(seq 1 60); do
  if docker compose exec -T db pg_isready -U "${POSTGRES_USER:-lms}" -d "${POSTGRES_DB:-lms}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose exec -T db pg_isready -U "${POSTGRES_USER:-lms}" -d "${POSTGRES_DB:-lms}"

echo "Restaurando base de datos..."
gunzip -c "$BACKUP_DIR/database.sql.gz" |   docker compose exec -T db psql     -v ON_ERROR_STOP=1     -U "${POSTGRES_USER:-lms}"     -d "${POSTGRES_DB:-lms}" >/dev/null

echo "Restaurando almacenamiento..."
docker compose run --rm -T --no-deps api   sh -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
cat "$BACKUP_DIR/storage.tar.gz" |   docker compose run --rm -T --no-deps api tar -C /data -xzf -

echo "Arrancando y aplicando migraciones actuales..."
docker compose up -d --build

READY=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done
if [[ $READY -ne 1 ]]; then
  echo "La instalación restaurada no respondió." >&2
  docker compose ps >&2 || true
  docker compose logs --tail=160 api web >&2 || true
  exit 1
fi

bash ./scripts/verify-installation.sh

echo
echo "Restauración completada y verificada."
echo "Se han recuperado LMS_AI_ENCRYPTION_SECRET, LMS_SESSION_SECRET y LMS_ADMIN_TOKEN."
echo "Se han conservado la URL pública y las credenciales PostgreSQL de esta instalación."
