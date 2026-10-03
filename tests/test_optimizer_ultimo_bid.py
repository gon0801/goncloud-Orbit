"""ultimo_bid_aplicado y permite_reversa_bid (D.2, ads-proteccion-01).

(a) UNITARIOS (sin DB): la decision pura permite_reversa_bid en sus bordes
    (D+9 bloquea, D+10 emite; misma direccion pasa; SinHistoriaBid pasa;
    HistoriaBidRota y fin_ventana_bids None bloquean por fail-closed).
(b) INTEGRACION (patron _db_temporal COPIADO de test_optimizer_goals):
    ultimo_bid_aplicado contra Postgres real — solo el bid aplicado mas
    reciente, solo verify_ok IS TRUE y solo ciclo ejecutor LIVE, y la fecha
    del cambio es la fecha UTC de confirmed_at (no la de la zona de sesion).
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from contextlib import contextmanager
from decimal import Decimal

import pytest
from psycopg.types.json import Json
from test_schema import SQL, SQL2, _postgres_obligatorio_ausente, _test_dsn

from app.optimizer import goals as g

CONFIRMADO = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.UTC)
D = dt.date(2026, 8, 1)


# ---------------------------------------------------------------------------
# (a) UNITARIOS - permite_reversa_bid (pura)
# ---------------------------------------------------------------------------


def test_permite_reversa_bid_bordes():
    arriba = g.UltimoBidAplicado(direccion=1, fecha_cambio=D)
    abajo = g.UltimoBidAplicado(direccion=-1, fecha_cambio=D)
    assert g.permite_reversa_bid(g.SinHistoriaBid(), nueva_direccion=-1, fin_ventana_bids=None)
    assert not g.permite_reversa_bid(
        g.HistoriaBidRota(), nueva_direccion=-1, fin_ventana_bids=D + dt.timedelta(days=99)
    )
    assert g.permite_reversa_bid(
        arriba, nueva_direccion=1, fin_ventana_bids=D + dt.timedelta(days=2)
    )
    assert not g.permite_reversa_bid(
        arriba, nueva_direccion=-1, fin_ventana_bids=D + dt.timedelta(days=9)
    )
    assert g.permite_reversa_bid(
        arriba, nueva_direccion=-1, fin_ventana_bids=D + dt.timedelta(days=10)
    )
    assert not g.permite_reversa_bid(arriba, nueva_direccion=-1, fin_ventana_bids=None)
    assert not g.permite_reversa_bid(
        abajo, nueva_direccion=1, fin_ventana_bids=D + dt.timedelta(days=9)
    )
    assert g.permite_reversa_bid(
        abajo, nueva_direccion=1, fin_ventana_bids=D + dt.timedelta(days=10)
    )


def test_permite_reversa_bid_bajo_cada_politica():
    """A6-r2 B2 (D.2 under each policy, pura): bajo bandas_v1 la reversa
    exige 10 dias (el cpc, si llega, se ignora); bajo evidencia_v2 el
    check de dias se REEMPLAZA por CPC post-cambio >= 20 clics (5 dias
    con 25 clics emite; 19 clics, post_cambio False o cpc None
    bloquean). Misma direccion y sin historia pasan en ambas."""
    from app.optimizer import evidencia as ev

    abajo = g.UltimoBidAplicado(direccion=-1, fecha_cambio=D)
    fin5 = D + dt.timedelta(days=5)

    def cpc(clicks, post_cambio=True):
        return ev.CostoPorClic(
            cost=Decimal("12.5"), clicks=clicks, desde=D, hasta=fin5, post_cambio=post_cambio
        )

    # v1 exacto: 5 dias bloquean aunque el cpc sobre; 10 dias emiten.
    assert not g.permite_reversa_bid(abajo, nueva_direccion=1, fin_ventana_bids=fin5)
    assert not g.permite_reversa_bid(abajo, nueva_direccion=1, fin_ventana_bids=fin5, cpc=cpc(25))
    assert g.permite_reversa_bid(
        abajo, nueva_direccion=1, fin_ventana_bids=D + dt.timedelta(days=10)
    )
    # v2: solo manda el cpc post-cambio (los dias se ignoran).
    assert g.permite_reversa_bid(
        abajo, nueva_direccion=1, fin_ventana_bids=fin5, motor_evidencia=True, cpc=cpc(25)
    )
    assert g.permite_reversa_bid(
        abajo,
        nueva_direccion=1,
        fin_ventana_bids=D + dt.timedelta(days=99),
        motor_evidencia=True,
        cpc=cpc(20),
    )
    assert not g.permite_reversa_bid(
        abajo, nueva_direccion=1, fin_ventana_bids=fin5, motor_evidencia=True, cpc=cpc(19)
    )
    assert not g.permite_reversa_bid(
        abajo,
        nueva_direccion=1,
        fin_ventana_bids=fin5,
        motor_evidencia=True,
        cpc=cpc(25, post_cambio=False),
    )
    assert not g.permite_reversa_bid(
        abajo, nueva_direccion=1, fin_ventana_bids=fin5, motor_evidencia=True, cpc=None
    )
    # Atajos intactos en ambas politicas.
    assert g.permite_reversa_bid(
        g.SinHistoriaBid(), nueva_direccion=1, fin_ventana_bids=None, motor_evidencia=True
    )
    assert g.permite_reversa_bid(
        abajo, nueva_direccion=-1, fin_ventana_bids=fin5, motor_evidencia=True, cpc=None
    )
    assert not g.permite_reversa_bid(
        g.HistoriaBidRota(),
        nueva_direccion=1,
        fin_ventana_bids=fin5,
        motor_evidencia=True,
        cpc=cpc(25),
    )


# ---------------------------------------------------------------------------
# (b) INTEGRACION - ultimo_bid_aplicado contra Postgres real
# ---------------------------------------------------------------------------


@contextmanager
def _db_temporal(prefijo: str):
    """DB temporal con la migracion entera (el esquema sellado, sin toques)."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # la migracion entera
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _config_version(conn) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-ultimo-bid", Json({"ads_optimizer_mode": "shadow"})),
    ).fetchone()[0]


