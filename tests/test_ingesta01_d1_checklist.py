"""Regresion del paso md5 del checklist D.1 de ingesta-01 (intento 20).

Antes contaba solo lineas '^>' del diff local-vs-servidor: si en el servidor
faltaba un archivo (md5sum lo manda a stderr, sin linea) o la lectura por ssh
salia vacia, el paso imprimia "OK ... distintos = 0". Estos tests fallan
contra esa version y exigen FALLA / NO MEDIDO.
"""

import os
import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIR = RAIZ / "docs/evidencia/ingesta-01/ejecucion/D.1"
CHECKLIST = DIR / "checklist.sh"

# ssh falso: responde cada consulta del checklist sin tocar la red.
FAKE_SSH = """#!/usr/bin/env python3
import hashlib
import os
import subprocess
import sys

cmd = " ".join(sys.argv[1:])
if "docker inspect" in cmd:
    print("true")
elif "xargs md5sum" in cmd:
    modo = os.environ.get("CK_MD5_MODE", "sano")
    if modo != "vacia":
        sha = os.environ["CK_SHA"]
        archivos = open(os.environ["CK_ARCHIVOS"], encoding="utf-8").read().split()
        if modo == "falta-uno":
            archivos = archivos[:-1]
        lineas = []
        for f in archivos:
            b = subprocess.run(
                ["git", "show", f"{sha}:{f}"], capture_output=True, check=True
            ).stdout
            lineas.append(f"{hashlib.md5(b).hexdigest()}  {f}")
        if modo == "hash-distinto" and lineas:
            _h, f = lineas[0].split("  ", 1)
            lineas[0] = "0" * 32 + "  " + f
        print("\\n".join(lineas))
elif "crontab -u gon -l" in cmd:
    sys.stdout.write(open(os.environ["CK_CRON"], encoding="utf-8").read())
elif "app.ads.descarga --help" in cmd:
    print("  --solo-faltantes   descarga solo lo que falta")
elif "app.cli cycle --help" in cmd:
    print("  --forzar   fuerza el ciclo aunque la guarda lo salte")
elif "docker exec -i orbit-app-1 python -" in cmd:
    print("metricas_de_ayer_listas amazon_mx=True")
    print("metricas_de_ayer_listas amazon_us=False")
else:
    print(f"ssh falso sin guion para: {cmd}", file=sys.stderr)
    sys.exit(64)
"""

# Los mismos grep -v del instalador de docs/DEPLOY.md (espejo del checklist).
PATS = [
    "Orbit (ORBIT 03",
    "job_key=ingest:",
    "job_key=ads-salud",
    "job_key=ads_optimizer",
    "job_key=avisos-campana",
    "job_key=impulso-vigia",
    "job_key=jev-senales",
    "app.cli ingest",
    "app.cli ads-salud",
    "app.cli cycle",
    "app.cli avisos-campana",
    "app.cli impulso-vigia",
    "app.cli jev-senales",
]


def _entorno(tmp_path, modo):
    fake = tmp_path / "ssh"
    fake.write_text(FAKE_SSH)
    fake.chmod(0o755)
    sha = (DIR / "sha-desplegado.txt").read_text(encoding="utf-8").strip()
    deploy = subprocess.run(
        ["git", "show", f"{sha}:docs/DEPLOY.md"],
        capture_output=True,
        text=True,
        check=True,
        cwd=RAIZ,
    ).stdout
    bloque_doc = re.search(r"ORBIT_BLOCK=\$\(cat <<'CRON'\n(.*?)\nCRON\n", deploy, re.S).group(1)
    bloque = bloque_doc.splitlines()
    orbit = lambda linea: any(p in linea for p in PATS)  # noqa: E731
    previo = (DIR / "crontab-antes.txt").read_text(encoding="utf-8").splitlines()
    cron = "\n".join([linea for linea in previo if not orbit(linea)] + bloque) + "\n"
    cron_f = tmp_path / "cron.txt"
    cron_f.write_text(cron, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "ORBIT_SSH": "gonserver",
            "CK_MD5_MODE": modo,
            "CK_SHA": sha,
            "CK_ARCHIVOS": str(DIR / "archivos.txt"),
            "CK_CRON": str(cron_f),
        }
    )
    return env


def _corre(tmp_path, modo):
    return subprocess.run(
        ["bash", str(CHECKLIST)],
        env=_entorno(tmp_path, modo),
        text=True,
        capture_output=True,
        check=False,
        cwd=RAIZ,
    )


def test_md5_faltante_en_servidor_es_falla(tmp_path):
    r = _corre(tmp_path, "falta-uno")
    assert "FALLA md5: el servidor reporto" in r.stdout, r.stdout
    assert r.returncode == 1


def test_md5_lectura_vacia_es_no_medido(tmp_path):
    r = _corre(tmp_path, "vacia")
    assert "NO MEDIDO md5" in r.stdout, r.stdout
    assert r.returncode == 4


def test_md5_hash_distinto_es_falla(tmp_path):
    r = _corre(tmp_path, "hash-distinto")
    assert "FALLA md5 de" in r.stdout, r.stdout
    assert r.returncode == 1


def test_servidor_sano_da_cero_fallas(tmp_path):
    r = _corre(tmp_path, "sano")
    assert r.returncode == 0, r.stdout
    assert "0 falla(s), 0 sin medir" in r.stdout
