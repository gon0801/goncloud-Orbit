"""Pruebas de regresion de Fase 2.3: integridad transaccional, locks y redaccion."""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import pq

from app import bloqueo_dueno_efecto, cycle, db, redaction, retiro_anuncios
from app.precio import corrida as precio_corrida
from tests.helpers.pg import db_desde_template, pg_disponible

_skip_pg = pytest.mark.skipif(not pg_disponible(), reason="sin Postgres local")


def test_secret_scrub_filter_redacta_exc_info_y_stack_info():
    secreto = "tok_secreto_fase2_999"
    redaction.register_secret(secreto)
    logger = logging.getLogger("app.test_scrub_exc")
    redaction.install_scrub_filter(logger)

    registros: list[str] = []

    class _Captura(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            fmt = logging.Formatter("%(message)s\n%(exc_text)s")
            registros.append(fmt.format(record))

    handler = _Captura()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        try:
            raise RuntimeError(f"fallo con {secreto} en traceback")
        except RuntimeError:
            logger.warning("error capturado %s", secreto, exc_info=True)
    finally:
        logger.removeHandler(handler)

    assert registros
    assert secreto not in registros[0]
    assert redaction.REDACTED in registros[0]


def test_aplicar_host_override_soporta_uri_y_conninfo():
    assert db.aplicar_host_override("postgresql://u:p@127.0.0.1:5432/db", "dbhost") == (
        "postgresql://u:p@dbhost:5432/db"
    )
    assert db.aplicar_host_override("host=127.0.0.1 port=5432 dbname=orbit", "dbhost") == (
        "host=dbhost port=5432 dbname=orbit"
    )
    assert db.aplicar_host_override("host=localhost dbname=orbit", "dbhost") == (
        "host=dbhost dbname=orbit"
    )


@_skip_pg
def test_db_connect_registra_password_incluso_en_exito():
    base = os.environ["ORBIT_TEST_DSN"]
    # El cluster temporal usa auth=trust, asi que una password distintiva conecta igual.
    dsn_con_pass = base.replace("orbit:orbit@", "orbit:clave_super_secreta_f2@")
    conn = db.connect(dsn_con_pass)
    try:
        assert redaction.scrub("fuga clave_super_secreta_f2 aqui") == (
            f"fuga {redaction.REDACTED} aqui"
        )
    finally:
        conn.close()


@_skip_pg
def test_guard_apply_y_tx_durable_sobreviven_rollback_posterior():
    """C1: _guard_apply no deja conn en INTRANS y tx_durable commitea de verdad."""
    with db_desde_template("orbit_f2_c1") as (conn, dsn):
        conn.execute(
            "INSERT INTO ads_optimizer_lock (job_key, owner, ttl_seconds)"
            " VALUES ('ads_opt:amazon_us', 'dueno-1', 600)"
        )
        conn.commit()
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE

        hb = psycopg.connect(dsn, autocommit=True)
        try:
            # Con hb: conn sigue IDLE
            guard_hb = cycle._guard_apply(conn, "ads_opt:amazon_us", "dueno-1", lambda: None, hb=hb)
            guard_hb()
            assert conn.info.transaction_status == pq.TransactionStatus.IDLE

            # Sin hb: conn vuelve a IDLE tras consultar el lock
            guard_sin_hb = cycle._guard_apply(conn, "ads_opt:amazon_us", "dueno-1", lambda: None)
            guard_sin_hb()
            assert conn.info.transaction_status == pq.TransactionStatus.IDLE

            # Incluso si hubo un SELECT previo (INTRANS), tx_durable hace COMMIT real
            conn.execute("SELECT 1").fetchone()
            assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
            with db.tx_durable(conn):
                conn.execute(
                    "INSERT INTO config_version (label, settings)"
                    " VALUES ('durable-c1', '{}'::jsonb)"
                )
            conn.rollback()  # simula aborto posterior del lote
            assert (
                conn.execute(
                    "SELECT count(*) FROM config_version WHERE label = 'durable-c1'"
                ).fetchone()[0]
                == 1
            )
        finally:
            hb.close()


@_skip_pg
def test_sello_fallido_se_commitea_aunque_conn_este_en_inerror():
    """A6: si una excepcion dejo conn en INERROR, _sello_fallido igual sella 'failed'."""
    with db_desde_template("orbit_f2_a6") as (conn, _dsn):
        cycle_id = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform, status)"
            " VALUES ('shadow', 'amazon_us', 'running') RETURNING id"
        ).fetchone()[0]
        conn.commit()

        with pytest.raises(psycopg.Error):
            conn.execute("SELECT * FROM tabla_inexistente_f2")
        assert conn.info.transaction_status == pq.TransactionStatus.INERROR

        cycle._sello_fallido(
            conn, cycle_id, RuntimeError("boom"), cycle._Contadores(), ciclos_muertos=[]
        )
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        estado = conn.execute(
            "SELECT status FROM optimizer_cycle WHERE id = %s", (cycle_id,)
        ).fetchone()[0]
        assert estado == "failed"


