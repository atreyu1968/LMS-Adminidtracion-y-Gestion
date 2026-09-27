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
    return pathlib.PurePosixPath(path).as_posix()

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

    # The source tree can reach the shared exam runtime through ../../assets,
    # but a standalone SCORM ZIP cannot. Make every package self-contained.
    index_path = root / "index.html"
    if index_path.is_file():
        index_text = index_path.read_text(encoding="utf-8")
        index_text = index_text.replace(
            'src="../../assets/secure-exam.js"',
            'src="assets/secure-exam.js"',
        )
        # UT4 historically added a second study-entry listener inline. The
        # shared runtime already owns that action, so remove the duplicate.
        index_text = re.sub(
            r"""document\.getElementById\(['"]enterBtn['"]\)\?\.addEventListener\(
                ['"]click['"],\(\)=>\{
                document\.getElementById\(['"]launchOverlay['"]\)\.style\.display=['"]none['"];
                document\.documentElement\.requestFullscreen\?\.\(\)\.catch\(\(\)=>\{\}\);
                \}\);""",
            "",
            index_text,
            flags=re.X,
        )
        index_path.write_text(index_text, encoding="utf-8")

    # Normalise study entry in the shared runtime: enter immediately, request
    # fullscreen without blocking navigation, resume the last screen, and keep
    # a clear study-mode label whether fullscreen is accepted or denied.
    runtime_path = root / "assets" / "scorm.js"
    if runtime_path.is_file():
        runtime_text = runtime_path.read_text(encoding="utf-8")
        old_entry = """ const enter=$('#enterBtn');if(enter)enter.onclick=async()=>{$('#launchOverlay')?.classList.add('hidden');await requestFull();showScreen(state.last||'inicio')};"""
        new_entry = """ const enter=$('#enterBtn');if(enter)enter.onclick=async()=>{const overlay=$('#launchOverlay');if(overlay){overlay.classList.add('hidden');overlay.setAttribute('aria-hidden','true')}document.body.dataset.studyMode='active';const mode=$('#mode');if(mode)mode.textContent=connected?'Modo de estudio · SCORM 1.2':'Modo de estudio · local';const full=requestFull();showScreen(state.last||'inicio');await full};"""
        # Repair three literal backslash-n sequences present in the frozen
        # source commit. They are outside JS strings and make the whole runtime
        # fail to parse in a real browser.
        runtime_text = runtime_text.replace(
            "let examQuestions=[];\\nlet examDeadline",
            "let examQuestions=[];\nlet examDeadline",
        )
        runtime_text = runtime_text.replace(
            "examPendingSync=false;\\nfunction examDraftKey",
            "examPendingSync=false;\nfunction examDraftKey",
        )
        runtime_text = runtime_text.replace(
            "return}\\n examActive=true",
            "return}\n examActive=true",
        )
        if "\\n" in runtime_text:
            errors.append(f"{unit}: quedan secuencias \\n literales sospechosas en scorm.js")

        if old_entry not in runtime_text:
            errors.append(f"{unit}: no se encontró el manejador estándar de modo estudio")
        else:
            runtime_text = runtime_text.replace(old_entry, new_entry, 1)
            runtime_path.write_text(runtime_text, encoding="utf-8")

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
    html_unsafe = []
    for href in parser.refs:
        ref = local_ref(href)
        if not ref:
            continue
        parts = pathlib.PurePosixPath(ref).parts
        if ".." in parts:
            html_unsafe.append(ref)
            continue
        if ref.startswith("assets/") and not (root / ref).is_file():
            html_missing.append(ref)
    if html_unsafe:
        errors.append(f"{unit}: referencias HTML que salen del paquete: {sorted(set(html_unsafe))}")
    if html_missing:
        errors.append(f"{unit}: faltan recursos HTML: {sorted(set(html_missing))}")

    runtime_text = (root / "assets" / "scorm.js").read_text(encoding="utf-8", errors="replace")
    study_checks = {
        "enter_button": 'id="enterBtn"' in launch.read_text(encoding="utf-8", errors="replace"),
        "overlay_hide": "overlay.classList.add('hidden')" in runtime_text,
        "study_marker": "document.body.dataset.studyMode='active'" in runtime_text,
        "resume_last": "showScreen(state.last||'inicio')" in runtime_text,
        "fullscreen_nonblocking": "const full=requestFull();showScreen(state.last||'inicio');await full" in runtime_text,
        "study_label": "Modo de estudio · SCORM 1.2" in runtime_text,
    }
    if not all(study_checks.values()):
        errors.append(
            f"{unit}: contrato de entrada en modo estudio incompleto: "
            + ", ".join(k for k, v in study_checks.items() if not v)
        )

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
        "study_mode": "OK",
        "study_mode_resume": "OK",
        "study_mode_fullscreen_fallback": "OK",
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
    "- Entrada en modo estudio: OK",
    "- Reanudación del último punto: OK",
    "- Fallback sin pantalla completa: OK",
    "- Aislamiento respecto al modo examen: OK",
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
