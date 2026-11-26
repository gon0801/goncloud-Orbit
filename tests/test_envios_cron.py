"""Contrato de las lineas de cron de Envios (ENVIOS 01, Task 5).

Las tres viven en el bloque canonico `tools/despliegue/crontab-orbit.txt`, que
instala `tools/despliegue/cron_orbit.sh`; el despliegue reinstala ese bloque
completo. Cada 4 h y escalonadas, con aviso (`cron-avisa --cada 4`) y candado
propio (`flock -n -E 75`).
"""

import shlex
from pathlib import Path

import pytest

from app import cli

RAIZ = Path(__file__).resolve().parents[1]
BLOQUE = RAIZ / "tools/despliegue/crontab-orbit.txt"

# job_key, minuto y hora UTC, nombre visible del aviso, candado, argumentos del CLI
CRONES = [
    (
        "envios:amazon_mx",
        "25 2-22/4",
        "Envíos de Amazon México",
        "/tmp/envios-amazon-mx.lock",
        "ingest spapi_orders --platform amazon_mx",
        "02:25, 06:25, 10:25, 14:25, 18:25, 22:25",
    ),
    (
        "envios:amazon_us",
        "32 2-22/4",
        "Envíos de Amazon Estados Unidos",
        "/tmp/envios-amazon-us.lock",
        "ingest spapi_orders --platform amazon_us",
        "02:32, 06:32, 10:32, 14:32, 18:32, 22:32",
    ),
    (
        "envios:meli",
        "40 2-22/4",
        "Envíos de Mercado Libre",
        "/tmp/envios-meli.lock",
        "ingest envios_meli",
        "02:40, 06:40, 10:40, 14:40, 18:40, 22:40",
    ),
]


def _linea_de(job_key: str) -> str:
    bloque = BLOQUE.read_text(encoding="utf-8").splitlines()
    marcas = [i for i, ln in enumerate(bloque) if ln.startswith(f"# job_key={job_key} ")]
    assert len(marcas) == 1, f"{job_key}: {len(marcas)} comentarios job_key"
    return bloque[marcas[0] + 1]


@pytest.mark.parametrize(("job_key", "cuando", "nombre", "candado", "argumentos", "_h"), CRONES)
def test_el_bloque_canonico_trae_la_linea_de_envios(
    job_key, cuando, nombre, candado, argumentos, _h
):
    assert _linea_de(job_key) == (
        f"{cuando} * * * /usr/local/bin/cron-avisa"
        f' --nombre "{nombre}" --cada 4 -- /bin/bash -c'
        f" '/usr/bin/flock -n -E 75 {candado}"
        f" docker exec orbit-app-1 python -m app.cli {argumentos}'"
        " >> /mnt/data/appdata/orbit/logs/envios.log 2>&1"
    )


@pytest.mark.parametrize("job_key", [c[0] for c in CRONES])
def test_el_comando_instalado_lo_acepta_el_cli(job_key, monkeypatch, capsys):
    """Sin DSN el pipeline sale 2 (fail-closed) despues de validar sus argumentos;
    un subcomando o una plataforma inexistentes reventarian antes, en argparse."""
    monkeypatch.delenv("ORBIT_DSN_INGEST", raising=False)
    envuelto = shlex.split(_linea_de(job_key).split(" /bin/bash -c ", 1)[1])[0]
    argv = shlex.split(envuelto.split("python -m app.cli ", 1)[1])

    assert cli.main(argv) == 2
    assert "ORBIT_DSN_INGEST no esta definido" in capsys.readouterr().err


@pytest.mark.parametrize(("job_key", "_c", "_n", "_l", "argumentos", "horarios"), CRONES)
def test_la_tabla_de_deploy_documenta_la_linea_de_envios(job_key, _c, _n, _l, argumentos, horarios):
    deploy = (RAIZ / "docs/DEPLOY.md").read_text(encoding="utf-8")
    filas = [ln for ln in deploy.splitlines() if ln.startswith("| ") and f"`{job_key}`" in ln]
    assert len(filas) == 1, f"{job_key}: {len(filas)} filas en la tabla de crons"
    assert filas[0].startswith(f"| {horarios} | `{job_key}` | `python -m app.cli {argumentos}`")


def test_la_verificacion_de_deploy_lee_el_crontab_de_gon():
    """`ssh goncloud` entra como root y el bloque Orbit vive en el crontab de gon:
    sin `-u gon` la verificacion sale vacia aunque la instalacion este bien."""
    deploy = (RAIZ / "docs/DEPLOY.md").read_text(encoding="utf-8")
    seccion = deploy.split(" cada 4 h (ENVIOS 01)\n", 1)[1].split("\n## ", 1)[0]
    seccion = seccion.split("\n### ", 1)[0]
    comandos = [
        ln.split("#")[0].rstrip() for ln in seccion.splitlines() if ln.startswith("crontab -")
    ]

    assert comandos == ["crontab -u gon -l | grep -A1 'job_key=envios:'"]
