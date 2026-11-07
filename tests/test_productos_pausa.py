"""AUTO-03 H.6, H.7 y H.8 (dinero y escritura): la pausa por producto sin ventas.

La regla decide que anuncios se pausan (dinero) y el ciclo la encola en la
base de produccion (escritura): cada caso fija que anuncio recibe propuesta,
que fila nace en la cola y que retiro se cierra. Los casos de base corren
contra PostgreSQL con el reloj real, como tests/test_productos_io.py: la
ingesta y S6 anclan las ventanas en now().
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_cycle import OWNER, _config_version, _entidad, _estado
from test_productos_io import (
    _anuncio,
    _db,
    _entrada,
    _fila_reporte,
    _ingesta,
    _mundo,
    _ventana,
)
from test_schema import _postgres_obligatorio_ausente

from app import apply_cola, productos_io
from app import cycle as ciclo
from app.apply_cola import FilaCola
from app.cycle import lee_goals
from app.optimizer import productos as p
from app.optimizer.replay import reproduce

_skip_db = pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)

DECIDIDO = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Regla pura (H.2)
# ---------------------------------------------------------------------------


def _mxn(valor: str) -> p.Dinero:
    return p.Dinero(Decimal(valor), "MXN")


def _actividad(
    clics: int = 100, gasto: str = "500", compras: str = "0", ventas: str = "0", mismo: str = "0"
) -> p.AdsCompletos:
    return p.AdsCompletos(
        ventana=p.ventana_pausa(DECIDIDO),
        gasto=_mxn(gasto),
        clics=clics,
        compras_total=Decimal(compras),
        ventas_total=_mxn(ventas),
        ventas_mismo_sku=_mxn(mismo),
        referencias_por_fecha=(),
    )


def _anuncio_elegible(ad: int, grupo: int) -> p.AnuncioElegible:
    return p.AnuncioElegible(ad, listing_id=7, grupo_id=grupo, huella_alcance=f"sha256:{grupo}")


def _entrada_mx(actividad, anuncios=None, **extra) -> p.EntradaProducto:
    return p.EntradaProducto(
        producto=p.Producto("amazon_mx", 77),
        decidido_el=DECIDIDO,
        margen=p.SinDato(("sin_margen",)),
        fraccion=Decimal("0.5"),
        ventana_halo=p.Ventana(dt.date(2026, 8, 8), dt.date(2026, 11, 4)),
        dias_halo=(),
        actividad_pausa=actividad,
        anuncios=(_anuncio_elegible(901, 51), _anuncio_elegible(902, 52))
        if anuncios is None
        else anuncios,
        grupos_aptos_para_meta=(),
        impedimentos=(),
        limites_pausa=p.limites_pausa_desde_settings({}, "amazon_mx"),
        **extra,
    )


def test_100_clics_y_500_mxn_sin_ventas_dan_una_propuesta_por_anuncio_elegible():
    ev = p.resolver_producto(_entrada_mx(_actividad()))
    assert [x.anuncio.ad_entity_id for x in ev.pausas] == [901, 902]
    assert {x.motivo for x in ev.pausas} == {"producto_sin_ventas"}
    assert ev.motivos_pausa == ()
    assert p.resolver_producto(_entrada_mx(_actividad(clics=99))).pausas == ()
    assert p.resolver_producto(_entrada_mx(_actividad(gasto="499.99"))).motivos_pausa == (
        "bajo_limites",
    )
    sin_anuncios = p.resolver_producto(_entrada_mx(_actividad(), anuncios=()))
    assert (sin_anuncios.pausas, sin_anuncios.motivos_pausa) == ((), ("sin_anuncios_elegibles",))


def test_una_compra_de_otro_sku_deja_sin_propuesta():
    otro_sku = _actividad(compras="1", ventas="30", mismo="0")
    ev = p.resolver_producto(_entrada_mx(otro_sku))
    assert (ev.pausas, ev.motivos_pausa) == ((), ("venta_otro_sku",))
    vendio = p.resolver_producto(_entrada_mx(_actividad(compras="1", ventas="30", mismo="30")))
    assert (vendio.pausas, vendio.motivos_pausa) == ((), ("vendio_en_ventana",))


def test_una_fecha_sin_cobertura_lleva_a_abstencion():
    ev = p.resolver_producto(_entrada_mx(p.SinDato(("fecha_desconocida",))))
    assert (ev.pausas, ev.motivos_pausa) == ((), ("fecha_desconocida",))


def test_politica_v1_se_reproduce_sin_la_regla_de_pausa():
    """Una evaluacion congelada antes de H (v1, sin pausas) la reproduce el
    replay con su politica, sin inventarle propuestas."""
    entrada = _entrada_mx(_actividad())
    v1 = p.resolver_producto(entrada, politica="v1")
    assert (v1.politica, v1.pausas) == ("v1", ())
    datos = v1.como_json()
    del datos["pausas"], datos["motivos_pausa"]
    vuelta = p.EvaluacionProducto.desde_json(datos)
    assert p.resolver_producto(vuelta.entrada, politica=vuelta.politica) == vuelta


# ---------------------------------------------------------------------------
# Mundo de base: un producto que gasto 60 USD con 120 clics sin vender
# ---------------------------------------------------------------------------


def _goal_live(conn) -> None:
    conn.execute(
        "INSERT INTO ads_optimizer_goal (scope, platform, bid_floor, bid_ceiling,"
        " bid_currency, harvest_campaign_id, harvest_ad_group_id, harvest_default_bid,"
        " enabled, mode) VALUES ('platform', 'amazon_us', 0.40, 2.50, 'USD', '9002',"
        " '9102', 0.75, true, 'live')"
    )


def _mundo_pausa(conn) -> dict:
    """El producto SKU-A (anuncio pa1, ENABLED, alta hace 200 dias) gasto
    2 USD con 4 clics cada dia de [D-60, D-30) sin vender, con cobertura S6:
    120 clics y 60 USD, sobre los limites por omision (100 clics, 40 USD)."""
    m = _mundo(conn)
    _goal_live(conn)
    conn.execute("UPDATE ad_entity SET first_seen_at = now() - interval '200 days'")
    fechas = _ventana()
    filas = [
        _fila_reporte(f, asin="ASIN-SKU-A", sku="SS-SKU-A", clicks=4, cost=2.0, ad_id="pa1")
        for f in fechas
    ]
    _ingesta(conn, fechas[0], fechas[-1], filas)
    return m


def _ciclo(conn, **interruptores: str):
    """Corre el ciclo de amazon_us al reloj real; por omision con la pausa en
    practica."""
    _config_version(
        conn,
        {
            "ads_optimizer_mode": "shadow",
            "ads_target_acos_pct_amazon_us": 30,
            **(interruptores or {"ads_producto_pausa_amazon_us": "shadow"}),
        },
    )
    res = ciclo.corre_ciclo(
        conn,
        platform="amazon_us",
        owner=OWNER,
        decided_at=dt.datetime.now(dt.UTC),
        heartbeat_cada=1,
    )
    assert res.status == "done", res.notes
    return res


def _pausas_de(conn, cycle_id: int) -> list:
    return conn.execute(
        "SELECT d.id, d.ad_entity_id, d.evaluacion_producto_id, d.window_start, d.window_end,"
        " d.inputs FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id"
        " WHERE d.cycle_id = %s AND d.kind = 'pause' AND e.kind = 'product_ad' ORDER BY d.id",
        (cycle_id,),
    ).fetchall()


def _cola(conn) -> list:
    return conn.execute(
        "SELECT q.decision_id, q.ad_entity_id, q.modo, q.estado, q.request_payload"
        " FROM apply_queue q JOIN ad_entity e ON e.id = q.ad_entity_id"
        " WHERE e.kind = 'product_ad' ORDER BY q.id"
    ).fetchall()


def _impulso_abierto(conn, pid: int, lid: int, *, ag_propio: int | None = None) -> int:
    """Impulso sin lectura final del producto: su grupo de campanas (con un ad
    group propio, o `ag_propio`) queda en v_ad_group_impulso_abierto."""
    conn.execute(
        "INSERT INTO fabrica_lote (lote, platform, tipo_producto, nombre_base, go_literal,"
        " huella, plan, modo_goal, estado) VALUES ('imp-h', 'amazon_us', 'aros', 'Base',"
        " 'go', 'h', '{}'::jsonb, 'shadow', 'applied')"
    )
    grupo = conn.execute(
        "INSERT INTO campana_grupo (platform, tipo_producto, nombre_base, lote,"
        " target_acos_pct, target_derivado_pct, fraccion, target_procedencia, go_literal,"
        " tipo) VALUES ('amazon_us', 'aros', 'Base', 'imp-h', 19.10, 19.1040, 0.5, 'm', 'go',"
        " 'impulso') RETURNING id"
    ).fetchone()[0]
    if ag_propio is None:
        camp = _entidad(conn, "amazon_us", "campaign", "imp-h-camp")
        ag = _entidad(conn, "amazon_us", "ad_group", "imp-h-ag", parent=camp)
    else:
        ag = ag_propio
        camp = conn.execute("SELECT parent_id FROM ad_entity WHERE id = %s", (ag,)).fetchone()[0]
    conn.execute(
        "INSERT INTO campana_grupo_rol (grupo_id, rol, ad_entity_id, ad_group_ad_entity_id)"
        " VALUES (%s, 'category_exact', %s, %s)",
        (grupo, camp, ag),
    )
    return conn.execute(
        "INSERT INTO impulso (platform, listing_id, product_id, grupo_id, lote, subfamilia,"
        " tope_monto, tope_moneda, presupuesto_diario, plazo_dias, lanzado_el, actor,"
        " go_literal) VALUES ('amazon_us', %s, %s, %s, 'imp-h', 'aros', 350, 'USD', 25, 14,"
        " now() - interval '3 days', 'dueno', 'go') RETURNING id",
        (lid, pid, grupo),
    ).fetchone()[0]


@_skip_db
def test_un_impulso_abierto_no_produce_nada():
    with _db("orbit_h6_impulso") as conn:
        m = _mundo_pausa(conn)
        assert [
            x.anuncio.ad_entity_id for x in p.resolver_producto(_entrada(conn, m["pid"])).pausas
        ] == [m["pa"]]
        impulso = _impulso_abierto(conn, m["pid"], m["lid"])
        entrada = _entrada(conn, m["pid"])
        assert entrada.bloqueos_pausa == ("impulso_abierto",)
        ev = p.resolver_producto(entrada)
        assert (ev.pausas, ev.motivos_pausa) == ((), ("impulso_abierto",))
        conn.execute(
            "INSERT INTO impulso_lectura (impulso_id, corte, leido_el, dias_observados,"
            " veredicto, es_final, hecho) VALUES (%s, CURRENT_DATE, now(), 14,"
            " 'clics_sin_ventas', true, 'pausado')",
            (impulso,),
        )
        assert [
            x.anuncio.ad_entity_id for x in p.resolver_producto(_entrada(conn, m["pid"])).pausas
        ] == [m["pa"]]


@_skip_db
def test_anuncio_en_ad_group_de_impulso_abierto_queda_fuera_del_alcance():
    with _db("orbit_h6_impulso_ag") as conn:
        m = _mundo_pausa(conn)
        pid_otro = conn.execute(
            "INSERT INTO product (odoo_sku, name) VALUES ('SKU-IMP', 'SKU-IMP') RETURNING id"
        ).fetchone()[0]
        lid_otro = conn.execute(
            "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
            " VALUES (%s, 'amazon_us', 'ASIN-IMP', 'SS-IMP') RETURNING id",
            (pid_otro,),
        ).fetchone()[0]
        _impulso_abierto(conn, pid_otro, lid_otro, ag_propio=m["ag"])
        entrada = _entrada(conn, m["pid"])
        assert entrada.bloqueos_pausa == ()
        assert entrada.anuncios == ()
        assert entrada.excluidos == (p.AnuncioExcluido(m["pa"], "impulso_abierto"),)


@_skip_db
def test_ciclo_en_practica_encola_la_pausa_shadow_y_nunca_la_libera():
    with _db("orbit_h6_ciclo") as conn:
        m = _mundo_pausa(conn)
        res = _ciclo(conn)
        notas = json.loads(res.notes)["productos"]
        assert (notas["modo_pausa"], notas["propuestas_pausa"], notas.get("error")) == (
            "shadow",
            1,
            None,
        )
        [(dec_id, ad, evaluacion_id, desde, hasta, inputs)] = _pausas_de(conn, res.cycle_id)
        ventana = p.ventana_pausa(dt.datetime.now(dt.UTC))
        assert (ad, desde, hasta) == (
            m["pa"],
            ventana.desde,
            ventana.hasta_exclusivo - dt.timedelta(days=1),
        )
        assert evaluacion_id is not None
        assert inputs["motor"] == "producto" and inputs["modo"] == "shadow"
        assert inputs["actividad"]["gasto"] == "60.0000" and inputs["actividad"]["clics"] == 120
        assert inputs["limites"] == {"clics_min": 100, "gasto_min": "40", "moneda": "USD"}
        assert reproduce(inputs) == ("pause", None, None)
        assert _cola(conn) == [
            (dec_id, m["pa"], "shadow", "pending_veto", {"adId": "pa1", "state": "PAUSED"})
        ]
        liberacion = apply_cola.libera_vencidos(
            conn,
            "amazon_us",
            ahora=dt.datetime.now(dt.UTC) + dt.timedelta(days=3),
            aplicador=object(),
        )
        assert (liberacion.liberadas, liberacion.aplicadas, liberacion.descartadas) == (0, 0, [])
        assert [fila[3] for fila in _cola(conn)] == ["pending_veto"]
        segundo = _ciclo(conn)
        assert _pausas_de(conn, segundo.cycle_id) == []
        assert json.loads(segundo.notes)["productos"]["anuncios_excluidos"] == {"clave_en_vuelo": 1}


@_skip_db
def test_pausa_apagada_evalua_sin_decidir():
    with _db("orbit_h6_apagada") as conn:
        _mundo_pausa(conn)
        res = _ciclo(conn, ads_producto_meta_amazon_us="shadow")
        assert json.loads(res.notes)["productos"]["modo_pausa"] == "off"
        assert _pausas_de(conn, res.cycle_id) == [] and _cola(conn) == []
        [resultado] = conn.execute(
            "SELECT resultado FROM evaluacion_producto_ciclo WHERE cycle_id = %s",
            (res.cycle_id,),
        ).fetchone()
        assert len(resultado["pausas"]) == 1


@_skip_db
def test_pausa_de_producto_es_shadow_mientras_la_config_no_la_encienda():
    with _db("orbit_h4_modo") as conn:
        m = _mundo_pausa(conn)
        _config_version(conn, {"ads_producto_pausa_amazon_us": "shadow"})

        def modo() -> str:
            return apply_cola._modo_efectivo_corte(
                conn, object(), "amazon_us", m["pa"], escalera_global="live"
            )

        assert modo() == "shadow"
        _config_version(conn, {"ads_producto_pausa_amazon_us": "live"})
        assert modo() == "live"
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'shadow'")
        assert modo() == "shadow"


@_skip_db
def test_revalida_lanza_ante_un_kind_o_una_entidad_sin_rama():
    with _db("orbit_h6_rama") as conn:
        m = _mundo_pausa(conn)

        def fila(kind: str, entidad: int) -> FilaCola:
            return FilaCola(1, kind, entidad, None, 1, {}, "released")

        ahora = dt.datetime.now(dt.UTC)
        with pytest.raises(ValueError, match="sin rama para pause sobre la entidad"):
            apply_cola._revalida(conn, object(), "amazon_us", fila("pause", m["pa"]), ahora)
        with pytest.raises(ValueError, match="sin rama para resume"):
            apply_cola._revalida(conn, object(), "amazon_us", fila("resume", m["kw"]), ahora)


# ---------------------------------------------------------------------------
# H.7: gracia de 7 dias para lo que regresaste (S17)
# ---------------------------------------------------------------------------


def _retiro(conn, pa: int, *, hace: str, modo: str = "pausa", motivo: str = "sin_pedidos") -> int:
    return conn.execute(
        "INSERT INTO anuncio_retiro (platform, motivo, modo, lote_retiro, impulso_id,"
        " ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin,"
        " reversa, retirado_el, actor) VALUES ('amazon_us', %s, %s, 'lote-h7', NULL, %s,"
        " 'pa1', 'g1', 'c1', 'SS-SKU-A', 'ASIN-SKU-A', 'state=ENABLED',"
        " now() - %s::interval, 'dueno') RETURNING id",
        (motivo, modo, pa, hace),
    ).fetchone()[0]


def _repone(conn, retiro: int, *, hace: str) -> None:
    conn.execute(
        "INSERT INTO anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, repuesto_el,"
        " actor) VALUES (%s, 'pa1', 'ENABLED', now() - %s::interval, 'dueno')",
        (retiro, hace),
    )


def _reposiciones(conn) -> list:
    return conn.execute(
        "SELECT retiro_id, actor FROM anuncio_reposicion ORDER BY retiro_id"
    ).fetchall()


@_skip_db
@pytest.mark.parametrize(("hace", "propone"), [("3 days", False), ("8 days", True)])
def test_regresado_hace_3_dias_no_se_propone_y_a_los_8_si(hace, propone):
    with _db(f"orbit_h7_regresado_{hace[0]}") as conn:
        m = _mundo_pausa(conn)
        _repone(conn, _retiro(conn, m["pa"], hace="20 days"), hace=hace)
        entrada = _entrada(conn, m["pid"])
        ev = p.resolver_producto(entrada)
        if propone:
            assert [x.anuncio.ad_entity_id for x in ev.pausas] == [m["pa"]]
        else:
            assert entrada.excluidos == (p.AnuncioExcluido(m["pa"], "regresado_por_ti"),)
            assert (ev.pausas, ev.motivos_pausa) == ((), ("sin_anuncios_elegibles",))


@_skip_db
def test_regresado_a_mano_en_amazon_no_se_propone_y_el_ciclo_cierra_su_retiro():
    with _db("orbit_h7_regresado_amazon") as conn:
        m = _mundo_pausa(conn)
        retiro = _retiro(conn, m["pa"], hace="2 days")
        entrada = _entrada(conn, m["pid"])
        assert entrada.excluidos == (p.AnuncioExcluido(m["pa"], "regresado_por_ti", retiro),)
        res = _ciclo(conn)
        assert _pausas_de(conn, res.cycle_id) == []
        assert _reposiciones(conn) == [(retiro, "reactivacion_manual_amazon")]
        notas = json.loads(res.notes)["productos"]
        assert notas["retiros_reactivados_cerrados"] == [retiro]
        assert _entrada(conn, m["pid"]).excluidos == (
            p.AnuncioExcluido(m["pa"], "regresado_por_ti"),
        )


@_skip_db
def test_regresado_cierre_como_app_decide_respeta_predicados_candado_y_tablas_temporales():
    with _db("orbit_h7_regresado_rol") as conn:
        m = _mundo_pausa(conn)
        pa_viejo = _anuncio(conn, m["ag"], m["lid"], "pa-viejo", "ENABLED")
        pa_archivo = _anuncio(conn, m["ag"], m["lid"], "pa-archivo", "ENABLED")
        pa_cerrado = _anuncio(conn, m["ag"], m["lid"], "pa-cerrado", "ENABLED")
        conn.execute(
            "UPDATE ad_entity_state SET synced_at = now() - interval '5 days'"
            " WHERE ad_entity_id = %s",
            (pa_viejo,),
        )
        viejo = _retiro(conn, pa_viejo, hace="1 day")
        archivo = _retiro(conn, pa_archivo, hace="1 day", modo="archivo")
        cerrado = _retiro(conn, pa_cerrado, hace="10 days")
        _repone(conn, cerrado, hace="4 days")
        abierto = _retiro(conn, m["pa"], hace="1 day")
        repuesto_previo = conn.execute(
            "SELECT repuesto_el FROM anuncio_reposicion WHERE retiro_id = %s", (cerrado,)
        ).fetchone()[0]

        otra = psycopg.connect(conn.info.dsn, autocommit=True)
        try:
            conn.autocommit = False
            conn.execute("SET ROLE app_decide")

            def cierra(retiro: int):
                return conn.execute("SELECT cierra_retiro_reactivado(%s)", (retiro,)).fetchone()[0]

            assert cierra(viejo) is None
            assert cierra(archivo) is None
            assert cierra(cerrado) == repuesto_previo
            conn.execute(
                "CREATE TEMP TABLE ad_entity_state (ad_entity_id bigint, status text,"
                " synced_at timestamptz)"
            )
            conn.execute(
                "INSERT INTO ad_entity_state VALUES (%s, 'ENABLED', now() + interval '1 day')",
                (pa_viejo,),
            )
            conn.execute(
                "CREATE TEMP TABLE v_ad_group_impulso_abierto (platform text,"
                " ad_group_ad_entity_id bigint, product_id bigint)"
            )
            conn.execute(
                "INSERT INTO v_ad_group_impulso_abierto VALUES ('amazon_us', %s, %s)",
                (m["ag"], m["pid"]),
            )
            assert cierra(viejo) is None
            otra.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"retiro-ad:{m['pa']}",))
            assert cierra(abierto) is None
            otra.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"retiro-ad:{m['pa']}",))
            assert cierra(abierto) is not None
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT 1 FROM anuncio_retiro LIMIT 1")
            conn.rollback()
            conn.execute("SET ROLE app_decide")
            assert cierra(abierto) is not None
            conn.commit()
        finally:
            otra.close()
            conn.autocommit = True
        conn.execute("RESET ROLE")
        assert _reposiciones(conn) == [
            (cerrado, "dueno"),
            (abierto, "reactivacion_manual_amazon"),
        ]


# ---------------------------------------------------------------------------
# H.8: simulacion de limites, solo lectura
# ---------------------------------------------------------------------------


def _goals(conn):
    campanas = [
        f[0]
        for f in conn.execute(
            "SELECT id FROM ad_entity WHERE platform = 'amazon_us' AND kind = 'campaign'"
        )
    ]
    return lee_goals(conn, "amazon_us", campanas)


def _simula(conn, limites: p.LimitesPausa) -> p.SimulacionPausa:
    return productos_io.simula_pausa(
        conn,
        "amazon_us",
        ahora=dt.datetime.now(dt.UTC),
        settings={},
        goals=_goals(conn),
        limites=limites,
    )


def _usd(clics: int, gasto: str) -> p.LimitesPausa:
    return p.LimitesPausa(clics, p.Dinero(Decimal(gasto), "USD"))


def _producto_b(conn, m) -> int:
    """SKU-B, en su propio ad group, gasto 0.5 USD con 1 clic cada dia de la
    ventana sin vender: 30 clics y 15 USD, bajo los limites por omision."""
    ag = _entidad(conn, "amazon_us", "ad_group", "g-b", parent=m["camp"])
    _estado(conn, ag, synced_at=dt.datetime.now(dt.UTC))
    pid = conn.execute(
        "INSERT INTO product (odoo_sku, name) VALUES ('SKU-B', 'SKU-B') RETURNING id"
    ).fetchone()[0]
    lid = conn.execute(
        "INSERT INTO listing (product_id, platform, external_id, seller_sku)"
        " VALUES (%s, 'amazon_us', 'ASIN-SKU-B', 'SS-SKU-B') RETURNING id",
        (pid,),
    ).fetchone()[0]
    _anuncio(conn, ag, lid, "pa-b", "ENABLED")
    conn.execute("UPDATE ad_entity SET first_seen_at = now() - interval '200 days'")
    fechas = _ventana()
    filas = [
        _fila_reporte(f, asin="ASIN-SKU-B", sku="SS-SKU-B", clicks=1, cost=0.5, ad_id="pa-b")
        for f in fechas
    ]
    _ingesta(conn, fechas[0], fechas[-1], filas)
    return pid


def _ventana_anterior() -> list[dt.date]:
    v = p.ventana_pausa(dt.datetime.now(dt.UTC) - dt.timedelta(days=30))
    return [v.desde + dt.timedelta(days=n) for n in range((v.hasta_exclusivo - v.desde).days)]


def _historia_anterior(conn, *, asin: str, sku: str, ad: str, observado: str = "31 days"):
    """[D-90, D-60) observado hace `observado` por una corrida integra de
    entonces: 4 clics y 2 USD por dia, sin ventas."""
    run = conn.execute(
        "INSERT INTO ingest_run (source, started_at, finished_at, ok) VALUES"
        " ('amazon_ads_products_v3', now() - %s::interval, now() - %s::interval, true)"
        " RETURNING id",
        (observado, observado),
    ).fetchone()[0]
    fechas = _ventana_anterior()
    conn.execute(
        "INSERT INTO ads_report_result (ingest_run_id, profile_id, platform, report_name,"
        " source_report_id, status, fecha_desde, fecha_hasta, cobertura_integra)"
        " VALUES (%s, 301, 'amazon_us', 'productos', %s, 'written', %s, %s, true)",
        (run, f"hist-{run}", fechas[0], fechas[-1]),
    )
    for f in fechas:
        _observacion(conn, run, f, asin=asin, sku=sku, ad=ad, observado=observado)


def _observacion(conn, run, fecha, *, asin, sku, ad, observado, ventas=0, clicks=4, cost="2"):
    conn.execute(
        "INSERT INTO ads_product_metric_observation (platform, advertised_asin,"
        " advertised_sku, metric_date, observed_at, metric_currency, clicks, cost,"
        " purchases30d, sales30d, purchases_same_sku30d, attributed_sales_same_sku_30d,"
        " ad_ids, ingest_run_id) VALUES ('amazon_us', %s, %s, %s, now() - %s::interval,"
        " 'USD', %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            asin,
            sku,
            fecha,
            observado,
            clicks,
            Decimal(cost),
            1 if ventas else 0,
            ventas,
            1 if ventas else 0,
            ventas,
            Jsonb([ad]),
            run,
        ),
    )


@_skip_db
def test_simula_bajar_los_limites_agrega_productos():
    with _db("orbit_h8_simula_limites") as conn:
        m = _mundo_pausa(conn)
        b = _producto_b(conn, m)
        vigentes = _simula(conn, _usd(100, "40"))
        assert [x.producto.product_id for x in vigentes.productos] == [m["pid"]]
        assert (vigentes.gasto_total, vigentes.anuncios_total) == (
            p.Dinero(Decimal("60.0000"), "USD"),
            1,
        )
        bajos = _simula(conn, _usd(20, "8"))
        assert [x.producto.product_id for x in bajos.productos] == [m["pid"], b]
        assert bajos.gasto_total == p.Dinero(Decimal("75.0000"), "USD")


@_skip_db
def test_simula_un_producto_que_vendio_despues_aparece_en_ventas_perdidas():
    with _db("orbit_h8_simula_perdidas") as conn:
        m = _mundo_pausa(conn)
        _historia_anterior(conn, asin="ASIN-SKU-A", sku="SS-SKU-A", ad="pa1")
        sin_ventas = _simula(conn, _usd(100, "40"))
        assert sin_ventas.productos_anteriores == (m["pid"],)
        assert sin_ventas.gasto_evitado == p.Dinero(Decimal("60.0000"), "USD")
        assert sin_ventas.ventas_perdidas == p.Dinero(Decimal("0"), "USD")
        run = conn.execute(
            "INSERT INTO ingest_run (source, ok, finished_at) VALUES ('test', true, now())"
            " RETURNING id"
        ).fetchone()[0]
        _observacion(
            conn,
            run,
            _ventana()[5],
            asin="ASIN-SKU-A",
            sku="SS-SKU-A",
            ad="pa1",
            observado="0 seconds",
            ventas=45,
        )
        vendio = _simula(conn, _usd(100, "40"))
        assert vendio.productos_anteriores == (m["pid"],)
        assert vendio.ventas_perdidas == p.Dinero(Decimal("45.0000"), "USD")
        assert vendio.productos == ()


@_skip_db
def test_simula_una_observacion_posterior_no_cambia_la_ventana_anterior():
    with _db("orbit_h8_simula_futuro") as conn:
        m = _mundo_pausa(conn)
        _historia_anterior(conn, asin="ASIN-SKU-A", sku="SS-SKU-A", ad="pa1")
        run = conn.execute(
            "INSERT INTO ingest_run (source, ok, finished_at) VALUES ('test', true, now())"
            " RETURNING id"
        ).fetchone()[0]
        _observacion(
            conn,
            run,
            _ventana_anterior()[3],
            asin="ASIN-SKU-A",
            sku="SS-SKU-A",
            ad="pa1",
            observado="1 day",
            ventas=80,
        )
        assert _simula(conn, _usd(100, "40")).productos_anteriores == (m["pid"],)
        antes = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        viendo_el_futuro = productos_io.lee_entradas_productos(
            conn,
            "amazon_us",
            decidido_el=antes,
            settings={},
            goals=_goals(conn),
            observadas_hasta=dt.datetime.now(dt.UTC),
        )
        assert p.motivos_economicos_pausa(viendo_el_futuro[0]) == ("vendio_en_ventana",)


@_skip_db
def test_simula_un_anuncio_con_practica_abierta_aparece_y_la_cola_no_cambia():
    with _db("orbit_h8_simula_practica") as conn:
        m = _mundo_pausa(conn)
        _ciclo(conn)
        cola = _cola(conn)
        assert [fila[2:4] for fila in cola] == [("shadow", "pending_veto")]
        assert _entrada(conn, m["pid"]).excluidos == (p.AnuncioExcluido(m["pa"], "clave_en_vuelo"),)
        simulacion = _simula(conn, _usd(100, "40"))
        assert [(x.producto.product_id, x.anuncios) for x in simulacion.productos] == [
            (m["pid"], 1)
        ]
        assert _cola(conn) == cola


def test_simula_sin_historia_no_es_cero():
    entrada = _entrada_mx(_actividad())
    sim = p.simula_pausa(
        [entrada],
        [],
        p.limites_pausa_desde_settings({}, "amazon_mx"),
        decidido_el=DECIDIDO,
        primer_dato=DECIDIDO.date() - dt.timedelta(days=80),
    )
    assert sim.gasto_evitado == sim.ventas_perdidas == p.SinDato(("sin_historia",))
    assert [(x.producto.product_id, x.anuncios) for x in sim.productos] == [(77, 2)]


@_skip_db
def test_simula_el_retroceso_solo_cuenta_productos_con_anuncios_al_alcance():
    """h_f09ef83e: el retroceso corre la regla completa de H.2. Con el goal en
    shadow el producto no tiene anuncios elegibles en la ventana anterior:
    no cuenta como pausado ni suma gasto evitado."""
    with _db("orbit_h8_simula_alcance") as conn:
        m = _mundo_pausa(conn)
        _historia_anterior(conn, asin="ASIN-SKU-A", sku="SS-SKU-A", ad="pa1")
        assert _simula(conn, _usd(100, "40")).productos_anteriores == (m["pid"],)
        conn.execute("UPDATE ads_optimizer_goal SET mode = 'shadow'")
        sim = _simula(conn, _usd(100, "40"))
        assert sim.productos == ()
        assert sim.productos_anteriores == ()
        assert sim.gasto_evitado == p.Dinero(Decimal("0"), "USD")
        assert sim.ventas_perdidas == p.Dinero(Decimal("0"), "USD")
