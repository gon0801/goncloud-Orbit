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
    evaluaciones_a_aplicar,
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


def _config(
    conn,
    campana,
    observed_at,
    *,
    top=0,
    resto=None,
    producto=None,
    estrategia="MANUAL",
    presupuesto="100",
):
    return conn.execute(
        "INSERT INTO ads_campana_config_observation (ad_entity_id, observed_at,"
        " presupuesto_diario, presupuesto_moneda, estrategia_puja, ajuste_top_pct,"
        " ajuste_resto_pct, ajuste_producto_pct) VALUES (%s, %s, %s,"
        " CASE WHEN %s::numeric IS NULL THEN NULL ELSE 'MXN'::currency END, %s, %s, %s, %s)"
        " RETURNING id",
        (campana, observed_at, presupuesto, presupuesto, estrategia, top, resto, producto),
    ).fetchone()[0]


def _hojas(conn, campana: int) -> list[int]:
    return [
        fila[0]
        for fila in conn.execute(
            "SELECT k.id FROM ad_entity k"
            " JOIN ad_entity g ON g.id = k.parent_id"
            " WHERE g.parent_id = %s AND k.kind = 'keyword' ORDER BY k.id",
            (campana,),
        )
    ]


def _config_version(conn, settings: dict | None = None) -> int:
    return conn.execute(
        "INSERT INTO config_version (label, settings) VALUES ('uio', %s::jsonb) RETURNING id",
        (json.dumps(settings or {}),),
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
        cv = _config_version(conn, {"ads_ubicacion_amazon_mx": "shadow"})
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


def _caso_simple(
    campana: int, config_id: int, *, plataforma="amazon_mx", gasto_arriba="5"
) -> CasoCampana:
    gasto = _D(gasto_arriba)
    return CasoCampana(
        campana_id=campana,
        plataforma=plataforma,
        config_id=config_id,
        estrategia="LEGACY_FOR_SALES",
        ventana_desde=_HOY.date() - dt.timedelta(days=32),
        ventana_hasta=_HOY.date() - dt.timedelta(days=3),
        ubicaciones=(
            EvidenciaUbicacion("arriba_de_busqueda", 0, 40, gasto, 4, _D("100"), 0),
            EvidenciaUbicacion("resto_de_busqueda", 0, 20, _D("2"), 0, _D("10"), 0),
            EvidenciaUbicacion("paginas_de_producto", 0, 20, _D("2"), 0, _D("10"), 0),
        ),
        meta=MetaCampana(_D("18"), _D("24"), ("goal",)),
        economia=_eco(),
        gasto_campana=gasto + _D("4"),
        gasto_placements_total=gasto + _D("4"),
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


@_skip_db
def test_goal_de_campana_manda_sobre_el_de_plataforma_en_elegibilidad():
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        conn.execute(
            "INSERT INTO ads_optimizer_goal (scope, ad_entity_id, bid_currency, bid_floor,"
            " bid_ceiling, enabled, mode) VALUES ('campaign', %s, 'MXN', 0.10, 2.50, false,"
            " 'live')",
            (camp,),
        )
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.elegible is False


@_skip_db
def test_resumen_aplicadas_solo_cuenta_confirmadas():
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        config = _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        cv = _config_version(conn)
        ciclo = _ciclo(conn, mode="live")
        mover = MoverUbicacion("arriba_de_busqueda", 0, 15, "bajo_meta_con_ventaja")
        ids = guarda_evaluaciones(
            conn,
            cycle_id=ciclo,
            config_version_id=cv,
            platform="amazon_mx",
            decided_at=_HOY,
            evaluaciones=[(_caso_simple(camp, config), "live", mover)],
        )
        eval_id = ids[camp]
        assert resumen_ubicaciones_ciclo(conn, ciclo)["aplicadas"] == 0
        conn.execute("SET ROLE app_decide")
        ajuste = conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, evaluacion_id) VALUES (%s, 'amazon_mx',"
            " 'ajuste_ubicacion', %s, %s, 'auto:uio', 'orbit_ubicacion', 'AUTO_UBICACION',"
            " %s) RETURNING id",
            (camp, config, json.dumps({"clase": "ajuste_ubicacion"}), eval_id),
        ).fetchone()[0]
        conn.execute("RESET ROLE")
        assert resumen_ubicaciones_ciclo(conn, ciclo)["aplicadas"] == 0
        conn.execute("SET ROLE app_decide")
        conn.execute("UPDATE campana_ajuste SET confirmado_el = %s WHERE id = %s", (_HOY, ajuste))
        conn.execute("RESET ROLE")
        assert resumen_ubicaciones_ciclo(conn, ciclo)["aplicadas"] == 1


@_skip_db
def test_evaluaciones_a_aplicar_ordena_y_filtra_pendientes():
    with _db() as (conn, _dsn):
        _goal(conn)
        sub = _campana(conn, external="c-uio-sub")["campana"]
        suave = _campana(conn, external="c-uio-suave")["campana"]
        fuerte = _campana(conn, external="c-uio-fuerte")["campana"]
        ajustada = _campana(conn, external="c-uio-ajustada")["campana"]
        sin_aplicar = _campana(conn, external="c-uio-sin")["campana"]
        configs = {
            sub: _config(conn, sub, _ANCLA - dt.timedelta(days=20), top=0),
            suave: _config(conn, suave, _ANCLA - dt.timedelta(days=20), top=30),
            fuerte: _config(conn, fuerte, _ANCLA - dt.timedelta(days=20), top=40),
            ajustada: _config(conn, ajustada, _ANCLA - dt.timedelta(days=20), top=40),
            sin_aplicar: _config(conn, sin_aplicar, _ANCLA - dt.timedelta(days=20), top=40),
        }
        cv = _config_version(conn)
        ciclo = _ciclo(conn, mode="live")
        # La subida se inserta PRIMERO (id menor): sin el sort saldria primera.
        ids = guarda_evaluaciones(
            conn,
            cycle_id=ciclo,
            config_version_id=cv,
            platform="amazon_mx",
            decided_at=_HOY,
            evaluaciones=[
                (
                    _caso_simple(sub, configs[sub]),
                    "live",
                    MoverUbicacion("arriba_de_busqueda", 0, 15, "bajo_meta_con_ventaja"),
                ),
                (
                    _caso_simple(suave, configs[suave], gasto_arriba="30"),
                    "live",
                    MoverUbicacion("arriba_de_busqueda", 30, 15, "acos_sobre_meta"),
                ),
                (
                    _caso_simple(fuerte, configs[fuerte], gasto_arriba="50"),
                    "live",
                    MoverUbicacion("arriba_de_busqueda", 40, 15, "acos_sobre_meta_fuerte"),
                ),
                (
                    _caso_simple(ajustada, configs[ajustada]),
                    "live",
                    MoverUbicacion("arriba_de_busqueda", 40, 15, "acos_sobre_meta_fuerte"),
                ),
                (
                    _caso_simple(sin_aplicar, configs[sin_aplicar]),
                    "live",
                    MoverUbicacion("arriba_de_busqueda", 40, 15, "acos_sobre_meta_fuerte"),
                ),
            ],
        )
        conn.execute("SET ROLE app_decide")
        conn.execute(
            "INSERT INTO campana_ajuste (campana_id, platform, clase, antes_config_id,"
            " despues, huella, actor, go_literal, evaluacion_id) VALUES (%s, 'amazon_mx',"
            " 'ajuste_ubicacion', %s, %s, 'auto:filtro', 'orbit_ubicacion',"
            " 'AUTO_UBICACION', %s)",
            (
                ajustada,
                configs[ajustada],
                json.dumps({"clase": "ajuste_ubicacion"}),
                ids[ajustada],
            ),
        )
        conn.execute("RESET ROLE")
        conn.execute(
            "INSERT INTO ubicacion_sin_aplicar (evaluacion_id, motivo, registrado_el)"
            " VALUES (%s, 'cuota_agotada', %s)",
            (ids[sin_aplicar], _HOY),
        )
        pendientes = evaluaciones_a_aplicar(conn, ciclo, "amazon_mx")
        assert [p[1].campana_id for p in pendientes] == [fuerte, suave, sub]
        assert [p[2].de_pct for p in pendientes] == [40, 30, 0]


@_skip_db
def test_ventana_vacia_por_cambio_reciente_no_revienta_el_check():
    from app.optimizer.ubicacion import decide_ubicacion

    for dias in (0, 1, 3):
        with _db() as (conn, _dsn):
            _goal(conn)
            camp = _campana(conn, external=f"c-uio-v{dias}")["campana"]
            _config(conn, camp, _HOY - dt.timedelta(days=20), top=9)
            _config(conn, camp, _HOY - dt.timedelta(days=dias), top=10 + dias)
            caso = next(c for c in _lee(conn) if c.campana_id == camp)
            assert caso.ventana_desde == caso.ventana_hasta
            guarda_evaluaciones(
                conn,
                cycle_id=_ciclo(conn),
                config_version_id=_config_version(conn),
                platform="amazon_mx",
                decided_at=_HOY,
                evaluaciones=[(caso, "shadow", decide_ubicacion(caso))],
            )


@_skip_db
def test_suma_placements_presentes_y_ceros_medidos_sin_fila():
    from app.optimizer.ubicacion import decide_ubicacion

    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        ingest = _ingest(conn)
        hasta = _HOY.date() - dt.timedelta(days=3)
        for ubicacion, cost in (
            ("arriba_de_busqueda", "40"),
            ("resto_de_busqueda", "30"),
            ("paginas_de_producto", "20"),
        ):
            _placement(conn, camp, ubicacion, hasta, cost=_D(cost), clics=0, pedidos=0, venta=0)
        _metrica(conn, camp, hasta, _D("90"), ingest)
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        assert caso.gasto_placements_total == _D("90")
        assert caso.ubicaciones[0].pedidos == 0
        assert caso.ubicaciones[0].venta == 0
        # La ausencia de `fuera_de_amazon` no bloquea el caso: el gasto suma
        # las ubicaciones presentes y la conciliacion cuadra.
        assert decide_ubicacion(caso).motivo != "datos_placements_incompletos"

    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        ingest = _ingest(conn)
        hasta = _HOY.date() - dt.timedelta(days=3)
        _placement(conn, camp, "arriba_de_busqueda", hasta, cost=_D("10"), clics=0, pedidos=0)
        conn.execute(
            "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
            " metric_date, observed_at, metric_currency, impressions, clicks, cost, orders,"
            " ad_revenue, source_report_id) VALUES ('amazon_mx', %s, 'paginas_de_producto',"
            " %s, %s, 'MXN', 1000, NULL, NULL, NULL, NULL, 'r-uio')",
            (camp, hasta, _ANCLA),
        )
        _metrica(conn, camp, hasta, _D("10"), ingest)
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        ubicaciones = {u.ubicacion: u for u in caso.ubicaciones}
        assert ubicaciones["arriba_de_busqueda"].pedidos == 0
        assert ubicaciones["resto_de_busqueda"].pedidos == 0
        assert ubicaciones["resto_de_busqueda"].gasto == _D("0")
        assert ubicaciones["paginas_de_producto"].pedidos is None
        assert ubicaciones["paginas_de_producto"].gasto is None
        assert caso.gasto_placements_total is None


@_skip_db
def test_inmaduros_exige_cobertura_y_cero_sin_filas():
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=10)
        ayer = _HOY.date() - dt.timedelta(days=1)
        antes = _HOY.date() - dt.timedelta(days=2)
        ingest = _ingest(conn)
        _placement(conn, camp, "arriba_de_busqueda", ayer, cost=_D("1"), pedidos=0)
        _metrica(conn, camp, ayer, _D("1"), ingest)
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.ubicaciones[0].pedidos_inmaduros is None
        assert caso.ubicaciones[1].pedidos_inmaduros is None

    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=10)
        ayer = _HOY.date() - dt.timedelta(days=1)
        antes = _HOY.date() - dt.timedelta(days=2)
        ingest = _ingest(conn)
        for dia in (antes, ayer):
            _placement(conn, camp, "arriba_de_busqueda", dia, cost=_D("1"), pedidos=0)
            _metrica(conn, camp, dia, _D("1"), ingest)
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.ubicaciones[0].pedidos_inmaduros == 0
        assert caso.ubicaciones[1].pedidos_inmaduros == 0

    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn, external="c-uio-null")["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=10)
        ayer = _HOY.date() - dt.timedelta(days=1)
        antes = _HOY.date() - dt.timedelta(days=2)
        ingest = _ingest(conn)
        for dia in (antes, ayer):
            conn.execute(
                "INSERT INTO ads_placement_observation (platform, ad_entity_id, placement,"
                " metric_date, observed_at, metric_currency, impressions, clicks, cost,"
                " orders, ad_revenue, source_report_id) VALUES ('amazon_mx', %s,"
                " 'arriba_de_busqueda', %s, %s, 'MXN', 1000, 5, 1, NULL, NULL, 'r-uio')",
                (camp, dia, _ANCLA),
            )
            _metrica(conn, camp, dia, _D("1"), ingest)
        caso = next(c for c in _lee(conn) if c.campana_id == camp)
        assert caso.ubicaciones[0].pedidos_inmaduros is None
        assert caso.ubicaciones[1].pedidos_inmaduros == 0


@_skip_db
def test_dias_topados_sin_presupuesto_es_none_y_con_presupuesto_mide():
    from app.optimizer.ubicacion import decide_ubicacion

    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0, presupuesto=None)
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        hasta = _HOY.date() - dt.timedelta(days=3)
        ingest = _ingest(conn)
        _placement(conn, camp, "arriba_de_busqueda", hasta, cost=_D("10"))
        _metrica(conn, camp, hasta, _D("10"), ingest)
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        assert caso.dias_topados_7d is None
        assert decide_ubicacion(caso).motivo == "presupuesto_sin_medir"

    # Presupuesto conocido pero sin dias medidos: None, no cero.
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn, external="c-uio-sin-dias")["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0, presupuesto="100")
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        assert caso.dias_topados_7d is None

    # Menos dias medidos que el corte (5 por omision): None.
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn, external="c-uio-pocos")["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0, presupuesto="100")
        ingest = _ingest(conn)
        ayer = _HOY.date() - dt.timedelta(days=1)
        for dias in range(1, 4):
            _metrica(conn, camp, ayer - dt.timedelta(days=dias - 1), _D("95"), ingest)
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        assert caso.dias_topados_7d is None

    # Con cinco dias medidos (el corte) el conteo se publica.
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn, external="c-uio-completo")["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0, presupuesto="100")
        ingest = _ingest(conn)
        ayer = _HOY.date() - dt.timedelta(days=1)
        for dias in range(5):
            _metrica(conn, camp, ayer - dt.timedelta(days=dias), _D("95"), ingest)
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        assert caso.dias_topados_7d == 5


@_skip_db
def test_lee_casos_produce_enteros_y_guarda_la_evaluacion():
    """Devin #785: `sum(bigint)` vuelve Decimal; el JSON de auditoria exige
    int y la evaluacion leida de SQL debe poder guardarse tal cual."""
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn)["campana"]
        _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        ingest = _ingest(conn)
        hasta = _HOY.date() - dt.timedelta(days=3)
        _placement(conn, camp, "arriba_de_busqueda", hasta, cost=_D("10"), clics=10, pedidos=0)
        _metrica(conn, camp, hasta, _D("10"), ingest)
        metas = {datos_hoja: (_D("18"), "goal") for datos_hoja in _hojas(conn, camp)}
        caso = next(c for c in _lee(conn, metas=metas) if c.campana_id == camp)
        arriba = caso.ubicaciones[0]
        assert isinstance(arriba.clics, int)
        assert isinstance(arriba.pedidos, int)
        assert arriba.pedidos_inmaduros is None  # sin ingesta en D-2..D-1

        ids = guarda_evaluaciones(
            conn,
            cycle_id=_ciclo(conn),
            config_version_id=_config_version(conn),
            platform="amazon_mx",
            decided_at=_HOY,
            evaluaciones=[(caso, "shadow", MantenerUbicacion("sin_evidencia_ubicacion"))],
        )
        assert ids[camp]


