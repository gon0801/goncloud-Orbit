# Prueba discriminante del guard de verify/Cleanup.md: el rechazo textual de
# ".." tiene que estar ARRIBA del match de prefijo, tanto en el guard de
# PGDATA del paso 1 (pg_ctl) como en borrar_si_launch del paso 2 (rm). El
# guard se EXTRAE del propio Cleanup.md (parseando los bloques ```bash), no
# se reimplementa aca: si el doc vuelve a aceptar un valor con "..", esta
# prueba se pone roja. Se corre sin Postgres: rm, pg_ctl y pg_isready quedan
# shadow-eados por funciones de shell que solo anotan sus argumentos en un
# log, bajo ningun concepto se toca disco fuera de /tmp/pytest.

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

CLEANUP_MD = Path(__file__).parent / "Cleanup.md"


def _bash_blocks() -> list[str]:
    texto = CLEANUP_MD.read_text(encoding="utf-8")
    return re.findall(r"```bash\n(.*?)```", texto, re.DOTALL)


def _bloque_paso1() -> str:
    for bloque in _bash_blocks():
        if 'case "$PGDATA"' in bloque:
            return bloque
    raise AssertionError("no encontre el bloque del guard de PGDATA (paso 1) en Cleanup.md")


def _bloque_paso2() -> str:
    for bloque in _bash_blocks():
        if "borrar_si_launch()" in bloque:
            return bloque
    raise AssertionError("no encontre el bloque de borrar_si_launch (paso 2) en Cleanup.md")


_SHADOW = """
rm() { printf 'RM %s\\n' "$*" >> "$LOGFILE"; return 0; }
pg_ctl() { printf 'PG_CTL %s\\n' "$*" >> "$LOGFILE"; return 0; }
pg_isready() { printf 'PG_ISREADY %s\\n' "$*" >> "$LOGFILE"; return 0; }
"""


