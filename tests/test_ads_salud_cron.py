"""Contrato del cron de salud con el bloque canonico (ARREGLOS 01 N)."""

import os
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
BLOQUE = RAIZ / "tools/despliegue/crontab-orbit.txt"


def _linea_canonica() -> str:
    return next(
        linea
        for linea in BLOQUE.read_text(encoding="utf-8").splitlines()
        if "app.cli ads-salud" in linea and not linea.startswith("#")
    )


def test_el_bloque_canonico_trae_una_sola_linea_de_salud():
    lineas = [
        linea
        for linea in BLOQUE.read_text(encoding="utf-8").splitlines()
        if "app.cli ads-salud" in linea and not linea.startswith("#")
    ]
    assert len(lineas) == 1, f"ads-salud: {len(lineas)} lineas"
    assert lineas[0].startswith("*/10 10-12 * * *")
    assert "cron-avisa" in lineas[0], "la linea conserva su envoltura de aviso"


def test_comentario_documentado_coincide_con_el_instalado():
    documentado = next(
        linea
        for linea in BLOQUE.read_text(encoding="utf-8").splitlines()
        if linea.startswith("# job_key=ads-salud")
    )
    evidencia = (RAIZ / "docs/evidencia/ads-proteccion-01/H4/cron-instalado.md").read_text(
        encoding="utf-8"
    )
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
    return _linea_canonica()


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


def test_checker_acepta_wrapper_productivo_sin_relajar_el_contrato(tmp_path):
    linea = (
        '*/10 10-12 * * * /usr/local/bin/cron-avisa --nombre "Salud de la publicidad" '
        "--cada 24 --tras 3 -- /bin/bash -c 'docker exec orbit-app-1 python -m app.cli ads-salud' "
        ">> /mnt/data/appdata/orbit/logs/ads-salud.log 2>&1"
    )
    resultado = _ejecuta_checker(tmp_path, salida=linea + "\n")
    assert resultado.returncode == 0, resultado.stderr
    assert "ads-salud cron ok" in resultado.stdout

    for invalida in (
        linea.replace("*/10 10-12", "*/5 10-12"),
        linea.replace("orbit-app-1", "otro-app-1"),
        linea.replace("--tras 3", "--tras 5"),
        linea + "; touch " + str(tmp_path / "ejecutado"),
    ):
        resultado = _ejecuta_checker(tmp_path, salida=invalida + "\n")
        assert resultado.returncode == 1, invalida
        assert "total=1 exacta=0" in resultado.stderr
    assert not (tmp_path / "ejecutado").exists()

    resultado = _ejecuta_checker(tmp_path, salida=linea + "\n" + _linea_documentada() + "\n")
    assert resultado.returncode == 1
    assert "total=2 exacta=2" in resultado.stderr
