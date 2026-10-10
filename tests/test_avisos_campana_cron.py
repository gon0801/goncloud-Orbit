"""Contrato de la linea de cron de `avisos-campana` (BIDS 02, V.4).

La linea vive suelta en docs/DEPLOY.md (como la de precio, fuera del bloque
del instalador de ORBIT 03) y el filtro de ese instalador no debe borrarla.
"""

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

LINEA_AVISOS_CAMPANA = (
    "5 8 * * * /usr/bin/flock -n /tmp/avisos-campana.lock"
    " docker exec orbit-app-1 python -m app.cli avisos-campana"
    " >> /mnt/data/appdata/orbit/logs/avisos-campana.log 2>&1"
)


def _filtro_instalador(deploy: str) -> str:
    instalador = next(
        linea for linea in deploy.splitlines() if "crontab -u gon -l 2>/dev/null | grep -v" in linea
    )
    return instalador.split("crontab -u gon -l 2>/dev/null | ", 1)[1].split(" ; printf", 1)[0]


def test_deploy_documenta_la_linea_de_avisos():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert LINEA_AVISOS_CAMPANA in deploy


def test_instalador_no_borra_la_linea_de_avisos():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert LINEA_AVISOS_CAMPANA in deploy
    filtros = _filtro_instalador(deploy)
    actual = f"# job_key=avisos-campana\n{LINEA_AVISOS_CAMPANA}\n"
    resultado = subprocess.run(
        ["bash", "-c", f"cat | {filtros}"],
        input=actual,
        text=True,
        capture_output=True,
        check=True,
    )
    assert LINEA_AVISOS_CAMPANA in resultado.stdout
