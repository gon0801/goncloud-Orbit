"""Pantalla de busquedas que gastan sin vender (BIDS 02, P.6).

Contrato puro (`resume_busquedas`) con casos armados a mano y lectura
(`lee_busquedas`) con Postgres real sobre la 0001. Terminos y ASIN
inventados (regla 16: nada de produccion en el repo publico).
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

from app import pantalla_busquedas as pb

RAIZ = Path(__file__).resolve().parents[1]
SQL0001 = (RAIZ / "migrations" / "0001_initial.sql").read_text(encoding="utf-8")
HOY = dt.date(2026, 10, 7)
MINIMO = Decimal("350")

_skip_db = pytest.mark.skipif(_postgres_obligatorio_ausente(), reason="sin Postgres")


def _fila(ag, campana, termino, clics, gasto, pedidos, *, es_asin=False):
    return pb.FilaBusqueda(
        ad_group_id=ag,
        campana=campana,
        termino=termino,
        es_asin=es_asin,
        clics=clics,
        gasto=None if gasto is None else Decimal(str(gasto)),
        pedidos=pedidos,
    )


def _resume(filas, propios=frozenset()):
    return pb.resume_busquedas(
        tuple(filas), gasto_para_concluir=MINIMO, asins_propios=frozenset(propios)
    )


def test_referencia_visto_y_azar():
    """P.6: el azar se calcula por fila con la conversion de la cuenta."""
    ref, _asins, _palabras, _am, _pm = _resume(
        [_fila(1, "C1", "alfa", 2, 60, 0), _fila(1, "C1", "beta", 2, 40, 2)]
    )
    assert ref == pb.ReferenciaAzar(Decimal("60"), Decimal("25"))


def test_sin_referencia_sin_pedidos_o_sin_clics():
    """P.6: sin pedidos o sin clics no se inventa conversion (regla 3)."""
    ref, *_ = _resume([_fila(1, "C1", "alfa", 2, 60, 0)])
    assert ref == pb.ReferenciaAzar(None, None)
    ref, *_ = _resume([_fila(1, "C1", "alfa", 0, 0, 0)])
    assert ref == pb.ReferenciaAzar(None, None)


def test_asin_sumado_en_toda_la_cuenta():
    """P.6: el ASIN suma entre ad groups y campanas; el borde del minimo
    y el pedido lo sacan, y el casi cuenta a medio camino."""
    filas = [
        _fila(1, "C1", "b0ajeno001", 30, 200, 0, es_asin=True),
        _fila(2, "C2", "B0AJENO001", 20, 150, 0, es_asin=True),
    ]
    _ref, asins, _p, am, _pm = _resume(filas)
    assert asins == (pb.FilaAsinAjeno("B0AJENO001", 50, Decimal("350"), ("C1", "C2"), False),)
    assert am == 0
    casi = [filas[0], _fila(2, "C2", "B0AJENO001", 20, "149.99", 0, es_asin=True)]
    _ref, asins, _p, am, _pm = _resume(casi)
    assert asins == ()
    assert am == 1
    con_pedido = [filas[0], _fila(2, "C2", "B0AJENO001", 20, 150, 1, es_asin=True)]
    _ref, asins, _p, am, _pm = _resume(con_pedido)
    assert asins == ()
    assert am == 0


def test_asin_propio_se_marca_no_se_esconde():
    """P.6 (mutante obligatorio): un ASIN propio sale marcado, no fuera."""
    filas = [
        _fila(1, "C1", "b0ajeno001", 30, 200, 0, es_asin=True),
        _fila(2, "C2", "B0AJENO001", 20, 150, 0, es_asin=True),
    ]
    _ref, asins, _p, _am, _pm = _resume(filas, propios={"B0AJENO001"})
    assert asins == (pb.FilaAsinAjeno("B0AJENO001", 50, Decimal("350"), ("C1", "C2"), True),)


def test_palabra_sin_pedido_y_medio_camino():
    """P.6: una palabra suma sus busquedas; con un pedido en cualquiera
    de ellas ya no entra (mutante obligatorio)."""
    filas = [
        _fila(1, "C1", "gama uno", 10, 200, 0),
        _fila(1, "C1", "gama dos", 10, 200, 0),
    ]
    _ref, _a, palabras, _am, pm = _resume(filas)
    assert palabras == (pb.FilaPalabra("gama", 2, 20, Decimal("400"), ("gama dos", "gama uno")),)
    assert pm == 4
    _ref, _a, palabras, _am, _pm = _resume([*filas, _fila(2, "C2", "gama tres", 3, 50, 1)])
    assert palabras == ()


def test_misma_busqueda_en_dos_ad_groups():
    """P.6: la busqueda repetida en otro ad group suma clics y gasto una
    vez por fila, pero cuenta un solo texto."""
    filas = [
        _fila(1, "C1", "gama uno", 10, 200, 0),
        _fila(1, "C1", "gama dos", 10, 200, 0),
        _fila(2, "C2", "gama uno", 1, 10, 0),
    ]
    _ref, _a, palabras, _am, _pm = _resume(filas)
    (gama,) = [p for p in palabras if p.palabra == "gama"]
    assert (gama.busquedas, gama.clics, gama.gasto) == (2, 21, Decimal("410"))


def test_palabras_vacias_solo_fuera_de_una():
    """P.6: la vacia no sale sola pero las de dos se arman con la
    busqueda completa, sin quitar ninguna (mutante obligatorio)."""
    _ref, _a, palabras, _am, _pm = _resume([_fila(1, "C1", "delta para omega", 5, 400, 0)])
    assert [p.palabra for p in palabras] == ["delta", "delta para", "omega", "para omega"]


def test_palabra_corta_no_sale_sola():
    """P.6: una o dos letras no forman entrada de una; la de dos si."""
    _ref, _a, palabras, _am, _pm = _resume([_fila(1, "C1", "xy omega", 5, 400, 0)])
    assert [p.palabra for p in palabras] == ["omega", "xy omega"]


def test_metrica_none_no_suma_ni_cuenta_cero():
    """P.6 (mutante obligatorio): un None no es 0: la referencia no se
    mueve y el ASIN sin dato de pedidos no entra."""
    filas = [
        _fila(1, "C1", "alfa", 2, 60, 0),
        _fila(1, "C1", "beta", 2, 40, 2),
        _fila(1, "C1", "nulo", 3, None, 0),
    ]
    ref, _a, _p, _am, _pm = _resume(filas)
    assert ref == pb.ReferenciaAzar(Decimal("60"), Decimal("25"))
    _ref, asins, _p, _am, _pm = _resume([_fila(1, "C1", "b0sindato1", 3, 400, None, es_asin=True)])
    assert asins == ()


@_skip_db
@contextmanager
def _db_p6():
    dsn = _test_dsn()
    db = f"orbit_p6_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL0001)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _campana(conn, ext, nombre, estado="ENABLED", platform="amazon_mx"):
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, name)"
        " VALUES (%s::platform, 'campaign', %s, %s) RETURNING id",
        (platform, ext, nombre),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, now())",
        (camp, estado),
    )
    return camp


def _ad_group(conn, camp, ext, estado="ENABLED", platform="amazon_mx"):
    ag = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id)"
        " VALUES (%s::platform, 'ad_group', %s, %s) RETURNING id",
        (platform, ext, camp),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at) VALUES (%s, %s, now())",
        (ag, estado),
    )
    return ag


def _obs(
    conn,
    ag,
    termino,
    fecha,
    *,
    visto,
    cost=0,
    clics=0,
    pedidos=0,
    asin=False,
    run,
    platform="amazon_mx",
    moneda="MXN",
    reporte="r1",
):
    conn.execute(
        "INSERT INTO search_term_observation (platform, ad_entity_id, search_term,"
        " metric_date, observed_at, metric_currency, cost, clicks, orders,"
        " ad_revenue, is_asin_like, source_report_id, ingest_run_id)"
        " VALUES (%s::platform, %s, %s, %s, %s, %s::currency, %s, %s, %s, NULL, %s, %s, %s)",
        (platform, ag, termino, fecha, visto, moneda, cost, clics, pedidos, asin, reporte, run),
    )


def _run(conn):
    return conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[0]


@_skip_db
def test_lee_ventana_90_dias_termina_hoy_menos_3():
    """P.6: desde=hasta-90 con hasta=hoy-3; los dos bordes entran."""
    with _db_p6() as conn:
        run = _run(conn)
        ag = _ad_group(conn, _campana(conn, "c-1", "Campana Uno"), "ag-1")
        for fecha, gasto in (
            (dt.date(2026, 7, 5), 900),
            (dt.date(2026, 7, 6), 200),
            (dt.date(2026, 10, 4), 200),
            (dt.date(2026, 10, 5), 900),
        ):
            _obs(
                conn,
                ag,
                "B0BORDE001",
                fecha,
                visto=dt.datetime(2026, 10, 6, tzinfo=dt.UTC),
                cost=gasto,
                clics=5,
                pedidos=0,
                asin=True,
                run=run,
            )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert (pantalla.desde, pantalla.hasta) == (dt.date(2026, 7, 6), dt.date(2026, 10, 4))
        assert pantalla.asins == (
            pb.FilaAsinAjeno("B0BORDE001", 10, Decimal("400"), ("Campana Uno",), False),
        )


@_skip_db
def test_lee_solo_ultima_observacion_por_fecha():
    """P.6 (mutante DISTINCT ON): dos observaciones del mismo dia cuentan
    una sola, la mas reciente."""
    with _db_p6() as conn:
        run = _run(conn)
        ag = _ad_group(conn, _campana(conn, "c-1", "Campana Uno"), "ag-1")
        _obs(
            conn,
            ag,
            "B0ULTIMA01",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=900,
            clics=9,
            pedidos=0,
            asin=True,
            run=run,
        )
        _obs(
            conn,
            ag,
            "B0ULTIMA01",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
            cost=100,
            clics=1,
            pedidos=0,
            asin=True,
            run=run,
            reporte="r2",
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert pantalla.asins == ()
        assert pantalla.asins_a_medio_camino == 0


@_skip_db
def test_lee_solo_campanas_encendidas():
    """P.6 (mutante obligatorio): la campana apagada no cuenta; el ad
    group pausado dentro de una encendida si."""
    with _db_p6() as conn:
        run = _run(conn)
        apagada = _campana(conn, "c-off", "Campana Off", estado="PAUSED")
        ag_off = _ad_group(conn, apagada, "ag-off")
        _obs(
            conn,
            ag_off,
            "B0APAGADO1",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=900,
            clics=9,
            pedidos=0,
            asin=True,
            run=run,
        )
        encendida = _campana(conn, "c-on", "Campana On")
        ag_pausa = _ad_group(conn, encendida, "ag-pausa", estado="PAUSED")
        _obs(
            conn,
            ag_pausa,
            "B0ENCENDI1",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=400,
            clics=4,
            pedidos=0,
            asin=True,
            run=run,
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert [a.asin for a in pantalla.asins] == ["B0ENCENDI1"]


@_skip_db
def test_lee_no_mezcla_mercados_y_moneda_del_mercado():
    """P.6 (mutante filtro de plataforma): el gasto de US no suma en MX
    y la pantalla trae la moneda de su mercado."""
    with _db_p6() as conn:
        run = _run(conn)
        ag_mx = _ad_group(conn, _campana(conn, "c-mx", "Campana MX"), "ag-mx")
        _obs(
            conn,
            ag_mx,
            "B0MIXTO001",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=200,
            clics=2,
            pedidos=0,
            asin=True,
            run=run,
        )
        ag_us = _ad_group(
            conn,
            _campana(conn, "c-us", "Campana US", platform="amazon_us"),
            "ag-us",
            platform="amazon_us",
        )
        _obs(
            conn,
            ag_us,
            "B0MIXTO001",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=900,
            clics=9,
            pedidos=0,
            asin=True,
            run=run,
            platform="amazon_us",
            moneda="USD",
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert pantalla.asins == ()
        assert pantalla.moneda == "MXN"


@_skip_db
def test_lee_un_dia_con_costo_nulo_anula_el_gasto():
    """P.6: una fecha con cost NULL deja el gasto en None y la busqueda
    no suma a ninguna lista."""
    with _db_p6() as conn:
        run = _run(conn)
        ag = _ad_group(conn, _campana(conn, "c-1", "Campana Uno"), "ag-1")
        _obs(
            conn,
            ag,
            "B0NULODIA1",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=900,
            clics=9,
            pedidos=0,
            asin=True,
            run=run,
        )
        _obs(
            conn,
            ag,
            "B0NULODIA1",
            dt.date(2026, 9, 2),
            visto=dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
            cost=None,
            clics=9,
            pedidos=0,
            asin=True,
            run=run,
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert pantalla.asins == ()
        assert pantalla.asins_a_medio_camino == 0


@_skip_db
def test_lee_asin_propio_de_listing_en_minusculas_y_de_us():
    """P.6: el ASIN propio sale marcado aunque su listing sea de US y
    la busqueda llegue en minusculas."""
    with _db_p6() as conn:
        run = _run(conn)
        pid = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('SKU-P6', 'Producto P6') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'B0PROPIO01', 'SKU-P6')",
            (pid,),
        )
        ag = _ad_group(conn, _campana(conn, "c-1", "Campana Uno"), "ag-1")
        _obs(
            conn,
            ag,
            "b0propio01",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=400,
            clics=4,
            pedidos=0,
            asin=True,
            run=run,
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert pantalla.asins == (
            pb.FilaAsinAjeno("B0PROPIO01", 4, Decimal("400"), ("Campana Uno",), True),
        )


@_skip_db
def test_lee_campana_sin_nombre_no_revienta():
    """P.6 (revision del PR 435): una campana con nombre NULL
    (ad_entity.name nullable) no revienta al ordenar los nombres del
    ASIN; se lista por su external_id y su gasto si suma."""
    with _db_p6() as conn:
        run = _run(conn)
        sin_nombre = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id)"
            " VALUES ('amazon_mx', 'campaign', 'c-sinnombre') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (sin_nombre,),
        )
        ag1 = _ad_group(conn, sin_nombre, "ag-sn")
        ag2 = _ad_group(conn, _campana(conn, "c-nombre", "Campana Uno"), "ag-nombre")
        visto = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
        _obs(
            conn,
            ag1,
            "B0SINNOMBR1",
            dt.date(2026, 9, 1),
            visto=visto,
            cost=200,
            clics=2,
            pedidos=0,
            asin=True,
            run=run,
        )
        _obs(
            conn,
            ag2,
            "B0SINNOMBR1",
            dt.date(2026, 9, 1),
            visto=visto,
            cost=150,
            clics=2,
            pedidos=0,
            asin=True,
            run=run,
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert pantalla.asins == (
            pb.FilaAsinAjeno(
                "B0SINNOMBR1", 4, Decimal("350"), ("Campana Uno", "c-sinnombre"), False
            ),
        )


@_skip_db
def test_lee_minimo_de_settings_y_default_350():
    """P.6: sin config_version manda el default de 350; con la clave de
    la plataforma manda ella."""
    with _db_p6() as conn:
        run = _run(conn)
        ag = _ad_group(conn, _campana(conn, "c-1", "Campana Uno"), "ag-1")
        _obs(
            conn,
            ag,
            "B0MINIMO01",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=349,
            clics=3,
            pedidos=0,
            asin=True,
            run=run,
        )
        _obs(
            conn,
            ag,
            "B0MINIMO02",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=350,
            clics=3,
            pedidos=0,
            asin=True,
            run=run,
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert [a.asin for a in pantalla.asins] == ["B0MINIMO02"]
        conn.execute(
            "INSERT INTO config_version (label, settings) VALUES ('p6', %s)",
            (Json({"ads_gasto_para_concluir_amazon_mx": "100"}),),
        )
        _obs(
            conn,
            ag,
            "B0MINIMO03",
            dt.date(2026, 9, 1),
            visto=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            cost=100,
            clics=1,
            pedidos=0,
            asin=True,
            run=run,
        )
        pantalla = pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
        assert {a.asin for a in pantalla.asins} == {"B0MINIMO01", "B0MINIMO02", "B0MINIMO03"}


class _CursorFalso:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _ConnFalsa:
    """Conexion de lectura falsa: respuestas en orden de llamada."""

    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.consultas = []

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        return _CursorFalso(self._respuestas.pop(0))


def test_lee_solo_tres_consultas_select():
    """P.6: tres consultas y todas de solo lectura."""
    conn = _ConnFalsa([[({"ads_gasto_para_concluir_amazon_mx": "350"},)], [], []])
    pb.lee_busquedas(conn, plataforma="amazon_mx", hoy=HOY)
    assert len(conn.consultas) == 3
    for sql, _params in conn.consultas:
        assert sql.lstrip().upper().startswith(("SELECT", "WITH"))


def test_como_dict_gastos_texto_y_none_none():
    """P.6: como_dict da cada gasto como texto y cada None como None."""
    pantalla = pb.PantallaBusquedas(
        plataforma="amazon_mx",
        moneda="MXN",
        desde=HOY - dt.timedelta(days=93),
        hasta=HOY - dt.timedelta(days=3),
        referencia=pb.ReferenciaAzar(None, None),
        asins=(pb.FilaAsinAjeno("B0DICT0001", 4, Decimal("400"), ("C1",), False),),
        palabras=(pb.FilaPalabra("gama", 2, 20, Decimal("400"), ("gama uno",)),),
        asins_a_medio_camino=1,
        palabras_a_medio_camino=2,
    )
    datos = pantalla.como_dict()
    assert datos["asins"][0]["gasto"] == "400"
    assert datos["palabras"][0]["gasto"] == "400"
    assert datos["referencia"] == {"visto_pct": None, "azar_pct": None}
    assert datos["asins_a_medio_camino"] == 1
    assert datos["palabras_a_medio_camino"] == 2
