"""Contrato de donde-poner-el-dinero (BIDS 02, P.1).

`FilaTipo`/`PantallaDinero`/`como_dict` son puros; `lee_dinero` es SOLO
SELECT (fakes aqui; Postgres real al final, como
`tests/test_pantalla_danadas.py`). Suma HOJAS activas por tipo de campana:
jamas filas de campana (no doble conteo) y jamas MX con US.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import get_args

import psycopg
import pytest
from psycopg import sql as pgsql
from test_schema import _postgres_obligatorio_ausente, _test_dsn

from app.pantalla_dinero import TipoCampana, lee_campanas, lee_dinero, lee_ubicaciones

RAIZ = Path(__file__).resolve().parents[1]
SQL1 = (RAIZ / "migrations" / "0001_initial.sql").read_text(encoding="utf-8")
SQL2 = (RAIZ / "migrations" / "0002_apply.sql").read_text(encoding="utf-8")
SQL60 = (RAIZ / "migrations" / "0060_bids02_base_lectura.sql").read_text(encoding="utf-8")
SQL61 = (RAIZ / "migrations" / "0061_bids02_campana_config.sql").read_text(encoding="utf-8")
SQL62 = (RAIZ / "migrations" / "0062_bids02_placement.sql").read_text(encoding="utf-8")
SQL67 = (RAIZ / "migrations" / "0067_bids02_campana_ajuste.sql").read_text(encoding="utf-8")

TIPOS = get_args(TipoCampana)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)


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


def _grupo(tipo, gasto, pedidos, venta, hojas=1):
    """Fila del SELECT: (tipo_campana, gasto, pedidos, venta, hojas)."""
    return (tipo, gasto, pedidos, venta, hojas)


def _lee(
    grupos,
    *,
    plataforma="amazon_mx",
    dias=90,
    hasta=None,
    target=None,
    ubicaciones=(),
    settings_fila=(),
    campanas=(),
    gasto_diario=(),
    fuera=(),
    configs_hist=(),
    regresables=(),
    ultimo_precio=(),
):
    notas = (
        [(json.dumps({"target": {"procedencia": "margen_plataforma", "target_aplicado": target}}),)]
        if target is not None
        else []
    )
    conn = _ConnFalsa(
        [
            list(grupos),
            list(ubicaciones),
            list(settings_fila),
            list(campanas),
            list(gasto_diario),
            list(fuera),
            list(configs_hist),
            list(regresables),
            list(ultimo_precio),
            list(settings_fila),
            notas,
        ]
    )
    pantalla = lee_dinero(conn, plataforma=plataforma, dias=dias, hasta=hasta)
    return pantalla, conn


def _cinco_grupos(**cambios):
    base = {
        "exact": (Decimal("100"), 10, Decimal("1000"), 2),
        "phrase": (Decimal("100"), 10, Decimal("1000"), 2),
        "broad": (Decimal("100"), 10, Decimal("1000"), 2),
        "automatica": (Decimal("100"), 10, Decimal("1000"), 2),
        "product_targeting": (Decimal("100"), 10, Decimal("1000"), 2),
    }
    base.update(cambios)
    return [_grupo(tipo, *vals) for tipo, vals in base.items()]


def test_cinco_tipos_dan_cinco_renglones_y_total_parte_100():
    pantalla, _ = _lee(_cinco_grupos(), hasta=dt.date(2026, 10, 4))
    assert [f.tipo for f in pantalla.filas] == list(TIPOS)
    assert pantalla.total.gasto == Decimal("500")
    assert pantalla.total.pedidos == 50
    assert pantalla.total.venta == Decimal("5000")
    assert pantalla.total.acos_pct == Decimal("10.0")
    assert sum(f.parte_del_gasto_pct for f in pantalla.filas) == Decimal("100.0")
    assert pantalla.total.parte_del_gasto_pct == Decimal("100.0")
    assert pantalla.hojas_sin_clasificar == 0
    assert pantalla.moneda == "MXN"
    assert (pantalla.desde, pantalla.hasta) == (dt.date(2026, 7, 6), dt.date(2026, 10, 4))


def test_pasa_plataforma_y_ventana_al_select():
    _, conn = _lee([], plataforma="amazon_us", dias=30, hasta=dt.date(2026, 10, 4))
    sql, params = conn.consultas[0]
    assert "v_hoja_activa" in sql and "ads_metric_observation" in sql
    assert params == ("amazon_us", dt.date(2026, 9, 4), dt.date(2026, 10, 4))


def test_venta_cero_da_acos_none_y_metrica_null_da_suma_none():
    grupos = _cinco_grupos(exact=(Decimal("100"), 10, Decimal("0"), 2))
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    exact = pantalla.filas[0]
    assert exact.acos_pct is None
    assert exact.sin_ventas is True
    assert pantalla.filas[1].sin_ventas is False

    grupos = _cinco_grupos(phrase=(None, 10, Decimal("1000"), 2))
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    assert pantalla.filas[1].gasto is None
    assert pantalla.filas[1].parte_del_gasto_pct is None
    assert pantalla.total.gasto is None

    grupos = _cinco_grupos(phrase=(Decimal("100"), None, Decimal("1000"), 2))
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    assert pantalla.total.pedidos is None
    grupos = _cinco_grupos(phrase=(Decimal("100"), 10, None, 2))
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    assert pantalla.total.venta is None


def test_tipo_null_va_al_total_y_se_cuenta_aparte():
    grupos = _cinco_grupos() + [_grupo(None, Decimal("50"), 5, Decimal("500"), 3)]
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    assert [f.tipo for f in pantalla.filas] == list(TIPOS)
    assert pantalla.total.gasto == Decimal("550")
    assert pantalla.total.pedidos == 55
    assert pantalla.hojas_sin_clasificar == 3


def test_tipo_fuera_de_vocabulario_es_sin_clasificar():
    grupos = _cinco_grupos() + [_grupo("raro", Decimal("50"), 5, Decimal("500"), 1)]
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    assert [f.tipo for f in pantalla.filas] == list(TIPOS)
    assert pantalla.total.gasto == Decimal("550")
    assert pantalla.hojas_sin_clasificar == 1


def test_gasto_cero_da_parte_none_en_filas_y_total():
    grupos = _cinco_grupos(exact=(Decimal("0"), 0, Decimal("0"), 1))
    pantalla, _ = _lee(grupos, hasta=dt.date(2026, 10, 4))
    assert pantalla.total.parte_del_gasto_pct == Decimal("100.0")
    ceros = [_grupo(t, Decimal("0"), 0, Decimal("0"), 1) for t in TIPOS]
    pantalla, _ = _lee(ceros, hasta=dt.date(2026, 10, 4))
    assert pantalla.total.gasto == Decimal("0")
    assert all(f.parte_del_gasto_pct is None for f in pantalla.filas)
    assert pantalla.total.parte_del_gasto_pct is None


def test_sin_hojas_salen_renglones_vacios_y_total_none():
    pantalla, _ = _lee([], hasta=dt.date(2026, 10, 4))
    assert [f.tipo for f in pantalla.filas] == list(TIPOS)
    assert all(f.gasto is None and f.hojas == 0 for f in pantalla.filas)
    assert all(f.pedidos is None and f.venta is None for f in pantalla.filas)
    assert pantalla.total.gasto is None
    assert pantalla.total.acos_pct is None
    assert pantalla.hojas_sin_clasificar == 0


def test_target_del_ciclo_y_none_sin_ciclo():
    pantalla, _ = _lee(_cinco_grupos(), hasta=dt.date(2026, 10, 4), target="13.3")
    assert pantalla.target_acos_pct == Decimal("13.3")
    pantalla, conn = _lee(_cinco_grupos(), hasta=dt.date(2026, 10, 4))
    assert pantalla.target_acos_pct is None
    params_target = next(p for s, p in conn.consultas if "optimizer_cycle" in s)
    assert params_target == ("amazon_mx",)


def test_hasta_omiso_es_ayer_utc():
    pantalla, _ = _lee(_cinco_grupos())
    ayer = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    assert pantalla.hasta == ayer
    assert pantalla.desde == ayer - dt.timedelta(days=90)


def test_como_dict_serializa_decimales_y_fechas():
    pantalla, _ = _lee(_cinco_grupos(), hasta=dt.date(2026, 10, 4), target="13.3")
    dato = pantalla.como_dict()
    assert dato["plataforma"] == "amazon_mx"
    assert dato["moneda"] == "MXN"
    assert (dato["desde"], dato["hasta"]) == ("2026-07-06", "2026-10-04")
    assert dato["hojas_sin_clasificar"] == 0
    assert dato["target_acos_pct"] == "13.3"
    assert dato["filas"][0] == {
        "tipo": "exact",
        "gasto": "100",
        "pedidos": 10,
        "venta": "1000",
        "acos_pct": "10.0",
        "parte_del_gasto_pct": "20.0",
        "hojas": 2,
        "sin_ventas": False,
    }
    assert dato["total"]["tipo"] == ""
    assert dato["total"]["gasto"] == "500"


@contextmanager
def _db_dinero(prefijo: str):
    dsn = _test_dsn()
    db = f"{prefijo}_{socket.gethostname().lower()}_{os.getpid()}"
    admin = psycopg.connect(dsn, autocommit=True)
    conn = None
    try:
        admin.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(db)))
        conn = psycopg.connect(dsn, dbname=db, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute(SQL1)
        conn.execute(SQL2)
        conn.execute(SQL60)
        conn.execute(SQL61)
        conn.execute(SQL62)
        conn.execute(SQL67)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(
            pgsql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(pgsql.Identifier(db))
        )
        admin.close()


def _siembra_entidad(conn, platform, kind, external, parent=None, match=None, texto=None):
    return conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type, keyword_text)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (platform, kind, external, parent, match, texto),
    ).fetchone()[0]


def _siembra_estado(conn, entidad, status, targeting=None):
    conn.execute(
        "INSERT INTO ad_entity_state (ad_entity_id, status, targeting_type, synced_at)"
        " VALUES (%s, %s, %s, now())",
        (entidad, status, targeting),
    )


def _siembra_metrica(
    conn, entidad, fecha, costo, pedidos, venta, observado, run, moneda="MXN", reporte="R1"
):
    conn.execute(
        "INSERT INTO ads_metric_observation"
        " (ad_entity_id, metric_date, observed_at, metric_currency, cost, orders, ad_revenue,"
        "  source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (entidad, fecha, observado, moneda, costo, pedidos, venta, reporte, run),
    )


@_skip_db
def test_pg_solo_mx_encendido():
    with _db_dinero("orbit_p1_pg") as conn:
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        camp = _siembra_entidad(conn, "amazon_mx", "campaign", "c1")
        ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a1", parent=camp)
        kws = [
            _siembra_entidad(
                conn, "amazon_mx", "keyword", f"k{i}", parent=ag, match=m, texto=f"t{i}"
            )
            for i, m in enumerate(["EXACT", "PHRASE", "BROAD"])
        ]
        pt = _siembra_entidad(conn, "amazon_mx", "product_target", "p1", parent=ag)
        auto_c = _siembra_entidad(conn, "amazon_mx", "campaign", "c2")
        auto_ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a2", parent=auto_c)
        auto_k = _siembra_entidad(
            conn, "amazon_mx", "keyword", "k9", parent=auto_ag, match="EXACT", texto="t9"
        )
        for e in [camp, ag, *kws, pt]:
            _siembra_estado(conn, e, "ENABLED")
        for e in [auto_c, auto_ag, auto_k]:
            _siembra_estado(conn, e, "ENABLED", targeting="AUTO" if e == auto_c else None)
        # Campana apagada: su hoja no cuenta aunque tenga metricas.
        off_c = _siembra_entidad(conn, "amazon_mx", "campaign", "c3")
        off_ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a3", parent=off_c)
        off_k = _siembra_entidad(
            conn, "amazon_mx", "keyword", "k8", parent=off_ag, match="EXACT", texto="t8"
        )
        _siembra_estado(conn, off_c, "PAUSED")
        _siembra_estado(conn, off_ag, "ENABLED")
        _siembra_estado(conn, off_k, "ENABLED")
        # Hoja de US: no entra a la tabla de MX.
        us_c = _siembra_entidad(conn, "amazon_us", "campaign", "c4")
        us_ag = _siembra_entidad(conn, "amazon_us", "ad_group", "a4", parent=us_c)
        us_k = _siembra_entidad(
            conn, "amazon_us", "keyword", "k7", parent=us_ag, match="EXACT", texto="t7"
        )
        for e in [us_c, us_ag, us_k]:
            _siembra_estado(conn, e, "ENABLED")
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        for e in [*kws, pt, auto_k, off_k]:
            _siembra_metrica(
                conn, e, dt.date(2026, 10, 4), Decimal("10"), 1, Decimal("100"), obs, run
            )
        _siembra_metrica(
            conn, us_k, dt.date(2026, 10, 4), Decimal("10"), 1, Decimal("100"), obs, run, "USD"
        )

        pantalla = lee_dinero(conn, plataforma="amazon_mx", dias=90, hasta=dt.date(2026, 10, 4))
        assert [f.tipo for f in pantalla.filas] == list(TIPOS)
        assert pantalla.total.gasto == Decimal("50")
        assert pantalla.total.pedidos == 5
        assert pantalla.filas[0].gasto == Decimal("10")


@_skip_db
def test_pg_null_envenena_grupo_ubicacion_y_campana():
    with _db_dinero("orbit_p1_null") as conn:
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        fecha = dt.date(2026, 10, 4)
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        camp = _siembra_entidad(conn, "amazon_mx", "campaign", "c1")
        ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a1", parent=camp)

        def _kw(ext, match, padre=ag, texto="t"):
            return _siembra_entidad(
                conn, "amazon_mx", "keyword", ext, parent=padre, match=match, texto=texto
            )

        ex1 = _kw("k1", "EXACT")
        ex2 = _kw("k2", "EXACT")
        ph = _kw("k3", "PHRASE")
        ph2 = _kw("k5", "PHRASE")
        br = _kw("k4", "BROAD")
        br2 = _kw("k6", "BROAD")
        auto_c = _siembra_entidad(conn, "amazon_mx", "campaign", "c2")
        auto_ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a2", parent=auto_c)
        auto_k = _kw("k9", "EXACT", padre=auto_ag)
        for e in [camp, ag, ex1, ex2, ph, ph2, br, br2, auto_ag, auto_k]:
            _siembra_estado(conn, e, "ENABLED")
        _siembra_estado(conn, auto_c, "ENABLED", targeting="AUTO")
        _siembra_metrica(conn, ex1, fecha, None, 1, Decimal("100"), obs, run)
        _siembra_metrica(conn, ex2, fecha, Decimal("10"), 1, Decimal("100"), obs, run)
        _siembra_metrica(conn, ph, fecha, Decimal("10"), None, Decimal("100"), obs, run)
        _siembra_metrica(conn, ph2, fecha, Decimal("10"), 3, Decimal("100"), obs, run)
        _siembra_metrica(conn, br, fecha, Decimal("10"), 1, None, obs, run)
        _siembra_metrica(conn, br2, fecha, Decimal("10"), 1, Decimal("50"), obs, run)
        _siembra_metrica(conn, auto_k, fecha, None, 1, Decimal("100"), obs, run)
        pantalla = lee_dinero(conn, plataforma="amazon_mx", dias=90, hasta=fecha)
        por_tipo = {f.tipo: f for f in pantalla.filas}
        assert por_tipo["exact"].gasto is None
        assert por_tipo["exact"].acos_pct is None
        assert por_tipo["exact"].pedidos == 2
        assert por_tipo["phrase"].pedidos is None
        assert por_tipo["broad"].venta is None
        assert por_tipo["automatica"].gasto is None

        c_ubi = _siembra_campana(conn, "amazon_mx", "cu", "Ubi")
        _siembra_placement(
            conn,
            "amazon_mx",
            c_ubi,
            "fuera_de_amazon",
            fecha,
            obs,
            None,
            5,
            0,
            None,
        )
        ubis = {
            u.ubicacion: u
            for u in lee_ubicaciones(
                conn, plataforma="amazon_mx", desde=fecha - dt.timedelta(days=29), hasta=fecha
            )
        }
        assert ubis["fuera_de_amazon"].gasto is None

        c_vacia = _siembra_campana(conn, "amazon_mx", "cv", "Vacia")
        c_sin_place = _siembra_campana(conn, "amazon_mx", "cs", "SinPlace")
        _siembra_gasto_campana(conn, c_sin_place, fecha, Decimal("10"), obs, run)
        campanas = {
            c.campana_id: c
            for c in lee_campanas(
                conn, plataforma="amazon_mx", desde=fecha - dt.timedelta(days=29), hasta=fecha
            )
        }
        assert campanas[c_vacia].gasto_medio_diario is None
        assert campanas[c_sin_place].gasto_fuera_de_amazon is None
        assert campanas[c_ubi].gasto_fuera_de_amazon is None


def _fila_ui(**cambios):
    fila = {
        "tipo": "exact",
        "gasto": "100",
        "pedidos": 10,
        "venta": "1000",
        "acos_pct": "10.0",
        "parte_del_gasto_pct": "20.0",
        "hojas": 2,
        "sin_ventas": False,
    }
    fila.update(cambios)
    return fila


def _html_dinero(filas, total=None, **cambios):
    from app import ui

    datos = {
        "pantalla": "donde-poner-el-dinero",
        "plataforma": "amazon_mx",
        "moneda": "MXN",
        "desde": "2026-07-06",
        "hasta": "2026-10-04",
        "filas": filas,
        "total": total or _fila_ui(tipo="", gasto="500", pedidos=50, venta="5000", acos_pct="10.0"),
        "hojas_sin_clasificar": 0,
        "target_acos_pct": "13.3",
        "clases_ajuste": _clases_ui(),
    }
    datos.update(cambios)
    return ui.templates.env.get_template("donde_poner_el_dinero.html").render(**datos)


def _plano(html):
    return " ".join(html.split())


def test_pantalla_dinero_pinta_aviso_renglones_y_target():
    html = _plano(_html_dinero([_fila_ui()]))
    assert (
        "Solo cuenta lo que hoy está encendido. Lo que apagaste no aparece, "
        "así que el total no coincide con el Resumen." in html
    )
    assert "exact" in html and "target 13.3 %" in html


def test_pantalla_dinero_venta_cero_dice_sin_ventas_y_null_pinta_guion():
    html = _plano(
        _html_dinero(
            [
                _fila_ui(
                    tipo="broad",
                    gasto="50",
                    pedidos=3,
                    venta="0",
                    acos_pct=None,
                    sin_ventas=True,
                ),
                _fila_ui(tipo="phrase", gasto=None, pedidos=None, venta=None, acos_pct=None),
            ],
            hojas_sin_clasificar=2,
        )
    )
    assert "sin ventas" in html
    assert "—" in html
    assert "2 hojas sin clasificar entran al total." in html


# ---------------------------------------------------------------------------
# P.2a: por ubicacion y por campana (contrato)
# ---------------------------------------------------------------------------


CONTROL_UBI = """
WITH ultima AS (
    SELECT DISTINCT ON (o.ad_entity_id, o.placement, o.metric_date)
           o.placement, o.cost, o.clicks, o.orders
      FROM ads_placement_observation o
      JOIN ad_entity_state s ON s.ad_entity_id = o.ad_entity_id AND s.status = 'ENABLED'
     WHERE o.platform = %s AND o.metric_date BETWEEN %s AND %s
     ORDER BY o.ad_entity_id, o.placement, o.metric_date, o.observed_at DESC)