@_skip_db
def test_frescura_placements_es_por_plataforma_y_conciliacion_caza_huecos():
    """P1-1: frescura a nivel plataforma permite recortar una campana sin
    impresiones exactamente en D-3; un hueco real en sus placements lo atrapa
    la conciliacion contra el gasto de campana."""
    from app.optimizer.ubicacion import decide_ubicacion

    with _db() as (conn, _dsn):
        _goal(conn)
        camp_recorte = _campana(conn, external="c-uio-sin-d3")["campana"]
        camp_fresca = _campana(conn, external="c-uio-fresca")["campana"]
        camp_hueco = _campana(conn, external="c-uio-hueco")["campana"]
        for camp, top in ((camp_recorte, 40), (camp_fresca, 0), (camp_hueco, 40)):
            _config(conn, camp, _ANCLA - dt.timedelta(days=30), top=top)
        ingest = _ingest(conn)
        hoy = _HOY.date()
        # Plataforma fresca: camp_fresca tiene placements en D-3, D-2 y D-1.
        for dias in (3, 2, 1):
            dia = hoy - dt.timedelta(days=dias)
            _placement(conn, camp_fresca, "arriba_de_busqueda", dia, cost=_D("10"))
            _metrica(conn, camp_fresca, dia, _D("10"), ingest)
        # camp_recorte: 29 dias con gasto (D-32..D-4), 0 impresiones en D-3.
        for dias in range(4, 33):
            dia = hoy - dt.timedelta(days=dias)
            _placement(
                conn, camp_recorte, "arriba_de_busqueda", dia, cost=_D("20"), clics=5, pedidos=0
            )
            _metrica(conn, camp_recorte, dia, _D("20"), ingest)
        # camp_hueco: faltan filas de placements frente al gasto real de campana.
        for dias in range(4, 14):
            dia = hoy - dt.timedelta(days=dias)
            _metrica(conn, camp_hueco, dia, _D("50"), ingest)
            if dias >= 8:
                _placement(
                    conn, camp_hueco, "arriba_de_busqueda", dia, cost=_D("50"), clics=5, pedidos=0
                )

        metas = {
            h: (_D("20"), "setting_plataforma")
            for c in (camp_recorte, camp_fresca, camp_hueco)
            for h in _hojas(conn, c)
        }
        casos = {c.campana_id: c for c in _lee(conn, metas=metas)}
        caso_rec = casos[camp_recorte]
        assert caso_rec.placements_frescos is True
        assert caso_rec.gasto_campana == _D("580")
        assert caso_rec.gasto_placements_total == _D("580")
        veredicto = decide_ubicacion(caso_rec)
        assert isinstance(veredicto, MoverUbicacion)
        assert veredicto.ubicacion == "arriba_de_busqueda"
        assert veredicto.de_pct == 40
        assert veredicto.a_pct == 25

        caso_hueco = casos[camp_hueco]
        assert caso_hueco.placements_frescos is True
        assert decide_ubicacion(caso_hueco) == MantenerUbicacion("descuadre_placements_campana")


