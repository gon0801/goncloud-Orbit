"""Contrato de la linea de cron de `impulso-vigia` (BIDS 02, I.3).

Desde ARREGLOS 01 N la linea vive en el bloque canonico
`tools/despliegue/crontab-orbit.txt` (con `job_key=impulso-vigia`), que instala
`tools/despliegue/cron_orbit.sh`; el despliegue reinstala ese bloque completo.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BLOQUE = RAIZ / "tools/despliegue/crontab-orbit.txt"


def _lineas(comando: str) -> list[str]:
    return [
        ln
        for ln in BLOQUE.read_text(encoding="utf-8").splitlines()
        if f"app.cli {comando}" in ln and not ln.startswith("#")
    ]


def test_el_bloque_canonico_trae_una_sola_linea_de_impulso_vigia():
    lineas = _lineas("impulso-vigia")
    assert len(lineas) == 1, f"impulso-vigia: {len(lineas)} lineas"
    assert lineas[0].startswith("20 8 * * *")
    assert "cron-avisa" in lineas[0], "la linea conserva su envoltura de aviso"
