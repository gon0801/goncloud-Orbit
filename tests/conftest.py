"""Fixtures compartidas de la suite.

ORBIT 04 3.3 (`app.notifica`): el canal Telegram JAMAS sale a la red real
desde los tests (prohibicion de la task: TODO mockeado). El autouse apunta
`ORBIT_SECRETS_DIR` a un dir VACIO (canal deshabilitado) y borra el cache de
config del canal antes y despues de CADA test; los tests que necesitan el
canal lo configuran con monkeypatch (se restaura solo) y `notifica._reset()`.

R21 de JEV ADS 01 (`app.redaction`): el registro de secretos es global al
proceso. Cada test termina con el registro que encontro, para que el token
corto de un fixture no deforme los textos de las pruebas siguientes.
"""

from __future__ import annotations

import os
import time

import pytest

# La base y el CI fechan en UTC; sin esto date.today() da otro dia en la Mac (UTC-7) por la tarde.
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):  # Windows no tiene tzset; ahí TZ solo afecta a subprocesos
    time.tzset()


def ahora_db(conn):
    """El "ahora" de Postgres. Las filas que sella now() se comparan contra
    este reloj y no contra datetime.now() de Python: en Docker de la Mac la
    base y Python difieren unos ms y una fila recien escrita queda en el futuro."""
    return conn.execute("SELECT clock_timestamp()").fetchone()[0]


@pytest.fixture(autouse=True)
def _aplicador_de_prueba_nace_con_el_claim(monkeypatch):
    """ARREGLOS 05 A: la cerca del sello exige la fila del claim, igual que el
    ciclo la exige al construir el aplicador (toma el claim antes). Todo
    `Aplicador` de prueba sobre una conexion real nace dueno de su claim
    `(job_key, owner)`.

    El claim se toma por una conexion PROPIA, corta y autocommit, y solo si la
    conexion del aplicador no tiene una transaccion abierta: tomado dentro de
    la transaccion de otra, el cerrojo de la fila se queda retenido y el latido
    del ciclo (que va por otra conexion) se cuelga esperandolo. Con tope de
    espera, para que un cerrojo ajeno falle la prueba en vez de colgarla."""
    import psycopg
    from psycopg import pq

    from app import apply, cycle, lock

    original = apply.Aplicador.__init__

    def nace(self, conn, *args, **kwargs):
        original(self, conn, *args, **kwargs)
        if (
            not isinstance(conn, psycopg.Connection)
            or conn.info.transaction_status != pq.TransactionStatus.IDLE
        ):
            return
        propia = cycle._abre_heartbeat(conn)
        if propia is None:
            return
        try:
            lock.acota_espera_de_cerrojos(propia, transaccional=False)
            lock.claim_lock(propia, self.job_key, self.owner)
        finally:
            propia.close()

    monkeypatch.setattr(apply.Aplicador, "__init__", nace)


@pytest.fixture(autouse=True)
def _canal_telegram_deshabilitado(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("ORBIT_SECRETS_DIR", str(tmp_path_factory.mktemp("secrets_vacios")))
    monkeypatch.delenv("ORBIT_DSN_READ", raising=False)
    from app import notifica

    notifica._reset()
    yield
    notifica._reset()


@pytest.fixture(autouse=True)
def _registro_de_secretos_aislado():
    from app import redaction

    with redaction._lock:
        antes = list(redaction._secrets)
    yield
    with redaction._lock:
        redaction._secrets[:] = antes


@pytest.fixture
def db_template():
    """Base Postgres aislada clonada del template canonico de la sesion."""
    from tests.helpers.pg import db_desde_template, pg_disponible

    if not pg_disponible():
        pytest.skip("sin Postgres local")
    with db_desde_template("orbit_fx") as (conn, dsn):
        yield conn, dsn


def pytest_sessionfinish(session, exitstatus):
    from tests.helpers.pg import limpiar_templates

    limpiar_templates()


def parte_de(nodeid: str, total: int) -> int:
    """Parte 1..total de una prueba. Cada prueba cae en una sola parte."""
    import zlib

    return zlib.crc32(nodeid.encode("utf-8")) % total + 1


def pytest_collection_modifyitems(config, items):
    # ORBIT_PARTE=i/N reparte la bateria entre N jobs de CI en paralelo: la
    # union de las N partes es la bateria entera (tests/test_partes.py).
    valor = __import__("os").environ.get("ORBIT_PARTE", "")
    if not valor:
        return
    i, n = (int(x) for x in valor.split("/"))
    if not 1 <= i <= n:
        raise pytest.UsageError(f"ORBIT_PARTE fuera de rango: {valor}")
    fuera = [item for item in items if parte_de(item.nodeid, n) != i]
    if fuera:
        config.hook.pytest_deselected(items=fuera)
        items[:] = [item for item in items if parte_de(item.nodeid, n) == i]