@_skip_db
def test_ultimas_evaluaciones_oculta_cuando_flag_off_o_ausente():
    """P2-A: `ultimas_evaluaciones_por_campana` devuelve {} si `ads_ubicacion_<platform>`
    esta en `off` o ausente, y muestra las sugerencias en `shadow` y `live`."""
    with _db() as (conn, _dsn):
        _goal(conn)
        camp = _campana(conn, external="c-uio-off")["campana"]
        config_id = _config(conn, camp, _ANCLA - dt.timedelta(days=20), top=0)
        cv = _config_version(conn, {})
        mover = MoverUbicacion("arriba_de_busqueda", 0, 15, "bajo_meta_con_ventaja")
        guarda_evaluaciones(
            conn,
            cycle_id=_ciclo(conn),
            config_version_id=cv,
            platform="amazon_mx",
            decided_at=_HOY,
            evaluaciones=[(_caso_simple(camp, config_id), "shadow", mover)],
        )
        assert ultimas_evaluaciones_por_campana(conn, "amazon_mx") == {}

        _config_version(conn, {"ads_ubicacion_amazon_mx": "off"})
        assert ultimas_evaluaciones_por_campana(conn, "amazon_mx") == {}

        _config_version(conn, {"ads_ubicacion_amazon_mx": "shadow"})
        sug_shadow = ultimas_evaluaciones_por_campana(conn, "amazon_mx")
        assert set(sug_shadow) == {camp}
        assert sug_shadow[camp].modo == "shadow"

        _config_version(conn, {"ads_ubicacion_amazon_mx": "live"})
        assert set(ultimas_evaluaciones_por_campana(conn, "amazon_mx")) == {camp}

        assert (
            ultimas_evaluaciones_por_campana(
                conn, "amazon_mx", settings={"ads_ubicacion_amazon_mx": "off"}
            )
            == {}
        )
        with pytest.raises(ValueError, match="ads_ubicacion_amazon_mx"):
            ultimas_evaluaciones_por_campana(
                conn, "amazon_mx", settings={"ads_ubicacion_amazon_mx": "invalido"}
            )
