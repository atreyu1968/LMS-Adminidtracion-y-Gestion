#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_COMMIT="${GTH_SOURCE_COMMIT:-6eaa240ce13a69e328c5b8d11ae2bdca99be7d17}"
SOURCE_DIR="${GTH_SOURCE_DIR:-}"
OUTPUT_DIR="${1:-downloads/gth0652}"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

if [[ -z "$SOURCE_DIR" ]]; then
  SOURCE_DIR="$TMP_ROOT/CFGSAF"
  git clone --quiet https://github.com/atreyu1968/CFGSAF.git "$SOURCE_DIR"
  git -C "$SOURCE_DIR" checkout --quiet "$SOURCE_COMMIT"
fi

BASE="$SOURCE_DIR/grh0652/scorm"
[[ -d "$BASE" ]] || { echo "No existe $BASE" >&2; exit 1; }

rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

python3 - "$BASE" "$OUTPUT_DIR" "$SOURCE_COMMIT" <<'PY'
from __future__ import annotations
import hashlib
import html.parser
import json
import pathlib
import re
import shutil
import tempfile
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

base = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
source_commit = sys.argv[3]

names = {
    "ut1": "GTH_RA1_Gestion_de_la_contratacion_laboral_SCORM_1.2.zip",
    "ut2": "GTH_RA2_Modificacion_suspension_y_extincion_SCORM_1.2.zip",
    "ut3": "GTH_RA3_Seguridad_Social_SCORM_1.2.zip",
    "ut4": "GTH_RA4_Retribucion_nominas_cotizacion_IRPF_SCORM_1.2.zip",
}
titles = {
    "ut1": "RA1 · Gestión de la contratación laboral",
    "ut2": "RA2 · Modificación, suspensión y extinción del contrato",
    "ut3": "RA3 · Obligaciones empresariales con la Seguridad Social",
    "ut4": "RA4 · Retribución, nóminas, cotización e IRPF",
}

class RefParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key.lower() in {"src", "href", "poster"} and value:
                self.refs.append(value)

def local_ref(ref: str) -> str | None:
    ref = ref.strip()
    if not ref or ref.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
        return None
    parsed = urllib.parse.urlsplit(ref)
    if parsed.scheme or parsed.netloc:
        return None
    path = urllib.parse.unquote(parsed.path)
    if not path:
        return None
    return path.lstrip("./")

report = {
    "source_repository": "atreyu1968/CFGSAF",
    "source_commit": source_commit,
    "standard": "SCORM 1.2",
    "packages": [],
}
errors = []

shared_secure_exam = base.parent / "assets" / "secure-exam.js"
if not shared_secure_exam.is_file():
    errors.append("falta el recurso compartido grh0652/assets/secure-exam.js")

for unit, zip_name in names.items():
    source_root = base / unit
    stage_parent = pathlib.Path(tempfile.mkdtemp(prefix=f"gth-{unit}-"))
    root = stage_parent / unit
    shutil.copytree(source_root, root)
    if shared_secure_exam.is_file():
        (root / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_secure_exam, root / "assets" / "secure-exam.js")

    manifest = root / "imsmanifest.xml"
    launch = root / "index.html"
    if not manifest.is_file():
        errors.append(f"{unit}: falta imsmanifest.xml")
        continue
    if not launch.is_file():
        errors.append(f"{unit}: falta index.html")
        continue

    try:
        ET.parse(manifest)
    except ET.ParseError as exc:
        errors.append(f"{unit}: imsmanifest.xml inválido: {exc}")
        continue

    xml_text = manifest.read_text(encoding="utf-8")
    if "adlcp_rootv1p2" not in xml_text:
        errors.append(f"{unit}: no declara SCORM 1.2")
    hrefs = re.findall(r'\bhref="([^"]+)"', xml_text)
    launch_hrefs = [h for h in hrefs if h.endswith(".html")]
    if "index.html" not in launch_hrefs:
        errors.append(f"{unit}: el manifiesto no lanza index.html")

    manifest_missing = []
    for href in hrefs:
        ref = local_ref(href)
        if ref and not (root / ref).is_file():
            manifest_missing.append(ref)
    if manifest_missing:
        errors.append(f"{unit}: faltan recursos declarados: {manifest_missing}")

    parser = RefParser()
    parser.feed(launch.read_text(encoding="utf-8", errors="replace"))
    html_missing = []
    for href in parser.refs:
        ref = local_ref(href)
        if ref and ref.startswith("assets/") and not (root / ref).is_file():
            html_missing.append(ref)
    if html_missing:
        errors.append(f"{unit}: faltan recursos HTML: {sorted(set(html_missing))}")

    files = [p for p in root.rglob("*") if p.is_file()]
    target = out / zip_name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for path in sorted(files):
            zf.write(path, path.relative_to(root).as_posix())

    with zipfile.ZipFile(target) as zf:
        if "imsmanifest.xml" not in zf.namelist():
            errors.append(f"{unit}: imsmanifest.xml no quedó en la raíz del ZIP")
        if "index.html" not in zf.namelist():
            errors.append(f"{unit}: index.html no quedó en la raíz del ZIP")
        bad = zf.testzip()
        if bad:
            errors.append(f"{unit}: CRC defectuoso en {bad}")

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    report["packages"].append({
        "unit": unit.upper(),
        "title": titles[unit],
        "filename": zip_name,
        "files": len(files),
        "bytes": target.stat().st_size,
        "sha256": digest,
        "manifest": "OK",
        "launch": "index.html",
        "internal_assets": "OK",
        "shared_secure_exam_embedded": True,
    })
    shutil.rmtree(stage_parent, ignore_errors=True)

if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)

(out / "validation.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

lines = [
    "# Validación — SCORM GTH finales",
    "",
    f"- Fuente: atreyu1968/CFGSAF@{source_commit}",
    "- Estándar: SCORM 1.2",
    "- Recurso de lanzamiento: index.html",
    "- imsmanifest.xml: en la raíz de cada ZIP",
    "- Integridad ZIP/CRC: OK",
    "- Recursos declarados en manifiesto: OK",
    "- Recursos locales assets/ referenciados desde HTML: OK",
    "",
    "| Unidad | Paquete | Ficheros | SHA-256 |",
    "|---|---|---:|---|",
]
for p in report["packages"]:
    lines.append(f"| {p['unit']} | {p['filename']} | {p['files']} | {p['sha256']} |")
(out / "VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

(
  cd "$OUTPUT_DIR"
  sha256sum GTH_RA*.zip > SHA256SUMS.txt
  zip -q GTH_SCORM_4_UNIDADES_COMPLETO.zip     GTH_RA1_Gestion_de_la_contratacion_laboral_SCORM_1.2.zip     GTH_RA2_Modificacion_suspension_y_extincion_SCORM_1.2.zip     GTH_RA3_Seguridad_Social_SCORM_1.2.zip     GTH_RA4_Retribucion_nominas_cotizacion_IRPF_SCORM_1.2.zip     VALIDATION.md validation.json SHA256SUMS.txt
  sha256sum GTH_SCORM_4_UNIDADES_COMPLETO.zip >> SHA256SUMS.txt
)

echo "SCORM finales preparados en $OUTPUT_DIR"
cat "$OUTPUT_DIR/VALIDATION.md"
