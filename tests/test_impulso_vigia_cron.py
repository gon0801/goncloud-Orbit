"""Contrato de la linea de cron de `impulso-vigia` (BIDS 02, I.3).

Desde ingesta-01 A.4 la linea vive DENTRO del bloque instalador de
docs/DEPLOY.md (con `job_key=impulso-vigia`): el instalador la reinstala y
su filtro BORRA la vieja linea suelta, para no duplicarla.
"""

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

LINEA_IMPULSO_VIGIA = (
    "20 8 * * * /usr/bin/flock -n /tmp/impulso-vigia.lock"
    " docker exec orbit-app-1 python -m app.cli impulso-vigia"
    " >> /mnt/data/appdata/orbit/logs/impulso-vigia.log 2>&1"
)


def _filtro_instalador(deploy: str) -> str:
    instalador = next(
        linea for linea in deploy.splitlines() if "crontab -u gon -l 2>/dev/null | grep -v" in linea
    )
    return instalador.split("crontab -u gon -l 2>/dev/null | ", 1)[1].split(" ; printf", 1)[0]


def test_deploy_documenta_la_linea_de_impulso_vigia():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert LINEA_IMPULSO_VIGIA in deploy


def test_instalador_reemplaza_la_linea_de_impulso_vigia():
    """El filtro borra la vieja linea suelta: la reinstalacion deja la del
    bloque una sola vez, no dos."""
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert LINEA_IMPULSO_VIGIA in deploy
    filtros = _filtro_instalador(deploy)
    actual = f"{LINEA_IMPULSO_VIGIA}\n# accounting\n0 0 * * * accounting\n"
    resultado = subprocess.run(
        ["bash", "-c", f"cat | {filtros}"],
        input=actual,
        text=True,
        capture_output=True,
        check=True,
    )
    assert LINEA_IMPULSO_VIGIA not in resultado.stdout
    assert "# accounting\n0 0 * * * accounting\n" in resultado.stdout
