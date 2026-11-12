"""Contrato de la linea de cron de `jev-senales` (JEV ADS 02, S.4).

Desde ARREGLOS 01 N la linea vive en el bloque canonico
`tools/despliegue/crontab-orbit.txt` (con `job_key=jev-senales`), que instala
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


def test_el_bloque_canonico_trae_una_sola_linea_de_jev_senales():
    lineas = _lineas("jev-senales --aplicar")
    assert len(lineas) == 1, f"jev-senales: {len(lineas)} lineas"
    assert lineas[0].startswith("30 9,21 * * *")
    assert "cron-avisa" in lineas[0], "la linea conserva su envoltura de aviso"


def test_el_bloque_documenta_su_job_key():
    bloque = BLOQUE.read_text(encoding="utf-8").splitlines()
    indice = next(i for i, ln in enumerate(bloque) if "app.cli jev-senales" in ln)
    assert bloque[indice - 1].startswith("# job_key=jev-senales")
