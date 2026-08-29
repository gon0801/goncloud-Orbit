"""Tests del orquestador del ciclo (`app.cycle`, task 3.1 de ORBIT 03).

INTEGRACION (patron _db_temporal de test_optimizer_hygiene, COPIADO; skipif
fail-closed `_postgres_obligatorio_ausente` de test_schema): el ciclo completo
contra la migracion entera, con fixture maestra siembrada por el test y reloj
FIJO tz-aware (decided_at por parametro; el modulo jamas esconde un now() en
las decisiones -- el reloj del LOCK si es now() de la DB, sellado por diseno
para la atomicidad del claim en una sola sentencia):

1. Ciclo shadow completo: decisiones EXACTAS pineadas (bid 1.00 -> 0.75 con
   factor -0.25, pause sin dinero, negative con search_term, harvest con
   default_bid) y notes JSON con contadores exactos por motivo.
2. Claim: lock vigente ajeno NO robable (CicloOcupado, sin envelope nuevo);
   TTL vencido SI reclamable (con rastro del ciclo muerto cerrado 'failed' y
   su id en ciclos_muertos); concurrencia REAL (2 threads + Barrier, 3
   rondas) deja exactamente un ganador. FLAKE TEORICO DECLARADO (residual
   del PR): si el perdedor llegara al claim DESPUES de que el ganador
   libero el lock, ganaria tambien — en la practica el perdedor falla en
   milisegundos mientras el ganador corre la fase de lecturas completa.
3. decisions_count del envelope cuadra contra SELECT count(*) de decision.
4. GOLDEN REPLAY: reproduce(inputs) == (kind, new_value, value_currency)
   para TODAS las decisions del ciclo (incluye pause y negative sin dinero).
5. Privilegio negativo con SET ROLE app_decide: escribe decision/lock/
   optimizer_cycle; JAMAS ads_optimizer_goal ni config_version (patron
   test_reports_pipeline; sin membresia por tunel -> skip salvo DSN explicito).
6. Guardas: watermark >7d -> degraded con motivo en notes; synced_at >48h ->
   idem; escalera global 'off' -> skipped.
7. Sello fail-closed: decide_bid que revienta a mitad -> excepcion re-lanzada,
   envelope 'failed' con el error scrubbado en notes y lock liberado.
8. Opt-out auditable: goal de campana enabled=false PISA al de plataforma
   (precedencia 2.4 resuelta EN LA APP): nadie decide, goal_disabled contado.
9. REPEATABLE READ verificable: la fase de lecturas corre en el nivel sellado
   (capturado con SHOW transaction_isolation dentro de un monkeypatch).
10. Sintaxis: las SQL del modulo parsean como Postgres real (pglast).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import threading
from contextlib import contextmanager
from decimal import Decimal

import pglast
import psycopg
import pytest
from psycopg.types.json import Json
from test_schema import SQL, SQL2, _postgres_obligatorio_ausente, _test_dsn

from app import cycle as ciclo
from app.optimizer import bid as bid_mod
from app.optimizer import cortes
from app.optimizer import windows as w

# ---------------------------------------------------------------------------
# Reloj FIJO y ventanas derivadas (mismas constantes que test_optimizer_windows)
# ---------------------------------------------------------------------------

DECIDED_AT = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
MAX_FECHA = dt.date(2026, 8, 19)  # max(metric_date) sembrado de las keywords
FIN_BIDS = dt.date(2026, 8, 16)  # MAX_FECHA - 3d
INICIO_BIDS = dt.date(2026, 7, 18)  # FIN_BIDS - 29d
FIN_CORTES = dt.date(2026, 8, 12)  # min(FIN_BIDS, DECIDED_AT - 10d)
INICIO_CORTES = dt.date(2026, 7, 14)  # FIN_CORTES - 29d
# Ventana de terminos: ancla en max(metric_date) de SUS observaciones (07-21)
# -> fin = min(07-18, 08-12) = 07-18, inicio = 06-19.
INICIO_TERMINOS = dt.date(2026, 6, 19)
FIN_TERMINOS = dt.date(2026, 7, 18)

OWNER = "test-host:1"
JOB_KEY = "ads_optimizer:amazon_us"

_DIA = dt.timedelta(days=1)

_DSN_EXPLICITO = bool(os.environ.get("ORBIT_TEST_DSN"))


def _obs(fecha: dt.date, hora: int = 1) -> dt.datetime:
    """observed_at de una observacion: medianoche + hora UTC (>= metric_date)."""
    return dt.datetime(fecha.year, fecha.month, fecha.day, hora, tzinfo=dt.UTC)


def _rango(inicio: dt.date, fin: dt.date) -> list[dt.date]:
    dias: list[dt.date] = []
    fecha = inicio
    while fecha <= fin:
        dias.append(fecha)
        fecha += _DIA
    return dias


# ---------------------------------------------------------------------------
# Patron _db_temporal COPIADO de test_optimizer_hygiene (con factory de conecs)
# ---------------------------------------------------------------------------


@contextmanager
def _db_temporal(prefijo: str):
    """DB temporal con la migracion entera; yields (conn, conectar_extra)."""
    from psycopg import sql as pgsql

    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None

    def conectar_extra():
        """Conexion adicional a la MISMA DB temporal (threads del test)."""
        return psycopg.connect(dsn, dbname=db, autocommit=True)

    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL)  # 0001: roles, esquema sellado, grants
        # ORBIT 04 2.4: la fase de apply del ciclo escribe en apply_queue/
        # apply_attempt (0002) — sin esta migracion el encolado revienta.
        conn.execute(SQL2)
        yield conn, conectar_extra
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


# ---------------------------------------------------------------------------
# Seeds (helpers del estilo test_optimizer_hygiene)
# ---------------------------------------------------------------------------


def _run(conn) -> int:
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('test') RETURNING id").fetchone()[
        0
    ]


def _entidad(conn, platform: str, kind: str, external: str, parent=None, **extra) -> int:
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, extra.get("match_type"), extra.get("keyword_text")),
    ).fetchone()[0]


def _metrica(
    conn,
    run_id,
    ad_entity_id,
    fecha,
    observed_at,
    *,
    moneda="USD",
    cost=None,
    ad_revenue=None,
    clicks=None,
    orders=None,
) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            ad_entity_id,
            fecha,
            observed_at,
            moneda,
            Decimal(cost) if cost is not None else None,
            Decimal(ad_revenue) if ad_revenue is not None else None,
            None,
            clicks,
            orders,
            run_id,
        ),
    )


def _termino(
    conn,
    run_id,
    ad_entity_id,
    term,
    fecha,
    observed_at,
    *,
    cost=None,
    ad_revenue=None,
    clicks=None,
    orders=None,
    asin=False,
) -> None:
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders, ad_revenue,"
        " is_asin_like, ingest_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            "amazon_us",
            ad_entity_id,
            term,
            fecha,
            observed_at,
            "USD",
            Decimal(cost) if cost is not None else None,
            clicks,
            orders,
            Decimal(ad_revenue) if ad_revenue is not None else None,
            asin,
            run_id,
        ),
    )


def _estado(
    conn,
    ad_entity_id,
    *,
    synced_at,
    current_bid=None,
    bid_currency=None,
    status="ENABLED",
    acos_target=None,
) -> None:
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, current_bid, bid_currency, status,"
        " acos_target, synced_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (ad_entity_id, current_bid, bid_currency, status, acos_target, synced_at),
    )


def _config_version(conn, settings: dict) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES (%s, %s) RETURNING id",
        ("test-cycle", Json(settings)),
    ).fetchone()[0]


def _goal_plataforma(conn) -> int:
    """Goal de plataforma habilitado: target 25, floor 0.40, ceiling 2.50 y
    config de harvest COMPLETA (default_bid 0.75 USD)."""
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,"
        " harvest_default_bid, enabled, mode)"
        " VALUES ('platform', 'amazon_us', 25, 0.40, 2.50, 'USD', '9002', '9102',"
        " 0.75, true, 'live') RETURNING id"
    ).fetchone()[0]


def _goal_campana_disabled(conn, camp_id) -> int:
    """El opt-out del Spec delta: goal de campana enabled=false que PISA al de
    plataforma (precedencia 2.4 resuelta EN LA APP)."""
    return conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
        " bid_ceiling, bid_currency, enabled, mode)"
        " VALUES ('campaign', %s, NULL, 0.10, 2.50, 'USD', false, 'shadow') RETURNING id",
        (camp_id,),
    ).fetchone()[0]


def _siembra_kw_bid(conn, run_id, kw) -> None:
    """37 fechas diarias (07-14..08-19) en tres segmentos: la ventana de BIDS
    (07-18..08-16) suma cost 36 / revenue 100 / clicks 50 / orders 5 (ACoS 36%
    > 1.35x25 con orders>=1 -> banda -25%: bid 1.00 -> 0.75) y la de CORTES
    (07-14..08-12) queda con orders 9 (no pause: orders != 0).

    Re-siembra CORTES 03: las 4 fechas de 07-14..07-17 llevan orders 1 y
    ad_revenue 1.00 (fuera de la ventana de BIDS, con cost/clicks intactos
    para no mover el golden 27.00 de la ventana de CORTES) -- suben la
    EVIDENCIA del grupo a 9 ordenes / 99.00 de revenue (AOV 11.0000) para
    que el bruto adaptativo quede en 22 junto a los clicks extra de
    _siembra_kw_pause."""
    for fecha in _rango(dt.date(2026, 7, 14), dt.date(2026, 7, 17)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.25",
            ad_revenue="1.00",
            clicks=0,
            orders=1,
        )
    for i, fecha in enumerate(_rango(dt.date(2026, 7, 18), dt.date(2026, 8, 12))):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="1.00",
            ad_revenue="2.50",
            clicks=1,
            orders=1 if i < 5 else 0,
        )
    for fecha in _rango(dt.date(2026, 8, 13), dt.date(2026, 8, 16)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="2.50",
            ad_revenue="8.75",
            clicks=6,
            orders=0,
        )
    for fecha in _rango(dt.date(2026, 8, 17), dt.date(2026, 8, 19)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.10",
            ad_revenue="0.10",
            clicks=1,
            orders=0,
        )


def _siembra_kw_pause(conn, run_id, kw) -> None:
    """La ventana de CORTES (07-14..08-12, las 30 fechas completas) suma
    orders 0 / clicks 105 / cost 45.00 -> PAUSE (umbrales us CORTES 03:
    clicks 100 y 40 USD). Re-siembra CORTES 03: clicks 105 (3 en fechas
    pares, 4 en impares) con cost 45.00 en las MISMAS 30 fechas ya sembradas
    (fechas_distintas de la evidencia intactas); junto a las ordenes extra
    de _siembra_kw_bid, la evidencia del grupo (D-90..D-10) queda en 131
    clicks / 9 ordenes / 30 fechas -> expected 14.5555... -> bruto 22 ->
    umbral pause 100 (el piso gana) y negative 22."""
    for i, fecha in enumerate(_rango(dt.date(2026, 7, 14), dt.date(2026, 8, 12))):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="1.50",
            ad_revenue="1.00",
            clicks=3 if i % 2 == 0 else 4,
            orders=0,
        )
    for fecha in _rango(dt.date(2026, 8, 13), dt.date(2026, 8, 19)):
        _metrica(
            conn,
            run_id,
            kw,
            fecha,
            _obs(fecha),
            cost="0.10",
            ad_revenue="0.10",
            clicks=1,
            orders=0,
        )


def _siembra_terminos(conn, run_id, ag) -> None:
    """Siete terminos del ad group, con ancla 07-21 (la ventana de terminos
    queda 06-19..07-18 y la entidad completa: 9 fechas dentro).

    Re-siembra CORTES 01 1.4 (declarada, mismo criterio que el hueco-legacy
    de 1.2): el piso de cost del grupo es adaptativo -- AOV del revenue sano
    del grupo x 1.0 -- y los cost 8.00/9.00 de la siembra original quedaban
    BAJO el piso; sin subirlos, "tortugas" no dispararia y el hueco-legacy
    quedaria doble-bloqueado (detectaria menos). Re-siembra CORTES 03: con
    el AOV nuevo 99.00/9 = 11.0000, el cost del hueco-piso baja de 15.00 a
    9.00 para seguir ENTRE el legacy 8 y el piso adaptativo (8 < 9 < 11)."""
    # NEGATIVE elegible: orders 0, clicks 23 (>= umbral adaptativo 22 del
    # grupo), cost 20.00 (>= piso adaptativo 11.0000 desde CORTES 03).
    # Re-siembras: con los 20 clicks viejos y umbral adaptativo 22 NO
    # disparaba (1.2); con el cost 8.00 viejo y el piso de entonces (19.40)
    # tampoco (1.4).
    for fecha, clicks, cost in (
        (dt.date(2026, 7, 10), 8, "8.00"),
        (dt.date(2026, 7, 11), 8, "8.00"),
        (dt.date(2026, 7, 12), 7, "4.00"),
    ):
        _termino(
            conn,
            run_id,
            ag,
            "tortugas ninja calzas",
            fecha,
            _obs(fecha),
            cost=cost,
            ad_revenue="1.00",
            clicks=clicks,
            orders=0,
        )
    # HUECO DISCRIMINANTE del cableado del umbral (hallazgo grok, 1.2):
    # clicks 21 vive ENTRE el legacy 20 y el umbral adaptativo 22 -- bajo la
    # regla nueva NO corta (21 < 22) pero bajo el legacy 20 SI dispararia.
    # Cost 20.00 desde 1.4: el hueco discrimina SOLO por clicks -- sobre el
    # piso de su epoca (19.40) y sobre el vigente (11.0000); con el cost
    # 9.00 original quedaba BAJO el piso de entonces y ya cortaba por cost
    # solo (hueco degradado).
    _termino(
        conn,
        run_id,
        ag,
        "hueco legacy",
        dt.date(2026, 7, 12),
        _obs(dt.date(2026, 7, 12), 2),
        cost="20.00",
        ad_revenue="1.00",
        clicks=21,
        orders=0,
    )
    # HUECO DISCRIMINANTE del cableado del PISO (1.4, re-siembra CORTES 03):
    # clicks 23 >= umbral 22 y cost 9.00 con 8 < 9 < 11.0000 (piso del grupo
    # re-sembrado) -- SOLO el piso lo bloquea. Si el ciclo dejara de pasar
    # piso_negative al motor (default legacy 8), generaria una negative
    # extra y el golden reventaria.
    _termino(
        conn,
        run_id,
        ag,
        "hueco piso",
        dt.date(2026, 7, 12),
        _obs(dt.date(2026, 7, 12), 3),
        cost="9.00",
        ad_revenue="1.00",
        clicks=23,
        orders=0,
    )
    # HARVEST elegible: orders 3 (1 por fecha, >= 2), ACoS 10% <= min(35, 25)
    for fecha, cost, revenue in (
        (dt.date(2026, 7, 13), "3.34", "30.00"),
        (dt.date(2026, 7, 14), "3.33", "30.00"),
        (dt.date(2026, 7, 15), "3.33", "40.00"),
    ):
        _termino(
            conn,
            run_id,
            ag,
            "buena yarda",
            fecha,
            _obs(fecha),
            cost=cost,
            ad_revenue=revenue,
            clicks=2,
            orders=1,
        )
    # ASIN-like: cumpliria negative pero SIEMPRE se salta
    _termino(
        conn,
        run_id,
        ag,
        "b0abcd1234",
        dt.date(2026, 7, 16),
        _obs(dt.date(2026, 7, 16)),
        cost="15.00",
        ad_revenue="1.00",
        clicks=30,
        orders=0,
        asin=True,
    )
    # orders None: DESCONOCIDO, jamas decision por dato faltante
    _termino(
        conn,
        run_id,
        ag,
        "sin orden conocida",
        dt.date(2026, 7, 17),
        _obs(dt.date(2026, 7, 17)),
        cost="1.00",
        ad_revenue="1.00",
        clicks=5,
        orders=None,
    )
    # relleno que SOLO sube el ancla de la ventana de terminos a 07-21
    for fecha in _rango(dt.date(2026, 7, 18), dt.date(2026, 7, 21)):
        _termino(
            conn,
            run_id,
            ag,
            "relleno diario",
            fecha,
            _obs(fecha),
            cost="0.01",
            ad_revenue="0.01",
            clicks=0,
            orders=0,
        )


def _siembra_maestra(conn, *, escalera: str = "shadow") -> dict:
    """Fixture maestra del DoD: config + goal de plataforma + campana/ad_group/
    2 keywords con ventanas maduras + 7 terminos. Reloj FIJO DECIDED_AT."""
    run_id = _run(conn)
    config_id = _config_version(conn, {"ads_optimizer_mode": escalera})
    _goal_plataforma(conn)
    camp = _entidad(conn, "amazon_us", "campaign", "9001")
    ag = _entidad(conn, "amazon_us", "ad_group", "9101", parent=camp)
    kw_bid = _entidad(
        conn, "amazon_us", "keyword", "9201", parent=ag, match_type="EXACT", keyword_text="kw bid"
    )
    kw_pause = _entidad(
        conn, "amazon_us", "keyword", "9202", parent=ag, match_type="EXACT", keyword_text="kw pause"
    )
    synced = DECIDED_AT - dt.timedelta(hours=4)
    _estado(conn, kw_bid, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
    _estado(conn, kw_pause, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
    _estado(conn, ag, synced_at=synced)
    _estado(conn, camp, synced_at=synced)
    _siembra_kw_bid(conn, run_id, kw_bid)
    _siembra_kw_pause(conn, run_id, kw_pause)
    _siembra_terminos(conn, run_id, ag)
    return {"config_id": config_id, "camp": camp, "ag": ag, "kw_bid": kw_bid, "kw_pause": kw_pause}


def _corre(conn, *, owner=OWNER, platform="amazon_us"):
    return ciclo.corre_ciclo(
        conn, platform=platform, owner=owner, decided_at=DECIDED_AT, heartbeat_cada=1
    )


def _decisions_de(conn, cycle_id: int) -> list:
    return conn.execute(
        "SELECT ad_entity_id, kind, search_term, old_value, new_value, value_currency,"
        " window_start, window_end, data_observed_at, inputs"
        " FROM decision WHERE cycle_id = %s ORDER BY id",
        (cycle_id,),
    ).fetchall()


def _envelope(conn, cycle_id: int):
    return conn.execute(
        "SELECT status, decisions_count, notes, finished_at FROM optimizer_cycle WHERE id = %s",
        (cycle_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# 1. Ciclo shadow completo: decisiones exactas pineadas + notes exactos
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_ciclo_shadow_completo_decisiones_y_notes_exactos():
    with _db_temporal("orbit_ciclo_full") as (conn, _c):
        ids = _siembra_maestra(conn)
        res = _corre(conn)

        assert res.status == "done"
        assert res.decisions_count == 4
        env = _envelope(conn, res.cycle_id)
        assert env[0] == "done"
        assert env[2] == res.notes
        assert env[3] is not None  # finished_at sellado

        filas = _decisions_de(conn, res.cycle_id)
        por = {(f[0], f[1], f[2]): f for f in filas}

        # keyword kw_bid: banda -25% (ACoS 36% > 1.35x25, orders 5) -> 0.75 USD
        bid_row = por[(ids["kw_bid"], "bid", None)]
        assert bid_row[3] == Decimal("1.00")
        assert bid_row[4] == Decimal("0.75")
        assert bid_row[5] == "USD"
        assert (bid_row[6], bid_row[7]) == (INICIO_BIDS, FIN_BIDS)
        assert bid_row[8] == _obs(FIN_BIDS)
        ins = bid_row[9]
        assert ins["motor"] == "bid"
        assert ins["platform"] == "amazon_us"
        assert ins["factor"] == "-0.25"
        assert ins["motivo"] == "banda_menos_25"
        assert ins["target_acos_pct_usado"] == "25.00"
        assert ins["bid_actual"] == "1.0000"
        assert ins["bid_moneda"] == "USD"
        assert ins["modo"] == "shadow"
        assert ins["ventanas"]["bids"]["cost"] == "36.0000"
        assert ins["ventanas"]["bids"]["ad_revenue"] == "100.0000"
        assert ins["ventanas"]["bids"]["fechas"] == 30
        assert ins["ventanas"]["bids"]["clicks"] == 50
        assert ins["ventanas"]["bids"]["orders"] == 5
        assert ins["ventanas"]["bids"]["moneda"] == "USD"
        assert ins["ventanas"]["cortes"]["cost"] == "27.0000"
        assert ins["goal"] == {
            "scope": "platform",
            "target_acos_pct": "25.00",
            "bid_floor": "0.4000",
            "bid_ceiling": "2.5000",
            "harvest": {
                "campaign_id": "9002",
                "ad_group_id": "9102",
                "default_bid": "0.7500",
                "moneda": "USD",
            },
        }

        # keyword kw_pause: PAUSE sobre la ventana de cortes, SIN dinero
        pause_row = por[(ids["kw_pause"], "pause", None)]
        assert pause_row[3] is None
        assert pause_row[4] is None
        assert pause_row[5] is None
        assert (pause_row[6], pause_row[7]) == (INICIO_CORTES, FIN_CORTES)
        assert pause_row[9]["motivo"] == "pause_umbral"
        assert pause_row[9]["ventanas"]["cortes"]["cost"] == "45.0000"
        assert pause_row[9]["ventanas"]["cortes"]["orders"] == 0
        assert pause_row[9]["ventanas"]["cortes"]["clicks"] == 105
        # CORTES 01 (1.3): pause TAMBIEN congela inputs.corte -- mismo shape
        # que negative. El grupo es elegible (131/9/30) con expected
        # 14.5555... -> bruto ceil(21.83) = 22, y el umbral FINAL del freeze
        # es el PISO 100 (CORTES 03: 105 >= 100 y 45 >= 40 dispara; sin el
        # piso el freeze diria 22)
        assert pause_row[9]["corte"]["umbral_clicks_usado"] == 100
        assert pause_row[9]["corte"]["elegible"] is True
        assert pause_row[9]["corte"]["expected_clicks"] == "14.55555555555555555555555556"
        assert pause_row[9]["corte"]["evidencia"]["clicks"] == 131
        # CORTES 03 (cierre replay, decision del lead 2026-08-28): el motor
        # de bids congela ADEMAS el piso de costo que consumio
        # (cost_min_usado, string Decimal) -- replay fiel por construccion
        assert pause_row[9]["corte"]["cost_min_usado"] == "40"
        # CORTES 01 (1.4): el piso de cost ADAPTATIVO es SOLO del camino
        # negative -- pause/bid NO congelan piso_cost_usado ni aov (quien no
        # consumio el piso adaptativo, no lo congela; hallazgo reviewer 1.4;
        # el cost_min_usado de arriba es el SELLADO de bid, no el adaptativo)
        assert "piso_cost_usado" not in pause_row[9]["corte"]
        assert "aov" not in pause_row[9]["corte"]
        # sello bitemporal: LEAST(decided_at, max(obs cortes, evidencia))
        assert pause_row[8] == _obs(FIN_CORTES)
        # y la decision BID del mismo ciclo congela EL MISMO freeze (misma
        # evidencia del grupo ag): decide_bid evalua PAUSE antes de las bandas
        # y toda decision del motor lleva el corte (spec 1.3)
        assert bid_row[9]["corte"] == pause_row[9]["corte"]

        # termino NEGATIVE: con search_term y SIN dinero
        neg_row = por[(ids["ag"], "negative", "tortugas ninja calzas")]
        assert neg_row[3] is None and neg_row[4] is None and neg_row[5] is None
        assert (neg_row[6], neg_row[7]) == (INICIO_TERMINOS, FIN_TERMINOS)
        assert neg_row[9]["motor"] == "hygiene"
        assert neg_row[9]["motivo"] == "negative_umbral"
        assert neg_row[9]["termino"]["search_term"] == "tortugas ninja calzas"
        assert neg_row[9]["termino"]["clicks"] == 23
        assert neg_row[9]["termino"]["cost"] == "20.0000"
        assert neg_row[9]["target_acos_pct_usado"] == "25.00"
        # SELLO BITEMPORAL (CORTES 01 1.2): data_observed_at = LEAST(decided_at,
        # max(obs directo del termino 07-12, observed_at_max de la evidencia
        # 08-12)) -> gana la evidencia (es mas reciente que el dato directo)
        assert neg_row[8] == _obs(FIN_CORTES)
        # inputs.corte TOP-LEVEL con el shape EXACTO del spec: umbral FINAL con
        # piso, elegible, expected como string Decimal y la evidencia del
        # GRUPO sembrado (131 clicks de las hojas kw_bid+kw_pause, 9 ordenes,
        # 30 fechas; ventana literal D-90..D-10). Desde 1.4 tambien congela el
        # PISO de cost resuelto: AOV 99.0000/9 = Decimal('11.0000') y piso
        # max(8, 11.0000 x 1.0) = Decimal('11.00000') -- la ESCALA nace del
        # money_amount NUMERIC(14,4) del revenue y _dec_str la conserva tal
        # cual (regla 4; hallazgo codex 1.4: pinear '19.40' mataba el golden
        # en CI). Sin la asercion del dict, un mapeo de grupo roto pasaria
        # los goldens en fallback sistematico (ronda 2 qwen).
        assert neg_row[9]["corte"] == {
            "umbral_clicks_usado": 22,
            "elegible": True,
            "expected_clicks": "14.55555555555555555555555556",
            "evidencia": {
                "clicks": 131,
                "orders": 9,
                "fechas": 30,
                "ventana_desde": "2026-05-24",
                "ventana_hasta": "2026-08-12",
                "observed_at_max": "2026-08-12T01:00:00+00:00",
            },
            "piso_cost_usado": "11.00000",
            "aov": "11.0000",
        }

        # termino HARVEST: new_value = default_bid del goal con SU moneda;
        # JAMAS lleva inputs.corte (su regla no usa umbral de clicks, sellado)
        harv_row = por[(ids["ag"], "harvest", "buena yarda")]
        assert harv_row[3] is None
        assert harv_row[4] == Decimal("0.75")
        assert harv_row[5] == "USD"
        assert harv_row[9]["motivo"] == "harvest_umbral"
        assert "corte" not in harv_row[9]

        # asin-like, orders=None y los DOS HUECOS: SIN decision (solo cuentan
        # en los notes)
        terminos_con_decision = {f[2] for f in filas if f[2] is not None}
        assert "b0abcd1234" not in terminos_con_decision
        assert "sin orden conocida" not in terminos_con_decision
        # el hueco 20<21<22 NO corta bajo la regla nueva; bajo el legacy 20
        # del default SI habria decision negative (cable roto detectado)
        assert "hueco legacy" not in terminos_con_decision
        # el hueco del PISO (clicks 23 >= 22, cost 9.00 con 8 < 9 < 11.0000)
        # SOLO el piso adaptativo lo bloquea: con el default legacy 8 este
        # termino generaria una negative extra (cable roto detectado, 1.4)
        assert "hueco piso" not in terminos_con_decision

        notes = json.loads(res.notes)
        assert notes["skips"] == {
            "entidad": {},
            "termino": {"asin_like": 1, "orders_desconocido": 1, "sin_umbral_negative": 3},
        }
        assert notes["decisiones"] == {"bid": 1, "pause": 1, "negative": 1, "harvest": 1}
        assert notes["entidades"] == 2
        assert notes["ad_groups"] == 1
        assert notes["terminos"] == 7
        assert notes["ciclos_muertos"] == []
        assert notes["degradacion_live"] is None

        # lock liberado al terminar
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == 0
        )


# ---------------------------------------------------------------------------
# 2. Claim: vigente ajeno / TTL vencido con rastro / concurrencia real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_claim_vigente_de_otro_owner_no_robable():
    with _db_temporal("orbit_ciclo_claim") as (conn, _c):
        _siembra_maestra(conn)
        conn.execute(
            "INSERT INTO ads_optimizer_lock (job_key, owner) VALUES (%s, 'otro')", (JOB_KEY,)
        )
        conn.execute(
            "UPDATE ads_optimizer_lock SET heartbeat_at = now() WHERE job_key = %s", (JOB_KEY,)
        )
        antes = conn.execute("SELECT count(*) FROM optimizer_cycle").fetchone()[0]

        with pytest.raises(ciclo.CicloOcupado):
            _corre(conn)

        # SIN envelope nuevo (nada corrio) y el lock sigue siendo del otro
        assert conn.execute("SELECT count(*) FROM optimizer_cycle").fetchone()[0] == antes
        dueno = conn.execute(
            "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
        ).fetchone()[0]
        assert dueno == "otro"


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_claim_ttl_vencido_reclamable_con_rastro_del_muerto():
    with _db_temporal("orbit_ciclo_ttl") as (conn, _c):
        _siembra_maestra(conn)
        conn.execute(
            "INSERT INTO ads_optimizer_lock (job_key, owner) VALUES (%s, 'muerto')", (JOB_KEY,)
        )
        conn.execute(
            "UPDATE ads_optimizer_lock SET heartbeat_at = now() - interval '31 minutes'"
            " WHERE job_key = %s",
            (JOB_KEY,),
        )
        # envelope huerfano del ciclo muerto (quedo 'running' para siempre)
        huerfano = conn.execute(
            "INSERT INTO optimizer_cycle (motor, mode, platform)"
            " VALUES ('ads_optimizer', 'shadow', 'amazon_us') RETURNING id"
        ).fetchone()[0]

        res = _corre(conn)  # gana el claim: TTL de 30 min vencido hace 1

        assert res.status == "done"
        fila_huerfana = conn.execute(
            "SELECT status, notes FROM optimizer_cycle WHERE id = %s", (huerfano,)
        ).fetchone()
        assert fila_huerfana[0] == "failed"
        assert "rastro" in fila_huerfana[1]
        assert json.loads(res.notes)["ciclos_muertos"] == [huerfano]


def _trabajador_race(owner, conectar, barrera, ganadores, ocupados) -> None:
    """Un competidor del claim: conexion propia, sincronizada por la barrera.
    Todo por parametro (B023): nada capturado del loop."""
    propia = conectar()
    try:
        barrera.wait(timeout=30)
        res = ciclo.corre_ciclo(
            propia,
            platform="amazon_us",
            owner=owner,
            decided_at=DECIDED_AT,
            heartbeat_cada=1,
        )
        ganadores.append(res.cycle_id)
    except ciclo.CicloOcupado:
        ocupados.append(True)
    finally:
        propia.close()


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_claim_concurrencia_real_dos_threads_un_ganador():
    with _db_temporal("orbit_ciclo_race") as (conn, conectar):
        _siembra_maestra(conn)
        for ronda in range(3):
            ganadores: list[int] = []
            ocupados: list[bool] = []
            barrera = threading.Barrier(2)

            hilos = [
                threading.Thread(
                    target=_trabajador_race,
                    args=(f"th-{ronda}-a", conectar, barrera, ganadores, ocupados),
                ),
                threading.Thread(
                    target=_trabajador_race,
                    args=(f"th-{ronda}-b", conectar, barrera, ganadores, ocupados),
                ),
            ]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join(timeout=60)
            assert not any(h.is_alive() for h in hilos)
            assert len(ganadores) == 1, f"ronda {ronda}: un solo ganador, hubo {ganadores}"
            assert len(ocupados) == 1, f"ronda {ronda}: el otro debe ver CicloOcupado"


# ---------------------------------------------------------------------------
# 3. decisions_count cuadra contra la tabla
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_decisions_count_cuadra_contra_decision():
    with _db_temporal("orbit_ciclo_count") as (conn, _c):
        _siembra_maestra(conn)
        res = _corre(conn)
        real = conn.execute(
            "SELECT count(*) FROM decision WHERE cycle_id = %s", (res.cycle_id,)
        ).fetchone()[0]
        assert res.decisions_count == real == 4
        assert _envelope(conn, res.cycle_id)[1] == real


# ---------------------------------------------------------------------------
# 4. GOLDEN TEST DE REPLAY: reproduce(inputs) == decision exacta
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_golden_replay_reproduce_todas_las_decisiones():
    with _db_temporal("orbit_ciclo_replay") as (conn, _c):
        _siembra_maestra(conn)
        res = _corre(conn)
        filas = conn.execute(
            "SELECT kind, new_value, value_currency, inputs FROM decision WHERE cycle_id = %s",
            (res.cycle_id,),
        ).fetchall()
        kinds = {f[0] for f in filas}
        assert kinds == {"bid", "pause", "negative", "harvest"}
        for kind, new_value, moneda, inputs in filas:
            # Decimal de string (regla 4): inputs congelo los numeros como str
            assert ciclo.reproduce(inputs) == (kind, new_value, moneda), kind


# ---------------------------------------------------------------------------
# 5. Privilegio negativo con SET ROLE app_decide (patron test_reports_pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_privilegio_negativo_app_decide():
    with _db_temporal("orbit_ciclo_priv") as (conn, _c):
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        camp = _entidad(conn, "amazon_us", "campaign", "6001")
        try:
            conn.execute("SET ROLE app_decide")
        except psycopg.errors.InsufficientPrivilege:
            # Señal CI igual que _postgres_obligatorio_ausente (hallazgo
            # CodeRabbit): si CI corre sin ORBIT_TEST_DSN y el usuario no
            # tiene membresia, el DoD no puede evaporarse en skip verde.
            if _DSN_EXPLICITO or os.environ.get("CI"):
                raise AssertionError(
                    "CI con usuario sin membresia app_decide: el privilegio "
                    "negativo del DoD quedo sin ejercer"
                ) from None
            pytest.skip(
                "usuario del DSN sin membresia app_decide: el privilegio negativo "
                "se ejercita en CI, donde el DSN es superuser"
            )
        try:
            # LO QUE SI: el motor escribe decisiones, envelopes y el lock
            ciclo_id = conn.execute(
                "INSERT INTO optimizer_cycle (motor, mode, platform)"
                " VALUES ('ads_optimizer', 'shadow', 'amazon_us') RETURNING id"
            ).fetchone()[0]
            conn.execute("INSERT INTO ads_optimizer_lock (job_key, owner) VALUES ('t:lock', 't')")
            conn.execute(
                "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at,"
                " config_version_id, data_observed_at, window_start, window_end,"
                " old_value, new_value, value_currency, inputs)"
                " VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, 1.00, 0.75, 'USD', '{}')",
                (
                    ciclo_id,
                    camp,
                    DECIDED_AT,
                    config_id,
                    DECIDED_AT - dt.timedelta(hours=1),
                    dt.date(2026, 7, 25),
                    dt.date(2026, 8, 12),
                ),
            )
            # LO QUE NO: goals y config son decision humana (app_admin)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO ads_optimizer_goal (scope, platform, bid_currency, enabled)"
                    " VALUES ('platform', 'amazon_us', 'USD', false)"
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO config_version (settings) VALUES ('{}')")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "UPDATE config_version SET settings = '{}' WHERE id = %s", (config_id,)
                )
        finally:
            conn.execute("SET ROLE NONE")


# ---------------------------------------------------------------------------
# 6. Guardas: watermark viejo / synced_at >48h -> degraded; escalera off -> skipped
# ---------------------------------------------------------------------------


def _siembra_guarda(conn, *, metric_date, synced_at) -> None:
    run_id = _run(conn)
    _config_version(conn, {"ads_optimizer_mode": "shadow"})
    camp = _entidad(conn, "amazon_us", "campaign", "8001")
    kw = _entidad(
        conn, "amazon_us", "keyword", "8101", parent=camp, match_type="EXACT", keyword_text="kw g"
    )
    _estado(conn, camp, synced_at=synced_at)
    _estado(conn, kw, synced_at=synced_at, current_bid=Decimal("1.00"), bid_currency="USD")
    _metrica(
        conn,
        run_id,
        kw,
        metric_date,
        _obs(metric_date),
        cost="1.00",
        ad_revenue="3.00",
        clicks=1,
        orders=1,
    )


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_guarda_watermark_viejo_degraded():
    with _db_temporal("orbit_ciclo_wm") as (conn, _c):
        _siembra_guarda(
            conn, metric_date=dt.date(2026, 8, 13), synced_at=DECIDED_AT - dt.timedelta(hours=4)
        )
        res = _corre(conn)
        assert res.status == "degraded"
        notes = json.loads(res.notes)
        assert notes["motivo_skip"] == "guarda_watermark"
        assert "watermark" in notes["detalle"]
        assert res.decisions_count == 0
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 0


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_guarda_synced_at_viejo_degraded():
    with _db_temporal("orbit_ciclo_sync") as (conn, _c):
        _siembra_guarda(
            conn, metric_date=dt.date(2026, 8, 21), synced_at=DECIDED_AT - dt.timedelta(hours=60)
        )
        res = _corre(conn)
        assert res.status == "degraded"
        notes = json.loads(res.notes)
        assert notes["motivo_skip"] == "guarda_synced_at"
        assert "sincronizada" in notes["detalle"] or "synced" in notes["detalle"]


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_escalera_global_off_skipped():
    with _db_temporal("orbit_ciclo_off") as (conn, _c):
        _siembra_maestra(conn, escalera="off")
        res = _corre(conn)
        assert res.status == "skipped"
        assert res.decisions_count == 0
        notes = json.loads(res.notes)
        assert notes["motivo_skip"] == "escalera_off"
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
        # el envelope existe, quedo cerrado skipped y el lock liberado
        env = _envelope(conn, res.cycle_id)
        assert env[0] == "skipped" and env[3] is not None
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == 0
        )


# ---------------------------------------------------------------------------
# 7. Sello fail-closed: re-lanza, sella 'failed' con error scrubbado, libera lock
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_sello_fail_closed_decide_bid_explota(monkeypatch):
    with _db_temporal("orbit_ciclo_sello") as (conn, _c):
        _siembra_maestra(conn)

        def _boom(**_kwargs):
            raise RuntimeError("boom decidir")

        monkeypatch.setattr(bid_mod, "decide_bid", _boom)
        with pytest.raises(RuntimeError, match="boom"):
            _corre(conn)
        monkeypatch.undo()

        ciclos = conn.execute(
            "SELECT id, status, notes FROM optimizer_cycle WHERE motor = 'ads_optimizer'"
        ).fetchall()
        assert len(ciclos) == 1
        ciclo_id, status, notes = ciclos[0]
        assert status == "failed"
        # el error scrubbado viaja DENTRO del notes JSON estructurado
        assert json.loads(notes)["error"].startswith("boom")
        # nada se decidio y el lock quedo liberado
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()[0]
            == 0
        )


# ---------------------------------------------------------------------------
# 8. Opt-out auditable: goal de campana enabled=false pisa al de plataforma
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_opt_out_goal_campana_deshabilitado():
    with _db_temporal("orbit_ciclo_optout") as (conn, _c):
        ids = _siembra_maestra(conn)
        _goal_campana_disabled(conn, ids["camp"])
        res = _corre(conn)

        assert res.status == "done"
        assert res.decisions_count == 0
        assert conn.execute("SELECT count(*) FROM decision").fetchone()[0] == 0
        notes = json.loads(res.notes)
        # las 2 keywords y los 7 terminos del ad group quedaron fuera por el
        # goal de campana deshabilitado (pisa a la plataforma: 2.4 EN LA APP)
        assert notes["skips"]["entidad"] == {"goal_disabled": 2}
        assert notes["skips"]["termino"] == {"goal_disabled": 7}


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_gates_de_elegibilidad_sin_goal_mode_off_estado_y_cooldown():
    """Los 4 gates de elegibilidad sin test propio (hallazgo verificador):
    sin_goal (ciclo 1, sin ningun goal), goal_mode_off, estado_no_enabled y
    cooldown_7d (ciclo 2, con goal de plataforma habilitado + overrides por
    campana). Cada keyword elegible queda contada por SU motivo exacto y
    ninguna genera decision."""
    with _db_temporal("orbit_ciclo_gates") as (conn, _c):
        run_id = _run(conn)
        config_id = _config_version(conn, {"ads_optimizer_mode": "shadow"})
        synced = DECIDED_AT - dt.timedelta(hours=4)

        # keyword S: campana SIN goal de ningun tipo
        camp_s = _entidad(conn, "amazon_us", "campaign", "9601")
        ag_s = _entidad(conn, "amazon_us", "ad_group", "9611", parent=camp_s)
        kw_s = _entidad(
            conn, "amazon_us", "keyword", "9621", parent=ag_s, match_type="EXACT", keyword_text="s"
        )
        _estado(conn, camp_s, synced_at=synced)
        _estado(conn, ag_s, synced_at=synced)
        _estado(conn, kw_s, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
        _siembra_kw_bid(conn, run_id, kw_s)

        # ciclo 1: sin goals -> todo elegible-pero-sin-goal
        res1 = _corre(conn)
        assert res1.status == "done"
        assert res1.decisions_count == 0
        assert json.loads(res1.notes)["skips"]["entidad"] == {"sin_goal": 1}

        # ciclo 2: plataforma habilitada (mode shadow) + overrides por campana
        _goal_plataforma(conn)
        # M: goal de campana habilitado con mode 'off' -> goal_mode_off
        camp_m = _entidad(conn, "amazon_us", "campaign", "9602")
        ag_m = _entidad(conn, "amazon_us", "ad_group", "9612", parent=camp_m)
        kw_m = _entidad(
            conn, "amazon_us", "keyword", "9622", parent=ag_m, match_type="EXACT", keyword_text="m"
        )
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, target_acos_pct, bid_floor,"
            " bid_ceiling, bid_currency, enabled, mode)"
            " VALUES ('campaign', %s, 25, 0.40, 2.50, 'USD', true, 'off')",
            (camp_m,),
        )
        _estado(conn, camp_m, synced_at=synced)
        _estado(conn, ag_m, synced_at=synced)
        _estado(conn, kw_m, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
        _siembra_kw_bid(conn, run_id, kw_m)
        # E: cubierta por la plataforma pero su keyword esta PAUSED -> estado_no_enabled
        camp_e = _entidad(conn, "amazon_us", "campaign", "9603")
        ag_e = _entidad(conn, "amazon_us", "ad_group", "9613", parent=camp_e)
        kw_e = _entidad(
            conn, "amazon_us", "keyword", "9623", parent=ag_e, match_type="EXACT", keyword_text="e"
        )
        _estado(conn, camp_e, synced_at=synced)
        _estado(conn, ag_e, synced_at=synced)
        _estado(
            conn,
            kw_e,
            synced_at=synced,
            current_bid=Decimal("1.00"),
            bid_currency="USD",
            status="PAUSED",
        )
        _siembra_kw_bid(conn, run_id, kw_e)
        # C: elegible PERO en cooldown: apply verificado de un ciclo LIVE hace <7d
        camp_c = _entidad(conn, "amazon_us", "campaign", "9604")
        ag_c = _entidad(conn, "amazon_us", "ad_group", "9614", parent=camp_c)
        kw_c = _entidad(
            conn, "amazon_us", "keyword", "9624", parent=ag_c, match_type="EXACT", keyword_text="c"
        )
        _estado(conn, camp_c, synced_at=synced)
        _estado(conn, ag_c, synced_at=synced)
        _estado(conn, kw_c, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
        _siembra_kw_bid(conn, run_id, kw_c)
        ciclo_vivo = conn.execute(
            "INSERT INTO optimizer_cycle (mode, platform) VALUES ('live', 'amazon_us') RETURNING id"
        ).fetchone()[0]
        dec_viva = conn.execute(
            "INSERT INTO decision (cycle_id, ad_entity_id, kind, decided_at, config_version_id,"
            " data_observed_at, window_start, window_end, old_value, new_value, value_currency,"
            " inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, 1.00, 0.75, 'USD', %s)"
            " RETURNING id",
            (
                ciclo_vivo,
                kw_c,
                DECIDED_AT - dt.timedelta(days=7),
                config_id,
                DECIDED_AT - dt.timedelta(days=7, hours=1),
                dt.date(2026, 7, 20),
                dt.date(2026, 8, 10),
                Json({"seed": "cooldown"}),
            ),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO decision_application (decision_id, attempted_at, confirmed_at,"
            " verify_ok, platform_ack, applied_cycle_id) VALUES (%s, %s, %s, true, %s, %s)",
            (
                dec_viva,
                DECIDED_AT - dt.timedelta(days=6),
                DECIDED_AT - dt.timedelta(days=6),
                Json({"estado": "ok"}),
                # ADV-06 (sellado 21): el cooldown mira el ciclo EJECUTOR
                # (aqui, el mismo live que decidio y aplico).
                ciclo_vivo,
            ),
        )

        res2 = _corre(conn)
        assert res2.status == "done"
        skips_entidad = json.loads(res2.notes)["skips"]["entidad"]
        # kw_s ya NO esta en sin_goal: con goal de plataforma y metricas
        # maduras DECIDE banda -25 (1 decision), no skip. Los tres gates
        # cuentan cada keyword gateada por SU motivo exacto.
        assert skips_entidad == {"goal_mode_off": 1, "estado_no_enabled": 1, "cooldown_7d": 1}
        decs = conn.execute(
            "SELECT d.ad_entity_id, d.kind FROM decision d JOIN optimizer_cycle oc"
            " ON oc.id = d.cycle_id WHERE oc.id = %s",
            (res2.cycle_id,),
        ).fetchall()
        # la unica decision del ciclo 2 es la banda de kw_s (target 25 por
        # plataforma, ACoS 36%): las tres gateadas NO decidieron
        assert [(d[0], d[1]) for d in decs] == [(kw_s, "bid")]


# ---------------------------------------------------------------------------
# 9. REPEATABLE READ verificable en la fase de lecturas
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_fase_de_lecturas_corre_en_repeatable_read(monkeypatch):
    with _db_temporal("orbit_ciclo_rr") as (conn, _c):
        _siembra_maestra(conn)
        capturado: dict[str, str] = {}
        original = w.ventanas_entidad

        def _espia(conn_entrante, ad_entity_id, decided_at):
            capturado["isolation"] = conn_entrante.execute("SHOW transaction_isolation").fetchone()[
                0
            ]
            return original(conn_entrante, ad_entity_id, decided_at)

        monkeypatch.setattr(w, "ventanas_entidad", _espia)
        res = _corre(conn)
        assert res.status == "done"
        assert capturado["isolation"] == "repeatable read"


# ---------------------------------------------------------------------------
# 10. Sintaxis: las SQL del modulo parsean como Postgres real
# ---------------------------------------------------------------------------

# TUPLA LITERAL hardcodeada (CORTES 01 1.2, DoD): cada SQL del modulo aparece
# EXPLICITO aqui, visible en cada diff que agregue uno. ORBIT 04 2.4 agrega
# los de la fase de apply (guard del cierre, ownership-check, sello apply y
# applied_count por columna).
_SQL_CYCLE = (
    "_SQL_CLAIM",
    "_SQL_RASTRO",
    "_SQL_ABRIR_ENVELOPE",
    "_SQL_CERRAR_ENVELOPE",
    "_SQL_SELLAR_FALLIDO",
    "_SQL_LIBERAR_LOCK",
    "_SQL_HEARTBEAT",
    "_SQL_CONFIG_RECIENTE",
    "_SQL_CAMPANAS",
    "_SQL_GOALS",
    "_SQL_DECISORAS",
    "_SQL_GRUPOS",
    "_SQL_INSERT_DECISION",
    "_SQL_OWNER_LOCK",
    "_SQL_SELLA_APPLY",
    "_SQL_APPLIED_COUNT_CICLO",
)


def test_sql_del_modulo_parsea_como_postgres():
    """Patron del repo: pglast es dev-dep declarada y su desaparicion debe
    FALLAR ruidosamente, no saltar en silencio. La lista es LITERAL (el SQL
    nuevo visible en diff) PERO exhaustiva contra el modulo (hallazgos
    codex+grok, cross-review 1.2): una constante _SQL_* futura sin listar
    revienta aqui en vez de quedar sin parsear en silencio -- la regression
    del candado vars() original cubierto por lista explicita."""
    assert set(_SQL_CYCLE) == {n for n in vars(ciclo) if n.startswith("_SQL_")}
    for nombre in _SQL_CYCLE:
        sql = getattr(ciclo, nombre).replace("%s", "NULL")
        assert pglast.parse_sql(sql), f"{nombre} no parseo"


# ---------------------------------------------------------------------------
# 11. Regresiones CodeRabbit (ronda ready): perdedor no libera lock ajeno;
#     congelado EFECTIVO de floor/ceiling
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_claim_perdido_no_libera_lock_ajeno_con_mismo_owner():
    """Hallazgo CodeRabbit (major): el finally liberaba el lock aunque el
    claim se hubiera PERDIDO -- con owners IGUALES entre procesos (owner fijo
    de config, o hostname:pid repetido en contenedores del mismo host) el
    perdedor borraba el lock del ganador y dos ciclos quedaban escribiendo en
    paralelo. Quien nunca gano el claim NO libera nada."""
    with _db_temporal("orbit_ciclo_mismo_owner") as (conn, conectar):
        _siembra_maestra(conn)
        ocupante = conectar()  # otro proceso que YA tiene el lock, mismo owner
        ocupante.execute(
            "INSERT INTO ads_optimizer_lock (job_key, owner) VALUES (%s, %s)",
            (JOB_KEY, "gemelo"),
        )
        try:
            with pytest.raises(ciclo.CicloOcupado):
                ciclo.corre_ciclo(
                    conn,
                    platform="amazon_us",
                    owner="gemelo",
                    decided_at=DECIDED_AT,
                    heartbeat_cada=1,
                )
            fila = conn.execute(
                "SELECT owner FROM ads_optimizer_lock WHERE job_key = %s", (JOB_KEY,)
            ).fetchone()
            assert fila is not None, "el perdedor NO borra el lock del ocupante"
            assert fila[0] == "gemelo"
        finally:
            ocupante.close()


def test_goal_json_congela_floor_ceiling_efectivos():
    """Hallazgo CodeRabbit (major): el congelado llevaba bid_floor/ceiling
    CRUDOS del goal, pero decide_bid consume los EFECTIVOS
    (resuelve_floor_ceiling). Un goal construido a mano con None debe
    congelar los defaults 0.10/2.50 -- exactamente lo que el motor uso; el
    valor crudo None romperia reproduce() con Decimal(None)."""
    from app.optimizer import goals as g

    goal = g.Goal(
        scope="platform",
        ad_entity_id=None,
        platform="amazon_us",
        target_acos_pct=Decimal("25"),
        bid_floor=None,
        bid_ceiling=None,
        bid_currency="USD",
        harvest_campaign_id=None,
        harvest_ad_group_id=None,
        harvest_default_bid=None,
        enabled=True,
        mode="shadow",
    )
    congelado = ciclo._goal_json(goal)
    assert congelado["bid_floor"] == "0.10"
    assert congelado["bid_ceiling"] == "2.50"


# ---------------------------------------------------------------------------
# 12. CORTES 01 (1.2): sello bitemporal de la evidencia + replay del corte
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_bitemporal_evidencia_reciente_entra_al_max_del_data_observed_at():
    """Una correccion (observacion NUEVA append-only) de una hoja del grupo
    con observed_at MAS RECIENTE que el dato directo del termino pero <
    decided_at: entra al max -- data_observed_at del negative es el
    observed_at_max de la EVIDENCIA, no el del termino (sello bitemporal
    ronda 1 codex). La obs nueva replica los valores de la que colapsa: las
    sumas del grupo y el umbral quedan intactos."""
    with _db_temporal("orbit_ciclo_bi1") as (conn, _c):
        ids = _siembra_maestra(conn)
        run_id = conn.execute("SELECT id FROM ingest_run LIMIT 1").fetchone()[0]
        reciente = dt.datetime(2026, 8, 20, 1, tzinfo=dt.UTC)
        _metrica(  # colapsa a esta (observed_at mayor): suma identica
            conn,
            run_id,
            ids["kw_pause"],
            FIN_CORTES,
            reciente,
            cost="1.50",
            ad_revenue="1.00",
            clicks=4,  # 08-12 es fecha IMPAR de _siembra_kw_pause -> clicks 4
            orders=0,
        )
        res = _corre(conn)
        assert res.status == "done"
        neg = conn.execute(
            "SELECT data_observed_at, inputs FROM decision"
            " WHERE cycle_id = %s AND kind = 'negative'",
            (res.cycle_id,),
        ).fetchone()
        assert neg is not None
        # el dato directo del termino es _obs(07-12); la evidencia trae 08-20:
        # gana la evidencia y decided_at (08-22) no clampea nada
        assert neg[0] == reciente
        assert neg[1]["corte"]["evidencia"]["observed_at_max"] == reciente.isoformat()


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_bitemporal_clamp_observed_at_futuro_a_decided_at():
    """El borde sellado (ronda 2 qwen): un observed_at POSTERIOR a
    decided_at (backfill/skew; la convencion _obs() del repo no lo produce,
    se siembra EXPLICITO) debe CLAMPEARSE a decided_at. Sin el clamp, el
    CHECK decision_dato_no_del_futuro viola y UNA fila aborta el
    executemany de TX3 -- el ciclo ENTERO de la plataforma muere (regla 9:
    el test demuestra que el ciclo sobrevive y data_observed_at == decided_at
    exacto)."""
    with _db_temporal("orbit_ciclo_bi2") as (conn, _c):
        ids = _siembra_maestra(conn)
        run_id = conn.execute("SELECT id FROM ingest_run LIMIT 1").fetchone()[0]
        futuro = dt.datetime(2026, 8, 24, 1, tzinfo=dt.UTC)  # > DECIDED_AT (08-22)
        # La obs futura se siembra en una HOJA NUEVA sin ad_entity_state:
        # aporta a la EVIDENCIA del grupo pero NO decide (estado_no_enabled),
        # asi el test aisla el sello de 1.2 (negative) del camino bid/pause,
        # cuyo data_observed_at es preexistente y su clamp llega en 1.3.
        # Valores neutros (clicks 0 / orders 0 / cost 0.00): elegibilidad,
        # expected y umbral del grupo quedan intactos.
        kw_solo_evidencia = _entidad(
            conn,
            "amazon_us",
            "keyword",
            "9299",
            parent=ids["ag"],
            match_type="EXACT",
            keyword_text="hoja solo evidencia",
        )
        _metrica(
            conn,
            run_id,
            kw_solo_evidencia,
            FIN_CORTES,
            futuro,
            cost="0.00",
            ad_revenue="0.00",
            clicks=0,
            orders=0,
        )
        res = _corre(conn)  # sin clamp: CheckViolation aborta TX3 aqui
        assert res.status == "done"
        assert res.decisions_count == 4  # el ciclo completo sobrevivio
        neg = conn.execute(
            "SELECT data_observed_at FROM decision WHERE cycle_id = %s AND kind = 'negative'",
            (res.cycle_id,),
        ).fetchone()
        assert neg[0] == DECIDED_AT  # LEAST(decided_at, futuro) = decided_at


def _inputs_negative(
    clicks_termino: int,
    *,
    corte: dict | None,
    cost: str = "9.0000",
    platform: str = "amazon_us",
    moneda: str = "USD",
) -> dict:
    """Fixture de inputs congelados de una decision negative (motor hygiene)
    con clicks del termino entre el legacy 20 y el umbral adaptativo: SOLO
    la clave `corte` decide si replayea a negative o a no-op. `cost`,
    `platform` y `moneda` se parametrizan desde 1.4 (el piso de cost es por
    plataforma y su replay discrimina el borde en la moneda correcta)."""
    inputs = {
        "motor": "hygiene",
        "platform": platform,
        "ventana_terminos": {
            "window_start": "2026-06-19",
            "window_end": "2026-07-18",
            "fechas": 9,
        },
        "termino": {
            "search_term": "tortugas ninja calzas",
            "cost": cost,
            "ad_revenue": "1.0000",
            "clicks": clicks_termino,
            "orders": 0,
            "fechas_distintas": 3,
            "moneda": moneda,
            "observed_at_max": "2026-07-12T01:00:00+00:00",
        },
        "goal": {"harvest": None},
        "target_acos_pct_usado": "25.00",
        "motivo": "negative_umbral",
        "modo": "shadow",
    }
    if corte is not None:
        inputs["corte"] = corte
    return inputs


def test_replay_hygiene_lee_inputs_corte_congelado():
    """reproduce() LEE inputs.corte.umbral_clicks_usado, JAMAS recalcula
    evidencia (spec): termino con 25 clicks y umbral congelado 26 -> NO
    negative. Una implementacion que ignorara la clave y usara el legacy 20
    dejaria pasar el corte en el replay (regla 9: 25 >= 20 dispararia)."""
    inputs = _inputs_negative(
        25,
        corte={
            "umbral_clicks_usado": 26,
            "elegible": True,
            "expected_clicks": None,
            "evidencia": None,
        },
    )
    assert ciclo.reproduce(inputs) == (None, None, None)  # sin_umbral_negative


def test_replay_hygiene_legacy_sin_inputs_corte_usa_20():
    """Fila HISTORICA sin inputs.corte (previa a CORTES 01): replay con el
    legacy 20 -> termino de 25 clicks SI dispara negative. Compatibilidad
    sellada: el replay de la historia previa no cambia."""
    inputs = _inputs_negative(25, corte=None)
    assert ciclo.reproduce(inputs) == ("negative", None, None)


def test_replay_hygiene_lee_piso_cost_usado_congelado():
    """CORTES 01 1.4: reproduce() LEE inputs.corte.piso_cost_usado, JAMAS
    recalcula el AOV (el snapshot de la evidencia ya no existe): termino con
    clicks 25 (>= umbral congelado 20) y cost 15 (< piso 19.40 pero >=
    legacy 8) -> NO negative. Una implementacion que ignorara el congelado
    aplicaria el legacy 8 y dispararia el corte en el replay (regla 9)."""
    inputs = _inputs_negative(
        25,
        corte={
            "umbral_clicks_usado": 20,
            "elegible": True,
            "expected_clicks": None,
            "evidencia": None,
            "piso_cost_usado": "19.40",
            "aov": "19.4",
        },
        cost="15.0000",
    )
    assert ciclo.reproduce(inputs) == (None, None, None)  # sin_umbral_negative


def test_replay_hygiene_corte_sin_piso_usa_legacy_8():
    """Transicion 1.2/1.3: fila CON inputs.corte PERO sin piso_cost_usado
    (congelada antes de 1.4) -> replay con el legacy 8 (cost 15 >= 8
    dispara). El replay de la historia congelada no cambia."""
    inputs = _inputs_negative(
        25,
        corte={
            "umbral_clicks_usado": 20,
            "elegible": True,
            "expected_clicks": None,
            "evidencia": None,
        },
        cost="15.0000",
    )
    assert ciclo.reproduce(inputs) == ("negative", None, None)


def test_replay_hygiene_legacy_mx_130_exacto():
    """Espejo MX del compat legacy: corte sin piso_cost_usado en amazon_mx,
    termino MXN con clicks sobre el umbral congelado 20 -> cost justo 130.00
    dispara negative (>= inclusivo) y 129.00 no. Sin el default por
    plataforma, un replay MX usaria el 8 USD y dispararia TODO."""
    corte_mx = {
        "umbral_clicks_usado": 20,
        "elegible": True,
        "expected_clicks": None,
        "evidencia": None,
    }
    for cost, esperado in (
        ("130.0000", ("negative", None, None)),
        ("129.0000", (None, None, None)),
    ):
        inputs = _inputs_negative(25, corte=corte_mx, cost=cost, platform="amazon_mx", moneda="MXN")
        assert ciclo.reproduce(inputs) == esperado


# ---------------------------------------------------------------------------
# 13. CORTES 01 (1.3): PAUSE adaptativo -- golden bid-que-bloqueo-pause,
#     piso cableado, camino unico, replay legacy y clamp del motor de bids
# ---------------------------------------------------------------------------


def _siembra_bid_bloquea_pause(conn) -> dict:
    """Fixture del GOLDEN 1.3: una entidad cuyo PAUSE es BLOQUEADO por el
    umbral adaptativo y cae a la banda -25% (kind final bid).

    - Ventana de CORTES (07-14..08-12): clicks 30 / cost 15.00 / orders 0
      -> PAUSE bajo los umbrales pre-CORTES 03 (legacy 25 / 12 USD); bajo
      los vigentes (fallback 100 / 40 USD) NO pausaria ni sin adaptativo.
    - Evidencia del grupo (misma hoja, D-90..D-10): orders 0 -> NO elegible
      -> umbral pause = fallback 100 (CORTES 03) -> 30 < 100 -> PAUSE
      BLOQUEADO.
    - Ventana de BIDS propia (max metric_date 08-16 -> fin 08-13): filas de
      07-15..08-13 -> cost 17.00 / revenue 29.10 / orders 1 (la del 08-13)
      -> ACoS 58% > 1.35x25 con orders>=1 -> banda -25%: bid 1.00 -> 0.75."""
    run_id = _run(conn)
    _config_version(conn, {"ads_optimizer_mode": "shadow"})
    _goal_plataforma(conn)
    camp = _entidad(conn, "amazon_us", "campaign", "9501")
    ag = _entidad(conn, "amazon_us", "ad_group", "9511", parent=camp)
    kw = _entidad(
        conn,
        "amazon_us",
        "keyword",
        "9521",
        parent=ag,
        match_type="EXACT",
        keyword_text="kw bloqueo",
    )
    synced = DECIDED_AT - dt.timedelta(hours=4)
    _estado(conn, camp, synced_at=synced)
    _estado(conn, ag, synced_at=synced)
    _estado(conn, kw, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
    for fecha in _rango(dt.date(2026, 7, 14), dt.date(2026, 8, 12)):
        _metrica(
            conn, run_id, kw, fecha, _obs(fecha), cost="0.50", ad_revenue="1.00", clicks=1, orders=0
        )
    for fecha in _rango(dt.date(2026, 8, 13), dt.date(2026, 8, 16)):
        _metrica(
            conn, run_id, kw, fecha, _obs(fecha), cost="2.50", ad_revenue="0.10", clicks=6, orders=1
        )
    return {"camp": camp, "ag": ag, "kw": kw}


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_golden_bid_que_bloqueo_pause():
    """El test estrella de 1.3: decision de kind final BID cuya existencia
    depende de que el umbral adaptativo de pause (fallback 100 con CORTES 03,
    grupo no elegible) BLOQUEO el PAUSE que los umbrales pre-CORTES 03
    (legacy 25 / 12 USD) si habrian cortado con esta geometria (30 / 15.00).
    LIMITACION (hallazgo grok, cross-review CORTES 03): con 100/40 esta
    geometria YA NO discrimina la lectura del freeze -- 30 clicks quedan
    bloqueados con freeze, sin el, y el costo 15 < 40 mata el pause en toda
    era; si _replay_bid ignorara umbral_clicks_usado, este test seguiria
    verde. Esa guarda vive ahora en el test PURO
    test_replay_pause_lee_el_umbral_congelado_jamas_el_default (freeze 150 vs
    default 100 con los mismos agregados). El replay-exacto de aqui sigue
    sellando que la decision bid persistida se rejugable."""
    with _db_temporal("orbit_ciclo_bqp") as (conn, _c):
        ids = _siembra_bid_bloquea_pause(conn)
        res = _corre(conn)
        assert res.status == "done"
        assert res.decisions_count == 1
        fila = conn.execute(
            "SELECT kind, old_value, new_value, value_currency, data_observed_at, inputs"
            " FROM decision WHERE cycle_id = %s AND ad_entity_id = %s",
            (res.cycle_id, ids["kw"]),
        ).fetchone()
        # el pause BLOQUEADO: la decision es la banda -25%, no el corte
        assert fila[0] == "bid"
        assert (fila[1], fila[2], fila[3]) == (Decimal("1.00"), Decimal("0.75"), "USD")
        # sello bitemporal: SU ventana de bids termina en max(08-16)-3d =
        # 08-13, mas reciente que la evidencia (08-12) -> gana el obs directo
        assert fila[4] == _obs(dt.date(2026, 8, 13))
        corte = fila[5]["corte"]
        # fallback 100 CABLEADO con evidencia del grupo (no elegible: orders 0)
        assert corte["umbral_clicks_usado"] == 100
        assert corte["elegible"] is False
        assert corte["expected_clicks"] is None
        assert corte["evidencia"]["clicks"] == 30
        assert corte["evidencia"]["orders"] == 0
        assert corte["evidencia"]["ventana_hasta"] == "2026-08-12"
        # idem 1.4: decision del motor de bids SIN piso congelado (solo negative)
        assert "piso_cost_usado" not in corte
        assert "aov" not in corte
        # REPLAY EXACTO leyendo el congelado (100): el pause sigue bloqueado
        # y la banda rejuega igual
        assert ciclo.reproduce(fila[5]) == ("bid", Decimal("0.75"), "USD")


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_piso_pause_100_en_el_freeze_del_ciclo():
    """Piso pause CABLEADO (DoD 1.3, piso CORTES 03): el grupo maestro es
    elegible con expected 14.5555... -> bruto ceil(21.83) = 22 < legacy 100.
    El umbral FINAL que el freeze congela en la decision pause es el PISO
    100 -- una implementacion sin el max() congelaria 22 y este test
    reventaria (la funcion pura ya vive en test_optimizer_cortes; esto
    testea el cableado por el ciclo)."""
    with _db_temporal("orbit_ciclo_piso") as (conn, _c):
        _siembra_maestra(conn)
        res = _corre(conn)
        assert res.status == "done"
        fila = conn.execute(
            "SELECT inputs FROM decision WHERE cycle_id = %s AND kind = 'pause'",
            (res.cycle_id,),
        ).fetchone()
        corte = fila[0]["corte"]
        assert corte["umbral_clicks_usado"] == 100  # el piso gano al bruto 22
        assert corte["elegible"] is True
        assert corte["expected_clicks"] == "14.55555555555555555555555556"
        # CORTES 03 (cierre replay): el freeze lleva el piso de costo usado
        assert corte["cost_min_usado"] == "40"


def _inputs_pause_legacy(
    clicks: int = 30, cost: str = "15.0000", corte: dict | None = None
) -> dict:
    """Fixture de inputs congelados de una pause HISTORICA (pre-CORTES 01,
    sin la clave corte): la fila clasica de 30 clicks / 15.00 USD y su
    ESPEJO de la pause real de produccion (119 clicks / 45.80 USD). Sin la
    clave, el replay rejuega con la HISTORIA congelada de su era (decision
    del lead 2026-08-28: 25 clicks / 12 USD, constantes REPLAY_*; jamas el
    vigente 100/40). Con `corte`, simula una fila CON freeze (era CORTES 01);
    `cost_min_usado` solo existe en freezes desde CORTES 03."""
    inputs = {
        "motor": "bid",
        "platform": "amazon_us",
        "ventanas": {
            "bids": None,
            "cortes": {
                "window_start": "2026-07-14",
                "window_end": "2026-08-12",
                "fechas": 30,
                "cost": cost,
                "ad_revenue": "30.0000",
                "revenue_same_sku": None,
                "clicks": clicks,
                "orders": 0,
                "moneda": "USD",
                "observed_at_max": "2026-08-12T01:00:00+00:00",
            },
        },
        "goal": {"bid_floor": "0.4000", "bid_ceiling": "2.5000", "harvest": None},
        "target_acos_pct_usado": "25.00",
        "bid_actual": "1.0000",
        "bid_moneda": "USD",
        "factor": None,
        "motivo": "pause_umbral",
        "modo": "shadow",
    }
    if corte is not None:
        inputs["corte"] = corte
    return inputs


def test_replay_legacy_pause_sin_inputs_corte_usa_historicos():
    """Fila de pause HISTORICA sin inputs.corte -> replay con la HISTORIA
    congelada de su era (decision del lead 2026-08-28: replay fiel por
    construccion; REPLAY_PAUSE_CLICKS_PRE_CORTES01=25 +
    REPLAY_PAUSE_COST_PRE_CORTES03=12 USD; JAMAS el vigente 100/40). DOS
    casos:
    (1) la fila clasica de 30 clicks / 15.00 USD REPRODUCE (30 >= 25 y
        15.00 >= 12) -- contra el default vigente 100/40 daba no-op (rojo
        del TDD); era una de las 27 pre-CORTES-01 que el piso vivo dejaba
        mudas.
    (2) la fila espejo de la real (119 clicks / 45.80 USD) -> PAUSE
        (119 >= 25 y 45.80 >= 12; VERDE TAMBIEN EN ROJO: con el default
        vigente 100/40 ya reproducia -- su fidelidad era coincidencia de
        datos, ahora es contrato)."""
    assert ciclo.reproduce(_inputs_pause_legacy()) == ("pause", None, None)
    espejo_real = _inputs_pause_legacy(clicks=119, cost="45.8000")
    assert ciclo.reproduce(espejo_real) == ("pause", None, None)


def test_replay_bid_kind_congelada_depende_del_piso_costo():
    """Guarda del hueco que vio grok (cross-review ronda del lead): no habia
    replay de una decision kind=BID cuya existencia dependa de la puerta de
    costo del pause -- si _pendiente_bid dejara de congelar cost_min_usado
    (o _replay_bid de leerlo), una fila viva CORTES 03 con clicks sobre el
    umbral y costo ENTRE el historico (12) y el vigente (40) rejugaria como
    pause y la suite seguiria verde. Espejo: freeze {umbral 100,
    cost_min "40"}, entidad 120 clicks / 25.21 USD / 0 ordenes (pause
    bloqueado SOLO por el piso congelado) y banda -25% en bids (36/100,
    orders 5). Si el replay usara el historico 12, rejugaria pause y la
    primera asercion reventaria."""
    inputs_bid = {
        "motor": "bid",
        "platform": "amazon_us",
        "ventanas": {
            "bids": {
                "window_start": "2026-07-18",
                "window_end": "2026-08-16",
                "fechas": 30,
                "cost": "36.0000",
                "ad_revenue": "100.0000",
                "revenue_same_sku": None,
                "clicks": 50,
                "orders": 5,
                "moneda": "USD",
                "observed_at_max": "2026-08-20T06:00:00+00:00",
            },
            "cortes": {
                "window_start": "2026-07-19",
                "window_end": "2026-08-17",
                "fechas": 30,
                "cost": "25.2100",
                "ad_revenue": "0.0000",
                "revenue_same_sku": None,
                "clicks": 120,
                "orders": 0,
                "moneda": "USD",
                "observed_at_max": "2026-08-25T08:06:11.871936+00:00",
            },
        },
        "goal": {"bid_floor": "0.4000", "bid_ceiling": "2.5000", "harvest": None},
        "target_acos_pct_usado": "25.00",
        "bid_actual": "1.0000",
        "bid_moneda": "USD",
        "factor": None,
        "motivo": "banda_menos_25",
        "modo": "shadow",
        "corte": {"umbral_clicks_usado": 100, "cost_min_usado": "40", "elegible": False},
    }
    assert ciclo.reproduce(inputs_bid) == ("bid", Decimal("0.75"), "USD")


def test_replay_pause_lee_el_umbral_congelado_jamas_el_default():
    """Guarda PURA del hallazgo grok (cross-review CORTES 03): el replay DEBE
    consumir inputs.corte.umbral_clicks_usado, jamas caer al default. Mismos
    agregados (120 clicks / 45 USD / 0 ordenes), dos filas: CON freeze 150
    (grupo elegible con bruto 150) NO pausa (120 < 150); SIN freeze los
    historicos REPLAY_* SI pausan (120 >= 25 y 45 >= 12). Si _replay_bid dejara de
    leer el congelado, ambas filas dan pause y la primera asercion
    reventaria. Es la discriminacion que el golden bid-que-bloqueo perdio con
    los umbrales 100/40 (su geometria de 30 clicks es bloqueada en toda era)."""
    con_freeze = _inputs_pause_legacy(
        clicks=120, cost="45.0000", corte={"umbral_clicks_usado": 150, "elegible": True}
    )
    assert ciclo.reproduce(con_freeze) == (None, None, None)
    sin_freeze = _inputs_pause_legacy(clicks=120, cost="45.0000")
    assert ciclo.reproduce(sin_freeze) == ("pause", None, None)


def test_replay_pause_congelada_cortes01_reproduce_con_piso_historico():
    """Misma geometria del hallazgo codex (cross-review CORTES 03), bajo el
    contrato NUEVO (decision del lead 2026-08-28: replay fiel por
    construccion): una pause CON inputs.corte de la era CORTES 01 (umbral de
    clicks congelado 50, SIN cost_min_usado -- la clave nace en CORTES 03)
    cuyo costo queda ENTRE el piso historico y el vigente -- 72 clicks /
    25.21 USD: la fila 30 del spot-check 4.4 (decision 774 y sus
    re-decisiones 423/541/659) -- REPRODUCE: 72 >= 50 congelado y
    25.21 >= 12 (REPLAY_PAUSE_COST_PRE_CORTES03, la historia de su era; el
    piso vigente 40 JAMAS entra al replay). Antes del cierre el piso VIVO
    mataba el pause y el replay daba (None, None, None)."""
    congelada = _inputs_pause_legacy(
        clicks=72,
        cost="25.2100",
        corte={"umbral_clicks_usado": 50, "elegible": False},
    )
    assert ciclo.reproduce(congelada) == ("pause", None, None)


def test_replay_fiel_por_construccion_ignora_el_vigente(monkeypatch):
    """FIDELIDAD POR CONSTRUCCION (decision del lead 2026-08-28): el replay
    de una fila CON `cost_min_usado` congelado manda sobre el piso VIVO. Con
    PAUSE_COST_MIN envenenado a 999, la fila 72 clicks / 45.00 USD con
    freeze {umbral_clicks_usado: 50, cost_min_usado: "40"} sigue pausando
    (45 >= 40 congelado); si _replay_bid dejara de leer el congelado, el
    999 vivo mataria el pause y la asercion reventaria. DESVIACION del pin
    literal de la task declarada: la task pidio esta fila con cost 25.21,
    pero 25.21 < 40 NO pausaria ni con el congelado (el test seria
    indistinguible: congelado y vivo dan el mismo no-op); el escenario
    historico real es una fila pausada BAJO el piso 40 de CORTES 03 cuyo
    vigente cambia despues."""
    monkeypatch.setitem(bid_mod.PAUSE_COST_MIN, "amazon_us", Decimal("999"))
    congelada = _inputs_pause_legacy(
        clicks=72,
        cost="45.0000",
        corte={"umbral_clicks_usado": 50, "cost_min_usado": "40"},
    )
    assert ciclo.reproduce(congelada) == ("pause", None, None)


def test_constantes_historicas_de_replay_son_inmutables():
    """Las constantes REPLAY_* son HISTORIA CONGELADA (decision del lead
    2026-08-28): solo las consume el replay de filas sin la clave, JAMAS el
    camino vivo. Cambiarlas ("actualizarlas" a la era nueva) rompe la
    auditoria de las 34 pauses historicas medidas fieles -- si alguien las
    toca, este test revienta. PAUSE_COST_MIN se pinea para detectar un
    cambio accidental del VIGENTE (su cambio legitimo exige re-medir el
    replay y actualizar este pin con evidencia)."""
    assert cortes.REPLAY_PAUSE_CLICKS_PRE_CORTES01 == 25
    assert {
        "amazon_us": Decimal("12"),
        "amazon_mx": Decimal("200"),
    } == bid_mod.REPLAY_PAUSE_COST_PRE_CORTES03
    assert {
        "amazon_us": Decimal("40"),
        "amazon_mx": Decimal("500"),
    } == bid_mod.PAUSE_COST_MIN


def test_replay_decision_774_real_reproduce_pause():
    """Replay con los inputs REALES congelados de la decision 774 (la fila 30
    del spot-check que motivo CORTES 03; extraidos por SELECT read-only
    2026-08-28 y espejados aqui), bajo el contrato NUEVO (decision del lead
    2026-08-28: replay fiel por construccion): 72 >= 50 (freeze de clicks) y
    25.21 >= 12 (la fila NO tiene cost_min_usado -> el piso historico de su
    era, REPLAY_PAUSE_COST_PRE_CORTES03) -> PAUSE, la decision persistida.
    Antes del cierre el piso VIVO 40 mataba el pause y el replay CAIA A LA
    BANDA -12% (bids: cost 25.21, ad_revenue 0, orders 0 -> baja sin minimo
    de ordenes; bid 0.25 x 0.88 = 0.22): un auditor veia un BID donde la
    decision persistida es PAUSE."""
    inputs_774 = {
        "goal": {
            "scope": "platform",
            "harvest": None,
            "bid_floor": "0.1000",
            "bid_ceiling": "2.5000",
            "target_acos_pct": None,
        },
        "modo": "shadow",
        "corte": {
            "elegible": False,
            "evidencia": {
                "clicks": 283,
                "fechas": 52,
                "orders": 1,
                "ventana_desde": "2026-05-30",
                "ventana_hasta": "2026-08-18",
                "observed_at_max": "2026-08-25T08:06:11.871936+00:00",
            },
            "expected_clicks": None,
            "umbral_clicks_usado": 50,
        },
        "motor": "bid",
        "factor": None,
        "motivo": "pause_umbral",
        "platform": "amazon_us",
        "ventanas": {
            "bids": {
                "cost": "25.2100",
                "clicks": 72,
                "fechas": 30,
                "moneda": "USD",
                "orders": 0,
                "ad_revenue": "0.0000",
                "window_end": "2026-08-17",
                "window_start": "2026-07-19",
                "observed_at_max": "2026-08-25T08:06:11.871936+00:00",
                "revenue_same_sku": "0.0000",
            },
            "cortes": {
                "cost": "25.2100",
                "clicks": 72,
                "fechas": 30,
                "moneda": "USD",
                "orders": 0,
                "ad_revenue": "0.0000",
                "window_end": "2026-08-17",
                "window_start": "2026-07-19",
                "observed_at_max": "2026-08-25T08:06:11.871936+00:00",
                "revenue_same_sku": "0.0000",
            },
        },
        "target_acos_pct_usado": "25.00",
        "bid_actual": "0.2500",
        "bid_moneda": "USD",
    }
    assert ciclo.reproduce(inputs_774) == ("pause", None, None)


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_camino_unico_negative_y_pause_por_la_misma_umbral_corte(monkeypatch):
    """Regla 9 (camino UNICO): negative y pause resuelven por LA MISMA
    cortes.umbral_corte con la MISMA evidencia del grupo. Un espia cuenta
    las llamadas del ciclo: si un camino reimplementara el calculo (o
    divergiera a otra funcion), su regla dejaria de aparecer aqui y el test
    reventaria. El grupo maestro resuelve 'negative' (via _procesa_grupo)
    y 'pause' (via _procesa_decisora) con la evidencia del MISMO ag."""
    with _db_temporal("orbit_ciclo_unico") as (conn, _c):
        ids = _siembra_maestra(conn)
        llamadas: list[tuple[str, int | None]] = []
        real = cortes.umbral_corte

        def espia(evidencia, regla):
            llamadas.append((regla, evidencia.ad_group_id if evidencia is not None else None))
            return real(evidencia, regla)

        monkeypatch.setattr(cortes, "umbral_corte", espia)
        res = _corre(conn)
        monkeypatch.undo()
        assert res.status == "done"
        reglas = {regla for regla, _ in llamadas}
        assert {"negative", "pause"} <= reglas
        # misma evidencia (mismo grupo) alimenta ambas reglas
        por_regla: dict[str, set[int | None]] = {}
        for regla, gid in llamadas:
            por_regla.setdefault(regla, set()).add(gid)
        assert ids["ag"] in por_regla["negative"]
        assert ids["ag"] in por_regla["pause"]
        # CARDINALIDAD sellada (spec: "Por ad group, una vez por ciclo" --
        # hallazgo codex+kimi, cross-review 1.3): el fixture tiene DOS
        # entidades decisoras del MISMO grupo (kw_bid y kw_pause) y la regla
        # pause debe resolverse UNA sola vez para el grupo. Sin el cacheo,
        # son dos llamadas y este assert revienta.
        llamadas_pause_ag = [1 for regla, gid in llamadas if regla == "pause" and gid == ids["ag"]]
        assert sum(llamadas_pause_ag) == 1


@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_bitemporal_pause_clamp_observed_at_futuro_a_decided_at():
    """Cierra el hallazgo residual de 1.2: el camino bid/pause PREEXISTENTE
    era vulnerable al CHECK decision_dato_no_del_futuro (un observed_at
    futuro en la ventana de una entidad que decide PAUSE abortaba el
    executemany de TX3; con el codigo de 1.2 este test reventaba con
    CheckViolation). Con el sello bitemporal de 1.3 en TODA decision del
    motor de bids, el LEAST(decided_at, ...) clampea y el ciclo sobrevive."""
    with _db_temporal("orbit_ciclo_bip") as (conn, _c):
        ids = _siembra_maestra(conn)
        run_id = conn.execute("SELECT id FROM ingest_run LIMIT 1").fetchone()[0]
        futuro = dt.datetime(2026, 8, 24, 1, tzinfo=dt.UTC)  # > DECIDED_AT (08-22)
        # obs futura en la ventana de CORTES de kw_pause: SU pause decide, y
        # tanto el obs directo (cortes) como la evidencia traen el futuro
        _metrica(
            conn,
            run_id,
            ids["kw_pause"],
            FIN_CORTES,
            futuro,
            cost="1.50",
            ad_revenue="1.00",
            clicks=4,  # 08-12 es fecha IMPAR de _siembra_kw_pause -> clicks 4
            orders=0,
        )
        res = _corre(conn)  # con codigo 1.2: CheckViolation aborta TX3 aqui
        assert res.status == "done"
        assert res.decisions_count == 4
        pause = conn.execute(
            "SELECT data_observed_at FROM decision WHERE cycle_id = %s AND kind = 'pause'",
            (res.cycle_id,),
        ).fetchone()
        assert pause[0] == DECIDED_AT  # LEAST(decided_at, futuro)
        bid = conn.execute(
            "SELECT data_observed_at FROM decision WHERE cycle_id = %s AND kind = 'bid'",
            (res.cycle_id,),
        ).fetchone()
        # el bid tambien sella con la evidencia: max(bids 08-16, futuro) =
        # futuro -> clamp a decided_at
        assert bid[0] == DECIDED_AT
