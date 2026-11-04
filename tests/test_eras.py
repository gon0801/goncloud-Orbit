"""Historia congelada de bandas_v1 (BIDS 02 M.3, `app/optimizer/eras.py`).

`decide_bid_era_bandas` re-decide UNA decision vieja (sin inputs.politica)
exactamente como el vivo v1 hasta el corte: bandas de ventana, regla A' de
cero ventas y pisos historicos REPLAY_*. Solo lo importa replay.py (candado
en tests/test_architecture.py). Las pruebas de bandas de
tests/test_optimizer_bid.py viven aqui; las de pausa se quedaron ahi.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.optimizer import bid as b
from app.optimizer import eras
from app.optimizer import windows as w
from app.optimizer.cortes import LEGACY_PAUSE

INICIO_BIDS = dt.date(2026, 7, 18)
FIN_BIDS = dt.date(2026, 8, 16)
INICIO_CORTES = dt.date(2026, 7, 14)
FIN_CORTES = dt.date(2026, 8, 12)
OBS_BIDS = dt.datetime(2026, 8, 20, 6, 0, tzinfo=dt.UTC)
OBS_CORTES = dt.datetime(2026, 8, 19, 7, 30, tzinfo=dt.UTC)

_DIA = dt.timedelta(days=1)


def _agregado(
    inicio: dt.date,
    fin: dt.date,
    observed_at: dt.datetime,
    *,
    cost,
    ad_revenue,
    orders,
    clicks=0,
    revenue_same_sku=None,
    moneda: str | None = "USD",
    n_fechas: int = 7,
) -> w.AgregadoMetricas:
    return w.AgregadoMetricas(
        window_start=inicio,
        window_end=fin,
        fechas=tuple(inicio + _DIA * i for i in range(n_fechas)),
        metric_currency=moneda,
        cost=cost,
        ad_revenue=ad_revenue,
        revenue_same_sku=revenue_same_sku,
        impressions=0,
        clicks=clicks,
        orders=orders,
        observed_at_max=observed_at,
    )


def _bids(**kw) -> w.AgregadoMetricas:
    return _agregado(INICIO_BIDS, FIN_BIDS, OBS_BIDS, **kw)


def _cortes(**kw) -> w.AgregadoMetricas:
    return _agregado(INICIO_CORTES, FIN_CORTES, OBS_CORTES, **kw)


def _ventana(*, cost, revenue, clicks, orders, fechas=30, moneda="USD"):
    fin = dt.date(2026, 8, 17)
    return {
        "window_start": (fin - dt.timedelta(days=29)).isoformat(),
        "window_end": fin.isoformat(),
        "fechas": fechas,
        "moneda": moneda,
        "cost": cost,
        "ad_revenue": revenue,
        "revenue_same_sku": revenue,
        "clicks": clicks,
        "orders": orders,
        "observed_at_max": "2026-08-25T08:06:11.871936+00:00",
    }


def _inputs_era(*, bids, cortes=None, umbral=100, target="25", bid_actual="1.00"):
    return {
        "motor": "bid",
        "platform": "amazon_us",
        "goal": {"scope": "platform", "bid_floor": "0.40", "bid_ceiling": "2.50"},
        "ventanas": {"bids": bids, "cortes": cortes},
        "target_acos_pct_usado": target,
        "bid_actual": bid_actual,
        "bid_moneda": "USD",
        "corte": {"umbral_clicks_usado": umbral, "cost_min_usado": "40"},
    }


def test_decide_bid_era_bandas_reproduce_banda_menos_12():
    """ACoS 30 % contra target 25 (1.2x, entre 1.15x y 1.35x) con 5
    pedidos -> -12 %: 1.00 -> 0.88, como el vivo v1."""
    from app.optimizer.eras import decide_bid_era_bandas

    bids = _ventana(cost="30.0000", revenue="100.0000", clicks=50, orders=5)
    assert decide_bid_era_bandas(_inputs_era(bids=bids)) == ("bid", Decimal("0.88"), "USD")


def test_decide_bid_era_bandas_pausa_gana_a_banda():
    """72 clics sin venta sobre el umbral 50 y 25.21 sobre el piso 12
    (sin cost_min_usado: piso historico de la era) -> pause."""
    from app.optimizer.eras import decide_bid_era_bandas

    cortes = _ventana(cost="25.2100", revenue="0.0000", clicks=72, orders=0)
    inputs = _inputs_era(
        bids=_ventana(cost="25.2100", revenue="0.0000", clicks=72, orders=0),
        cortes=cortes,
        umbral=50,
    )
    del inputs["corte"]["cost_min_usado"]
    assert decide_bid_era_bandas(inputs) == ("pause", None, None)


def test_eras_muda_motivos_y_pisos_sin_cambios():
    """Los motivos de banda y los pisos historicos viven en eras con los
    mismos valores que el vivo v1 (muda tal cual de bid.py y cortes.py)."""
    from app.optimizer import eras

    assert {
        Decimal("-0.25"): "banda_menos_25",
        Decimal("-0.12"): "banda_menos_12",
        Decimal("0.15"): "banda_mas_15",
    } == eras._MOTIVO_BANDA
    assert eras.MOTIVO_BANDA_MENOS_25_CERO_VENTAS == "banda_menos_25_cero_ventas"
    assert {
        "amazon_us": Decimal("12"),
        "amazon_mx": Decimal("200"),
    } == eras.REPLAY_PAUSE_COST_PRE_CORTES03
    assert eras.REPLAY_PAUSE_CLICKS_PRE_CORTES01 == 25


# ---------------------------------------------------------------------------
# Bandas v1 mudadas de tests/test_optimizer_bid.py (BIDS 02 M.3): juzgan el
# camino v1 puro (_decide_v1) sobre agregados a mano, como el vivo hasta el
# corte. Las de pausa se quedaron ahi (decide_pause).
# ---------------------------------------------------------------------------


def _decide_v1(
    bids: w.AgregadoMetricas | None,
    cortes: w.AgregadoMetricas | None,
    *,
    platform: str = "amazon_us",
    target: str = "25",
    bid_actual: str | None = "1.00",
    bid_moneda: str | None = "USD",
    floor: str = "0.40",
    ceiling: str = "2.50",
    cost_min: str | None = None,
    expected_clicks: str | None = None,
    umbral_pause: int | None = None,
):
    return eras._decide_v1(
        platform=platform,
        bids=bids,
        cortes=cortes,
        target_acos_pct=Decimal(target),
        bid_actual=None if bid_actual is None else Decimal(bid_actual),
        bid_moneda=bid_moneda,
        floor=Decimal(floor),
        ceiling=Decimal(ceiling),
        cost_min=None if cost_min is None else Decimal(cost_min),
        expected_clicks=None if expected_clicks is None else Decimal(expected_clicks),
        umbral_pause=LEGACY_PAUSE if umbral_pause is None else umbral_pause,
    )


def test_ejemplo_sellado_target_25_acos_36_baja_a_075():
    """Ejemplo sellado v2: target 25, bid 1.00, floor 0.40, ceiling 2.50,
    ACoS 36 (cost=36, ad_revenue=100, orders=5) -> banda -25% -> 0.75 exacto.
    La ventana que decide es la de BIDS, con SU observed_at."""
    r = _decide_v1(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind == "bid"
    assert r.motivo == "banda_menos_25"
    assert r.old_value == Decimal("1.00")
    assert r.new_value == Decimal("0.75")
    assert r.factor == Decimal("-0.25")
    assert r.value_currency == "USD"
    assert (r.window_start, r.window_end) == (INICIO_BIDS, FIN_BIDS)
    assert r.data_observed_at == OBS_BIDS


def test_ejemplo_sellado_floor_080_clampa_a_080():
    """Misma entidad con floor 0.80: -25% daria 0.75, el clamp al rango
    [floor, ceiling] lo sube a 0.80. El factor reportado es la banda ANTES
    de los clamps."""
    r = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5), None, floor="0.80"
    )
    assert r.kind == "bid"
    assert r.new_value == Decimal("0.80")
    assert r.factor == Decimal("-0.25")


def test_borde_exacto_acos_135x_no_menos_25_si_menos_12():
    """ACoS justo 1.35x target (cost 33.75 = 1.35 * 0.25 * 100) NO dispara
    -25 (estricto >) pero SI -12 (> 1.15x)."""
    r = _decide_v1(_bids(cost=Decimal("33.75"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind == "bid"
    assert r.motivo == "banda_menos_12"
    assert r.factor == Decimal("-0.12")


def test_borde_exacto_acos_115x_no_dispara_menos_12():
    """ACoS justo 1.15x target (cost 28.75) NO dispara -12; 0.85x queda
    lejos por abajo -> sin banda."""
    r = _decide_v1(_bids(cost=Decimal("28.75"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind is None
    assert r.motivo == "sin_banda"


def test_borde_exacto_acos_085x_no_dispara_mas_15():
    """ACoS justo 0.85x target (cost 21.25) NO dispara +15 (estricto <)."""
    r = _decide_v1(_bids(cost=Decimal("21.25"), ad_revenue=Decimal("100"), orders=3), None)
    assert r.kind is None
    assert r.motivo == "sin_banda"


def test_orders_1_exacto_habilita_menos_25():
    r = _decide_v1(_bids(cost=Decimal("34"), ad_revenue=Decimal("100"), orders=1), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")


def test_orders_3_exacto_habilita_mas_15():
    r = _decide_v1(_bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=3), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("0.15")
    assert r.new_value == Decimal("1.15")
    # orders=2 con la misma geometria NO habilita +15 (exige >=3)
    r2 = _decide_v1(_bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=2), None)
    assert r2.kind is None


def test_precedencia_menos_25_gana_a_menos_12():
    """ACoS 36% con orders 5 dispara -25 (>1.35x) Y -12 (>1.15x) a la vez:
    gana -25 (precedencia explicita)."""
    r = _decide_v1(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")


def test_delta_0_009_tras_clamp_a_ceiling_es_no_op():
    """Geometria sellada: bid 1.00, ceiling 1.009, +15% -> 1.15 clampa a
    1.009; |delta| = 0.009 < 0.01 (estricto) -> no-op con motivo."""
    r = _decide_v1(
        _bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=3),
        None,
        ceiling="1.009",
    )
    assert r.kind is None
    assert r.motivo == "delta_bajo_umbral"


def test_clamp_floor_no_puede_invertir_una_baja():
    """Hallazgo codex [alta], cross-review ronda 1: banda -25% con bid 0.05 y
    floor 0.10 -> el clamp de resultado SUBIRIA el bid a 0.10 (+100%) con un
    motivo que dice -25%. El cambio FINAL tambien debe obedecer el clamp por
    decision [-30%, +20%]: no existe valor que cumpla ambos -> no-op."""
    r = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual="0.05",
        floor="0.10",
    )
    assert r.kind is None
    assert r.motivo == "rango_bloquea_ajuste"
    assert r.new_value is None


def test_clamp_ceiling_no_puede_invertir_una_subida():
    """Cara complementaria del mismo hallazgo: +15% con bid 3.00 y ceiling
    2.50 -> el clamp BAJARIA a 2.50 (-16.7%) con un motivo que dice +15%.
    Direccion invertida -> no-op 'rango_bloquea_ajuste'."""
    r = _decide_v1(
        _bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual="3.00",
        ceiling="2.50",
    )
    assert r.kind is None
    assert r.motivo == "rango_bloquea_ajuste"


def test_clamp_ceiling_con_baja_dentro_del_rango_si_decide_v1():
    """No todo bid fuera de rango bloquea: -12% con bid 3.00 y ceiling 2.50
    clampa a 2.50 con delta -16.7%, MISMA direccion y dentro de [-30%, +20%]
    -> si se emite (el ajuste es ejecutable)."""
    r = _decide_v1(
        _bids(cost=Decimal("30"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual="3.00",
        ceiling="2.50",
    )
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.12")
    assert r.new_value == Decimal("2.50")


def test_bid_actual_cero_es_dato_roto():
    """bid_actual <= 0 no existe en Amazon y romperia la aritmetica del
    cambio: skip con motivo, jamas decision (regla 3)."""
    r = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual="0",
    )
    assert r.kind is None
    assert r.motivo == "bid_actual_invalido"


def test_regla_9_acos_completo_jamas_revenue_same_sku():
    """ACoS sellado = cost / ad_revenue COMPLETO (halo incluido). Con
    ad_revenue=200, revenue_same_sku=100, cost=30 y target 25: ACoS real
    15% < 21.25 (0.85x) con orders 5 >= 3 -> +15%. Si el motor usara
    revenue_same_sku (30/100 = 30% > 28.75 = 1.15x) daria -12% y este test
    FALLA (regla 9: la regresion se demuestra en rojo por mutacion)."""
    r = _decide_v1(
        _bids(
            cost=Decimal("30"),
            ad_revenue=Decimal("200"),
            revenue_same_sku=Decimal("100"),
            orders=5,
        ),
        None,
    )
    assert r.kind == "bid"
    assert r.factor == Decimal("0.15")


def test_documental_bandas_selladas_dentro_del_clamp_por_decision():
    """TEST DOCUMENTAL: las tres bandas selladas {-0.25, -0.12, +0.15} viven
    DENTRO del clamp por decision [-0.30, +0.20], asi que hoy ese clamp es
    INALCANZABLE (el clamp a [floor, ceiling] y el |delta|<0.01 si actuan).
    Si manana alguien agrega una banda fuera de ese rango, este test lo
    obliga a revisar el clamp por decision y su motivacion antes de mergear:
    una banda fuera de rango seria silenciosamente recortada por un clamp
    pensado para OTRO vocabulario de bandas."""
    for factor in (b.FACTOR_BAJA_FUERTE, b.FACTOR_BAJA_SUAVE, b.FACTOR_SUBIDA):
        assert b.CLAMP_FACTOR_MIN <= factor <= b.CLAMP_FACTOR_MAX


def test_orders_none_en_bids_no_satisface_orders_minimos():
    """orders=None tampoco satisface orders>=1 (bloquea -25; -12 no exige
    orders y SI dispara) ni orders>=3 (bloquea +15)."""
    r = _decide_v1(_bids(cost=Decimal("34"), ad_revenue=Decimal("100"), orders=None), None)
    assert r.factor == Decimal("-0.12")
    r2 = _decide_v1(_bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=None), None)
    assert r2.kind is None
    assert r2.motivo == "sin_banda"


def test_bid_moneda_divergente_skip():
    """kind 'bid' exige bid_moneda igual a la del agregado (USD en amazon_us)."""
    r = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_moneda="MXN",
    )
    assert r.kind is None
    assert r.motivo == "bid_moneda_invalida"
    r_none = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_moneda=None,
    )
    assert r_none.kind is None
    assert r_none.motivo == "bid_moneda_invalida"


def test_ad_revenue_cero_con_cost_positivo_dispara_baja_sin_division():
    """La comparacion sellada por MULTIPLICACION exacta evita la division
    por cero: ad_revenue=0 con cost>0 dispara la banda de baja (-25 si
    orders>=1; -12 no exige orders)."""
    r = _decide_v1(_bids(cost=Decimal("10"), ad_revenue=Decimal("0"), orders=1), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")
    r2 = _decide_v1(_bids(cost=Decimal("10"), ad_revenue=Decimal("0"), orders=None), None)
    assert r2.factor == Decimal("-0.12")


def test_cortes_incompleto_no_bloquea_bandas_sobre_bids():
    """ASIMETRIA SELLADA (ventanas independientes): cortes con 6 fechas no
    puede decidir un pause, pero NO impide que la ventana de bids (completa)
    decida su banda."""
    r = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=50, n_fechas=6),
    )
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")
    assert (r.window_start, r.window_end) == (INICIO_BIDS, FIN_BIDS)


def test_floor_mayor_que_ceiling_raise():
    with pytest.raises(ValueError, match="floor"):
        _decide_v1(None, None, floor="2.50", ceiling="0.40")


def test_orders_0_literal_no_habilita_menos_25_cae_a_menos_12():
    """orders=0 LITERAL (no None) con ACoS sobre 1.35x: la banda -25 exige
    orders minimo 1, asi que cae a -12. El 0 literal merece su propio pin
    en vez de inferirse de la rama None (review 2.2-2.4)."""
    r = _decide_v1(_bids(cost=Decimal("40"), ad_revenue=Decimal("100"), orders=0), None)
    assert r.kind == "bid"
    assert r.motivo == "banda_menos_12"
    assert r.factor == Decimal("-0.12")


def test_cero_ventas_con_clics_esperados_y_gasto_sobre_piso_baja_25():
    """BIDS 01 (regla 9): orders=0 y ad_revenue=0 con clicks >= expected_clicks
    del grupo y cost >= cost_min -> -25% con motivo propio. Antes del fix el
    motor solo podia dar -12% a cero ventas (-25% exigia orders >= 1)."""
    r = _decide_v1(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=120, orders=0),
        # DESV-1.1.1 (veredicto lead: cortes=None): con el _cortes del plan
        # (clicks 120 >= LEGACY_PAUSE 100) el PAUSE robaba la decision antes y
        # despues del fix; el camino de BANDAS puro es lo que el plan quiso
        # pinear (su rojo esperado era 'banda_menos_12', no 'pause_umbral').
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")
    assert r.motivo == "banda_menos_25_cero_ventas"
    assert r.new_value == Decimal("0.75")


def test_cero_ventas_un_click_bajo_los_esperados_sigue_menos_12():
    r = _decide_v1(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=119, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_gasto_bajo_el_piso_sigue_menos_12():
    r = _decide_v1(
        _bids(cost=Decimal("39.99"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_sin_expected_clicks_no_aplica():
    """Grupo sin evidencia (expected None): regla 3, nada inventado -> -12%."""
    r = _decide_v1(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks=None,
    )
    assert r.motivo == "banda_menos_12"


def test_cero_ventas_orders_o_revenue_desconocidos_no_aplica():
    """Revision PR #132 (menor): resultado COMPLETO pineado, no solo !=.
    orders None con revenue 0 cae a -12%; revenue None es ACoS desconocido
    (no-op sin banda)."""
    r1 = _decide_v1(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=None),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    r2 = _decide_v1(
        _bids(cost=Decimal("45"), ad_revenue=None, clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r1.kind, r1.motivo, r1.factor) == ("bid", "banda_menos_12", Decimal("-0.12"))
    assert (r2.kind, r2.motivo, r2.factor) == (None, b.MOTIVO_ACOS_DESCONOCIDO, None)


def test_bid_actual_none_no_ajustable():
    """bid_actual None -> no se puede ajustar (skip con motivo)."""
    r = _decide_v1(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual=None,
    )
    assert r.kind is None
    assert r.motivo == "bid_actual_ausente"


def test_decide_bid_era_bandas_cero_ventas_con_marcador_baja_25():
    """A' de la era bandas: cero ventas con los clics esperados y gasto
    sobre el piso, CON el marcador congelado -> -25% con motivo propio
    (sin marcador es -12%: test_replay_pre_bids en test_cycle.py)."""
    from test_cycle import _inputs_pre_bids

    from app.optimizer.eras import _replay_bid, decide_bid_era_bandas

    inputs = _inputs_pre_bids()
    inputs["corte"]["cero_ventas_expected_usado"] = "120"
    assert decide_bid_era_bandas(inputs) == ("bid", Decimal("0.75"), "USD")
    assert _replay_bid(inputs).motivo == "banda_menos_25_cero_ventas"
