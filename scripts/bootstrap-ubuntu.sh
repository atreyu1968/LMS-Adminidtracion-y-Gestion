#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${LMS_INSTALL_REPOSITORY:-https://github.com/atreyu1968/LMS-Adminidtracion-y-Gestion.git}"
BRANCH="${LMS_INSTALL_BRANCH:-main}"
INSTALL_DIR="${LMS_INSTALL_DIR:-/opt/lms-administracion-y-gestion}"
CONFIG_FILE=""
PUBLIC_URL="${LMS_PUBLIC_BASE_URL:-}"
CLOUDFLARE_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"
CLOUDFLARE_TOKEN_FILE=""
PROVISION=1

usage() {
  cat <<'EOF'
Instalación desatendida completa desde un Ubuntu limpio.

Uso:
  sudo bash bootstrap-ubuntu.sh --url https://lms.ejemplo.es [opciones]

Opciones:
  --url URL                    URL pública HTTPS.
  --config FICHERO             Carga variables desde un fichero shell/env.
  --cloudflare-token TOKEN     Token de un Cloudflare Tunnel existente.
  --cloudflare-token-file FILE Lee el token desde un fichero (recomendado).
  --install-dir DIR            Directorio destino. Por defecto:
                               /opt/lms-administracion-y-gestion
  --repository URL             Repositorio Git alternativo.
  --branch REF                 Rama/tag/commit a instalar. Por defecto: main.
  --no-provision               No aprovisionar GTH/NOMINASOL.
  -h, --help                   Mostrar esta ayuda.

Variables admitidas en --config:
  LMS_PUBLIC_BASE_URL
  CLOUDFLARE_TUNNEL_TOKEN
  LMS_INSTALL_DIR
  LMS_INSTALL_REPOSITORY
  LMS_INSTALL_BRANCH
  LMS_INSTALL_PROVISION=0|1

El script no solicita datos de forma interactiva.
EOF
}

if [[ $EUID -ne 0 ]]; then
  echo "Ejecuta el bootstrap con sudo/root." >&2
  exit 1
fi

# First pass: load configuration before evaluating other values.
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "--config" ]]; then
    [[ $((i+1)) -lt ${#args[@]} ]] || { echo "Falta fichero tras --config" >&2; exit 2; }
    CONFIG_FILE="${args[$((i+1))]}"
    [[ -f "$CONFIG_FILE" ]] || { echo "No existe $CONFIG_FILE" >&2; exit 2; }
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
  fi
done

REPOSITORY="${LMS_INSTALL_REPOSITORY:-$REPOSITORY}"
BRANCH="${LMS_INSTALL_BRANCH:-$BRANCH}"
INSTALL_DIR="${LMS_INSTALL_DIR:-$INSTALL_DIR}"
PUBLIC_URL="${LMS_PUBLIC_BASE_URL:-$PUBLIC_URL}"
CLOUDFLARE_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-$CLOUDFLARE_TOKEN}"
PROVISION="${LMS_INSTALL_PROVISION:-$PROVISION}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) PUBLIC_URL="$2"; shift 2 ;;
    --config) shift 2 ;;
    --cloudflare-token) CLOUDFLARE_TOKEN="$2"; shift 2 ;;
    --cloudflare-token-file) CLOUDFLARE_TOKEN_FILE="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --repository) REPOSITORY="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --no-provision) PROVISION=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -n "$CLOUDFLARE_TOKEN_FILE" ]]; then
  [[ -r "$CLOUDFLARE_TOKEN_FILE" ]] || {
    echo "No se puede leer $CLOUDFLARE_TOKEN_FILE" >&2
    exit 2
  }
  CLOUDFLARE_TOKEN="$(tr -d '\r\n' < "$CLOUDFLARE_TOKEN_FILE")"
fi

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Debes indicar --url o LMS_PUBLIC_BASE_URL en el fichero de configuración." >&2
  exit 2
fi
if [[ "$PUBLIC_URL" != https://* && "$PUBLIC_URL" != http://localhost* ]]; then
  echo "La URL pública debe usar HTTPS en producción." >&2
  exit 2
fi
if [[ "$PROVISION" != "0" && "$PROVISION" != "1" ]]; then
  echo "LMS_INSTALL_PROVISION debe valer 0 o 1." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git

if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
  echo "El destino existe y no es un clon Git válido: $INSTALL_DIR" >&2
  exit 1
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "Ya existe una instalación en $INSTALL_DIR." >&2
  echo "Para actualizarla usa: sudo bash $INSTALL_DIR/scripts/update-ubuntu.sh" >&2
  exit 1
fi

install -d -m 0755 "$(dirname "$INSTALL_DIR")"
git clone --branch "$BRANCH" --single-branch "$REPOSITORY" "$INSTALL_DIR"
cd "$INSTALL_DIR"

INSTALL_ARGS=(--url "$PUBLIC_URL")
if [[ -n "$CLOUDFLARE_TOKEN" ]]; then
  INSTALL_ARGS+=(--cloudflare-token "$CLOUDFLARE_TOKEN")
fi
if [[ "$PROVISION" == "0" ]]; then
  INSTALL_ARGS+=(--no-provision)
fi

exec bash ./scripts/install-ubuntu.sh "${INSTALL_ARGS[@]}"