def _run(
    script_body: str, env_extra: dict[str, str | None], log: Path
) -> subprocess.CompletedProcess[str]:
    """Corre el script bash dado con rm/pg_ctl/pg_isready neutralizados.

    env_extra mapea nombre de variable -> valor (o None para dejarla NO
    definida, aunque exista en el ambiente heredado).
    """
    env = dict(os.environ)
    for var in ("PGDATA", "SOCK", "ORBIT_SECRETS_DIR"):
        env.pop(var, None)
    for var, val in env_extra.items():
        if val is not None:
            env[var] = val
    env["LOGFILE"] = str(log)
    script = _SHADOW + "\n" + script_body
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _log_lineas(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [linea for linea in log.read_text(encoding="utf-8").splitlines() if linea]


# --- casos compartidos: valores legitimos y ajenos ------------------------


def _mktemp_pgdata() -> str:
    return tempfile.mkdtemp(prefix="orbit-pgdata.", dir="/tmp")


AJENOS_AL_PREFIJO = [
    pytest.param("/mnt/data/appdata/orbit/secrets", id="default-produccion"),
    pytest.param("/tmp", id="tmp-pelado"),
    pytest.param("", id="string-vacio"),
    pytest.param(None, id="variable-no-definida"),
]


# --- paso 1: guard de PGDATA antes de pg_ctl -------------------------------


def test_paso1_pgdata_legitimo_para_pg_ctl(tmp_path):
    pgdata = _mktemp_pgdata()
    log = tmp_path / "log1"
    r = _run(_bloque_paso1(), {"PGDATA": pgdata}, log)
    lineas = _log_lineas(log)
    assert any(linea.startswith("PG_CTL") for linea in lineas), (
        f"esperaba una llamada a pg_ctl para un PGDATA legitimo; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" not in r.stderr, f"no deberia refusar un PGDATA legitimo; stderr={r.stderr!r}"


@pytest.mark.parametrize(
    "valor",
    [
        "/tmp/orbit-pgdata.abc/../../Users/dn",
        "/tmp/orbit-pgdata.abc/../../etc/passwd",
    ],
)
def test_paso1_pgdata_con_traversal_fuera_del_sandbox_es_refusado(valor, tmp_path):
    log = tmp_path / "log1"
    r = _run(_bloque_paso1(), {"PGDATA": valor}, log)
    lineas = _log_lineas(log)
    assert not any(linea.startswith("PG_CTL") for linea in lineas), (
        f"no debia llamarse pg_ctl para un PGDATA con traversal; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" in r.stderr, f"esperaba REFUSADO en stderr; stderr={r.stderr!r}"


def test_paso1_pgdata_con_dotdot_que_resuelve_adentro_del_sandbox_es_refusado(tmp_path):
    # Caso que discrimina el rechazo textual nuevo: el ".." resuelve DE VUELTA
    # adentro del prefix (el chequeo por resolucion solo, sin el rechazo
    # textual, lo aceptaria). Solo lo atrapa el rechazo textual de "..".
    dir_a = _mktemp_pgdata()
    dir_b = _mktemp_pgdata()
    valor = f"{dir_a}/../{Path(dir_b).name}"
    log = tmp_path / "log1"
    r = _run(_bloque_paso1(), {"PGDATA": valor}, log)
    lineas = _log_lineas(log)
    assert not any(linea.startswith("PG_CTL") for linea in lineas), (
        f"un valor con '..' debe refusarse aunque resuelva adentro del sandbox; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" in r.stderr, f"esperaba REFUSADO en stderr; stderr={r.stderr!r}"


@pytest.mark.parametrize("valor", AJENOS_AL_PREFIJO)
def test_paso1_pgdata_ajeno_al_prefijo_es_refusado(valor, tmp_path):
    log = tmp_path / "log1"
    r = _run(_bloque_paso1(), {"PGDATA": valor}, log)
    lineas = _log_lineas(log)
    assert not any(linea.startswith("PG_CTL") for linea in lineas), (
        f"no debia llamarse pg_ctl para PGDATA={valor!r}; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" in r.stderr, f"esperaba REFUSADO en stderr; stderr={r.stderr!r}"


# --- paso 2: borrar_si_launch antes de rm ----------------------------------


def _script_paso2() -> str:
    # borrar_si_launch depende de resolver_path, definida en el bloque del
    # paso 1: se sourcean ambos, en orden, tal como los ejecutaria un humano
    # siguiendo el doc de arriba hacia abajo.
    return _bloque_paso1() + "\n" + _bloque_paso2()


def test_paso2_pgdata_legitimo_se_borra(tmp_path):
    pgdata = _mktemp_pgdata()
    esperado = os.path.realpath(pgdata)
    log = tmp_path / "log2"
    r = _run(_script_paso2(), {"PGDATA": pgdata}, log)
    lineas = _log_lineas(log)
    rm_calls = [linea for linea in lineas if linea.startswith("RM ")]
    assert any(esperado in linea for linea in rm_calls), (
        f"esperaba un rm sobre '{esperado}'; log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )


@pytest.mark.parametrize(
    "valor",
    [
        "/tmp/orbit-pgdata.abc/../../Users/dn",
        "/tmp/orbit-pgdata.abc/../../etc/passwd",
    ],
)
def test_paso2_pgdata_con_traversal_fuera_del_sandbox_es_refusado(valor, tmp_path):
    log = tmp_path / "log2"
    r = _run(_script_paso2(), {"PGDATA": valor}, log)
    lineas = _log_lineas(log)
    assert not any(linea.startswith("RM ") for linea in lineas), (
        f"no debia llamarse rm para PGDATA={valor!r}; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" in r.stderr, f"esperaba REFUSADO en stderr; stderr={r.stderr!r}"


def test_paso2_pgdata_con_dotdot_que_resuelve_adentro_del_sandbox_es_refusado(tmp_path):
    # Mismo caso discriminante que en el paso 1, ahora sobre borrar_si_launch:
    # el ".." resuelve DE VUELTA adentro de /tmp/orbit-pgdata.*, asi que el
    # re-chequeo por resolucion (ya existente en el working tree) lo deja
    # pasar; solo el rechazo textual de ".." (agregado en este cambio) lo
    # atrapa.
    dir_a = _mktemp_pgdata()
    dir_b = _mktemp_pgdata()
    valor = f"{dir_a}/../{Path(dir_b).name}"
    log = tmp_path / "log2"
    r = _run(_script_paso2(), {"PGDATA": valor}, log)
    lineas = _log_lineas(log)
    assert not any(linea.startswith("RM ") for linea in lineas), (
        f"un valor con '..' debe refusarse aunque resuelva adentro del sandbox; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" in r.stderr, f"esperaba REFUSADO en stderr; stderr={r.stderr!r}"


@pytest.mark.parametrize("valor", AJENOS_AL_PREFIJO)
def test_paso2_pgdata_ajeno_al_prefijo_es_refusado(valor, tmp_path):
    log = tmp_path / "log2"
    r = _run(_script_paso2(), {"PGDATA": valor}, log)
    lineas = _log_lineas(log)
    assert not any(linea.startswith("RM ") for linea in lineas), (
        f"no debia llamarse rm para PGDATA={valor!r}; "
        f"log={lineas!r} stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "REFUSADO" in r.stderr, f"esperaba REFUSADO en stderr; stderr={r.stderr!r}"
