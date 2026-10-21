"""Regresion: el motor corre `bash desplegar.sh` SIN argumentos.

effects.py hace `bash {desplegar}` sobre la copia desconectada del commit
mergeado, stdin=DEVNULL, tope 900 s. La version que exigia
`APROBADO=${1:?uso:...}` salia 1 al instante; el rollback sin STAMP tambien
(Deployed ok=false, "desplegar.sh: salida 1 / rollback: salida 1", intentos
22-24). Estos tests fallan contra esa version y exigen que sin args se
resuelva HEAD, no se pida Quality (el motor ya la midio en la rama) y se
llegue a LISTO con ssh falso.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ORIG = RAIZ / "docs/evidencia/ingesta-01/ejecucion/D.1"
DESPLEGAR = ORIG / "desplegar.sh"

FAKE_SSH = r"""#!/usr/bin/env python3
import hashlib, os, subprocess, sys
cmd = " ".join(sys.argv[1:])
sha = os.environ.get("CK_SHA", "")
archivos = os.environ.get("CK_ARCHIVOS", "")
if "docker inspect" in cmd and "State.Running" in cmd:
    print("true")
elif "predeploy-" in cmd and ("mkdir" in cmd or "cp -a" in cmd or "ls predeploy" in cmd):
    print("app")
    print("IMAGE")
    print("SHA")
elif "tar --no-same-owner" in cmd or "tar -xf" in cmd:
    sys.stdin.buffer.read()
elif "md5sum /app/app/ads/descarga.py" in cmd:
    b = subprocess.run(
        ["git", "show", f"{sha}:app/ads/descarga.py"],
        capture_output=True, check=True,
    ).stdout
    print(hashlib.md5(b).hexdigest(), "/app/app/ads/descarga.py")
elif "md5sum" in cmd:
    f = cmd.split()[-1]
    pref = "/mnt/data/appdata/orbit/"
    rel = f[len(pref):] if f.startswith(pref) else f
    b = subprocess.run(
        ["git", "show", f"{sha}:{rel}"], capture_output=True, check=True,
    ).stdout
    print(hashlib.md5(b).hexdigest(), rel)
elif "curl" in cmd and "health" in cmd:
    if "-w" in cmd or "%{http_code}" in cmd:
        print("200")
    else:
        print('{"status":"ok"}')
elif "crontab -u gon -l" in cmd:
    sys.stdout.write(open(os.environ["CK_CRON"], encoding="utf-8").read())
elif cmd.strip().endswith("bash -s") or "sudo bash -s" in cmd:
    sys.stdin.read()
    print("# job_key=ingest:diario")
    print("# job_key=ads_optimizer:amazon_us")
elif "flock" in cmd:
    sys.exit(0)
elif "psql" in cmd:
    print("0")
elif "docker top" in cmd:
    print("UID\tCOMMAND")
elif "docker compose" in cmd or "docker ps" in cmd:
    print("Container orbit-app-1 Running")
elif "envia_recordatorio" in cmd or "ingesta01-d1.txt" in cmd:
    sys.exit(0)
else:
    print(f"ssh falso sin guion para: {cmd}", file=sys.stderr)
    sys.exit(64)
"""


def _entorno(tmp_path: Path) -> dict:
    fake = tmp_path / "ssh"
    fake.write_text(FAKE_SSH)
    fake.chmod(0o755)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=RAIZ,
    ).stdout.strip()
    d1 = tmp_path / "d1"
    d1.mkdir()
    shutil.copy(ORIG / "archivos.txt", d1 / "archivos.txt")
    shutil.copy(ORIG / "crontab-antes.txt", d1 / "crontab-antes.txt")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "ORBIT_SSH": "gonserver",
            "INGESTA01_D1_DIR": str(d1),
            "INGESTA01_D1_STAMP_FILE": str(tmp_path / "stamp"),
            "CK_SHA": sha,
            "CK_ARCHIVOS": str(d1 / "archivos.txt"),
            "CK_CRON": str(d1 / "crontab-antes.txt"),
            "GH": "false",
        }
    )
    return env


def test_sin_argumento_llega_a_listo(tmp_path):
    # El motor no pasa SHA. La version con ${1:?uso:...} salia 1 al instante
    # (stderr "uso: desplegar.sh") y nunca llegaba a LISTO.
    r = subprocess.run(
        ["bash", str(DESPLEGAR)],
        env=_entorno(tmp_path),
        text=True,
        capture_output=True,
        check=False,
        cwd=RAIZ,
        timeout=120,
    )
    out = r.stdout + r.stderr
    assert "uso: desplegar.sh" not in out, out
    assert r.returncode == 0, out
    assert "== LISTO." in r.stdout, out
    assert "no se recrea orbit-app-1" in r.stdout, out
