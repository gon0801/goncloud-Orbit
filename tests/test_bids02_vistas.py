"""Vistas base de lectura de BIDS 02 (0.b): v_hoja_activa y v_cambio_bid.

v_hoja_activa = una fila por hoja (keyword/product_target) con hoja, ad group
y campana ENABLED, mas tipo_campana (automatica/product_targeting/exact/
phrase/broad). v_cambio_bid = historial UNION de bids del motor confirmados
en ciclo live (origen motor o regreso_por_desplome por inputs.motivo) mas
reversas ok del ledger (origen regreso_del_dueno, bid_despues = old_value
revertido).

Son INTEGRACION: base temporal con TODAS las migraciones en orden, sin la
0011 y sin las reversas, como tests/test_schema_docs.py:41.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

ROOT = Path(__file__).resolve().parents[1]

_ANCLA = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)
_DECIDIDA = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)
_OBSERVADO = dt.datetime(2026, 9, 19, 12, 0, tzinfo=dt.UTC)
_VENTANA_DESDE = dt.date(2026, 8, 15)
_VENTANA_HASTA = dt.date(2026, 9, 5)
_CONFIRMADA = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)
_SELLADA = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.UTC)

MONEDA = {"amazon_mx": "MXN", "amazon_us": "USD"}

FALTA_POSTGRES = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def _db_bids02(prefijo: str):
    """DB temporal con todas las migraciones en orden, sin la 0011 y sin las
    reversas (igual que tests/test_schema_docs.py:41)."""
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        for ruta in sorted((ROOT / "migrations").glob("*.sql")):
            if "0011_" in ruta.name or "_reversa_" in ruta.name:
                continue
            conn.execute(ruta.read_text(encoding="utf-8"))
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _entidad(conn, platform, kind, external, parent=None, *, match_type=None, texto=None):
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, match_type, texto),
    ).fetchone()[0]


def _estado(conn, ad_entity_id, status="ENABLED", *, targeting=None, bid=None, moneda=None):
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, targeting_type, current_bid,"
        " bid_currency, synced_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (ad_entity_id, status, targeting, bid, moneda, _ANCLA),
    )


def _triple(
    conn,
    platform="amazon_mx",
    *,
    campana="ENABLED",
    grupo="ENABLED",
    hoja="ENABLED",
    targeting="MANUAL",
    hoja_kind="keyword",
    match="EXACT",
    tag="",
):
    """Campana + ad group + hoja con sus estados. Devuelve (campana, grupo, hoja)."""
    c = _entidad(conn, platform, "campaign", f"c-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-{tag}", c)
    if hoja_kind == "keyword":
        h = _entidad(conn, platform, "keyword", f"h-{tag}", g, match_type=match, texto=f"kw-{tag}")
    else:
        h = _entidad(conn, platform, "product_target", f"h-{tag}", g)
    _estado(conn, c, campana, targeting=targeting)
    _estado(conn, g, grupo)
    _estado(conn, h, hoja, bid=Decimal("10"), moneda=MONEDA[platform])
    return c, g, h


def _config(conn):
    return conn.execute(
        "INSERT INTO config_version (settings) VALUES (%s) RETURNING id", (Json({}),)
    ).fetchone()[0]


def _ciclo(conn, mode, platform="amazon_mx"):
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES (%s, %s) RETURNING id",
        (mode, platform),
    ).fetchone()[0]


def _decision_bid(conn, *, ciclo, hoja, config, old, new, moneda="MXN", motivo=None, tag=""):
    inputs = {"motivo": motivo, "tag": tag} if motivo else {"tag": tag}
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
        (
            ciclo,
            hoja,
            config,
            _DECIDIDA,
            _OBSERVADO,
            _VENTANA_DESDE,
            _VENTANA_HASTA,
            old,
            new,
            moneda,
            Json(inputs),
        ),
    ).fetchone()[0]


def _decision_pausa(conn, *, ciclo, hoja, config):
    # Ventana madura: pausar exige window_end <= decided_at - 10d (trigger
    # decision_madurez_corte).
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, inputs)"
        " VALUES (%s, %s, 'pause', %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            ciclo,
            hoja,
            config,
            _DECIDIDA,
            _OBSERVADO,
            _VENTANA_DESDE,
            _VENTANA_HASTA,
            Json({}),
        ),
    ).fetchone()[0]


def _aplicacion(conn, decision_id, ciclo_ejecutor, *, verify_ok=True):
    conn.execute(
        "INSERT INTO decision_application (decision_id, applied_cycle_id, confirmed_at,"
        " platform_ack, verify_ok) VALUES (%s, %s, %s, %s, %s)",
        (decision_id, ciclo_ejecutor, _CONFIRMADA, Json({"readback": True}), verify_ok),
    )


def _reversa_ok(conn, decision_id):
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
        " quota_cobrada, resultado, finished_at)"
        " VALUES (%s, 1, 'reversa', %s, false, 'ok', %s)",
        (decision_id, Json({"bid": "viejo"}), _SELLADA),
    )


def _intento(conn, decision_id, seq, tipo, resultado):
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
        " quota_cobrada, resultado, finished_at)"
        " VALUES (%s, %s, %s, %s, false, %s, %s)",
        (decision_id, seq, tipo, Json({"bid": "intento"}), resultado, _SELLADA),
    )


def _login(conn, rol: str, grupo: str) -> None:
    try:
        conn.execute(f"DROP ROLE IF EXISTS {rol}")
        conn.execute(f"CREATE ROLE {rol} LOGIN PASSWORD 'clave' NOSUPERUSER")
    except psycopg.errors.InsufficientPrivilege:
        pytest.fail(
            "ORBIT_TEST_DSN sin CREATEROLE: esta prueba es la unica evidencia de"
            " lectura de las vistas 0060 y no puede saltarse en silencio"
        )
    conn.execute(f"GRANT {grupo} TO {rol}")


@FALTA_POSTGRES
def test_v_hoja_activa_exige_triple_enabled():
    """La vista trae la hoja con hoja, ad group y campana ENABLED, con su
    tipo y su bid. No trae la hoja si UNO de los tres esta PAUSED; tampoco
    un product_ad aunque cuelgue de un triple ENABLED."""
    with _db_bids02("orbit_bids02_hoja") as conn:
        camp, grupo, viva = _triple(conn, tag="viva")
        fantasma = _entidad(conn, "amazon_mx", "product_ad", "pad-fantasma", grupo)
        _estado(conn, fantasma, "ENABLED", bid=Decimal("10"), moneda="MXN")
        _triple(conn, hoja="PAUSED", tag="hoja-pausada")
        _triple(conn, grupo="PAUSED", tag="grupo-pausado")
        _triple(conn, campana="PAUSED", tag="campana-pausada")
        filas = conn.execute(
            "SELECT hoja_id, platform, kind, ad_group_id, campana_id, tipo_campana,"
            " current_bid, bid_currency FROM v_hoja_activa ORDER BY hoja_id"
        ).fetchall()
        assert filas == [(viva, "amazon_mx", "keyword", grupo, camp, "exact", Decimal("10"), "MXN")]


@FALTA_POSTGRES
def test_v_hoja_activa_tipo_campana_cinco_casos():
    """automatica para un product target de campana AUTO; product_targeting
    para uno de campana manual; exact/phrase/broad segun el match type. Un
    match no listado deja tipo_campana NULL (fila presente, no clasificable)."""
    with _db_bids02("orbit_bids02_tipo") as conn:
        _, _, automatica = _triple(conn, targeting="AUTO", hoja_kind="product_target", tag="auto")
        _, _, targeting = _triple(conn, hoja_kind="product_target", tag="manual")
        _, _, exact = _triple(conn, match="EXACT", tag="exact")
        _, _, phrase = _triple(conn, match="PHRASE", tag="phrase")
        _, _, broad = _triple(conn, match="BROAD", tag="broad")
        _, _, minuscula = _triple(conn, match="exact", tag="minuscula")
        filas = dict(conn.execute("SELECT hoja_id, tipo_campana FROM v_hoja_activa").fetchall())
        assert filas == {
            automatica: "automatica",
            targeting: "product_targeting",
            exact: "exact",
            phrase: "phrase",
            broad: "broad",
            minuscula: None,
        }


@FALTA_POSTGRES
def test_v_cambio_bid_motor_live_y_sus_exclusiones():
    """La vista trae el bid confirmado por un ciclo live, con motivo o sin
    el. No trae el de un ciclo shadow, el no verificado, el no-op ni la
    pausa verificada."""
    with _db_bids02("orbit_bids02_cambio") as conn:
        _, _, hoja = _triple(conn, tag="hoja")
        config = _config(conn)
        live = _ciclo(conn, "live")
        shadow = _ciclo(conn, "shadow")
        d_live = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "live"),
            hoja=hoja,
            config=config,
            old=Decimal("10"),
            new=Decimal("8"),
            tag="live",
        )
        _aplicacion(conn, d_live, live)
        d_desplome = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "live"),
            hoja=hoja,
            config=config,
            old=Decimal("8"),
            new=Decimal("10"),
            motivo="regreso_por_desplome",
            tag="desplome",
        )
        _aplicacion(conn, d_desplome, live)
        d_shadow = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "shadow"),
            hoja=hoja,
            config=config,
            old=Decimal("10"),
            new=Decimal("8"),
            tag="shadow",
        )
        _aplicacion(conn, d_shadow, shadow)
        d_sin_verify = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "live"),
            hoja=hoja,
            config=config,
            old=Decimal("10"),
            new=Decimal("8"),
            tag="sin-verify",
        )
        _aplicacion(conn, d_sin_verify, live, verify_ok=False)
        d_noop = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "live"),
            hoja=hoja,
            config=config,
            old=Decimal("10"),
            new=Decimal("10"),
            tag="noop",
        )
        _aplicacion(conn, d_noop, live)
        # La rama motor es solo kind='bid': una pausa verificada en live no entra.
        d_pausa = _decision_pausa(conn, ciclo=_ciclo(conn, "live"), hoja=hoja, config=config)
        _aplicacion(conn, d_pausa, live)
        filas = conn.execute(
            "SELECT hoja_id, confirmado_el, bid_antes, bid_despues, moneda, origen,"
            " decision_id FROM v_cambio_bid ORDER BY decision_id"
        ).fetchall()
        assert filas == [
            (hoja, _CONFIRMADA, Decimal("10"), Decimal("8"), "MXN", "motor", d_live),
            (
                hoja,
                _CONFIRMADA,
                Decimal("8"),
                Decimal("10"),
                "MXN",
                "regreso_por_desplome",
                d_desplome,
            ),
        ]


@FALTA_POSTGRES
def test_v_cambio_bid_regreso_del_dueno():
    """Cada reversa ok de una decision de bid es una fila de origen
    regreso_del_dueno, con bid_despues = old_value revertido. La reversa
    de una pausa no es un cambio de bid; tampoco el intento normal ok ni
    la reversa con error."""
    with _db_bids02("orbit_bids02_dueno") as conn:
        _, _, hoja = _triple(conn, tag="hoja")
        config = _config(conn)
        d_bid = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "live"),
            hoja=hoja,
            config=config,
            old=Decimal("10"),
            new=Decimal("8"),
            tag="bid",
        )
        _reversa_ok(conn, d_bid)
        _intento(conn, d_bid, 2, "normal", "ok")
        _intento(conn, d_bid, 3, "reversa", "error")
        d_pausa = _decision_pausa(conn, ciclo=_ciclo(conn, "live"), hoja=hoja, config=config)
        _reversa_ok(conn, d_pausa)
        filas = conn.execute(
            "SELECT hoja_id, confirmado_el, bid_antes, bid_despues, moneda, origen,"
            " decision_id FROM v_cambio_bid ORDER BY decision_id"
        ).fetchall()
        assert filas == [(hoja, _SELLADA, None, Decimal("10"), "MXN", "regreso_del_dueno", d_bid)]


@FALTA_POSTGRES
def test_vistas_legibles_para_decide_read_admin():
    """app_decide, app_read y app_admin leen las dos vistas, con login real
    (CREATE ROLE LOGIN + GRANT del grupo), no con SET ROLE."""
    with _db_bids02("orbit_bids02_permisos") as conn:
        _, _, hoja = _triple(conn, tag="hoja")
        config = _config(conn)
        live = _ciclo(conn, "live")
        d = _decision_bid(
            conn,
            ciclo=_ciclo(conn, "live"),
            hoja=hoja,
            config=config,
            old=Decimal("10"),
            new=Decimal("8"),
            tag="cambio",
        )
        _aplicacion(conn, d, live)
        base = (
            _test_dsn().rsplit("/", 1)[0]
            + "/orbit_bids02_permisos_"
            + (f"{socket.gethostname().lower()}_{os.getpid()}")
        )
        roles = {
            "app_decide": f"bids02_{os.getpid()}_decide",
            "app_read": f"bids02_{os.getpid()}_read",
            "app_admin": f"bids02_{os.getpid()}_admin",
        }
        for grupo, rol in roles.items():
            _login(conn, rol, grupo)
        try:
            for grupo, rol in roles.items():
                dsn = make_conninfo(base, user=rol, password="clave")
                with psycopg.connect(dsn, autocommit=True) as otro:
                    assert otro.execute("SELECT count(*) FROM v_hoja_activa").fetchone()[0] == 1, (
                        grupo
                    )
                    assert otro.execute("SELECT count(*) FROM v_cambio_bid").fetchone()[0] == 1, (
                        grupo
                    )
        finally:
            for rol in roles.values():
                conn.execute(f"DROP OWNED BY {rol}")
                conn.execute(f"DROP ROLE {rol}")
