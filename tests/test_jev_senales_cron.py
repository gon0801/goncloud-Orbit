"""Contrato de la linea de cron de `jev-senales` (JEV ADS 02, S.4).

La linea vive suelta en docs/DEPLOY.md (como la de precio, fuera del bloque
del instalador de ORBIT 03) y el filtro de ese instalador no debe borrarla.
"""

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

LINEA_JEV_SENALES = (
    "30 9,21 * * * /usr/bin/flock -n /tmp/jev-senales.lock"
    " docker exec orbit-app-1 python -m app.cli jev-senales --aplicar"
    " >> /mnt/data/appdata/orbit/logs/jev-senales.log 2>&1"
)


def _filtro_instalador(deploy: str) -> str:
    instalador = next(
        linea for linea in deploy.splitlines() if "crontab -u gon -l 2>/dev/null | grep -v" in linea
    )
    return instalador.split("crontab -u gon -l 2>/dev/null | ", 1)[1].split(" ; printf", 1)[0]


def test_deploy_documenta_la_linea_de_jev_senales():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert LINEA_JEV_SENALES in deploy


def test_instalador_no_borra_la_linea_de_jev_senales():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert LINEA_JEV_SENALES in deploy
    filtros = _filtro_instalador(deploy)
    actual = f"# job_key=jev-senales\n{LINEA_JEV_SENALES}\n"
    resultado = subprocess.run(
        ["bash", "-c", f"cat | {filtros}"],
        input=actual,
        text=True,
        capture_output=True,
        check=True,
    )
    assert LINEA_JEV_SENALES in resultado.stdout
