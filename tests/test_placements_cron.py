"""Contrato del cron de placements con el instalador documentado (ingesta-01).

Antes habia una linea propia `ingest metrics --placements` (07:25); desde
ingesta-01 A.4 placements se ingiere junto con metricas y productos en
`ingest diario`. Este test pinza que el cron viejo ya no se agenda y que el
instalador lo borra de crontabs ajenos, sin tocar las lineas de accounting.
"""

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

LINEA_VIEJA = (
    '25 7 * * * FECHA=$(date -u -d "31 days ago" +\\%F)'
    ' FECHA_FIN=$(date -u -d "1 day ago" +\\%F) && docker exec orbit-app-1'
    ' python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN"'
    " --placements >> /mnt/data/appdata/orbit/logs/ingest-placements.log 2>&1"
)


def _filtro_instalador(deploy: str) -> str:
    instalador = next(
        linea for linea in deploy.splitlines() if "crontab -u gon -l 2>/dev/null | grep -v" in linea
    )
    return instalador.split("crontab -u gon -l 2>/dev/null | ", 1)[1].split(" ; printf", 1)[0]


def test_ya_no_hay_cron_propio_de_placements():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    assert "--placements >> /mnt/data/appdata/orbit/logs/ingest-placements.log" not in deploy, (
        " placements vive dentro del diario desde ingesta-01 A.4"
    )
    assert "python -m app.cli ingest diario" in deploy


def test_instalador_borra_el_cron_viejo_de_placements():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    filtros = _filtro_instalador(deploy)
    actual = (
        f"# job_key=ingest:metrics:placements\n{LINEA_VIEJA}\n"
        "# accounting\n0 0 * * * accounting\n"
    )
    resultado = subprocess.run(
        ["bash", "-c", f"cat | {filtros}"],
        input=actual,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "ingest-placements" not in resultado.stdout
    assert "# accounting\n0 0 * * * accounting\n" in resultado.stdout
