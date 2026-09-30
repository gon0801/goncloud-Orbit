"""Contrato del cron de salud con el instalador documentado."""

import os
import subprocess
from pathlib import Path

import pytest

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


def test_comentario_documentado_coincide_con_el_instalado():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    documentado = next(
        linea for linea in deploy.splitlines() if linea.startswith("# job_key=ads-salud")
    )
    evidencia = (RAIZ / "docs/evidencia/ads-proteccion-01/H4/cron-instalado.md").read_text()
    instalado = next(linea for linea in evidencia.splitlines() if linea.startswith("comentario='"))
    assert documentado == instalado.removeprefix("comentario='").removesuffix("'")


def _ejecuta_checker(tmp_path, *, salida="", error="", estado=0):
    fake = tmp_path / "crontab"
    fake.write_text(
        '#!/bin/sh\n[ "$*" = "-u gon -l" ] || exit 66\n'
        'printf "%s" "$FAKE_OUT"\nprintf "%s" "$FAKE_ERR" >&2\nexit "$FAKE_STATUS"\n'
    )
    fake.chmod(0o755)
    entorno = os.environ.copy()
    entorno.update(
        {
            "PATH": f"{tmp_path}:{entorno['PATH']}",
            "FAKE_OUT": salida,
            "FAKE_ERR": error,
            "FAKE_STATUS": str(estado),
        }
    )
    return subprocess.run(
        ["bash", str(RAIZ / "tools/check_ads_salud_cron.sh")],
        env=entorno,
        text=True,
        capture_output=True,
        check=False,
    )


def _linea_documentada():
    deploy = (RAIZ / "docs/DEPLOY.md").read_text()
    return next(linea for linea in deploy.splitlines() if "app.cli ads-salud >>" in linea)


def test_checker_sin_crontab_informa_cero_lineas(tmp_path):
    resultado = _ejecuta_checker(tmp_path, error="no crontab for gon\n", estado=1)
    assert resultado.returncode == 1
    assert "ads-salud cron invalido: total=0 exacta=0" in resultado.stderr


@pytest.mark.parametrize(
    ("lineas", "codigo", "mensaje"),
    [
        (1, 0, "ads-salud cron ok: una linea exacta"),
        (2, 1, "ads-salud cron invalido: total=2 exacta=2"),
    ],
)
def test_checker_cuenta_lineas_exactas(tmp_path, lineas, codigo, mensaje):
    resultado = _ejecuta_checker(tmp_path, salida=(_linea_documentada() + "\n") * lineas)
    assert resultado.returncode == codigo
    assert mensaje in resultado.stdout + resultado.stderr


def test_checker_rechaza_linea_parecida(tmp_path):
    linea = _linea_documentada().replace("*/10 10-12", "*/5 10-12")
    resultado = _ejecuta_checker(tmp_path, salida=linea + "\n")
    assert resultado.returncode == 1
    assert "ads-salud cron invalido: total=1 exacta=0" in resultado.stderr


def test_checker_propaga_error_operativo(tmp_path):
    resultado = _ejecuta_checker(tmp_path, error="permission denied\n", estado=23)
    assert resultado.returncode == 23
    assert "permission denied" in resultado.stderr
    assert "cron invalido" not in resultado.stderr
