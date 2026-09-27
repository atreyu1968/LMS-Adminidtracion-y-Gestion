#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import http.server
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import zipfile


PACKAGES = [
    "GTH_RA1_Gestion_de_la_contratacion_laboral_SCORM_1.2.zip",
    "GTH_RA2_Modificacion_suspension_y_extincion_SCORM_1.2.zip",
    "GTH_RA3_Seguridad_Social_SCORM_1.2.zip",
    "GTH_RA4_Retribucion_nominas_cotizacion_IRPF_SCORM_1.2.zip",
]


def browser_binary() -> str:
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("No se encontró Chrome/Chromium para la prueba de modo estudio")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def harness(package_dir: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><title>Smoke modo estudio</title></head>
<body data-result="PENDING">
<div id="out">PENDING</div>
<iframe id="sco" src="/{package_dir}/index.html" allow="fullscreen" allowfullscreen
        style="width:1200px;height:800px"></iframe>
<script>
const out=document.getElementById('out');
const frame=document.getElementById('sco');
function finish(ok,detail){{
  document.body.dataset.result=ok?'PASS':'FAIL';
  out.textContent=(ok?'PASS: ':'FAIL: ')+detail;
}}
frame.addEventListener('load',()=>{{
  setTimeout(()=>{{
    try{{
      const w=frame.contentWindow,d=frame.contentDocument;
      const button=d.getElementById('enterBtn');
      const overlay=d.getElementById('launchOverlay');
      if(!button||!overlay){{finish(false,'faltan controles de entrada');return;}}
      // Headless browsers normalmente rechazan fullscreen. Esto prueba que el
      // modo estudio no depende de que el permiso sea concedido.
      button.click();
      setTimeout(()=>{{
        const active=d.querySelector('.screen.active');
        const hidden=overlay.classList.contains('hidden')||w.getComputedStyle(overlay).display==='none';
        const study=d.body.dataset.studyMode==='active';
        const mode=(d.getElementById('mode')?.textContent||'').toLowerCase();
        const examMode=d.body.classList.contains('exam-mode');
        const ok=hidden&&study&&!!active&&mode.includes('modo de estudio')&&!examMode;
        finish(ok,
          'hidden='+hidden+
          '; study='+study+
          '; active='+(active?.id||'ninguna')+
          '; mode='+mode+
          '; examMode='+examMode);
      }},700);
    }}catch(err){{finish(false,String(err));}}
  }},500);
}});
setTimeout(()=>{{
  if(document.body.dataset.result==='PENDING')finish(false,'timeout');
}},4500);
</script>
</body></html>"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: test-gth-study-mode-browser.py DIRECTORIO_PAQUETES", file=sys.stderr)
        return 2

    source = pathlib.Path(sys.argv[1]).resolve()
    chrome = browser_binary()

    with tempfile.TemporaryDirectory(prefix="gth-study-browser-") as tmp:
        root = pathlib.Path(tmp)
        for idx, filename in enumerate(PACKAGES, start=1):
            zip_path = source / filename
            if not zip_path.is_file():
                raise SystemExit(f"Falta {zip_path}")
            target = root / f"ut{idx}"
            target.mkdir()
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(target)

        old_cwd = pathlib.Path.cwd()
        os.chdir(root)
        port = free_port()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for idx, filename in enumerate(PACKAGES, start=1):
                harness_path = root / f"smoke-ut{idx}.html"
                harness_path.write_text(harness(f"ut{idx}"), encoding="utf-8")
                url = f"http://127.0.0.1:{port}/{harness_path.name}"
                proc = subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--autoplay-policy=no-user-gesture-required",
                        "--virtual-time-budget=6000",
                        "--dump-dom",
                        url,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    check=False,
                )
                output = proc.stdout
                if proc.returncode != 0 or 'data-result="PASS"' not in output:
                    print(f"ERROR {filename}", file=sys.stderr)
                    print(proc.stderr[-4000:], file=sys.stderr)
                    print(output[-8000:], file=sys.stderr)
                    return 1
                marker = output.find("PASS:")
                detail = output[marker:marker + 500] if marker >= 0 else "PASS"
                print(f"OK {filename}: {detail}")
        finally:
            server.shutdown()
            server.server_close()
            os.chdir(old_cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
