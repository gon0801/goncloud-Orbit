"""Regresion: el motor corre `bash desplegar.sh` SIN argumentos.

effects.py hace `bash {desplegar}` sobre la copia desconectada del commit
mergeado, con MOTOR_SHA=ese commit, stdin=DEVNULL, tope 900 s por script.
La version con `APROBADO=${1:?uso:...}` salia 1 al instante. La que usaba
HEAD y no esperaba Quality abortaba el caso real: justo despues del merge
Quality sigue in_progress y una conclusion distinta de success no es motivo
para saltarse el gate. Sin argumento se usa MOTOR_SHA y se espera hasta 15 min.

El job `completa` hace checkout shallow (fetch-depth por defecto): no trae
origin/master y `git rev-parse origin/master` sale 128 (run 39704242659,
shards 2/4/5/6; gate falla porque completa no queda en success). Si el ref
no esta, el SHA del test es HEAD y un git al frente del PATH deja pasar el
fetch y el ancestro. Con origin/master presente se usa ese SHA y el git real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ORIG = RAIZ / "docs/evidencia/ingesta-01/ejecucion/D.1"
DESPLEGAR = ORIG / "desplegar.sh"

FAKE_GH = r"""#!/usr/bin/env python3
import os
import sys

npath = os.environ.get("CK_GH_N", "")
n = 0
if npath and os.path.exists(npath):
    n = int(open(npath, encoding="utf-8").read() or "0")
n += 1
if npath:
    open(npath, "w", encoding="utf-8").write(str(n))
seq = os.environ.get("CK_GH_SEQ", "completed|success").split(",")
item = seq[min(n, len(seq)) - 1]
if os.environ.get("CK_GH_FALLA") == "1":
    print("gh falso roto", file=sys.stderr)
    sys.exit(1)
print(item)
"""

FAKE_SSH = r"""#!/usr/bin/env python3
import hashlib, os, subprocess, sys
cmd = " ".join(sys.argv[1:])
sha = os.environ.get("CK_SHA", "")
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


FAKE_GIT = r"""#!/usr/bin/env python3
import os
import sys

real = os.environ["CK_REAL_GIT"]
args = sys.argv[1:]
# Checkout shallow de completa: origin/master no existe (run 39704242659).
# fetch no toca la red ni pisa refs; el ancestro del SHA del test se acepta.
if args[:1] == ["fetch"]:
    raise SystemExit(0)
if (
    args[:2] == ["merge-base", "--is-ancestor"]
    and len(args) >= 4
    and args[2] == os.environ.get("CK_SHA", "")
    and args[3] == "origin/master"
):
    raise SystemExit(0)
os.execv(real, [real, *args])
"""


def _rev_parse(ref: str) -> str | None:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", ref],
        capture_output=True,
        text=True,
        check=False,
        cwd=RAIZ,
    )
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    return sha or None


def _entorno(
    tmp_path: Path,
    *,
    sha: str | None = None,
    seq: str = "completed|success",
    sin_master: bool = False,
) -> dict:
    # which() antes de anteponer tmp_path: ahi viven ssh/gh/git falsos.
    real_git = shutil.which("git") or "/usr/bin/git"
    real_ssh = shutil.which("ssh")
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(FAKE_SSH)
    fake_ssh.chmod(0o755)
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(FAKE_GH)
    fake_gh.chmod(0o755)
    master = None if sin_master else _rev_parse("origin/master")
    if sha is None:
        sha = master or _rev_parse("HEAD")
    if not sha:
        raise RuntimeError("no hay SHA local para el test de desplegar")
    if master is None:
        fake_git = tmp_path / "git"
        fake_git.write_text(FAKE_GIT)
        fake_git.chmod(0o755)
    d1 = tmp_path / "d1"
    d1.mkdir()
    shutil.copy(ORIG / "archivos.txt", d1 / "archivos.txt")
    shutil.copy(ORIG / "crontab-antes.txt", d1 / "crontab-antes.txt")
    env = os.environ.copy()
    # git fetch habla por el ssh de verdad; el falso solo cubre al servidor.
    env.update(
        {
            "GIT_SSH_COMMAND": real_ssh or "ssh",
            "PATH": f"{tmp_path}:{env['PATH']}",
            "ORBIT_SSH": "gonserver",
            "INGESTA01_D1_DIR": str(d1),
            "INGESTA01_D1_STAMP_FILE": str(tmp_path / "stamp"),
            "CK_SHA": sha,
            "CK_REAL_GIT": real_git,
            "CK_CRON": str(d1 / "crontab-antes.txt"),
            "CK_GH_SEQ": seq,
            "CK_GH_N": str(tmp_path / "gh-n"),
            "QUALITY_PAUSA_S": "0",
            "QUALITY_ESPERA_S": "30",
            "MOTOR_SHA": sha,
        }
    )
    return env


def _corre(tmp_path: Path, env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DESPLEGAR), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        cwd=RAIZ,
        timeout=180,
    )


def test_sin_argumento_usa_motor_sha_y_llega_a_listo(tmp_path):
    env = _entorno(tmp_path)
    r = _corre(tmp_path, env)
    out = r.stdout + r.stderr
    assert "uso: desplegar.sh" not in out, out
    assert "falta el SHA" not in out, out
    assert r.returncode == 0, out
    assert f"APROBADO={env['MOTOR_SHA']} CI=success" in r.stdout, out
    assert "== LISTO." in r.stdout, out
    assert "no se recrea orbit-app-1" in r.stdout, out


def test_sin_argumento_ni_motor_sha_aborta(tmp_path):
    env = _entorno(tmp_path)
    env.pop("MOTOR_SHA")
    r = _corre(tmp_path, env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "falta el SHA" in r.stdout, out
    assert "uso: desplegar.sh" not in out, out


def test_quality_in_progress_espera_y_sigue(tmp_path):
    env = _entorno(tmp_path, seq="in_progress|,completed|success")
    r = _corre(tmp_path, env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "sigue in_progress" in r.stdout, out
    assert "CI=success" in r.stdout, out
    assert "== LISTO." in r.stdout, out


def test_quality_failure_aborta_sin_esperar(tmp_path):
    env = _entorno(tmp_path, seq="completed|failure")
    r = _corre(tmp_path, env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "Quality sobre" in r.stdout and "failure" in r.stdout, out
    assert "== LISTO." not in r.stdout, out
    assert "orbit-app-1" not in r.stdout, out


def test_quality_no_responde_aborta(tmp_path):
    env = _entorno(tmp_path)
    env["CK_GH_FALLA"] = "1"
    r = _corre(tmp_path, env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "no pude leer Quality" in r.stdout, out


def test_checkout_shallow_sin_origin_master_llega_a_listo(tmp_path):
    # completa no trae origin/master. El script igual tiene que llegar a LISTO
    # con MOTOR_SHA=HEAD: fetch no aborta y el ancestro de ese SHA pasa.
    env = _entorno(tmp_path, sin_master=True)
    r = _corre(tmp_path, env)
    out = r.stdout + r.stderr
    assert "uso: desplegar.sh" not in out, out
    assert "git fetch origin fallo" not in out, out
    assert "no esta en origin/master" not in out, out
    assert r.returncode == 0, out
    assert f"APROBADO={env['MOTOR_SHA']} CI=success" in r.stdout, out
    assert "== LISTO." in r.stdout, out
