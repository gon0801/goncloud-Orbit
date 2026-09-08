"""Nucleo PURO de la fabrica (FABRICA 01 tarea 5): target del grupo y su
clamp, montos por moneda, nombres, semillas, huella, payloads y acks."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from app import fabrica_plan as fp
from app.fabrica_bids import Expresion, Recomendacion
from app.optimizer import goals as g
from app.optimizer import hygiene

FECHA = dt.date(2026, 9, 5)


def _producto(pid=1, margen="38.20", sku="SS-1", asin="B0AAAAAAAA"):
    return fp.ProductoGrupo(
        product_id=pid,
        odoo_sku=f"ODOO-{pid}",
        listing_id=10 + pid,
        asin=asin,
        seller_sku=sku,
        margen_neto_pct=Decimal(margen),
    )


def _parametros(bid="5.00", budget="120"):
    return {
        rol: fp.ParametrosRol(rol=rol, budget=Decimal(budget), bid=Decimal(bid))
        for rol in fp.ROLES_ORDEN_CREACION
    }


def _plan(**cambios):
    base = dict(
        platform="amazon_mx",
        tipo_producto="collar_perro",
        nombre_base="Collar reflectante",
        fecha=FECHA,
        moneda="MXN",
        modo="shadow",
        productos=(_producto(),),
        parametros=_parametros(),
        target=fp.target_del_grupo([Decimal("38.20")], Decimal("0.5")),
        semillas=fp.Semillas(
            keywords=("collar perro",), asins=("B0BBBBBBBB",), negativos=("gato",), exact=()
        ),
    )
    base.update(cambios)
    return fp.PlanGrupo(**base)


# --- target ----------------------------------------------------------------


def test_target_es_fraccion_por_margen_minimo_con_procedencia():
    r = fp.target_del_grupo([Decimal("38.20"), Decimal("52.00")], Decimal("0.5"))
    assert r.minimo == Decimal("38.20")
    assert r.derivado == Decimal("19.100")  # crudo, NUMERIC(10,4)
    assert r.aplicado == Decimal("19.10") and r.aplicado.as_tuple().exponent == -2
    assert "38.20" in r.procedencia and "0.5" in r.procedencia and "clamp" not in r.procedencia


def test_target_aplicado_cuantizado_a_numeric_6_2_una_sola_vez():
    """0.5 x 38.21 = 19.105 -> 19.10 (HALF_EVEN a 2 decimales, la precision de
    NUMERIC(6,2) de campana_grupo.target_acos_pct y ads_optimizer_goal): la
    huella, el JSON, la DB y el goal ven el MISMO numero; el derivado crudo
    queda y la procedencia declara el redondeo."""
    r = fp.target_del_grupo([Decimal("38.21")], Decimal("0.5"))
    assert r.derivado == Decimal("19.105")
    assert r.aplicado == Decimal("19.10") and str(r.aplicado) == "19.10"
    assert "clamp" not in r.procedencia and "redondeo NUMERIC(6,2)" in r.procedencia


def test_target_clampea_a_la_banda_del_motor_y_lo_declara():
    bajo = fp.target_del_grupo([Decimal("12")], Decimal("0.5"))  # 6 -> 10
    alto = fp.target_del_grupo([Decimal("100")], Decimal("1"))  # 100 -> 45
    assert bajo.aplicado == g.MARGEN_BANDA_MIN and bajo.derivado == Decimal("6.0")
    assert alto.aplicado == g.MARGEN_BANDA_MAX
    assert "clamp" in bajo.procedencia and "clamp" in alto.procedencia


def test_target_sin_fraccion_o_sin_margenes_aborta():
    with pytest.raises(fp.PlanInvalido, match="fraccion"):
        fp.target_del_grupo([Decimal("30")], None)
    with pytest.raises(ValueError):  # invalida = config corrupta (misma ley que el motor)
        fp.target_del_grupo([Decimal("30")], Decimal("1.5"))
    with pytest.raises(fp.PlanInvalido, match="margen"):
        fp.target_del_grupo([], Decimal("0.5"))
    with pytest.raises(fp.PlanInvalido, match="margen"):
        fp.target_del_grupo([Decimal("30"), None], Decimal("0.5"))


# --- montos ---------------------------------------------------------------


def test_valida_parametros_usa_piso_y_techo_de_la_moneda():
    fp.valida_parametros(_parametros(bid="45.00", budget="150"), "MXN")  # techo MXN inclusivo
    with pytest.raises(fp.PlanInvalido, match="bid"):
        fp.valida_parametros(_parametros(bid="45.01"), "MXN")
    with pytest.raises(fp.PlanInvalido, match="bid"):
        fp.valida_parametros(_parametros(bid="0.50"), "MXN")  # bajo el piso 1.00
    fp.valida_parametros(_parametros(bid="2.50", budget="10"), "USD")
    with pytest.raises(fp.PlanInvalido, match="budget"):
        fp.valida_parametros(_parametros(bid="2.00", budget="1.50"), "USD")  # budget < bid
    with pytest.raises(fp.PlanInvalido, match="moneda"):
        fp.valida_parametros(_parametros(), "EUR")
    faltante = _parametros()
    del faltante["auto_discovery"]
    with pytest.raises(fp.PlanInvalido, match="auto_discovery"):
        fp.valida_parametros(faltante, "MXN")


def test_moneda_por_plataforma_iguala_la_capa_http():
    from app.ads.write import PLATAFORMA_MONEDA

    assert dict(PLATAFORMA_MONEDA) == fp.MONEDA_POR_PLATAFORMA


def test_monto_wire_cuantiza_a_dos_decimales_como_el_write_client():
    assert fp.monto_wire(Decimal("4.5")) == 4.5
    assert fp.monto_wire(Decimal("4.005")) == 4.0  # HALF_EVEN
    with pytest.raises(TypeError):
        fp.monto_wire(4.5)


# --- nombres y tipo_producto ---------------------------------------------


def test_tipo_producto_es_etiqueta_ascii_minuscula():
    assert fp.valida_tipo_producto("collar_perro") == "collar_perro"
    for malo in ("Collar", "collar perro", "", "collar-perro", "ñu"):
        with pytest.raises(fp.PlanInvalido):
            fp.valida_tipo_producto(malo)


def test_nombres_llevan_tipo_base_rol_y_fecha():
    assert (
        fp.nombre_campana("collar_perro", "Collar reflectante", "category_exact", FECHA)
        == "collar_perro | Collar reflectante | category_exact | 2026-09-05"
    )
    assert fp.nombre_ad_group(
        "collar_perro", "Collar reflectante", "category_exact", FECHA
    ).endswith("| ag")


# --- semillas -------------------------------------------------------------


def _termino(texto, orders=1, cost="10", revenue="100", asin_like=False):
    return fp.TerminoProducto(
        texto=texto,
        is_asin_like=asin_like,
        orders=orders,
        cost=Decimal(cost),
        revenue=Decimal(revenue),
    )


def test_semillas_reparten_biblioteca_y_terminos_por_rol():
    s = fp.semillas_desde_terminos(
        terminos=[
            _termino("collar perro", orders=1),
            _termino("b0cccccccc", orders=2, asin_like=True),
            _termino(
                "collar led", orders=3, cost="20", revenue="100"
            ),  # acos 20 <= min(35, 19.1)? no
            _termino("collar noche", orders=2, cost="10", revenue="100"),  # acos 10: exact
            _termino("sin ventas", orders=0),
        ],
        biblioteca_keywords=["collar reflectante", "B0DDDDDDDD", "collar perro"],
        biblioteca_negativos=["gato", "gato"],
        target_acos_pct=Decimal("19.10"),
    )
    assert s.keywords == ("collar led", "collar noche", "collar perro", "collar reflectante")
    assert s.asins == ("B0CCCCCCCC", "B0DDDDDDDD")
    assert s.negativos == ("gato",)
    assert s.exact == ("collar noche",)


def test_negativos_de_biblioteca_excluyen_entradas_asin():
    """Regresion D-GLM-4-5-6 (revision PR 174): la biblioteca de negativos
    puede traer ASINs mezclados y el contrato (spec §6) es negativos = SOLO
    keywords. (a) el ASIN no cae en semillas.negativos, (b) ningun payload de
    negative_keyword del rol auto_discovery lleva texto ASIN-like."""
    s = fp.semillas_desde_terminos(
        terminos=[_termino("collar perro")],
        biblioteca_keywords=["collar reflectante"],
        biblioteca_negativos=["B0AAAAAAAA", "gato"],
        target_acos_pct=Decimal("19.10"),
    )
    assert "b0aaaaaaaa" not in s.negativos
    assert s.negativos == ("gato",)
    plan = _plan(semillas=s)
    negativos = [
        paso.payload["keywordText"]
        for paso in fp.pasos_del_rol(plan, "auto_discovery")
        if paso.recurso == "negative_keyword"
    ]
    assert "gato" in negativos
    assert not [texto for texto in negativos if fp.PATRON_ASIN.match(texto)]


def test_semilla_exact_usa_el_criterio_harvest_del_motor():
    """orders >= HARVEST_ORDERS_MIN y cost*100 <= min(35, target)*revenue
    (comparacion cruzada SIN dividir, hygiene.py camino (6)): mismas
    constantes del motor, no copias."""
    t = _termino("x", orders=hygiene.HARVEST_ORDERS_MIN, cost="35", revenue="100")
    assert fp.semillas_desde_terminos([t], [], [], Decimal("40")).exact == ("x",)
    assert fp.semillas_desde_terminos([t], [], [], Decimal("30")).exact == ()
    t2 = _termino("y", orders=hygiene.HARVEST_ORDERS_MIN - 1, cost="1", revenue="100")
    assert fp.semillas_desde_terminos([t2], [], [], Decimal("40")).exact == ()
    t3 = _termino("z", orders=5, cost="5", revenue="0")  # revenue 0: ACoS no evaluable
    assert fp.semillas_desde_terminos([t3], [], [], Decimal("40")).exact == ()


def test_semilla_exact_se_evalua_sobre_la_ventana_de_cortes():
    """Spec §6 (ventana y madurez ya selladas, regla 6): la excepcion exact se
    evalua sobre `terminos_exact` (ventana de CORTES del motor), no sobre la
    ventana [D-105, D-15) del margen; keywords siguen saliendo de `terminos`."""
    t_margen = _termino("collar noche", orders=1, cost="90", revenue="100")
    t_cortes = _termino("collar noche", orders=hygiene.HARVEST_ORDERS_MIN, cost="10", revenue="100")
    s = fp.semillas_desde_terminos([t_margen], [], [], Decimal("19.10"), terminos_exact=[t_cortes])
    assert s.exact == ("collar noche",)  # cumple harvest en la ventana de cortes
    assert s.keywords == ("collar noche",)  # la keyword sale de la ventana del margen


def test_ventana_cortes_es_la_del_motor():
    """Regla 2 (un numero, una fuente): la ventana de los candidatos a exact
    ES la ventana de cortes del motor: 30 dias inclusive (DIAS_VENTANA) que
    terminan a mas tardar hoy - 10d (DIAS_MADUREZ_CORTES, regla 6)."""
    from app.optimizer import windows

    desde, hasta = fp.VENTANA_CORTES_DIAS
    assert hasta == windows.DIAS_MADUREZ_CORTES - 1  # "< hoy-9" == "<= hoy-10"
    assert desde - hasta == windows.DIAS_VENTANA  # [D-39, D-10] = 30 fechas


# --- huella y json --------------------------------------------------------


def test_huella_cambia_con_bids_productos_o_semillas():
    base = _plan()
    assert fp.huella_plan(base) == fp.huella_plan(_plan())
    assert fp.huella_plan(base) != fp.huella_plan(_plan(parametros=_parametros(bid="5.50")))
    assert fp.huella_plan(base) != fp.huella_plan(_plan(productos=(_producto(), _producto(2))))
    assert fp.huella_plan(base) != fp.huella_plan(_plan(modo="live"))
    canonico = json.dumps(fp.plan_como_json(base), sort_keys=True, separators=(",", ":"))
    assert fp.huella_plan(base) == hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def test_bids_amazon_van_por_objetivo_y_quedan_en_plan_firmado():
    recomendaciones = (
        Recomendacion(
            Expresion("KEYWORD_EXACT_MATCH", "kw exact"),
            Decimal("4.00"),
            Decimal("5.00"),
            Decimal("6.00"),
        ),
    )
    parametros = _parametros()
    parametros["category_exact"] = fp.ParametrosRol(
        "category_exact",
        Decimal("120"),
        Decimal("5.00"),
        fuente_bid="amazon_v4",
        recomendaciones=recomendaciones,
    )
    plan = _plan(
        parametros=parametros,
        semillas=fp.Semillas(("kw phrase",), (), (), ("kw exact",)),
    )

    pasos = fp.pasos_del_rol(plan, "category_exact")
    keyword = next(paso for paso in pasos if paso.recurso == "keyword")
    assert keyword.payload["bid"] == 5.0
    serializado = fp.plan_como_json(plan)
    assert serializado["parametros"]["category_exact"]["fuente_bid"] == "amazon_v4"
    assert serializado["parametros"]["category_exact"]["recomendaciones"] == [
        {
            "tipo": "KEYWORD_EXACT_MATCH",
            "valor": "kw exact",
            "minimo": "4.00",
            "sugerido": "5.00",
            "maximo": "6.00",
        }
    ]
    assert fp.plan_desde_json(serializado).parametros["category_exact"].recomendaciones == (
        recomendaciones[0],
    )


def test_plan_amazon_aborta_si_falta_bid_de_una_keyword():
    parametros = _parametros()
    parametros["category_exact"] = fp.ParametrosRol(
        "category_exact",
        Decimal("120"),
        Decimal("5.00"),
        fuente_bid="amazon_v4",
        recomendaciones=(),
    )
    plan = _plan(
        parametros=parametros,
        semillas=fp.Semillas((), (), (), ("sin sugerencia",)),
    )
    with pytest.raises(fp.PlanInvalido, match="sin recomendaciones"):
        fp.pasos_del_rol(plan, "category_exact")


def test_plan_v1_rechaza_recomendaciones_de_semillas_anteriores_antes_de_pasos():
    parametros = _parametros()
    parametros["product_targeting"] = fp.ParametrosRol(
        "product_targeting",
        Decimal("120"),
        Decimal("5.00"),
        fuente_bid="amazon_v4",
        recomendaciones=(
            Recomendacion(
                Expresion("PAT_ASIN", "B0BBBBBBBB"),
                Decimal("4.00"),
                Decimal("5.00"),
                Decimal("6.00"),
            ),
        ),
    )
    plan = _plan(
        parametros=parametros,
        semillas=fp.Semillas((), ("B0BBBBBBBB", "B0CCCCCCCC"), (), ()),
    )

    with pytest.raises(fp.PlanInvalido, match="semillas vigentes"):
        fp.pasos_del_rol(plan, "category_exact")


def test_expresiones_bid_cubren_keywords_productos_y_cuatro_temas_auto():
    semillas = fp.Semillas(("kw",), ("B0BBBBBBBB",), (), ("exacta",))
    assert fp.expresiones_bid(semillas, "category_exact") == (
        Expresion("KEYWORD_EXACT_MATCH", "exacta"),
    )
    assert fp.expresiones_bid(semillas, "category_phrase") == (
        Expresion("KEYWORD_PHRASE_MATCH", "kw"),
    )
    assert fp.expresiones_bid(semillas, "category_broad") == (
        Expresion("KEYWORD_BROAD_MATCH", "kw"),
    )
    assert fp.expresiones_bid(semillas, "product_targeting") == (
        Expresion("PAT_ASIN", "B0BBBBBBBB"),
    )
    assert fp.expresiones_bid(semillas, "auto_discovery") == tuple(
        Expresion(tipo) for tipo in ("CLOSE_MATCH", "LOOSE_MATCH", "SUBSTITUTES", "COMPLEMENTS")
    )


def test_bid_amazon_de_rol_debe_coincidir_con_mediana_firmada():
    parametros = _parametros()
    parametros["category_exact"] = fp.ParametrosRol(
        "category_exact",
        Decimal("120"),
        Decimal("1.00"),
        fuente_bid="amazon_v4",
        recomendaciones=(
            Recomendacion(
                Expresion("KEYWORD_EXACT_MATCH", "arras"),
                Decimal("8.00"),
                Decimal("9.00"),
                Decimal("10.00"),
            ),
        ),
    )
    with pytest.raises(fp.PlanInvalido, match="mediana"):
        fp.valida_parametros(parametros, "MXN")


def test_budget_amazon_cubre_cada_bid_de_objetivo():
    parametros = _parametros()
    parametros["category_exact"] = fp.ParametrosRol(
        "category_exact",
        Decimal("6.00"),
        Decimal("5.00"),
        fuente_bid="amazon_v4",
        recomendaciones=(
            Recomendacion(
                Expresion("KEYWORD_EXACT_MATCH", "bajo"),
                Decimal("1"),
                Decimal("2"),
                Decimal("3"),
            ),
            Recomendacion(
                Expresion("KEYWORD_EXACT_MATCH", "alto"),
                Decimal("7"),
                Decimal("8"),
                Decimal("9"),
            ),
        ),
    )
    with pytest.raises(fp.PlanInvalido, match="objetivo"):
        fp.valida_parametros(parametros, "MXN")


def test_plan_como_json_lleva_dinero_como_string():
    j = fp.plan_como_json(_plan())
    assert j["target_acos_pct"] == "19.10"
    assert j["parametros"]["category_exact"]["bid"] == "5.00"
    assert j["productos"][0]["margen_neto_pct"] == "38.20"


def _plan_v2(*, objetivo="25.00", orden=(22, 11)):
    publicaciones = {
        11: fp.PublicacionGrupoV2(11, 1, "B0AAAAAAAA", "SKU-A", "amazon_mx", None),
        22: fp.PublicacionGrupoV2(22, 1, "B0BBBBBBBB", "SKU-B", "amazon_mx", Decimal("18.50")),
    }
    return fp.PlanGrupoV2(
        "amazon_mx",
        "collar_perro",
        "Collar reflectante",
        FECHA,
        "MXN",
        "shadow",
        tuple(publicaciones[listing_id] for listing_id in orden),
        _parametros(),
        fp.ObjetivoPlanV2("manual_lanzamiento", Decimal(objetivo), "confirmado por el dueno"),
        fp.Semillas(keywords=(), asins=(), negativos=(), exact=()),
    )


def test_plan_v2_acepta_margen_nulo_y_varios_listings_del_mismo_producto():
    plan = _plan_v2()
    serializado = fp.plan_v2_como_json(plan)
    assert serializado["schema_version"] == 2
    assert serializado["publicaciones"][0]["margen_neto_pct"] is None
    assert [p["product_id"] for p in serializado["publicaciones"]] == [1, 1]
    assert [p.listing_id for p in fp.plan_v2_desde_json(serializado).publicaciones] == [11, 22]


def test_plan_v2_rechaza_recomendaciones_de_semillas_anteriores():
    plan = _plan_v2()
    parametros = dict(plan.parametros)
    parametros["product_targeting"] = fp.ParametrosRol(
        "product_targeting",
        Decimal("120"),
        Decimal("5.00"),
        fuente_bid="amazon_v4",
        recomendaciones=(
            Recomendacion(
                Expresion("PAT_ASIN", "B0AAAAAAAA"),
                Decimal("4.00"),
                Decimal("5.00"),
                Decimal("6.00"),
            ),
        ),
    )
    obsoleto = replace(
        plan,
        parametros=parametros,
        semillas=fp.Semillas((), ("B0AAAAAAAA", "B0BBBBBBBB"), (), ()),
    )

    with pytest.raises(fp.PlanInvalido, match="semillas vigentes"):
        fp.plan_v2_como_json(obsoleto)


def test_plan_v2_rechaza_asin_ausente_al_deserializar():
    serializado = fp.plan_v2_como_json(_plan_v2())
    serializado["publicaciones"][0]["asin"] = ""
    with pytest.raises(fp.PlanInvalido, match="ASIN"):
        fp.plan_v2_desde_json(serializado)


def test_huella_v2_no_depende_del_orden_y_cambia_con_el_objetivo():
    base = _plan_v2(orden=(22, 11))
    assert fp.huella_plan_v2(base) == fp.huella_plan_v2(_plan_v2(orden=(11, 22)))
    assert fp.huella_plan_v2(base) != fp.huella_plan_v2(_plan_v2(objetivo="26.00"))


def test_pasos_v2_crean_un_product_ad_por_publicacion():
    pasos = fp.pasos_del_rol(_plan_v2(), "category_exact")
    anuncios = [paso for paso in pasos if paso.recurso == "product_ad"]
    assert [paso.payload["sku"] for paso in anuncios] == ["SKU-A", "SKU-B"]


def test_configuracion_de_creacion_v2_es_fail_closed_y_v1_por_ausencia():
    assert fp.version_creacion_desde_settings({}) == "v1"
    assert fp.version_creacion_desde_settings({"fabrica.creacion": "v2"}) == "v2"
    assert fp.version_creacion_desde_settings({"fabrica.creacion": "invalida"}) == "v1"


@pytest.mark.parametrize("settings", [None, [], "v2", 1])
def test_configuracion_de_creacion_no_objeto_es_fail_closed_a_v1(settings):
    assert fp.version_creacion_desde_settings(settings) == "v1"


def test_plan_json_ida_y_vuelta():
    """fabrica_lote.plan -> PlanGrupo -> misma huella (lo que --registrar
    reconstruye es EXACTAMENTE lo autorizado)."""
    plan = _plan()
    assert fp.huella_plan(fp.plan_desde_json(fp.plan_como_json(plan))) == fp.huella_plan(plan)


# --- pasos y payloads -----------------------------------------------------


def test_pasos_de_la_exact_son_campana_ad_group_product_ads_y_semillas_exact():
    plan = _plan(semillas=fp.Semillas(("kw",), ("B0X",), ("neg",), ("hv",)))
    pasos = fp.pasos_del_rol(plan, "category_exact")
    assert [p.recurso for p in pasos] == ["campaign", "ad_group", "product_ad", "keyword"]
    assert pasos[0].path == "/sp/campaigns" and pasos[0].payload["targetingType"] == "MANUAL"
    assert pasos[0].payload["budget"] == {"budgetType": "DAILY", "budget": 120.0}
    assert pasos[0].payload["state"] == "ENABLED" and pasos[0].payload["startDate"] == "2026-09-05"
    assert pasos[1].payload["defaultBid"] == 5.0
    assert pasos[2].payload["sku"] == "SS-1" and pasos[2].payload["state"] == "ENABLED"
    assert pasos[3].payload == {
        "keywordText": "hv",
        "matchType": "EXACT",
        "state": "ENABLED",
        "bid": 5.0,
    }


def test_pasos_por_rol_semillas_correctas():
    plan = _plan(semillas=fp.Semillas(("kw",), ("B0X",), ("neg",), ("hv",)))
    phrase = fp.pasos_del_rol(plan, "category_phrase")
    assert phrase[-1].payload["matchType"] == "PHRASE" and phrase[-1].payload["keywordText"] == "kw"
    broad = fp.pasos_del_rol(plan, "category_broad")
    assert broad[-1].payload["matchType"] == "BROAD"
    prod = fp.pasos_del_rol(plan, "product_targeting")
    assert prod[-1].path == "/sp/targets"
    assert prod[-1].payload["expression"] == [{"type": "ASIN_SAME_AS", "value": "B0X"}]
    assert prod[-1].payload["expressionType"] == "MANUAL"
    auto = fp.pasos_del_rol(plan, "auto_discovery")
    assert auto[0].payload["targetingType"] == "AUTO"
    assert auto[-1].path == "/sp/negativeKeywords"
    assert auto[-1].payload["matchType"] == "NEGATIVE_EXACT"
    assert "bid" not in auto[-1].payload


def test_pasos_sin_semillas_son_solo_estructura():
    plan = _plan(semillas=fp.Semillas((), (), (), ()))
    assert [p.recurso for p in fp.pasos_del_rol(plan, "category_phrase")] == [
        "campaign",
        "ad_group",
        "product_ad",
    ]


def test_vendors_y_envolturas_por_path():
    assert fp.VENDOR_POR_PATH["/sp/campaigns"] == "application/vnd.spcampaign.v3+json"
    assert fp.VENDOR_POR_PATH["/sp/targets"] == "application/vnd.sptargetingclause.v3+json"
    assert fp.ENVOLTURA_POR_PATH["/sp/targets"] == "targetingClauses"
    assert fp.CLAVE_ID_POR_PATH["/sp/productAds"] == "adId"
    assert fp.LIST_POR_PATH["/sp/adGroups"] == "/sp/adGroups/list"
    assert fp.FILTRO_ID_POR_LIST["/sp/negativeKeywords/list"] == "negativeKeywordIdFilter"
    assert set(fp.PATH_CREATE.values()) == set(fp.VENDOR_POR_PATH) == set(fp.ENVOLTURA_POR_PATH)


# --- acks -----------------------------------------------------------------


def test_id_creado_lee_success_anidado_o_plano():
    plano = {
        "status": 207,
        "cuerpo": {"campaigns": {"success": [{"index": 0, "campaignId": "c1"}]}},
    }
    anidado = {"status": 207, "cuerpo": {"keywords": {"success": [{"keyword": {"keywordId": 9}}]}}}
    assert fp.id_creado(plano, "campaignId") == "c1"
    assert fp.id_creado(anidado, "keywordId") == "9"
    assert (
        fp.id_creado({"status": 207, "cuerpo": {"keywords": {"success": []}}}, "keywordId") is None
    )
    assert fp.id_creado({"status": 500, "cuerpo": {}}, "keywordId") is None


def test_ack_ok_exige_2xx_sin_errores_207():
    assert fp.ack_ok({"status": 207, "cuerpo": {"campaigns": {"success": [{}], "error": []}}})
    assert not fp.ack_ok({"status": 207, "cuerpo": {"campaigns": {"error": [{"index": 0}]}}})
    assert not fp.ack_ok({"status": 400, "cuerpo": {}})
    assert fp.errores_207({"status": 207, "cuerpo": "no json"}) == [{"no_json": None}]


def test_lineas_dry_run_declaran_semillas_cero():
    lineas = fp.lineas_dry_run(_plan(semillas=fp.Semillas((), (), (), ())))
    assert any("category_phrase" in linea and "semillas=0" in linea for linea in lineas)
    assert any("target=19.10" in linea for linea in lineas)


# --- acoplamiento con la migracion 0018 (regla 2: un numero, una fuente) --


def test_dias_minimos_y_arranque_de_ventana_pineados_contra_el_sql():
    """Regla 2 (un numero, una fuente): la constante del nucleo y el guard
    `dias_con_venta < N` de v_margen_producto son el MISMO numero, y el
    arranque fijo de la ventana es la MISMA fecha (decision del dueno,
    tarea 1)."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1] / "migrations" / "0018_fabrica_campanas.sql"
    ).read_text(encoding="utf-8")
    assert fp.MARGEN_DIAS_MIN_PRODUCTO == 30
    assert fp.MARGEN_VENTANA_DESDE.isoformat() == "2026-02-20"
    assert f"a.dias_con_venta < {fp.MARGEN_DIAS_MIN_PRODUCTO} THEN NULL" in sql
    assert (
        f"SELECT DATE '{fp.MARGEN_VENTANA_DESDE.isoformat()}' AS desde,"
        " (now() AT TIME ZONE 'UTC')::date - 15 AS hasta"
    ) in sql


def test_cobertura_minima_pineada_contra_el_sql():
    """Mismo trato que MARGEN_DIAS_MIN (regla 2): el guard `cobertura < X` de
    v_margen_producto y MARGEN_COBERTURA_MIN del motor son el MISMO numero."""
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[1] / "migrations" / "0018_fabrica_campanas.sql"
    assert f"< {g.MARGEN_COBERTURA_MIN} THEN NULL" in ruta.read_text(encoding="utf-8")
