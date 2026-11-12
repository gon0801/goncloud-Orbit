"""Contrato del cron de placements con el bloque canonico (ingesta-01).

Antes habia una linea propia `ingest metrics --placements` (07:25); desde
ingesta-01 A.4 placements se ingiere junto con metricas y productos en
`ingest diario`. Desde ARREGLOS 01 N el bloque vive en
`tools/despliegue/crontab-orbit.txt`; este test pinza que el cron viejo ya no
esta y que el diario si.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BLOQUE = RAIZ / "tools/despliegue/crontab-orbit.txt"


def test_ya_no_hay_cron_propio_de_placements():
    bloque = BLOQUE.read_text(encoding="utf-8")
    assert "--placements >> /mnt/data/appdata/orbit/logs/ingest-placements.log" not in bloque, (
        "placements vive dentro del diario desde ingesta-01 A.4"
    )
    assert "python -m app.cli ingest diario" in bloque
