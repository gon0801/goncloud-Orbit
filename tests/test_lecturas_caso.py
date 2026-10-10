"""Lectura por plataforma para armar CasoHoja (BIDS 02 M.2).

`lee_plataforma` ejecuta cuatro consultas por plataforma; `caso` ejecuta
cero para una hoja sin cambio de bid en 90 dias y una para una hoja con
cambio. Con `visto_el`, la lectura reproduce lo que el ciclo veia ese dia.
Un dia sin fila de la hoja y con ingesta de la plataforma cuenta como cero;
un dia sin ingesta deja el tramo en None. El ad group y la cuenta suman
solo hojas de `v_hoja_activa`.

Son INTEGRACION: base temporal con TODAS las migraciones en orden, sin la
0011 y sin las reversas, como tests/test_bids02_vistas.py.
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
from psycopg.types.json import Json
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.lecturas_caso import lee_plataforma
from app.optimizer.caso import (
    BidVigente,
    CasoHoja,
    Economia,
    EconomiaPlataforma,
    EvidenciaNivel,
    InsumosPausa,
    PrecioVentana,
    Tramo,
    Trayectoria,
)

ROOT = Path(__file__).resolve().parents[1]

_D = dt.date(2026, 10, 9)
_DECIDIDO = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.UTC)
_ANCLA = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.UTC)
_DECIDIDA = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.UTC)
_OBSERVADO = dt.datetime(2026, 9, 19, 12, 0, tzinfo=dt.UTC)
_VENTANA_DESDE = dt.date(2026, 8, 15)
_VENTANA_HASTA = dt.date(2026, 9, 5)

_MONEDA = {"amazon_mx": "MXN", "amazon_us": "USD"}

FALTA_PG = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


@contextmanager
def _db_lecturas():
    """DB temporal con todas las migraciones en orden, sin la 0011 y sin las
    reversas (igual que `tests/test_bids02_vistas.py`)."""
    dsn = _test_dsn()
    db = f"orbit_bids02_lec_{socket.gethostname().lower()}_{os.getpid()}"
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


def _entidad(conn, platform, kind, external, parent=None, *, match=None, texto=None):
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
        " keyword_text) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, match, texto),
    ).fetchone()[0]


def _estado(conn, ad_entity_id, status="ENABLED", *, targeting=None, bid=None, moneda=None):
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, targeting_type, current_bid,"
        " bid_currency, synced_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (ad_entity_id, status, targeting, bid, moneda, _ANCLA),
    )


def _triple(conn, platform="amazon_mx", *, campana="ENABLED", bid=Decimal("6"), tag=""):
    c = _entidad(conn, platform, "campaign", f"c-{tag}")
    g = _entidad(conn, platform, "ad_group", f"g-{tag}", c)
    h = _entidad(conn, platform, "keyword", f"h-{tag}", g, match="EXACT", texto=f"kw-{tag}")
    _estado(conn, c, campana, targeting="MANUAL")
    _estado(conn, g, "ENABLED")
    _estado(conn, h, "ENABLED", bid=bid, moneda=_MONEDA[platform])
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


def _recorte(conn, *, hoja, config, ejecutor, old, new, confirmado, motivo=None, moneda="MXN"):
    """Decision de bid aplicada por un ciclo live. Devuelve el decision_id."""
    decision = conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
        (
            _ciclo(conn, "live"),
            hoja,
            config,
            _DECIDIDA,
            _OBSERVADO,
            _VENTANA_DESDE,
            _VENTANA_HASTA,
            old,
            new,
            moneda,
            Json({"motivo": motivo} if motivo else {}),
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO decision_application (decision_id, applied_cycle_id, confirmed_at,"
        " platform_ack, verify_ok) VALUES (%s, %s, %s, %s, true)",
        (decision, ejecutor, confirmado, Json({"readback": True})),
    )
    return decision


def _reversa(conn, decision, *, sellada):
    conn.execute(
        "INSERT INTO apply_attempt (decision_id, seq, tipo, request_payload,"
        " quota_cobrada, resultado, finished_at)"
        " VALUES (%s, 1, 'reversa', %s, false, 'ok', %s)",
        (decision, Json({"bid": "viejo"}), sellada),
    )


def _ingest(conn):
    return conn.execute(
        "INSERT INTO ingest_run (source) VALUES ('prueba') RETURNING id"
    ).fetchone()[0]


def _metrica(
    conn,
    ingest,
    hoja,
    fecha,
    observado,
    *,
    clics=0,
    pedidos=0,
    venta=0,
    gasto=0,
    impresiones=0,
    moneda="MXN",
):
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, ad_revenue, impressions, clicks, orders, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            hoja,
            fecha,
            observado,
            moneda,
            Decimal(gasto) if gasto is not None else None,
            Decimal(venta) if venta is not None else None,
            impresiones,
            clics,
            pedidos,
            ingest,
        ),
    )


def _relleno(conn, ingest, hoja, desde, hasta, observado, *, salta=frozenset()):
    """Una fila por dia para marcar ingesta de la plataforma (la hoja de
    relleno es inactiva: sus filas no entran a ningun roll-up)."""
    dia = desde
    while dia <= hasta:
        if dia not in salta:
            _metrica(conn, ingest, hoja, dia, observado)
        dia += dt.timedelta(days=1)


class _Contadora:
    """Conexion que cuenta `execute` (la guia M.2 pide contar consultas asi)."""

    def __init__(self, conn):
        self._conn = conn
        self.n = 0

    def execute(self, sql, params=()):
        self.n += 1
        return self._conn.execute(sql, params)


def _economia(moneda="MXN"):
    return EconomiaPlataforma(
        moneda=moneda,
        equilibrio_acos_pct=None,
        gasto_para_concluir=Decimal("350") if moneda == "MXN" else Decimal("36"),
        confianza_recorte=Decimal("0.80"),
        confianza_subida=Decimal("0.70"),
    )


def _bid(valor=Decimal("6")):
    return BidVigente(valor=valor, moneda="MXN", piso=Decimal("1"), techo=Decimal("20"))


def _pausa():
    return InsumosPausa(
        cortes=None,
        umbral_clics=20,
        gasto_minimo=Decimal("100"),
        expected_clicks=None,
        politica_economica=None,
    )


def _arma(lecturas, conn, hoja, grupo):
    return lecturas.caso(
        conn,
        hoja_id=hoja,
        ad_group_id=grupo,
        bid=_bid(),
        target_acos_pct=Decimal("15"),
        pausa=_pausa(),
    )


@FALTA_PG
def test_cuatro_consultas_por_plataforma_y_cero_o_una_por_hoja():
    """`lee_plataforma` ejecuta cuatro consultas; `caso` ejecuta cero para
    una hoja sin cambio de bid en 90 dias y una para una hoja con cambio."""
    with _db_lecturas() as conn:
        config = _config(conn)
        vivo = _ciclo(conn, "live")
        _, _, con_cambio = _triple(conn, tag="cambio", bid=Decimal("6"))
        _, gc, sin_cambio = _triple(conn, tag="quieta", bid=Decimal("6"))
        _recorte(
            conn,
            hoja=con_cambio,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("6"),
            confirmado=dt.datetime(2026, 9, 19, 12, 0, tzinfo=dt.UTC),
        )
        contada = _Contadora(conn)
        lecturas = lee_plataforma(contada, "amazon_mx", _DECIDIDO, economia=_economia())
        assert contada.n == 4
        caso_quieta = _arma(lecturas, contada, sin_cambio, gc)
        assert contada.n == 4
        assert caso_quieta.trayectoria.cambios == ()
        assert caso_quieta.trayectoria.efecto is None
        caso_cambio = _arma(lecturas, contada, con_cambio, None)
        assert contada.n == 5
        assert len(caso_cambio.trayectoria.cambios) == 1
        assert caso_cambio.trayectoria.efecto is not None


@FALTA_PG
def test_visto_el_reproduce_lo_que_el_ciclo_veia():
    """Con dos observaciones de la misma hoja y fecha, `visto_el` entre las
    dos devuelve la primera; un cambio confirmado despues de `visto_el` no
    entra a la trayectoria. La lectura sin fecha ve lo ultimo."""
    with _db_lecturas() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        vivo = _ciclo(conn, "live")
        _, g, h = _triple(conn, tag="bitemporal", bid=Decimal("6"))
        _, _, rell = _triple(conn, tag="rell", campana="PAUSED")
        obs_temprana = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.UTC)
        _relleno(
            conn, ingest, rell, _D - dt.timedelta(days=180), _D - dt.timedelta(days=1), obs_temprana
        )
        fecha = _D - dt.timedelta(days=20)
        _metrica(
            conn,
            ingest,
            h,
            fecha,
            dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.UTC),
            clics=5,
            pedidos=1,
            venta=50,
            gasto=7,
            impresiones=40,
        )
        _metrica(
            conn,
            ingest,
            h,
            fecha,
            dt.datetime(2026, 10, 4, 12, 0, tzinfo=dt.UTC),
            clics=8,
            pedidos=1,
            venta=50,
            gasto=7,
            impresiones=40,
        )
        _recorte(
            conn,
            hoja=h,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("8"),
            confirmado=dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC),
        )
        _recorte(
            conn,
            hoja=h,
            config=config,
            ejecutor=vivo,
            old=Decimal("8"),
            new=Decimal("6"),
            confirmado=dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC),
        )
        visto = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)
        contada = _Contadora(conn)
        lecturas = lee_plataforma(
            contada, "amazon_mx", _DECIDIDO, economia=_economia(), visto_el=visto
        )
        assert contada.n == 4
        caso_visto = _arma(lecturas, contada, h, g)
        assert caso_visto.propia.reciente.clics == 5
        cambios = caso_visto.trayectoria.cambios
        assert [c.fecha for c in cambios] == [dt.date(2026, 9, 14)]
        assert caso_visto.trayectoria.efecto is not None
        reciente = lee_plataforma(conn, "amazon_mx", _DECIDIDO, economia=_economia())
        caso_ahora = _arma(reciente, conn, h, g)
        assert caso_ahora.propia.reciente.clics == 8
        assert [c.fecha for c in caso_ahora.trayectoria.cambios] == [
            dt.date(2026, 9, 14),
            dt.date(2026, 10, 6),
        ]


@FALTA_PG
def test_dia_sin_fila_con_ingesta_es_cero_y_sin_ingesta_es_none():
    """Los dias sin fila de la hoja pero con ingesta de la plataforma suman
    cero; un dia sin ingesta deja el tramo entero en None (aunque la hoja
    tenga filas otros dias del tramo)."""
    with _db_lecturas() as conn:
        ingest = _ingest(conn)
        _, g, h = _triple(conn, tag="cero", bid=Decimal("6"))
        _, _, rell = _triple(conn, tag="rell", campana="PAUSED")
        hueco = _D - dt.timedelta(days=60)
        _relleno(
            conn,
            ingest,
            rell,
            _D - dt.timedelta(days=180),
            _D - dt.timedelta(days=1),
            dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC),
            salta={hueco},
        )
        obs = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)
        _metrica(
            conn,
            ingest,
            h,
            _D - dt.timedelta(days=20),
            obs,
            clics=3,
            pedidos=1,
            venta=50,
            gasto=7,
            impresiones=40,
        )
        _metrica(
            conn,
            ingest,
            h,
            _D - dt.timedelta(days=61),
            obs,
            clics=2,
            pedidos=0,
            venta=0,
            gasto=1,
            impresiones=10,
        )
        lecturas = lee_plataforma(conn, "amazon_mx", _DECIDIDO, economia=_economia())
        caso = _arma(lecturas, conn, h, g)
        assert caso.propia.reciente == Tramo(
            clics=3,
            pedidos=1,
            venta=Decimal("50"),
            gasto=Decimal("7"),
            impresiones=40,
        )
        assert caso.propia.antiguo == Tramo(None, None, None, None, None)


@FALTA_PG
def test_grupo_y_cuenta_suman_solo_hojas_activas():
    """El roll-up a ad group y cuenta excluye hojas inactivas, hojas sin
    filas y la otra plataforma. Una hoja sin impresiones despues de su
    ultimo cambio da `impresiones_post == 0`, no None."""
    with _db_lecturas() as conn:
        ingest = _ingest(conn)
        config = _config(conn)
        vivo = _ciclo(conn, "live")
        _, g, a = _triple(conn, tag="a", bid=Decimal("8"))
        _, _, b = _triple(conn, tag="b", campana="PAUSED", bid=Decimal("8"))
        _, _, _sin_filas = _triple(conn, tag="c", bid=Decimal("8"))
        _, _, u = _triple(conn, "amazon_us", tag="u", bid=Decimal("1"))
        _, _, rell = _triple(conn, tag="rell", campana="PAUSED")
        obs = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)
        _relleno(conn, ingest, rell, _D - dt.timedelta(days=180), _D - dt.timedelta(days=1), obs)
        _metrica(
            conn,
            ingest,
            a,
            _D - dt.timedelta(days=20),
            obs,
            clics=3,
            pedidos=1,
            venta=50,
            gasto=7,
            impresiones=40,
        )
        _metrica(
            conn,
            ingest,
            a,
            _D - dt.timedelta(days=4),
            obs,
            clics=1,
            pedidos=0,
            venta=0,
            gasto=2,
            impresiones=5,
        )
        _metrica(
            conn,
            ingest,
            b,
            _D - dt.timedelta(days=20),
            obs,
            clics=999,
            pedidos=999,
            venta=999,
            gasto=999,
            impresiones=999,
        )
        _metrica(
            conn,
            ingest,
            u,
            _D - dt.timedelta(days=20),
            obs,
            clics=500,
            pedidos=5,
            venta=500,
            gasto=50,
            impresiones=500,
            moneda="USD",
        )
        _recorte(
            conn,
            hoja=a,
            config=config,
            ejecutor=vivo,
            old=Decimal("10"),
            new=Decimal("8"),
            confirmado=dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC),
        )
        lecturas = lee_plataforma(conn, "amazon_mx", _DECIDIDO, economia=_economia())
        caso = _arma(lecturas, conn, a, g)
        assert caso.propia == caso.grupo == caso.cuenta
        assert caso.grupo.reciente.clics == 3
        assert caso.trayectoria.efecto.impresiones_post == 0


@FALTA_PG
def test_caso_igual_a_valor_literal():
    """Para un fixture pequeno, `caso` devuelve un `CasoHoja` igual a un
    valor literal: tramos, inmaduros, grupo, cuenta, precio, trayectoria
    vacia, ventana y sello de observacion."""
    with _db_lecturas() as conn:
        ingest = _ingest(conn)
        _, g, h = _triple(conn, tag="literal", bid=Decimal("6"))
        _, _, rell = _triple(conn, tag="rell", campana="PAUSED")
        _relleno(
            conn,
            ingest,
            rell,
            _D - dt.timedelta(days=180),
            _D - dt.timedelta(days=1),
            dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC),
        )
        obs = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)
        _metrica(
            conn,
            ingest,
            h,
            _D - dt.timedelta(days=60),
            obs,
            clics=2,
            pedidos=1,
            venta=100,
            gasto=10,
            impresiones=50,
        )
        _metrica(
            conn,
            ingest,
            h,
            _D - dt.timedelta(days=20),
            obs,
            clics=3,
            pedidos=0,
            venta=0,
            gasto=5,
            impresiones=30,
        )
        _metrica(
            conn,
            ingest,
            h,
            _D - dt.timedelta(days=5),
            obs,
            clics=4,
            pedidos=1,
            venta=200,
            gasto=8,
            impresiones=60,
        )
        lecturas = lee_plataforma(conn, "amazon_mx", _DECIDIDO, economia=_economia())
        propia = EvidenciaNivel(
            reciente=Tramo(
                clics=3, pedidos=0, venta=Decimal("0"), gasto=Decimal("5"), impresiones=30
            ),
            antiguo=Tramo(
                clics=2, pedidos=1, venta=Decimal("100"), gasto=Decimal("10"), impresiones=50
            ),
        )
        esperado = CasoHoja(
            plataforma="amazon_mx",
            hoja_id=h,
            ad_group_id=g,
            bid=_bid(),
            economia=Economia(plataforma=_economia(), target_acos_pct=Decimal("15")),
            propia=propia,
            pedidos_inmaduros=1,
            grupo=propia,
            cuenta=propia,
            precio=PrecioVentana(gasto=Decimal("13"), clics=7),
            trayectoria=Trayectoria(cambios=(), efecto=None),
            pausa=_pausa(),
            ventana_desde=_D - dt.timedelta(days=90),
            ventana_hasta=_D - dt.timedelta(days=10),
            observado_al=obs,
        )
        assert _arma(lecturas, conn, h, g) == esperado


@FALTA_PG
def test_hoja_sin_filas_en_ventana_da_propia_none():
    """Una hoja activa sin filas en la ventana madura da `propia` None
    (aunque la plataforma tenga ingesta completa): None = sin filas."""
    with _db_lecturas() as conn:
        ingest = _ingest(conn)
        _, g, h = _triple(conn, tag="vacia", bid=Decimal("6"))
        _, _, rell = _triple(conn, tag="rell", campana="PAUSED")
        _relleno(
            conn,
            ingest,
            rell,
            _D - dt.timedelta(days=180),
            _D - dt.timedelta(days=1),
            dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC),
        )
        lecturas = lee_plataforma(conn, "amazon_mx", _DECIDIDO, economia=_economia())
        caso = _arma(lecturas, conn, h, g)
        assert caso.propia is None
        assert caso.grupo is None
        assert caso.cuenta is None
        assert caso.precio == PrecioVentana(gasto=None, clics=None)
        assert caso.pedidos_inmaduros is None


def _decision_suelta(conn, *, hoja, config, old, new):
    """Decision de bid SIN aplicacion: invisible para la rama motor de
    `v_cambio_bid` (sirve para sembrar un regreso huerfano de previo)."""
    return conn.execute(
        "INSERT INTO decision (cycle_id, ad_entity_id, kind, config_version_id,"
        " decided_at, data_observed_at, window_start, window_end, old_value, new_value,"
        " value_currency, inputs) VALUES (%s, %s, 'bid', %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s) RETURNING id",
        (
            _ciclo(conn, "live"),
            hoja,
            config,
            _DECIDIDA,
            _OBSERVADO,
            _VENTANA_DESDE,
            _VENTANA_HASTA,
            old,
            new,
            "MXN",
            Json({}),
        ),
    ).fetchone()[0]


@FALTA_PG
def test_regreso_sin_cambio_previo_se_omite():
    """Un `regreso_del_dueno` sin cambio anterior en la ventana no puede
    derivar su `bid_antes` (seria inventar): se omite de la trayectoria y
    `caso` no consulta. La vista si trae la fila (la prueba no es vacua)."""
    with _db_lecturas() as conn:
        config = _config(conn)
        _, g, h = _triple(conn, tag="regreso", bid=Decimal("10"))
        suelta = _decision_suelta(conn, hoja=h, config=config, old=Decimal("10"), new=Decimal("6"))
        _reversa(conn, suelta, sellada=dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC))
        cruda = conn.execute(
            "SELECT bid_antes, bid_despues, origen FROM v_cambio_bid WHERE hoja_id = %s",
            (h,),
        ).fetchall()
        assert cruda == [(None, Decimal("10"), "regreso_del_dueno")]
        contada = _Contadora(conn)
        lecturas = lee_plataforma(contada, "amazon_mx", _DECIDIDO, economia=_economia())
        assert contada.n == 4
        caso = _arma(lecturas, contada, h, g)
        assert contada.n == 4
        assert caso.trayectoria.cambios == ()
        assert caso.trayectoria.efecto is None


@FALTA_PG
def test_datetimes_naive_se_rechazan_sin_consultas():
    """`decidido_el` y `visto_el` naive se rechazan ruidosamente antes de
    tocar la base (misma regla que `windows._fecha_utc`)."""
    with _db_lecturas() as conn:
        contada = _Contadora(conn)
        with pytest.raises(ValueError):
            lee_plataforma(
                contada,
                "amazon_mx",
                dt.datetime(2026, 10, 9, 12, 0),
                economia=_economia(),
            )
        with pytest.raises(ValueError):
            lee_plataforma(
                contada,
                "amazon_mx",
                _DECIDIDO,
                economia=_economia(),
                visto_el=dt.datetime(2026, 10, 1, 12, 0),
            )
        assert contada.n == 0


@FALTA_PG
def test_null_envenena_solo_su_metrica_y_contagia_al_grupo():
    """Una observacion NULL envenena solo ESA metrica del tramo; el roll-up
    al grupo hereda el veneno por metrica (semantica del `bool_and`)."""
    with _db_lecturas() as conn:
        ingest = _ingest(conn)
        _, g, h = _triple(conn, tag="nula", bid=Decimal("6"))
        h2 = _entidad(conn, "amazon_mx", "keyword", "h-nula-2", g, match="EXACT", texto="kw-nula-2")
        _estado(conn, h2, "ENABLED", bid=Decimal("6"), moneda="MXN")
        _, _, rell = _triple(conn, tag="rell", campana="PAUSED")
        obs = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)
        _relleno(conn, ingest, rell, _D - dt.timedelta(days=180), _D - dt.timedelta(days=1), obs)
        _metrica(
            conn,
            ingest,
            h,
            _D - dt.timedelta(days=20),
            obs,
            clics=None,
            pedidos=2,
            venta=50,
            gasto=7,
            impresiones=40,
        )
        _metrica(
            conn,
            ingest,
            h2,
            _D - dt.timedelta(days=21),
            obs,
            clics=10,
            pedidos=3,
            venta=60,
            gasto=9,
            impresiones=100,
        )
        lecturas = lee_plataforma(conn, "amazon_mx", _DECIDIDO, economia=_economia())
        caso = _arma(lecturas, conn, h, g)
        assert caso.propia.reciente == Tramo(
            clics=None,
            pedidos=2,
            venta=Decimal("50"),
            gasto=Decimal("7"),
            impresiones=40,
        )
        assert caso.grupo.reciente == Tramo(
            clics=None,
            pedidos=5,
            venta=Decimal("110"),
            gasto=Decimal("16"),
            impresiones=140,
        )
