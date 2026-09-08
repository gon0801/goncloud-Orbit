#!/usr/bin/env python3
"""Escribe evidencia B.3 en docs/ (script explicito; pytest no toca docs/).

Uso desde la raiz del repo (requiere ORBIT_TEST_DSN / Postgres de test):

  PYTHONPATH=. .venv/bin/python tools/evidencia_b3_recorrido_persistido.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "docs/evidencia/margen-estimado-01/B.3/recorrido-persistido-1213.json"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    from test_estimacion_venta import test_b3_recorrido_persistido_proyeccion_s5_ac14

    with tempfile.TemporaryDirectory(prefix="orbit-b3-evidencia-") as tmp:
        out = Path(tmp)
        test_b3_recorrido_persistido_proyeccion_s5_ac14(out)
        src = out / "recorrido-persistido-1213.json"
        if not src.is_file():
            print(f"no se genero {src}", file=sys.stderr)
            return 1
        DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, DEST)
        print(DEST.read_text(encoding="utf-8"))
        print(f"escrito {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
