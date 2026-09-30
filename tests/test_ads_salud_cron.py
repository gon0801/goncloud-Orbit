"""Contrato del cron de salud con el instalador documentado."""

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def test_reinstalar_orbit_conserva_una_sola_linea_de_salud():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    linea = next(linea for linea in deploy.splitlines() if "app.cli ads-salud >>" in linea)
    comentario = next(
        linea for linea in deploy.splitlines() if linea.startswith("# job_key=ads-salud")
    )
    instalador = next(
        linea for linea in deploy.splitlines() if "crontab -u gon -l 2>/dev/null | grep -v" in linea
    )
    filtros = instalador.split("crontab -u gon -l 2>/dev/null | ", 1)[1].split(" ; printf", 1)[0]

    actual = f"{comentario}\n{linea}\n# accounting\n0 0 * * * accounting\n"
    resultado = subprocess.run(
        ["bash", "-c", f"cat | {filtros}"],
        input=actual,
        text=True,
        capture_output=True,
        check=True,
    )
    reinstalado = resultado.stdout + comentario + "\n" + linea + "\n"

    assert reinstalado.count(linea + "\n") == 1
    assert sum(linea.startswith("# job_key=ads-salud") for linea in reinstalado.splitlines()) == 1
    assert "# accounting\n0 0 * * * accounting\n" in reinstalado


def test_checker_exige_la_linea_documentada():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    linea = next(linea for linea in deploy.splitlines() if "app.cli ads-salud >>" in linea)
    checker = (RAIZ / "tools/check_ads_salud_cron.sh").read_text()
    assert f"esperada='{linea}'" in checker