@_skip_pg
def test_bloqueo_dueno_suelta_lock_en_inerror_y_retiro_deja_idle():
    """A4: _suelta libera el advisory lock aunque conn este en INERROR; _bloqueados deja IDLE."""
    with db_desde_template("orbit_f2_a4") as (conn1, dsn):
        conn2 = psycopg.connect(dsn, autocommit=False)
        try:
            assert bloqueo_dueno_efecto._toma(conn1, 42, "abc123") is True
            assert bloqueo_dueno_efecto._toma(conn2, 42, "abc123") is False

            with pytest.raises(psycopg.Error):
                conn1.execute("SELECT 1/0")
            assert conn1.info.transaction_status == pq.TransactionStatus.INERROR

            bloqueo_dueno_efecto._suelta(conn1, 42, "abc123")
            assert conn1.info.transaction_status == pq.TransactionStatus.IDLE
            assert bloqueo_dueno_efecto._toma(conn2, 42, "abc123") is True
            bloqueo_dueno_efecto._suelta(conn2, 42, "abc123")

            with retiro_anuncios._bloqueados(conn1, ["k1", "k2"]):
                pass
            assert conn1.info.transaction_status == pq.TransactionStatus.IDLE
        finally:
            conn2.close()


@_skip_pg
def test_corrida_lock_ocupado_no_borra_claim_ajeno():
    """A4: si otro dueno tiene el lock de precio, _tomar_lock falla sin borrar el claim ajeno."""
    with db_desde_template("orbit_f2_precio_lock", autocommit=True) as (conn, _dsn):
        precio_corrida._tomar_lock(conn, "amazon_mx", "dueno-a")
        try:
            with pytest.raises(precio_corrida.LockOcupado):
                precio_corrida._tomar_lock(conn, "amazon_mx", "dueno-b")
            fila = conn.execute(
                "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s",
                (precio_corrida.motor_lock("amazon_mx"),),
            ).fetchone()
            assert fila == ("dueno-a",)
        finally:
            precio_corrida._soltar_lock(conn, "amazon_mx", "dueno-a")


def test_retiro_ejecuta_pendientes_no_traga_apply_abortado(monkeypatch):
    """A2: ApplyAbortado (perdida de lease) debe propagarse, no quedar como 'pendiente'."""

    def _aborta(*_args, **_kwargs):
        raise cycle.ApplyAbortado("lease perdido")

    monkeypatch.setattr(retiro_anuncios.archivar, "pausar_anuncios", _aborta)

    class _FakeConn:
        autocommit = True

        def execute(self, sql, *_a):
            val = ("rev-1",) if "reversa" in sql else (1,)
            return type("_C", (), {"fetchone": lambda _s: val})()

    anuncio = SimpleNamespace(ad_id_externo="ad-1")
    plan = SimpleNamespace(plataforma="amazon_us", motivo="sin_pedidos")

    with pytest.raises(cycle.ApplyAbortado):
        retiro_anuncios._ejecuta_pendientes(
            _FakeConn(),
            plan,
            [(10, anuncio, 99)],
            {"ad-1": {"adId": "ad-1", "state": "ENABLED"}},
            "lote-1",
            None,
            object(),
            "dueno",
            None,
        )
