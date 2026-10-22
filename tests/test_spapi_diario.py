"""scripts/spapi-diario.sh: orden, serie y una sola repeticion de lo que fallo.

Corre el guion real con un `docker` falso al frente del PATH. El falso anota
cada llamada en un archivo y sale 1 para las combinaciones "fuente plataforma"
listadas en FALLAN (cada vez que se invocan).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GUION = Path(__file__).resolve().parents[1] / "scripts" / "spapi-diario.sh"
ORDEN = [
    f"{f} {m}"
    for f in ("spapi_orders", "spapi_listings", "spapi_inventario", "spapi_pricing")
    for m in ("amazon_mx", "amazon_us")
]

DOCKER_FALSO = """#!/usr/bin/env bash
# docker exec orbit-app-1 python -m app.cli ingest <fuente> --platform <mercado>
par="$7 $9"
echo "$par" >> "$LLAMADAS"
case ",$FALLAN," in *",${7}:${9},"*) exit 1 ;; esac
exit 0
"""


def corre(tmp_path: Path, fallan: tuple[str, ...] = (), ahora: str | None = "05:00"):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    docker = bin_ / "docker"
    docker.write_text(DOCKER_FALSO)
    docker.chmod(0o755)
    llamadas = tmp_path / "llamadas.txt"
    llamadas.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "LLAMADAS": str(llamadas),
        "FALLAN": ",".join(f.replace(" ", ":") for f in fallan),
    }
    env.pop("SPAPI_DIARIO_AHORA", None)
    if ahora:
        env["SPAPI_DIARIO_AHORA"] = ahora
    r = subprocess.run(["bash", str(GUION)], env=env, capture_output=True, text=True, check=False)
    return r, llamadas.read_text().splitlines()


def test_todo_bien_no_repite(tmp_path):
    r, llamadas = corre(tmp_path)
    assert r.returncode == 0
    assert llamadas == ORDEN
    assert "repito" not in r.stdout


def test_falla_se_repite_una_vez_al_final(tmp_path):
    r, llamadas = corre(tmp_path, fallan=("spapi_inventario amazon_mx",))
    assert llamadas == [*ORDEN, "spapi_inventario amazon_mx"]
    assert "repito spapi_inventario amazon_mx" in r.stdout
    assert "no repito" not in r.stdout


def test_la_falla_no_cancela_las_siguientes(tmp_path):
    _, llamadas = corre(tmp_path, fallan=("spapi_orders amazon_mx",))
    assert llamadas[: len(ORDEN)] == ORDEN


def test_repeticiones_en_el_mismo_orden(tmp_path):
    fallan = ("spapi_pricing amazon_mx", "spapi_orders amazon_us")
    _, llamadas = corre(tmp_path, fallan=fallan)
    assert llamadas[len(ORDEN) :] == ["spapi_orders amazon_us", "spapi_pricing amazon_mx"]


def test_repeticion_que_vuelve_a_fallar_no_se_repite_otra_vez(tmp_path):
    _, llamadas = corre(tmp_path, fallan=("spapi_listings amazon_us",))
    assert llamadas.count("spapi_listings amazon_us") == 2


def test_despues_de_las_0615_no_se_repite_y_el_log_dice_por_que(tmp_path):
    r, llamadas = corre(tmp_path, fallan=("spapi_inventario amazon_mx",), ahora="06:20")
    assert llamadas == ORDEN
    assert "no repito spapi_inventario amazon_mx: ya son las 06:20 UTC" in r.stdout


def test_a_las_0615_justas_todavia_repite(tmp_path):
    _, llamadas = corre(tmp_path, fallan=("spapi_inventario amazon_mx",), ahora="06:15")
    assert llamadas[-1] == "spapi_inventario amazon_mx"
    assert len(llamadas) == len(ORDEN) + 1


def test_sin_variable_usa_la_hora_de_date(tmp_path):
    r, _ = corre(tmp_path, fallan=("spapi_orders amazon_mx",), ahora=None)
    assert (
        "repito spapi_orders amazon_mx" in r.stdout
        or "no repito spapi_orders amazon_mx: ya son las" in r.stdout
    )
