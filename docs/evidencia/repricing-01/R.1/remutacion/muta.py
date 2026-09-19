"""Re-mutación de R.1: siembra cada mutante, corre sus tests con base real y restaura.

Uso (desde la raiz del repo, con la base local de pruebas arriba):
    MUTA_RAIZ=$(pwd) python3 docs/evidencia/repricing-01/R.1/remutacion/muta.py \
        docs/evidencia/repricing-01/R.1/remutacion/cat_A2.json [ids...]
El catálogo es una lista JSON de {id, archivo, viejo, nuevo, tests, cuenta=1,
ocurrencia=None, extra=None}.
`viejo` tiene que aparecer exactamente `cuenta` veces (sino: INFIEL, no se corre).
Clasificación por código de pytest: 1 = MUERTO, 0 = SOBREVIVE, otro = INVALIDO
(colección rota, sintaxis, sin tests). Después de cada mutante el archivo vuelve a
sus bytes originales y `git status --porcelain` del archivo tiene que salir vacío.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(os.environ.get("MUTA_RAIZ", Path.cwd()))
DSN = "postgresql://orbit:orbit@localhost:5432/postgres"
PY = str(RAIZ / ".venv/bin/python")


def correr(tests: list[str]) -> tuple[int, str, float]:
    env = dict(os.environ)
    env["ORBIT_TEST_DSN"] = DSN
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="muta-pyc-")
    ini = time.time()
    p = subprocess.run(
        [PY, "-m", "pytest", *tests, "-q", "-x", "-p", "no:cacheprovider"],
        cwd=RAIZ,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    lineas = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()]
    ultima = lineas[-1] if lineas else ""
    fallo = next((ln for ln in lineas if ln.startswith(("E ", "FAILED", "ERROR"))), "")
    return p.returncode, (fallo[:220] + " || " + ultima[:160]).strip(" |"), time.time() - ini


def limpio(archivo: str) -> bool:
    out = subprocess.run(
        ["/opt/homebrew/bin/git", "status", "--porcelain", "--", archivo],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    ).stdout
    return out.strip() == ""


def main() -> None:
    mutantes = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ids = set(sys.argv[2:])
    salida = Path(sys.argv[1]).with_suffix(".resultado.jsonl")
    with salida.open("w", encoding="utf-8") as fh:
        for m in mutantes:
            if ids and m["id"] not in ids:
                continue
            ruta = RAIZ / m["archivo"]
            original = ruta.read_bytes()
            texto = original.decode("utf-8")
            cuenta = texto.count(m["viejo"])
            res = {
                "id": m["id"],
                "archivo": m["archivo"],
                "tests": m["tests"],
                "extra": m.get("extra"),
            }
            if cuenta != m.get("cuenta", 1):
                res.update(veredicto="INFIEL", detalle=f"viejo aparece {cuenta} veces")
            elif not limpio(m["archivo"]):
                res.update(veredicto="INFIEL", detalle="archivo sucio antes de mutar")
            else:
                n = m.get("ocurrencia")
                if n is None:
                    mutado = texto.replace(m["viejo"], m["nuevo"])
                else:
                    partes = texto.split(m["viejo"])
                    mutado = m["viejo"].join(partes[:n]) + m["nuevo"] + m["viejo"].join(partes[n:])
                ruta.write_text(mutado, encoding="utf-8")
                try:
                    rc, detalle, seg = correr(m["tests"])
                finally:
                    ruta.write_bytes(original)
                veredicto = {1: "MUERTO", 0: "SOBREVIVE"}.get(rc, "INVALIDO")
                res.update(veredicto=veredicto, rc=rc, detalle=detalle, seg=round(seg, 1))
                if not limpio(m["archivo"]):
                    res["veredicto"] += "+SUCIO"
            print(json.dumps(res, ensure_ascii=False), flush=True)
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
