#!/usr/bin/env python3
"""Importa la parte pública de GTH desde CFGSAF para preparar su migración.

No importa bancos privados, bases de datos, tokens ni otros secretos.
"""

from __future__ import annotations

import io
import shutil
import tarfile
import urllib.request
from pathlib import Path


GTH_SOURCE_COMMIT = "6eaa240ce13a69e328c5b8d11ae2bdca99be7d17"\nARCHIVE = f"https://codeload.github.com/atreyu1968/CFGSAF/tar.gz/{GTH_SOURCE_COMMIT}"
TARGET = Path(__file__).resolve().parents[1] / "modules" / "gth0652" / "legacy"
ALLOWED_PREFIXES = (
    "grh0652/scorm/",
    "grh0652/assets/",
)
ALLOWED_FILES = {
    "grh0652/index.html",
    "grh0652/course.html",
    "grh0652/player.html",
    "grh0652/teacher.html",
    "grh0652/README.md",
    "grh0652/TRACEABILITY.md",
}


def safe_rel(name: str) -> Path | None:
    parts = Path(name).parts
    if len(parts) < 2:
        return None
    rel = Path(*parts[1:])
    raw = rel.as_posix()
    if raw in ALLOWED_FILES or any(raw.startswith(p) for p in ALLOWED_PREFIXES):
        return rel.relative_to("grh0652")
    return None


def main() -> None:
    print("Descargando fuente pública de GTH...")
    with urllib.request.urlopen(ARCHIVE, timeout=60) as response:
        data = response.read()

    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            rel = safe_rel(member.name)
            if rel is None or not member.isfile():
                continue
            src = tf.extractfile(member)
            if src is None:
                continue
            dst = (TARGET / rel).resolve()
            if TARGET.resolve() not in dst.parents:
                raise RuntimeError("Ruta insegura en el archivo")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read())

    print(f"GTH público importado en {TARGET}")
    print("Los bancos privados NO se han importado deliberadamente.")


if __name__ == "__main__":
    main()