SELECT placement, sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos
  FROM ultima GROUP BY 1
"""

CONTROL_CAMP = """
SELECT c.id, v.presupuesto_diario, v.presupuesto_moneda, v.estrategia_puja,
       v.ajuste_top_pct, v.ajuste_resto_pct, v.ajuste_producto_pct
  FROM ad_entity c JOIN ad_entity_state s ON s.ad_entity_id = c.id AND s.status = 'ENABLED'
  LEFT JOIN LATERAL (SELECT * FROM ads_campana_config_observation o WHERE o.ad_entity_id = c.id
                      ORDER BY o.observed_at DESC LIMIT 1) v ON true
 WHERE c.platform = %s AND c.kind = 'campaign' ORDER BY 1
"""

HASTA_2A = dt.date(2026, 10, 4)
DESDE_30 = dt.date(2026, 9, 5)


def _siembra_campana(conn, platform, external, nombre, status="ENABLED"):
    cid = _siembra_entidad(conn, platform, "campaign", external)
    conn.execute("UPDATE ad_entity SET name = %s WHERE id = %s", (nombre, cid))
    _siembra_estado(conn, cid, status)
    return cid


def _siembra_placement(
    conn, platform, campana, placement, fecha, observed, costo, clics, pedidos, venta, reporte="R1"
):
    conn.execute(
        "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement, metric_date,"
        " observed_at, metric_currency, impressions, clicks, cost, orders, ad_revenue,"
        " source_report_id) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)",
        (
            platform,
            campana,
            placement,
            fecha,
            observed,
            "MXN" if platform == "amazon_mx" else "USD",
            clics,
            costo,
            pedidos,
            venta,
            reporte,
        ),
    )


def _siembra_config(
    conn,
    campana,
    observed,
    presupuesto,
    estrategia,
    top=None,
    resto=None,
    prod=None,
    moneda="MXN",
):
    conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at, presupuesto_diario,"
        " presupuesto_moneda, estrategia_puja, ajuste_top_pct, ajuste_resto_pct,"
        " ajuste_producto_pct) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (campana, observed, presupuesto, moneda, estrategia, top, resto, prod),
    )


def _siembra_gasto_campana(conn, campana, fecha, gasto, observed, run, moneda="MXN"):
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, source_report_id, ingest_run_id)"
        " VALUES (%s, %s, %s, %s, %s, 'R1', %s)",
        (campana, fecha, observed, moneda, gasto, run),
    )


@_skip_db
def test_pg_ubicaciones_igual_que_control_y_marca_fuera():
    with _db_dinero("orbit_p2a_ubi") as conn:
        c1 = _siembra_campana(conn, "amazon_mx", "c1", "Campana 1")
        c2 = _siembra_campana(conn, "amazon_mx", "c2", "Apagada", status="PAUSED")
        c3 = _siembra_campana(conn, "amazon_us", "c3", "US 1")
        obs_vieja = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        obs_nueva = dt.datetime(2026, 10, 6, tzinfo=dt.UTC)
        _siembra_placement(
            conn,
            "amazon_mx",
            c1,
            "fuera_de_amazon",
            HASTA_2A,
            obs_vieja,
            Decimal("999"),
            5,
            5,
            Decimal("10"),
            reporte="R-viejo",
        )
        _siembra_placement(
            conn,
            "amazon_mx",
            c1,
            "fuera_de_amazon",
            HASTA_2A,
            obs_nueva,
            Decimal("350"),
            1127,
            0,
            Decimal("0"),
            reporte="R-nuevo",
        )
        _siembra_placement(
            conn,
            "amazon_mx",
            c1,
            "arriba_de_busqueda",
            HASTA_2A,
            obs_nueva,
            Decimal("400"),
            100,
            1,
            Decimal("2000"),
        )
        _siembra_placement(
            conn,
            "amazon_mx",
            c2,
            "fuera_de_amazon",
            HASTA_2A,
            obs_nueva,
            Decimal("50"),
            5,
            0,
            Decimal("0"),
        )
        _siembra_placement(
            conn,
            "amazon_us",
            c3,
            "fuera_de_amazon",
            HASTA_2A,
            obs_nueva,
            Decimal("60"),
            6,
            0,
            Decimal("0"),
        )
        filas = lee_ubicaciones(conn, plataforma="amazon_mx", desde=DESDE_30, hasta=HASTA_2A)
        assert [f.ubicacion for f in filas] == [
            "arriba_de_busqueda",
            "resto_de_busqueda",
            "paginas_de_producto",
            "fuera_de_amazon",
        ]
        control = {
            r[0]: r[1:]
            for r in conn.execute(CONTROL_UBI, ("amazon_mx", DESDE_30, HASTA_2A)).fetchall()
        }
        for fila in filas:
            ctrl = control.get(fila.ubicacion)
            if ctrl is None:
                assert fila.gasto is None and fila.pedidos is None
                assert fila.venta is None and fila.clics is None
            else:
                assert (fila.gasto, fila.clics, fila.pedidos) == ctrl
        fuera = filas[3]
        assert fuera.gasta_sin_vender is True
        assert filas[0].gasta_sin_vender is False


@_skip_db
def test_pg_campanas_igual_que_control_tope_y_estrategias():
    with _db_dinero("orbit_p2a_camp") as conn:
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        obs_cfg = dt.datetime(2026, 9, 27, tzinfo=dt.UTC)
        c1 = _siembra_campana(conn, "amazon_mx", "c1", "Tope 5")
        c2 = _siembra_campana(conn, "amazon_mx", "c2", "Sin config")
        c3 = _siembra_campana(conn, "amazon_mx", "c3", "Legacy")
        c4 = _siembra_campana(conn, "amazon_mx", "c4", "Auto")
        c5 = _siembra_campana(conn, "amazon_mx", "c5", "Rara")
        c8 = _siembra_campana(conn, "amazon_mx", "c8", "Sin estrategia")
        c9 = _siembra_campana(conn, "amazon_mx", "c9", "Doble config")
        _siembra_campana(conn, "amazon_mx", "c6", "Apagada", status="PAUSED")
        _siembra_campana(conn, "amazon_us", "c7", "US 1")
        _siembra_config(conn, c1, obs_cfg, Decimal("100"), "MANUAL", top=40, prod=10)
        _siembra_config(conn, c3, obs_cfg, Decimal("50"), "LEGACY_FOR_SALES")
        _siembra_config(conn, c4, obs_cfg, Decimal("50"), "AUTO_FOR_SALES")
        _siembra_config(conn, c5, obs_cfg, Decimal("50"), "ALGO_NUEVO")
        _siembra_config(conn, c8, obs_cfg, Decimal("50"), None)
        _siembra_config(
            conn, c9, dt.datetime(2026, 9, 27, 20, tzinfo=dt.UTC), Decimal("100"), "MANUAL"
        )
        _siembra_config(
            conn, c9, dt.datetime(2026, 9, 27, 8, tzinfo=dt.UTC), Decimal("50"), "MANUAL"
        )
        for i in range(7):
            dia = HASTA_2A - dt.timedelta(days=6 - i)
            _siembra_gasto_campana(
                conn, c1, dia, Decimal("90") if i < 5 else Decimal("0"), obs, run
            )
        _siembra_gasto_campana(conn, c2, HASTA_2A, Decimal("10"), obs, run)
        _siembra_gasto_campana(conn, c9, HASTA_2A, Decimal("60"), obs, run)
        _siembra_placement(
            conn,
            "amazon_mx",
            c1,
            "fuera_de_amazon",
            HASTA_2A,
            obs,
            Decimal("25"),
            10,
            0,
            Decimal("0"),
        )
        filas = lee_campanas(conn, plataforma="amazon_mx", desde=DESDE_30, hasta=HASTA_2A)
        assert [f.campana_id for f in filas] == [c1, c2, c3, c4, c5, c8, c9]
        control = {r[0]: r[1:] for r in conn.execute(CONTROL_CAMP, ("amazon_mx",)).fetchall()}
        assert set(control) == {c1, c2, c3, c4, c5, c8, c9}
        for fila in filas:
            presup, moneda, _, top, resto, prod = control[fila.campana_id]
            assert fila.presupuesto_diario == presup
            assert (
                fila.ajustes_ubicacion
                == tuple(
                    par
                    for par in (
                        ("arriba_de_busqueda", top),
                        ("resto_de_busqueda", resto),
                        ("paginas_de_producto", prod),
                    )
                    if par[1] is not None
                )
            ) and (moneda == "MXN" or fila.presupuesto_diario is None)
        por_id = {f.campana_id: f for f in filas}
        assert [por_id[c].estrategia for c in (c1, c3, c4, c5)] == [
            "fija",
            "solo_hacia_abajo",
            "arriba_y_abajo",
            "otra",
        ]
        assert por_id[c2].estrategia is None
        assert por_id[c2].presupuesto_diario is None and por_id[c2].uso_presupuesto_pct is None
        assert por_id[c8].estrategia is None and por_id[c8].presupuesto_diario == Decimal("50")
        assert por_id[c9].dias_al_tope_7d == 0
        assert por_id[c1].dias_al_tope_7d == 5
        assert por_id[c1].gasto_medio_diario == Decimal("64.29")
        assert por_id[c1].uso_presupuesto_pct == Decimal("64.3")
        assert por_id[c1].gasto_fuera_de_amazon == Decimal("25")
        assert por_id[c1].avisos == ()
        assert por_id[c2].dias_al_tope_7d is None


@_skip_db
def test_pg_lee_dinero_llena_ubicaciones_y_campanas():
    with _db_dinero("orbit_p2a_todo") as conn:
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        c1 = _siembra_campana(conn, "amazon_mx", "c1", "Campana 1")
        ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a1", parent=c1)
        kw = _siembra_entidad(
            conn, "amazon_mx", "keyword", "k1", parent=ag, match="EXACT", texto="t1"
        )
        for e in [ag, kw]:
            _siembra_estado(conn, e, "ENABLED")
        _siembra_metrica(conn, kw, HASTA_2A, Decimal("10"), 1, Decimal("100"), obs, run)
        _siembra_placement(
            conn,
            "amazon_mx",
            c1,
            "fuera_de_amazon",
            HASTA_2A,
            obs,
            Decimal("350"),
            1127,
            0,
            Decimal("0"),
        )
        _siembra_config(conn, c1, obs, Decimal("100"), "MANUAL")
        _siembra_gasto_campana(conn, c1, HASTA_2A, Decimal("90"), obs, run)
        pantalla = lee_dinero(conn, plataforma="amazon_mx", dias=90, hasta=HASTA_2A)
        assert pantalla.total.gasto == Decimal("10")
        assert len(pantalla.por_ubicacion) == 4
        assert pantalla.por_ubicacion[3].gasta_sin_vender is True
        assert [f.campana_id for f in pantalla.por_campana] == [c1]
        assert pantalla.por_campana[0].estrategia == "fija"
        assert pantalla.por_campana[0].gasto_medio_diario == Decimal("90.00")
        assert pantalla.por_campana[0].uso_presupuesto_pct == Decimal("90.0")
        assert pantalla.por_campana[0].avisos == ()


def test_como_dict_trae_ubicaciones_y_campanas_con_settings():
    pantalla, _ = _lee(
        _cinco_grupos(),
        hasta=HASTA_2A,
        ubicaciones=[("fuera_de_amazon", Decimal("100"), 50, 0, Decimal("0"))],
        settings_fila=[({"ads_gasto_para_concluir_amazon_mx": "100"},)],
        campanas=[(7, "Campana", 99, Decimal("100"), "MANUAL", 40, None, 10)],
        gasto_diario=[(7, HASTA_2A, Decimal("90"))],
        fuera=[(7, Decimal("50"))],
        configs_hist=[(7, HASTA_2A, Decimal("100"))],
    )
    dato = pantalla.como_dict()
    assert dato["por_ubicacion"][3]["gasta_sin_vender"] is True
    assert dato["por_campana"][0]["estrategia"] == "fija"
    assert dato["por_campana"][0]["ajustes_ubicacion"] == [
        ["arriba_de_busqueda", 40],
        ["paginas_de_producto", 10],
    ]
    assert dato["por_campana"][0]["gasto_medio_diario"] == "90.00"
    assert dato["por_campana"][0]["uso_presupuesto_pct"] == "90.0"
    assert dato["por_campana"][0]["avisos"] == []


def _fila_ubi_ui(**cambios):
    fila = {
        "ubicacion": "fuera_de_amazon",
        "gasto": "1735",
        "clics": 1127,
        "pedidos": 0,
        "venta": "0",
        "cpc": "1.54",
        "conversion_pct": "0.0",
        "acos_pct": None,
        "parte_del_gasto_pct": "10.0",
        "gasta_sin_vender": True,
    }
    fila.update(cambios)
    return fila


def _fila_camp_ui(**cambios):
    fila = {
        "campana_id": 7,
        "nombre": "Campana 7",
        "presupuesto_diario": "100",
        "gasto_medio_diario": "43.00",
        "uso_presupuesto_pct": "43.0",
        "estrategia": "solo_hacia_abajo",
        "ajustes_ubicacion": [["paginas_de_producto", 40]],
        "gasto_fuera_de_amazon": "25",
        "dias_al_tope_7d": 5,
        "avisos": ["Este presupuesto no limita"],
        "ajustes_regresables": [],
        "aviso_ajuste": None,
    }
    fila.update(cambios)
    return fila


def _clases_ui():
    from app.campana_ajustes import CLASES_SELLADAS, ORDEN_CLASES

    return [c for c in ORDEN_CLASES if c in CLASES_SELLADAS]


def test_pantalla_dinero_pinta_ubicaciones_con_frase_y_ajuste():
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[
                _fila_ubi_ui(ubicacion="arriba_de_busqueda", gasta_sin_vender=False),
                _fila_ubi_ui(ubicacion="resto_de_busqueda", gasta_sin_vender=False),
                _fila_ubi_ui(ubicacion="paginas_de_producto", gasta_sin_vender=False),
                _fila_ubi_ui(),
                _fila_ubi_ui(ubicacion="resto_de_busqueda", clics=None),
            ],
            por_campana=[_fila_camp_ui()],
        )
    )
    assert "arriba de búsqueda" in html
    assert "resto de búsqueda" in html
    assert "páginas de producto" in html
    assert "Fuera de Amazon: 1,735 MXN en 30 días, 1,127 clics, ningún pedido." in html
    assert "Resto de búsqueda: 1,735 MXN en 30 días, — clics, ningún pedido." in html
    assert "+40 % en páginas de producto" in html
    assert "solo hacia abajo" in html
    ubicacion = html.split("<h3>Por ubicación", 1)[1].split("<h3>Por campaña", 1)[0]
    assert "<button" not in ubicacion and "<form" not in ubicacion


def test_pantalla_dinero_boton_por_clase_sellada_en_cada_campana():
    """P.2b: cada fila de campaña trae un botón por cada clase de
    CLASES_SELLADAS, con su clase en data-ajuste-clase."""
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui(gasta_sin_vender=False)],
            por_campana=[_fila_camp_ui(), _fila_camp_ui(campana_id=8, nombre="Campana 8")],
        )
    )
    campanas = html.split("<h3>Por campaña", 1)[1]
    for clase in ("presupuesto", "ajuste_ubicacion", "fuera_de_amazon"):
        assert campanas.count(f'data-ajuste-clase="{clase}"') == 2
    assert "Cambiar presupuesto" in campanas
    assert "Limitar fuera de Amazon" in campanas


def test_pantalla_dinero_clase_sin_sellar_sin_boton_y_dato_pintado(monkeypatch):
    """P.2b: con una clase fuera de CLASES_SELLADAS no hay botón, pero su
    dato sí se pinta."""
    import app.campana_ajustes as ca

    monkeypatch.setattr(ca, "CLASES_SELLADAS", frozenset({"presupuesto", "ajuste_ubicacion"}))
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui(gasta_sin_vender=False)],
            por_campana=[_fila_camp_ui()],
        )
    )
    campanas = html.split("<h3>Por campaña", 1)[1]
    assert 'data-ajuste-clase="fuera_de_amazon"' not in campanas
    assert campanas.count("data-ajuste-clase=") == 2
    assert "+40 % en páginas de producto" in campanas


def test_pantalla_dinero_regresar_y_avisos_en_fila_campana():
    """P.2b: ajuste confirmado sin regreso trae su botón Regresar; la fila
    pinta cada frase de avisos y el aviso propio del ajuste."""
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui(gasta_sin_vender=False)],
            por_campana=[
                _fila_camp_ui(
                    ajustes_regresables=[
                        {
                            "ajuste_id": 9,
                            "frase": "Regresar el presupuesto del 2026-10-03",
                        }
                    ],
                    aviso_ajuste=(
                        "Orbit movió el precio hace 2 días; espera al día 7 para juzgarlo."
                    ),
                )
            ],
        )
    )
    campanas = html.split("<h3>Por campaña", 1)[1]
    assert 'data-regresar-ajuste="9"' in campanas
    assert ">Regresar<" in campanas
    assert "Regresar el presupuesto del 2026-10-03" in campanas
    assert "Este presupuesto no limita" in campanas
    assert "Orbit movió el precio hace 2 días; espera al día 7 para juzgarlo." in campanas


def test_pantalla_dinero_sin_avisos_no_pinta_frases():
    """P.2b: fila sin avisos ni aviso propio no pinta ninguna frase."""
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui(gasta_sin_vender=False)],
            por_campana=[_fila_camp_ui(avisos=[])],
        )
    )
    campanas = html.split("<h3>Por campaña", 1)[1]
    assert "Este presupuesto no limita" not in campanas
    assert "Orbit movió el precio" not in campanas


def test_pantalla_dinero_estrategias_none_y_sin_frase():
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui(gasta_sin_vender=False)],
            por_campana=[
                _fila_camp_ui(estrategia="arriba_y_abajo"),
                _fila_camp_ui(estrategia="fija"),
                _fila_camp_ui(
                    estrategia=None,
                    presupuesto_diario=None,
                    gasto_medio_diario=None,
                    uso_presupuesto_pct=None,
                    ajustes_ubicacion=[],
                    gasto_fuera_de_amazon=None,
                ),
            ],
        )
    )
    assert "hacia arriba y hacia abajo" in html
    assert "fija" in html
    assert "—" in html
    assert "ningún pedido" not in html


def _fila_html(html, marcador):
    import re

    for m in re.finditer(r"<tr>(.*?)</tr>", html, re.S):
        if marcador in m.group(1):
            return m.group(1)
    raise AssertionError(f"sin fila con {marcador!r}")


def test_guion_en_cada_celda_tipo_y_total():
    nulos = dict(gasto=None, pedidos=None, venta=None, acos_pct=None, parte_del_gasto_pct=None)
    html = _html_dinero(
        [_fila_ui(**nulos)],
        total=_fila_ui(tipo="", **nulos),
    )
    assert _fila_html(html, ">exact<").count("—") == 5
    assert _fila_html(html, ">Total<").count("—") == 5
    sin_ventas = _html_dinero(
        [_fila_ui()],
        total=_fila_ui(tipo="", venta="0", sin_ventas=True),
    )
    assert "sin ventas" in _fila_html(sin_ventas, ">Total<")


def test_guion_en_cada_celda_ubicacion():
    html = _html_dinero(
        [_fila_ui()],
        por_ubicacion=[
            _fila_ubi_ui(
                gasto=None,
                pedidos=None,
                venta=None,
                cpc=None,
                conversion_pct=None,
                acos_pct=None,
                parte_del_gasto_pct=None,
                gasta_sin_vender=False,
            )
        ],
    )
    assert _fila_html(html, ">fuera de Amazon<").count("—") == 6


def test_guion_en_frase_ubicacion_con_nones():
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui(gasto=None, clics=None, gasta_sin_vender=True)],
        )
    )
    assert "Fuera de Amazon: — MXN en 30 días, — clics, ningún pedido." in html


def test_guion_en_cada_celda_campana():
    html = _html_dinero(
        [_fila_ui()],
        por_campana=[
            _fila_camp_ui(
                nombre=None,
                presupuesto_diario=None,
                gasto_medio_diario=None,
                uso_presupuesto_pct=None,
                estrategia=None,
                ajustes_ubicacion=[],
                gasto_fuera_de_amazon=None,
            )
        ],
    )
    fila = _fila_html(html, "<td>—</td>")
    assert fila.count("—") == 7


def test_como_dict_none_viaja_none_en_todos_los_campos():
    from app.pantalla_dinero import (
        FilaCampana,
        FilaTipo,
        FilaUbicacion,
        PantallaDinero,
    )

    tipo = FilaTipo(
        tipo="exact",
        gasto=None,
        pedidos=None,
        venta=None,
        acos_pct=None,
        parte_del_gasto_pct=None,
        hojas=0,
    )
    assert tipo.como_dict() == {
        "tipo": "exact",
        "gasto": None,
        "pedidos": None,
        "venta": None,
        "acos_pct": None,
        "parte_del_gasto_pct": None,
        "hojas": 0,
        "sin_ventas": False,
    }
    ubi = FilaUbicacion(
        ubicacion="fuera_de_amazon",
        gasto=None,
        clics=None,
        pedidos=None,
        venta=None,
        cpc=None,
        conversion_pct=None,
        acos_pct=None,
        parte_del_gasto_pct=None,
        gasta_sin_vender=False,
    )
    dato_ubi = ubi.como_dict()
    for campo in (
        "gasto",
        "clics",
        "pedidos",
        "venta",
        "cpc",
        "conversion_pct",
        "acos_pct",
        "parte_del_gasto_pct",
    ):
        assert dato_ubi[campo] is None
    camp = FilaCampana(
        campana_id=7,
        nombre=None,
        presupuesto_diario=None,
        gasto_medio_diario=None,
        uso_presupuesto_pct=None,
        estrategia=None,
        ajustes_ubicacion=(),
        gasto_fuera_de_amazon=None,
        dias_al_tope_7d=None,
    )
    dato_camp = camp.como_dict()
    for campo in (
        "nombre",
        "presupuesto_diario",
        "gasto_medio_diario",
        "uso_presupuesto_pct",
        "estrategia",
        "gasto_fuera_de_amazon",
        "dias_al_tope_7d",
    ):
        assert dato_camp[campo] is None
    pantalla = PantallaDinero(
        plataforma="amazon_mx",
        moneda="MXN",
        desde=DESDE_30,
        hasta=HASTA_2A,
        filas=(),
        total=tipo,
        hojas_sin_clasificar=0,
        target_acos_pct=None,
    )
    assert pantalla.como_dict()["target_acos_pct"] is None


# B8 (R05 r3): la pantalla de US habla USD y concluye con 36.
# ---------------------------------------------------------------------------


def _notas_target(valor):
    return json.dumps({"target": {"procedencia": "margen_plataforma", "target_aplicado": valor}})


@_skip_db
def test_pg_us_moneda_y_target_del_ciclo_us():
    """U7/U9: `lee_dinero` US rotula USD y muestra el target del ciclo US."""
    with _db_dinero("orbit_b8_us") as conn:
        ahora = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        for plataforma, valor in (("amazon_mx", 13.3), ("amazon_us", 25.0)):
            conn.execute(
                "INSERT INTO optimizer_cycle (motor, mode, platform, status,"
                " finished_at, decisions_count, notes)"
                " VALUES ('ads_optimizer', 'shadow', %s::platform, 'done', %s, 0, %s)",
                (plataforma, ahora, _notas_target(valor)),
            )
        pantalla = lee_dinero(conn, plataforma="amazon_us", dias=90, hasta=dt.date(2026, 10, 4))
        assert pantalla.moneda == "USD"
        assert pantalla.target_acos_pct == Decimal("25.0")


@_skip_db
def test_pg_us_ubicacion_concluye_con_36():
    """U8: gasto 100 USD sin pedidos marca gasta-sin-vender en US."""
    with _db_dinero("orbit_b8_u8") as conn:
        fecha = dt.date(2026, 10, 4)
        obs = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
        camp = _siembra_campana(conn, "amazon_us", "cu", "US 1")
        _siembra_placement(
            conn,
            "amazon_us",
            camp,
            "fuera_de_amazon",
            fecha,
            obs,
            Decimal("100"),
            5,
            0,
            None,
        )
        ubis = {
            u.ubicacion: u
            for u in lee_ubicaciones(
                conn, plataforma="amazon_us", desde=fecha - dt.timedelta(days=29), hasta=fecha
            )
        }
        assert ubis["fuera_de_amazon"].gasta_sin_vender is True


def test_con_avisos_us_pide_el_umbral_de_us(monkeypatch):
    """U10: los avisos de la pantalla US se piden con 36, no 350."""
    from app import avisos_campana
    from app.pantalla_dinero import FilaCampana, _con_avisos

    fila = FilaCampana(
        campana_id=1,
        nombre="C",
        presupuesto_diario=None,
        gasto_medio_diario=None,
        uso_presupuesto_pct=None,
        estrategia=None,
        ajustes_ubicacion=(),
        gasto_fuera_de_amazon=None,
        dias_al_tope_7d=None,
    )
    pedidas = {}
    real = avisos_campana.avisos_del_dia

    def _espia(*args, **kwargs):
        pedidas.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr("app.avisos_campana.avisos_del_dia", _espia)
    _con_avisos(_ConnFalsa([[]]), "amazon_us", (), (fila,))
    assert pedidas["gasto_para_concluir"] == Decimal("36")


def test_con_avisos_us_frase_en_usd():
    """U11: el aviso de presupuesto expuesto de US se redacta en USD."""
    from app.pantalla_dinero import FilaCampana, _con_avisos

    fila = FilaCampana(
        campana_id=1,
        nombre="C",
        presupuesto_diario=Decimal("100"),
        gasto_medio_diario=Decimal("5"),
        uso_presupuesto_pct=None,
        estrategia=None,
        ajustes_ubicacion=(),
        gasto_fuera_de_amazon=None,
        dias_al_tope_7d=None,
    )
    (con_aviso,) = _con_avisos(_ConnFalsa([[]]), "amazon_us", (), (fila,))
    (frase,) = con_aviso.avisos
    assert "USD" in frase
    assert "MXN" not in frase


def test_pantalla_us_encabezado_y_frase_en_usd():
    """U14/U15: encabezado (USD) y frase 'USD en 30 días'."""
    html = _plano(
        _html_dinero(
            [_fila_ui()],
            por_ubicacion=[_fila_ubi_ui()],
            plataforma="amazon_us",
            moneda="USD",
        )
    )
    assert "(USD)" in html
    assert "USD en 30 días" in html


def test_pantalla_enlace_amazon_us_abre_us():
    """U16: el enlace 'Amazon US' lleva a ?plataforma=amazon_us."""
    html = _html_dinero([_fila_ui()], plataforma="amazon_us", moneda="USD")
    assert 'href="/donde-poner-el-dinero?plataforma=amazon_us"' in html


def test_como_dict_target_se_pinta_con_un_decimal():
    """NB1: el target largo sale como '20.8 %', no con 26 decimales."""
    from app.pantalla_dinero import FilaTipo, PantallaDinero

    total = FilaTipo(
        tipo="",
        gasto=None,
        pedidos=None,
        venta=None,
        acos_pct=None,
        parte_del_gasto_pct=None,
        hojas=0,
    )
    pantalla = PantallaDinero(
        plataforma="amazon_mx",
        moneda="MXN",
        desde=dt.date(2026, 7, 6),
        hasta=dt.date(2026, 10, 4),
        filas=(),
        total=total,
        hojas_sin_clasificar=0,
        target_acos_pct=Decimal("20.76500719978248922407256205"),
    )
    assert pantalla.como_dict()["target_acos_pct"] == "20.8"


@_skip_db
def test_pg_grupos_toman_la_ultima_observacion_del_dia():
    """L1: con dos observaciones del mismo dia, la tabla por tipo suma la
    mas reciente (Amazon re-emite 31 dias cada dia)."""
    with _db_dinero("orbit_p1_ultima") as conn:
        run = conn.execute("INSERT INTO ingest_run (source) VALUES ('t') RETURNING id").fetchone()[
            0
        ]
        fecha = dt.date(2026, 10, 4)
        camp = _siembra_entidad(conn, "amazon_mx", "campaign", "c1")
        ag = _siembra_entidad(conn, "amazon_mx", "ad_group", "a1", parent=camp)
        kw = _siembra_entidad(
            conn, "amazon_mx", "keyword", "k1", parent=ag, match="EXACT", texto="t1"
        )
        for e in [camp, ag, kw]:
            _siembra_estado(conn, e, "ENABLED")
        _siembra_metrica(
            conn,
            kw,
            fecha,
            Decimal("10"),
            1,
            Decimal("100"),
            dt.datetime(2026, 10, 5, 8, 0, tzinfo=dt.UTC),
            run,
        )
        _siembra_metrica(
            conn,
            kw,
            fecha,
            Decimal("99"),
            2,
            Decimal("200"),
            dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.UTC),
            run,
            reporte="R2",
        )
        pantalla = lee_dinero(conn, plataforma="amazon_mx", dias=90, hasta=fecha)
        exact = [f for f in pantalla.filas if f.tipo == "exact"][0]
        assert exact.gasto == Decimal("99")
        assert exact.pedidos == 2
        assert exact.venta == Decimal("200")


@_skip_db
def test_clases_ajuste_del_dict_filtra_sin_sellar(monkeypatch):
    """P.2b: el dict trae las selladas en ORDEN_CLASES; una clase fuera
    no llega al template y no trae boton."""
    import app.campana_ajustes as ca
    from app.pantalla_dinero import FilaTipo, PantallaDinero

    monkeypatch.setattr(ca, "CLASES_SELLADAS", frozenset({"presupuesto"}))
    pantalla = PantallaDinero(
        plataforma="amazon_mx",
        moneda="MXN",
        desde=dt.date(2026, 9, 5),
        hasta=dt.date(2026, 10, 4),
        filas=(),
        total=FilaTipo(
            tipo="",
            gasto=None,
            pedidos=None,
            venta=None,
            acos_pct=None,
            parte_del_gasto_pct=None,
            hojas=0,
        ),
        hojas_sin_clasificar=0,
        target_acos_pct=None,
    )
    assert pantalla.como_dict()["clases_ajuste"] == ["presupuesto"]


def test_pg_regresables_y_aviso_propio_en_fila_campana():
    """V.3: confirmado sin regreso sale en regresables (fuera_de_amazon
    no, sin regreso sellado); ajuste de ubicacion reciente pinta aviso."""
    import json

    with _db_dinero("orbit_p1_reg") as conn:
        fecha = dt.date(2026, 10, 4)
        camp = _siembra_campana(conn, "amazon_mx", "cr", "Reg")
        cfg = conn.execute(
            "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
            " presupuesto_diario, presupuesto_moneda, estrategia_puja) VALUES"
            " (%s, %s, 100, 'MXN', 'MANUAL') RETURNING id",
            (camp, dt.datetime(2026, 10, 1, tzinfo=dt.UTC)),
        ).fetchone()[0]

        def _ajuste(clase, huella, confirmado, regresa_a=None):
            return conn.execute(
                "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
                " despues, huella, actor, go_literal, regresa_a, confirmado_el)"
                " VALUES (%s, 'amazon_mx', %s, %s, %s, %s, 'dueno', 'APLICAR AJUSTE',"
                " %s, %s) RETURNING id",
                (camp, clase, cfg, json.dumps({"clase": clase}), huella, regresa_a, confirmado),
            ).fetchone()[0]

        ubi = _ajuste("ajuste_ubicacion", "h-ubi", dt.datetime(2026, 10, 3, tzinfo=dt.UTC))
        pre = _ajuste("presupuesto", "h-pre", dt.datetime(2026, 10, 3, tzinfo=dt.UTC))
        _ajuste("fuera_de_amazon", "h-fuera", dt.datetime(2026, 10, 3, tzinfo=dt.UTC))
        _ajuste("presupuesto", "h-pend", None)
        reg = _ajuste(
            "presupuesto", "h-reg", dt.datetime(2026, 10, 4, tzinfo=dt.UTC), regresa_a=pre
        )
        filas = {
            f.campana_id: f
            for f in lee_campanas(
                conn, plataforma="amazon_mx", desde=fecha - dt.timedelta(days=29), hasta=fecha
            )
        }
        fila = filas[camp]
        assert [(r.ajuste_id, r.clase) for r in fila.ajustes_regresables] == [
            (ubi, "ajuste_ubicacion"),
            (reg, "presupuesto"),
        ]
        assert [r.frase for r in fila.ajustes_regresables] == [
            "Regresar el ajuste de ubicación del 2026-10-03",
            "Regresar el presupuesto del 2026-10-04",
        ]
        assert (
            fila.aviso_ajuste == "Orbit movió el precio hace 2 días; espera al día 7 para juzgarlo."
        )
