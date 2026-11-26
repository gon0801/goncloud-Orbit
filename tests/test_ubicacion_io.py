"""Tests de integracion de la lectura y persistencia de ubicaciones (AUTO-04)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from test_schema import _postgres_obligatorio_ausente

from app.optimizer.caso import EconomiaPlataforma
from app.optimizer.temporada_config import CampanaTopada
from app.optimizer.ubicacion import (
    CasoCampana,
    EvidenciaUbicacion,
    MantenerUbicacion,
    MetaCampana,
    MoverUbicacion,
    caso_desde_json,
)
from app.ubicacion_io import (
    MOTIVO_PRODUCTO_SIN_EVIDENCIA,
    guarda_evaluaciones,
    lee_casos,
    marca_producto_sin_evidencia,
    resumen_ubicaciones_ciclo,
    ultimas_evaluaciones_por_campana,
)
from tests.helpers.pg import db_desde_template

_D = Decimal
_HOY = dt.datetime(2026, 11, 26, 8, 0, tzinfo=dt.UTC)
_ANCLA = dt.datetime(2026, 11, 20, 0, 0, tzinfo=dt.UTC)

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(), reason="sin Postgres utilizable en ORBIT_TEST_DSN"
)


def _db():
    return db_desde_template("orbit_uio", autocommit=True)


def _eco() -> EconomiaPlataforma:
    return EconomiaPlataforma(
        moneda="MXN",
        equilibrio_acos_pct=None,
        gasto_para_concluir=_D("350"),
        confianza_recorte=_D("0.80"),
        confianza_subida=_D("0.70"),
    )


def _goal(conn, platform="amazon_mx") -> None:
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, bid_currency, bid_floor,"
        " bid_ceiling, enabled, mode) VALUES ('platform', %s, 'MXN', 0.10, 2.50, true,"
        " 'live')",
        (platform,),
    )


def _campana(conn, platform="amazon_mx", external="c-uio") -> dict:
    camp = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id) VALUES (%s, 'campaign', %s)"
        " RETURNING id",
        (platform, external),
    ).fetchone()[0]
    grupo = conn.execute(
        "INSERT INTO ad_entity (platform, kind, external_id, parent_id) VALUES (%s,"
        " 'ad_group', %s, %s) RETURNING id",
        (platform, external + "-g", camp),
    ).fetchone()[0]
    for entidad in (camp, grupo):
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at)"
            " VALUES (%s, 'ENABLED', now())",
            (entidad,),
        )
    hojas = []
    for i, match in enumerate(("EXACT", "PHRASE")):
        hoja = conn.execute(
            "INSERT INTO ad_entity (platform, kind, external_id, parent_id, match_type,"
            " keyword_text) VALUES (%s, 'keyword', %s, %s, %s, 't') RETURNING id",
            (platform, f"{external}-k{i}", grupo, match),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO ad_entity_state (ad_entity_id, status, synced_at, current_bid,"
            " bid_currency) VALUES (%s, 'ENABLED', now(), 2.50, 'MXN')",
            (hoja,),
        )
        hojas.append(hoja)
    return {"campana": camp, "grupo": grupo, "hojas": hojas}


def _config(conn, campana, observed_at, *, top=0, resto=None, producto=None, estrategia="MANUAL"):
    return conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
        " ajuste_resto_pct, ajuste_producto_pct) VALUES (%s, %s, 100, 'MXN', %s, %s, %s, %s)"
        " RETURNING id",
        (campana, observed_at, estrategia, top, resto, producto),
    ).fetchone()[0]


def _config_version(conn) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('uio', '{}'::jsonb) RETURNING id"
    ).fetchone()[0]


def _ingest(conn) -> int:
    return conn.execute(
        "INSERT INTO ingest_run (source, finished_at, rows_written, ok)"
        " VALUES ('prueba', now(), 1, true) RETURNING id"
    ).fetchone()[0]


def _metrica(conn, campana, dia, cost, ingest) -> None:
    conn.execute(
        "INSERT INTO ads_metric_observation (ad_entity_id, metric_date, observed_at,"
        " metric_currency, cost, clicks, orders, ingest_run_id) VALUES (%s, %s, %s,"
        " 'MXN', %s, 10, 1, %s)",
        (campana, dia, _ANCLA, cost, ingest),
    )


def _placement(conn, campana, ubicacion, dia, *, cost, clics=10, pedidos=0, venta=0):
    conn.execute(
        "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
        " metric_date, observed_at, metric_currency, impressions, clicks, cost, orders,"
        " ad_revenue, source_report_id) VALUES ('amazon_mx', %s, %s, %s, %s, 'MXN', 1000,"
        " %s, %s, %s, %s, 'r-uio')",
        (campana, ubicacion, dia, _ANCLA, clics, cost, pedidos, venta),
    )


def _settings(modo="shadow"):
    return {"ads_ubicacion_amazon_mx": modo}


def _lee(conn, metas=None, decidido=_HOY, settings=None, fase="normal"):
    from app.cycle_grupos import lee_goals

    campanas = [f[0] for f in conn.execute("SELECT id FROM ad_entity WHERE kind = 'campaign'")]
    plataforma, por_campana = lee_goals(conn, "amazon_mx", campanas)
    return lee_casos(
        conn,
        "amazon_mx",
        decidido,
        settings=settings if settings is not None else _settings(),
        goals=por_campana,
        metas_hoja=metas or {},
        goal_plataforma=plataforma,
        economia=_eco(),
        fase_temporada=fase,
    )


@_skip_db
def test_ultimo_cambio_config_ignora_primera_obs_y_unifica_null_cero():
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=9)
        _config(conn, camp, _ANCLA - dt.timedelta(days=19), top=9, resto=0, producto=0)
        casos = _lee(conn)
        caso = next(c for c in casos if c.campana_id == camp)
        assert caso.dias_desde_ultimo_cambio is None
        assert caso.ventana_hasta == _HOY.date() - dt.timedelta(days=3)
        assert caso.ventana_desde == caso.ventana_hasta - dt.timedelta(days=29)

        _config(conn, camp, _HOY - dt.timedelta(days=10), top=15)
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.dias_desde_ultimo_cambio == 10

    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=9)
        _config(conn, camp, _HOY - dt.timedelta(days=16), top=22)
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.dias_desde_ultimo_cambio == 16
        assert caso.ventana_desde == _HOY.date() - dt.timedelta(days=15)
        assert caso.ventana_hasta == _HOY.date() - dt.timedelta(days=3)


@_skip_db
def test_excluye_campana_en_impulso_abierto_y_detecta_ajuste_en_vuelo():
    with _db() as (conn, _dsn):
        _goal(conn)
        datos = _campana(conn)
        camp = datos["campana"]
        config = _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=9)
        conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal) VALUES (%s, 'amazon_mx',"
            " 'ajuste_ubicacion', %s, %s, 'h-uio', 'dueno', 'APLICAR AJUSTE')",
            (camp, config, json.dumps({"clase": "ajuste_ubicacion"})),
        )
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.ajuste_en_vuelo is True

    with _db() as (conn, _dsn):
        _goal(conn)
        datos = _campana(conn)
        camp = datos["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=9)
        producto = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('sku-uio', 'Producto') RETURNING id"
        ).fetchone()[0]
        listing = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_mx', 'ASIN-UIO', 'sku-uio') RETURNING id",
            (producto,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base,"
            " go_literal, huella, plan, modo_goal, estado)"
            " VALUES ('lote-uio', 'amazon_mx', 'simple', 'base', 'go', 'huella',"
            " '{}'::jsonb, 'shadow', 'applied')"
        )
        grupo = conn.execute(
            "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote, tipo,"
            " target_acos_pct, target_derivado_pct, fraccion, target_procedencia,"
            " go_literal) VALUES ('amazon_mx', 'simple', 'base', 'lote-uio', 'impulso', 20,"
            " 20, 0.5, 'prueba', 'go') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id,"
            " ad_group_ad_entity_id) VALUES (%s, 'category_exact', %s, %s)",
            (grupo, camp, datos["grupo"]),
        )
        conn.execute(
            "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote,"
            " subfamilia, tope_monto, tope_moneda, presupuesto_diario, plazo_dias,"
            " lanzado_el, actor, go_literal) VALUES ('amazon_mx', %s, %s, %s, 'lote-uio',"
            " 'simple', 100, 'MXN', 50, 30, now(), 'dueno', 'LANZAR IMPULSO')",
            (listing, producto, grupo),
        )
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.en_impulso is True


@_skip_db
def test_banda_de_metas_desde_hojas_y_conciliacion_de_gasto():
    with _db() as (conn, _dsn):
        _goal(conn)
        datos = _campana(conn)
        camp = datos["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        ingest = _ingest(conn)
        hasta = _HOY.date() - dt.timedelta(days=3)
        for ubicacion, cost in (
            ("arriba_de_busqueda", "40"),
            ("resto_de_busqueda", "30"),
            ("paginas_de_producto", "20"),
            ("fuera_de_amazon", "10"),
        ):
            _placement(conn, camp, ubicacion, hasta, cost=_D(cost))
        _metrica(conn, camp, hasta, _D("100"), ingest)
        metas = {
            datos["hojas"][0]: (_D("18"), "goal_campana"),
            datos["hojas"][1]: (_D("25"), "margen_familia"),
        }
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        assert caso.meta == MetaCampana(
            exigente_pct=_D("18"),
            laxa_pct=_D("25"),
            procedencias=("goal_campana", "margen_familia"),
        )
        assert caso.gasto_placements_total == _D("100")
        assert caso.gasto_campana == _D("100")
        assert caso.placements_frescos is True

    with _db() as (conn, _dsn):
        _goal(conn)
        datos = _campana(conn)
        camp = datos["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        ingest = _ingest(conn)
        atrasado = _HOY.date() - dt.timedelta(days=6)
        for ubicacion in (
            "arriba_de_busqueda",
            "resto_de_busqueda",
            "paginas_de_producto",
            "fuera_de_amazon",
        ):
            _placement(conn, camp, ubicacion, atrasado, cost=_D("10"))
        _metrica(conn, camp, atrasado, _D("40"), ingest)
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.placements_frescos is False


@_skip_db
def test_guarda_evaluaciones_y_lee_sugerencia_vigente():
    with _db() as (conn, _dsn):
        _goal(conn)
        a = _campana(conn, external="c-uio-a")
        b = _campana(conn, external="c-uio-b")
        config_a = _config(conn, a["campana"], _ANCLA - dt.timedelta(days=20), top=0)
        config_b = _config(conn, b["campana"], _ANCLA - dt.timedelta(days=20), top=10)
        cv = _config_version(conn)
        eco = _eco()
        caso_a = CasoCampana(
            campana_id=a["campana"],
            plataforma="amazon_mx",
            config_id=config_a,
            estrategia="LEGACY_FOR_SALES",
            ventana_desde=_HOY.date() - dt.timedelta(days=32),
            ventana_hasta=_HOY.date() - dt.timedelta(days=3),
            ubicaciones=(
                EvidenciaUbicacion("arriba_de_busqueda", 0, 40, _D("5"), 4, _D("100"), 0),
                EvidenciaUbicacion("resto_de_busqueda", 0, 20, _D("2"), 0, _D("10"), 0),
                EvidenciaUbicacion("paginas_de_producto", 0, 20, _D("2"), 0, _D("10"), 0),
            ),
            meta=MetaCampana(_D("18"), _D("24"), ("goal",)),
            economia=eco,
            gasto_campana=_D("9"),
            gasto_placements_total=_D("9"),
            placements_frescos=True,
            dias_desde_ultimo_cambio=None,
            ajuste_en_vuelo=False,
            elegible=True,
            en_impulso=False,
            solo_bajadas=False,
            dias_topados_7d=0,
            topada=CampanaTopada(_D("0.90"), 5),
            fase_temporada="normal",
        )
        caso_b = CasoCampana(
            **{**caso_a.__dict__, "campana_id": b["campana"], "config_id": config_b}
        )
        mover = MoverUbicacion("arriba_de_busqueda", 0, 15, "bajo_meta_con_ventaja")
        ids = guarda_evaluaciones(
            conn,
            cycle_id=_ciclo(conn),
            config_version_id=cv,
            platform="amazon_mx",
            decided_at=_HOY,
            evaluaciones=[
                (caso_a, "shadow", mover),
                (caso_b, "shadow", MantenerUbicacion("sin_evidencia_ubicacion")),
            ],
        )
        assert set(ids) == {a["campana"], b["campana"]}
        sugerencias = ultimas_evaluaciones_por_campana(conn, "amazon_mx")
        assert set(sugerencias) == {a["campana"]}
        assert sugerencias[a["campana"]].ubicacion == "arriba_de_busqueda"
        assert sugerencias[a["campana"]].de_pct == 0
        assert sugerencias[a["campana"]].a_pct == 15

        resumen = resumen_ubicaciones_ciclo(conn, _ultimo_ciclo(conn))
        assert resumen["evaluadas"] == 2
        assert resumen["mover"] == 1
        assert resumen["en_practica"] == 1

        _config(conn, a["campana"], _HOY - dt.timedelta(days=1), top=5)
        assert a["campana"] not in ultimas_evaluaciones_por_campana(conn, "amazon_mx")


def _ciclo(conn, mode="shadow") -> int:
    return conn.execute(
        "INSERT INTO optimizer_cycle (mode, platform) VALUES (%s, 'amazon_mx') RETURNING id",
        (mode,),
    ).fetchone()[0]


def _ultimo_ciclo(conn) -> int:
    return conn.execute("SELECT max(id) FROM optimizer_cycle").fetchone()[0]


def test_marca_producto_sin_evidencia_reemplaza_el_veredicto():
    caso = CasoCampana(
        campana_id=1,
        plataforma="amazon_mx",
        config_id=1,
        estrategia="MANUAL",
        ventana_desde=_HOY.date(),
        ventana_hasta=_HOY.date(),
        ubicaciones=(),
        meta=None,
        economia=_eco(),
        gasto_campana=None,
        gasto_placements_total=None,
        placements_frescos=False,
        dias_desde_ultimo_cambio=None,
        ajuste_en_vuelo=False,
        elegible=True,
        en_impulso=False,
        solo_bajadas=False,
        dias_topados_7d=None,
        topada=CampanaTopada(_D("0.90"), 5),
        fase_temporada=None,
    )
    marca = MoverUbicacion("arriba_de_busqueda", 0, 15, "bajo_meta_con_ventaja")
    salida = marca_producto_sin_evidencia([(caso, "shadow", marca)], [1])
    assert salida[0][2] == MantenerUbicacion(MOTIVO_PRODUCTO_SIN_EVIDENCIA)
    assert caso_desde_json(json.loads(json.dumps(_caso_json(caso)))) == caso


def _caso_json(caso: CasoCampana):
    from app.optimizer.ubicacion import caso_a_json

    return caso_a_json(caso)
