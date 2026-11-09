"""CLARIDAD 01 F: `tools/refresh_estimacion.sh` copia el bridge despues de que termina
`amazon-prices-sync`, no a las :45 fijas.

Falla real del 2 al 9-nov: a las 12:35 el reporte de Amazon US terminaba entre 12:45:38 y
12:51:45; la copia de las 12:45 salia con la oferta US de las 06:36 y el motor de precios de
las 13:10 dejaba las 99 publicaciones de US en `oferta_desactualizada`. Corre el script real con
`systemctl`, `docker`, `sleep` y `python3` falsos en el PATH (`python3` solo cambia la ruta del
bridge por una SQLite temporal).
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = "/mnt/data/appdata/bridge/data/bridge.db"

SYSTEMCTL = """#!/usr/bin/env bash
n=$(( $(cat "$CTL/systemctl" 2>/dev/null || echo 0) + 1 ))
echo $n > "$CTL/systemctl"
if [ $n -lt 3 ]; then echo activating; exit 0; fi
"$REAL_PY" "$CTL/termina_us.py"
echo inactive
"""

TERMINA_US = """import os, sqlite3
con = sqlite3.connect(os.environ["FAKE_BRIDGE"])
con.execute(
    "UPDATE amazon_listing_prices SET fetched_at = '2026-11-09T12:48:44Z'"
    " WHERE marketplace_name = 'amazon_us'"
)
con.commit()
"""

DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$CTL/docker.log"
if [ "$1" = cp ]; then cp "$2" "$CTL/copia.db"; fi
"""

PYTHON3 = """#!{py}
import os, sys
args = [a.replace({bridge!r}, os.environ["FAKE_BRIDGE"]) for a in sys.argv[1:]]
os.execv({py!r}, [{py!r}, *args])
"""


SIEMPRE_CORRIENDO = """#!/usr/bin/env bash
echo activating
"""


def _corre(tmp_path, systemctl):
    bridge = tmp_path / "bridge.db"
    con = sqlite3.connect(bridge)
    con.execute("CREATE TABLE amazon_listing_prices (marketplace_name TEXT, fetched_at TEXT)")
    con.executemany(
        "INSERT INTO amazon_listing_prices VALUES (?, ?)",
        [("amazon_mx", "2026-11-09T12:42:39Z"), ("amazon_us", "2026-11-09T06:36:52Z")],
    )
    con.commit()
    con.close()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    falsos = {
        "systemctl": systemctl,
        "docker": DOCKER,
        "sleep": "#!/bin/sh\n",
        "python3": PYTHON3.format(py=sys.executable, bridge=BRIDGE),
    }
    for nombre, texto in falsos.items():
        (bin_ / nombre).write_text(texto, encoding="utf-8", newline="\n")
        (bin_ / nombre).chmod(0o755)
    (tmp_path / "termina_us.py").write_text(TERMINA_US, encoding="utf-8")
    (tmp_path / "logs").mkdir()
    return subprocess.run(
        [shutil.which("bash") or "bash", str(ROOT / "tools/refresh_estimacion.sh")],
        text=True,
        capture_output=True,
        timeout=60,
        env={
            "PATH": f"{bin_}:/usr/bin:/bin",
            "CTL": str(tmp_path),
            "FAKE_BRIDGE": str(bridge),
            "REAL_PY": sys.executable,
            "ORBIT_LOGDIR": str(tmp_path / "logs"),
        },
    )


def test_claridad_f_copia_del_bridge_antes_de_que_termine_el_sync(tmp_path):
    r = _corre(tmp_path, SYSTEMCTL)

    copia = sqlite3.connect(tmp_path / "copia.db")
    us = copia.execute(
        "SELECT fetched_at FROM amazon_listing_prices WHERE marketplace_name = 'amazon_us'"
    ).fetchone()
    copia.close()
    assert us == ("2026-11-09T12:48:44Z",), "la copia salio antes de que el bridge terminara US"
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ingest estimacion --sqlite" in (tmp_path / "docker.log").read_text()


def test_claridad_f_bridge_que_no_termina_avisa(tmp_path):
    r = _corre(tmp_path, SIEMPRE_CORRIENDO)

    assert "ingest estimacion --sqlite" in (tmp_path / "docker.log").read_text()
    assert r.returncode != 0, "el bridge no termino y el script salio 0: cron-avisa no avisa"
    assert "sigue corriendo tras 20 min" in r.stdout
