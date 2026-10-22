"""Regresion del paso 4 de rollback.sh D.1 de ingesta-01 (intento 22).

Antes verificaba "descarga.py NO existe en el contenedor": cierto solo la
primera vez que se revierte el despliegue D.1. Al revertir un re-despliegue
(el respaldo predeploy-<STAMP> ya trae descarga.py) la restauracion terminaba
bien pero el chequeo daba falso fallo y rollback salia 1 — el incidente real
de 2026-10-21 18:14 (rollback-intento-fallido-1814.txt). Ahora el paso compara
el contenedor contra predeploy-<STAMP>, que es lo que una reversa debe
garantizar, exista o no descarga.py en el respaldo.

Para reprobar contra la version anterior:
  git show <sha-anterior>:docs/evidencia/ingesta-01/ejecucion/D.1/rollback.sh \
    > /tmp/rollback-viejo.sh
  ORBIT_ROLLBACK_SH=/tmp/rollback-viejo.sh pytest -q \
    tests/test_ingesta01_d1_rollback.py::test_reversa_de_redespliegue_termina_ok
"""

import os
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIR = RAIZ / "docs/evidencia/ingesta-01/ejecucion/D.1"
ROLLBACK = Path(os.environ.get("ORBIT_ROLLBACK_SH", DIR / "rollback.sh"))
CRON_ANTES = DIR / "crontab-antes.txt"
STAMP = "20991231-2359"

# ssh falso: responde cada consulta del rollback sin tocar la red.
FAKE_SSH = """#!/usr/bin/env python3
import os
import sys

cmd = " ".join(sys.argv[1:])
if "ls -1d" in cmd and "predeploy-2" in cmd:
    print(os.environ.get("CK_PREDEPLOY", ""), end="")
    sys.exit(0)
elif "test ! -e /app/app/ads/descarga.py" in cmd:
    # escenario re-despliegue: descarga.py existe en el respaldo restaurado
    sys.exit(1)
elif "test -d" in cmd and "predeploy" in cmd:
    sys.exit(0)
elif "/SHA" in cmd and "cat" in cmd:
    print("sha-anterior-falso")
elif "docker top" in cmd:
    print("UID\\tCOMMAND")
elif "psql" in cmd:
    print("0")
elif "docker compose up" in cmd:
    print("Image orbit-app Built")
    print("Container orbit-app-1 Started")
elif "crontab -u gon -l" in cmd:
    sys.stdout.write(open(os.environ["CK_CRON"], encoding="utf-8").read())
elif cmd.strip().endswith("crontab -u gon -"):
    sys.stdin.read()
elif "docker exec orbit-app-1 sh -c" in cmd:
    sys.stdout.write(open(os.environ["CK_CONT"], encoding="utf-8").read())
elif "predeploy" in cmd and "find" in cmd:
    sys.stdout.write(open(os.environ["CK_RESP"], encoding="utf-8").read())
elif "curl" in cmd and "health" in cmd:
    print("200")
else:
    print(f"ssh falso sin guion para: {cmd}", file=sys.stderr)
    sys.exit(64)
"""

# Respaldo de un re-despliegue: trae descarga.py (lo que rompio la version vieja).
PY_RED = (
    "11111111111111111111111111111111  app/ads/descarga.py\n"
    "22222222222222222222222222222222  app/main.py\n"
    "33333333333333333333333333333333  tools/jev_ads.py\n"
)
# Mismo respaldo con un hash distinto: el contenedor NO es el restaurado.
PY_OTRO = "99999999999999999999999999999999  app/ads/descarga.py\n" + PY_RED.split("\n", 1)[1]


def _entorno(tmp_path, cont, resp):
    fake = tmp_path / "ssh"
    fake.write_text(FAKE_SSH)
    fake.chmod(0o755)
    f_cont = tmp_path / "cont.txt"
    f_cont.write_text(cont, encoding="utf-8")
    f_resp = tmp_path / "resp.txt"
    f_resp.write_text(resp, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "ORBIT_SSH": "gonserver",
            "CK_CRON": str(CRON_ANTES),
            "CK_CONT": str(f_cont),
            "CK_RESP": str(f_resp),
        }
    )
    return env


def _corre(tmp_path, cont, resp):
    return subprocess.run(
        ["bash", str(ROLLBACK), STAMP],
        env=_entorno(tmp_path, cont, resp),
        text=True,
        capture_output=True,
        check=False,
        cwd=RAIZ,
    )


def test_reversa_de_redespliegue_termina_ok(tmp_path):
    # El respaldo ya trae descarga.py: la version vieja daba "FALLA: descarga.py
    # sigue en el contenedor" despues de restaurar bien (salida 1, incidente
    # 20261021-1814). La reversa de un re-despliegue es legitima y debe terminar.
    r = _corre(tmp_path, PY_RED, PY_RED)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"contenedor == predeploy-{STAMP}" in r.stdout
    assert "REVERSA LISTA" in r.stdout
    assert "FALLA" not in r.stdout


def test_contenedor_distinto_del_respaldo_es_falla(tmp_path):
    # Si el contenedor no coincide con el respaldo, la reversa no termino y
    # el checklist de reversa debe fallar (el chequeo nuevo si discrimina).
    r = _corre(tmp_path, PY_OTRO, PY_RED)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"el contenedor difiere de predeploy-{STAMP}" in r.stdout


def _sin_argumento(tmp_path, cont, resp, *, predeploy, motor_sha):
    env = _entorno(tmp_path, cont, resp)
    env["CK_PREDEPLOY"] = predeploy
    if motor_sha is not None:
        env["MOTOR_SHA"] = motor_sha
    else:
        env.pop("MOTOR_SHA", None)
    return subprocess.run(
        ["bash", str(ROLLBACK)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        cwd=RAIZ,
        timeout=15,
    )


def test_sin_argumento_sin_predeploy_es_noop(tmp_path):
    # El motor corre `bash rollback.sh` sin STAMP (effects.py). Si no hay
    # predeploy en el servidor, no hay nada que revertir: salir 0.
    # La version con STAMP=${1:?uso:...} salia 1 al instante (incidentes 22-24).
    r = _sin_argumento(tmp_path, PY_RED, PY_RED, predeploy="", motor_sha="sha-de-este-merge")
    out = r.stdout + r.stderr
    assert "uso: rollback.sh" not in out, out
    assert r.returncode == 0, out
    assert "no hay predeploy" in r.stdout, out


def test_sin_argumento_predeploy_de_otro_sha_es_noop(tmp_path):
    # Desplegar aborto antes del respaldo: el predeploy mas reciente es de un
    # despliegue anterior. Revertirlo bajaria produccion. Con MOTOR_SHA distinto
    # se sale 0.
    r = _sin_argumento(
        tmp_path,
        PY_RED,
        PY_RED,
        predeploy="/mnt/data/appdata/orbit/predeploy-20991231-2359",
        motor_sha="sha-de-este-merge",
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "no es de este despliegue" in r.stdout, out
    assert "REVERSA LISTA" not in r.stdout, out


def test_sin_argumento_restaura_el_predeploy_de_este_sha(tmp_path):
    # Sin STAMP se toma el predeploy mas reciente. Si su SHA es MOTOR_SHA,
    # este despliegue si creo el respaldo y la reversa corre.
    r = _sin_argumento(
        tmp_path,
        PY_RED,
        PY_RED,
        predeploy="/mnt/data/appdata/orbit/predeploy-20991231-2359",
        motor_sha="sha-anterior-falso",
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert f"contenedor == predeploy-{STAMP}" in r.stdout, out
    assert "REVERSA LISTA" in r.stdout, out