def _ciclo(conn, mode: str = "live") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (motor, mode, platform)"
        " VALUES ('ads_optimizer', %s, %s) RETURNING id",
        (mode, "amazon_us"),
    ).fetchone()[0]


def _campana(conn, external: str) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id)"
        " VALUES (%s, 'campaign', %s) RETURNING id",
        ("amazon_us", external),
    ).fetchone()[0]


def _decision_bid(conn, ciclo: int, config_id: int, entidad: int, *, viejo: str, nuevo: str) -> int:
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
        " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
        " inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s, 'USD', %s) RETURNING id",
        (
            ciclo,
            entidad,
            CONFIRMADO,
            config_id,
            CONFIRMADO - dt.timedelta(hours=1),
            D,
            D + dt.timedelta(days=29),
            Decimal(viejo),
            Decimal(nuevo),
            Json({"seed": "ultimo_bid"}),
        ),
    ).fetchone()[0]


def _apply(
    conn,
    decision_id: int,
    *,
    confirmed_at: dt.datetime,
    verify_ok: bool = True,
    ciclo_ejecutor: int | None = None,
) -> None:
    if ciclo_ejecutor is None:
        conn.execute(
            "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
            " verify_ok, platform_ack, applied_cycle_id)"
            " SELECT %s, %s, %s, %s, %s, d.cycle_id FROM decision d WHERE d.id = %s",
            (
                decision_id,
                confirmed_at - dt.timedelta(minutes=5),
                confirmed_at,
                verify_ok,
                Json({"estado": "ok" if verify_ok else "divergente"}),
                decision_id,
            ),
        )
        return
    conn.execute(
        "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
        " verify_ok, platform_ack, applied_cycle_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            decision_id,
            confirmed_at - dt.timedelta(minutes=5),
            confirmed_at,
            verify_ok,
            Json({"estado": "ok"}),
            ciclo_ejecutor,
        ),
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ultimo_bid_cuenta_solo_el_mas_reciente():
    """Dos applies verificados en vivo: la historia es la SUBIDA de CONFIRMADO
    (la mas reciente), no la bajada de hace 2 dias. Contra el mutante ORDER BY
    ASC (tomar el primero) la historia saldria bajada."""
    with _db_temporal("orbit_d2_reciente") as conn:
        conn.execute(SQL2)  # decision_application.applied_cycle_id vive en 0002
        config_id = _config_version(conn)
        ciclo = _ciclo(conn)
        entidad = _campana(conn, "8001")
        ciclo2 = _ciclo(conn)  # una decision por (ciclo, entidad): UNIQUE
        d1 = _decision_bid(conn, ciclo, config_id, entidad, viejo="1.00", nuevo="0.75")
        _apply(conn, d1, confirmed_at=CONFIRMADO - dt.timedelta(days=2))
        d2 = _decision_bid(conn, ciclo2, config_id, entidad, viejo="0.75", nuevo="1.00")
        _apply(conn, d2, confirmed_at=CONFIRMADO)
        assert g.ultimo_bid_aplicado(conn, entidad) == g.UltimoBidAplicado(
            direccion=1, fecha_cambio=CONFIRMADO.date()
        )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_divergente_en_vuelo_y_shadow_no_cuentan_como_aplicados():
    """verify_ok FALSE (divergencia, se reintenta), NULL (en vuelo) y un apply
    verificado de ciclo ejecutor shadow NO son un bid aplicado: sin NINGUNO
    valido la historia es SinHistoriaBid y la hoja decide libre."""
    with _db_temporal("orbit_d2_filtro") as conn:
        conn.execute(SQL2)
        config_id = _config_version(conn)
        ciclo_live = _ciclo(conn)
        ciclo_live_2 = _ciclo(conn)
        ciclo_shadow = _ciclo(conn, mode="shadow")
        entidad = _campana(conn, "8002")
        d_divergente = _decision_bid(
            conn, ciclo_live, config_id, entidad, viejo="1.00", nuevo="0.75"
        )
        _apply(conn, d_divergente, confirmed_at=CONFIRMADO - dt.timedelta(days=1), verify_ok=False)
        d_en_vuelo = _decision_bid(
            conn, ciclo_live_2, config_id, entidad, viejo="1.00", nuevo="1.20"
        )
        conn.execute(
            "INSERT INTO decision_application (decision_id, attempted_at) VALUES (%s, %s)",
            (d_en_vuelo, CONFIRMADO),
        )
        d_shadow = _decision_bid(conn, ciclo_shadow, config_id, entidad, viejo="1.00", nuevo="1.30")
        _apply(
            conn,
            d_shadow,
            confirmed_at=CONFIRMADO - dt.timedelta(hours=1),
            ciclo_ejecutor=ciclo_shadow,
        )
        assert g.ultimo_bid_aplicado(conn, entidad) == g.SinHistoriaBid()


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_confirmed_at_en_otra_zona_de_sesion_da_fecha_utc():
    """confirmed_at 02:00 UTC = 01-sep 20:00 en America/Mexico_City: la fecha
    del cambio es la UTC (02-sep). Contra el mutante `confirmed_at::date` en
    SQL, la zona de la sesion pintaria 01-sep."""
    with _db_temporal("orbit_d2_tz") as conn:
        conn.execute(SQL2)
        conn.execute("SET TIME ZONE 'America/Mexico_City'")
        config_id = _config_version(conn)
        ciclo = _ciclo(conn)
        entidad = _campana(conn, "8003")
        d = _decision_bid(conn, ciclo, config_id, entidad, viejo="1.00", nuevo="1.25")
        _apply(conn, d, confirmed_at=dt.datetime(2026, 9, 2, 2, 0, tzinfo=dt.UTC))
        assert g.ultimo_bid_aplicado(conn, entidad) == g.UltimoBidAplicado(
            direccion=1, fecha_cambio=dt.date(2026, 9, 2)
        )
