"""Tests del bid engine puro (`app.optimizer.bid`, task 2.2).

Todos UNITARIOS y PUROS: cero IO, cero DB. El motor decide sobre agregados de
`app.optimizer.windows` construidos a mano (AgregadoMetricas con >=7 fechas =>
completa). Acoplamiento numerico sellado contra el diseno v2
(docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md, reglas 1-5):

- Ejemplo sellado del diseno: target 25, bid 1.00, ACoS 36 -> -25% -> 0.75;
  floor 0.80 -> 0.80 (clamp por floor).
- Bordes EXACTOS por mercado: 1.35x, 1.15x y 0.85x (estrictos), orders 1/3,
  clicks 25 y cost 12 USD / 200 MXN (inclusivos >=).
- Precedencia explicita: PAUSE gana a cualquier ajuste; -25 gana a -12.
- |delta| < 0.01 tras ambos clamps -> no-op con motivo.
- Regla 9 del repo (ACoS = cost / ad_revenue COMPLETO; revenue_same_sku
  JAMAS entra): el test regla 9 falla si alguien cambia la fuente del ACoS.
- Semantica de None (regla 3: faltante != cero): orders None jamas pausa ni
  satisface orders>=N; clicks/cost None no pausan; ACoS desconocido no
  decide banda; bid_actual None no ajusta (PAUSE no lo necesita).
- Ventana usada: pause lleva la de CORTES, bid la de BIDS, cada una con su
  data_observed_at (insumo de decision.data_observed_at).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.optimizer import bid as b
from app.optimizer import windows as w

# ---------------------------------------------------------------------------
# Reloj/ventanas FIJAS (mismas fechas que test_optimizer_windows: bids
# termina 08-16, cortes 08-12; observed_at distintos para discriminar el
# agregado que decidio cada camino).
# ---------------------------------------------------------------------------

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
    """AgregadoMetricas a mano: 7 fechas => completa (unidad sellada 2.1)."""
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


def _decide(
    bids: w.AgregadoMetricas | None,
    cortes: w.AgregadoMetricas | None,
    *,
    platform: str = "amazon_us",
    target: str = "25",
    bid_actual: str | None = "1.00",
    bid_moneda: str | None = "USD",
    floor: str = "0.40",
    ceiling: str = "2.50",
) -> b.ResultadoBid:
    return b.decide_bid(
        platform=platform,
        bids=bids,
        cortes=cortes,
        target_acos_pct=Decimal(target),
        bid_actual=None if bid_actual is None else Decimal(bid_actual),
        bid_moneda=bid_moneda,
        floor=Decimal(floor),
        ceiling=Decimal(ceiling),
    )


# ---------------------------------------------------------------------------
# DoD 1: ejemplo sellado del diseno
# ---------------------------------------------------------------------------


def test_ejemplo_sellado_target_25_acos_36_baja_a_075():
    """Ejemplo sellado v2: target 25, bid 1.00, floor 0.40, ceiling 2.50,
    ACoS 36 (cost=36, ad_revenue=100, orders=5) -> banda -25% -> 0.75 exacto.
    La ventana que decide es la de BIDS, con SU observed_at."""
    r = _decide(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5), None)
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
    r = _decide(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5), None, floor="0.80")
    assert r.kind == "bid"
    assert r.new_value == Decimal("0.80")
    assert r.factor == Decimal("-0.25")


# ---------------------------------------------------------------------------
# DoD 2: bordes EXACTOS de cada umbral por mercado
# ---------------------------------------------------------------------------


def test_borde_exacto_acos_135x_no_menos_25_si_menos_12():
    """ACoS justo 1.35x target (cost 33.75 = 1.35 * 0.25 * 100) NO dispara
    -25 (estricto >) pero SI -12 (> 1.15x)."""
    r = _decide(_bids(cost=Decimal("33.75"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind == "bid"
    assert r.motivo == "banda_menos_12"
    assert r.factor == Decimal("-0.12")


def test_borde_exacto_acos_115x_no_dispara_menos_12():
    """ACoS justo 1.15x target (cost 28.75) NO dispara -12; 0.85x queda
    lejos por abajo -> sin banda."""
    r = _decide(_bids(cost=Decimal("28.75"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind is None
    assert r.motivo == "sin_banda"


def test_borde_exacto_acos_085x_no_dispara_mas_15():
    """ACoS justo 0.85x target (cost 21.25) NO dispara +15 (estricto <)."""
    r = _decide(_bids(cost=Decimal("21.25"), ad_revenue=Decimal("100"), orders=3), None)
    assert r.kind is None
    assert r.motivo == "sin_banda"


def test_orders_1_exacto_habilita_menos_25():
    r = _decide(_bids(cost=Decimal("34"), ad_revenue=Decimal("100"), orders=1), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")


def test_orders_3_exacto_habilita_mas_15():
    r = _decide(_bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=3), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("0.15")
    assert r.new_value == Decimal("1.15")
    # orders=2 con la misma geometria NO habilita +15 (exige >=3)
    r2 = _decide(_bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=2), None)
    assert r2.kind is None


def test_pause_exacto_us_clicks_25_cost_12():
    """Bordes inclusivos exactos en amazon_us: clicks=25 y cost=12 USD
    PAUSEAN (>=). kind 'pause' no lleva dinero: value_currency NULL (esquema
    sellado), old/new None, factor None. La ventana que decide es la de
    CORTES, con SU observed_at."""
    r = _decide(None, _cortes(cost=Decimal("12"), ad_revenue=Decimal("0"), orders=0, clicks=25))
    assert r.kind == "pause"
    assert r.motivo == "pause_umbral"
    assert r.old_value is None
    assert r.new_value is None
    assert r.value_currency is None
    assert r.factor is None
    assert (r.window_start, r.window_end) == (INICIO_CORTES, FIN_CORTES)
    assert r.data_observed_at == OBS_CORTES


def test_pause_exacto_mx_cost_200_mxn():
    """Cost justo 200 MXN en amazon_mx PAUSEA (>=, umbral por plataforma)."""
    r = _decide(
        None,
        _cortes(cost=Decimal("200"), ad_revenue=Decimal("0"), orders=0, clicks=25, moneda="MXN"),
        platform="amazon_mx",
        bid_moneda="MXN",
    )
    assert r.kind == "pause"
    assert r.value_currency is None


def test_clicks_24_no_pause():
    r = _decide(None, _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=24))
    assert r.kind is None


def test_cost_11_99_no_pause_us():
    r = _decide(None, _cortes(cost=Decimal("11.99"), ad_revenue=Decimal("0"), orders=0, clicks=25))
    assert r.kind is None


# ---------------------------------------------------------------------------
# DoD 3: precedencias explicitas
# ---------------------------------------------------------------------------


def test_precedencia_pause_gana_a_banda_menos_25():
    """Entidad que cumple PAUSE por cortes Y banda -25 por bids: PAUSE gana,
    y la ventana reportada es la de CORTES (la que decidio el pause)."""
    r = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=30),
    )
    assert r.kind == "pause"
    assert (r.window_start, r.window_end) == (INICIO_CORTES, FIN_CORTES)
    assert r.data_observed_at == OBS_CORTES


def test_precedencia_menos_25_gana_a_menos_12():
    """ACoS 36% con orders 5 dispara -25 (>1.35x) Y -12 (>1.15x) a la vez:
    gana -25 (precedencia explicita)."""
    r = _decide(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")


# ---------------------------------------------------------------------------
# DoD 4: |delta| < 0.01 tras clamps -> no-op
# ---------------------------------------------------------------------------


def test_delta_0_009_tras_clamp_a_ceiling_es_no_op():
    """Geometria sellada: bid 1.00, ceiling 1.009, +15% -> 1.15 clampa a
    1.009; |delta| = 0.009 < 0.01 (estricto) -> no-op con motivo."""
    r = _decide(
        _bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=3),
        None,
        ceiling="1.009",
    )
    assert r.kind is None
    assert r.motivo == "delta_bajo_umbral"


# ---------------------------------------------------------------------------
# DoD 5: regla 9 -- ACoS COMPLETO, revenue_same_sku jamas
# ---------------------------------------------------------------------------


def test_regla_9_acos_completo_jamas_revenue_same_sku():
    """ACoS sellado = cost / ad_revenue COMPLETO (halo incluido). Con
    ad_revenue=200, revenue_same_sku=100, cost=30 y target 25: ACoS real
    15% < 21.25 (0.85x) con orders 5 >= 3 -> +15%. Si el motor usara
    revenue_same_sku (30/100 = 30% > 28.75 = 1.15x) daria -12% y este test
    FALLA (regla 9: la regresion se demuestra en rojo por mutacion)."""
    r = _decide(
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


# ---------------------------------------------------------------------------
# DoD 6: test documental del clamp por decision
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DoD 7: semantica de None (regla 3: faltante != cero)
# ---------------------------------------------------------------------------


def test_orders_none_no_pause_motivo_dato_faltante():
    """orders=None es DESCONOCIDO, no 0: con clicks 50 y cost 15 (>= umbrales
    us) NO hay pause; el motivo declara el dato faltante (auditable en 3.1)."""
    r = _decide(None, _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=None, clicks=50))
    assert r.kind is None
    assert r.motivo == "pause_orders_desconocido"


def test_clicks_o_cost_none_no_pause():
    """clicks/cost None en cortes -> no PAUSE (umbrales no evaluables)."""
    r_clicks = _decide(
        None, _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=None)
    )
    assert r_clicks.kind is None
    r_cost = _decide(None, _cortes(cost=None, ad_revenue=Decimal("0"), orders=0, clicks=50))
    assert r_cost.kind is None
    assert r_cost.motivo == "pause_clicks_o_cost_desconocidos"


def test_orders_none_en_bids_no_satisface_orders_minimos():
    """orders=None tampoco satisface orders>=1 (bloquea -25; -12 no exige
    orders y SI dispara) ni orders>=3 (bloquea +15)."""
    r = _decide(_bids(cost=Decimal("34"), ad_revenue=Decimal("100"), orders=None), None)
    assert r.factor == Decimal("-0.12")
    r2 = _decide(_bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=None), None)
    assert r2.kind is None
    assert r2.motivo == "sin_banda"


def test_cost_none_en_bids_no_banda_acos_desconocido():
    """cost o ad_revenue None -> ACoS desconocido -> ninguna banda."""
    r = _decide(_bids(cost=None, ad_revenue=Decimal("100"), orders=5), None)
    assert r.kind is None
    assert r.motivo == "acos_desconocido"
    r2 = _decide(_bids(cost=Decimal("30"), ad_revenue=None, orders=5), None)
    assert r2.kind is None
    assert r2.motivo == "acos_desconocido"


def test_bids_none_sin_observaciones_skip():
    """Agregados None (sin observaciones) -> skip con motivo, jamas excepcion."""
    r = _decide(None, None)
    assert r.kind is None
    assert r.motivo == "bids_sin_observaciones"


def test_bids_incompleto_6_fechas_skip():
    """Ventana de bids con 6 fechas (<7): ESA ventana no decide banda."""
    r = _decide(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5, n_fechas=6), None)
    assert r.kind is None
    assert r.motivo == "bids_incompleto"


def test_moneda_agregado_bids_invalida_skip():
    """Coherencia de moneda (fail-closed): agregado de bids en MXN (o sin
    moneda) sobre amazon_us -> skip con motivo aunque la banda dispararia."""
    r = _decide(_bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5, moneda="MXN"), None)
    assert r.kind is None
    assert r.motivo == "bids_moneda_agregado_invalida"
    r_none = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5, moneda=None), None
    )
    assert r_none.kind is None
    assert r_none.motivo == "bids_moneda_agregado_invalida"


def test_moneda_agregado_cortes_invalida_no_pause():
    """Misma defensa en el camino de pause: cortes en MXN sobre amazon_us con
    umbrales cumplidos -> NO pause (fail-closed antes de decidir)."""
    r = _decide(
        None,
        _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=50, moneda="MXN"),
    )
    assert r.kind is None
    assert r.motivo == "pause_moneda_agregado_invalida"


def test_bid_moneda_divergente_skip():
    """kind 'bid' exige bid_moneda igual a la del agregado (USD en amazon_us)."""
    r = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_moneda="MXN",
    )
    assert r.kind is None
    assert r.motivo == "bid_moneda_invalida"
    r_none = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_moneda=None,
    )
    assert r_none.kind is None
    assert r_none.motivo == "bid_moneda_invalida"


def test_bid_actual_none_no_ajustable_pero_pause_no_lo_necesita():
    """bid_actual None -> no se puede ajustar (skip con motivo); PAUSE no
    necesita bid_actual y SI decide."""
    r = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual=None,
    )
    assert r.kind is None
    assert r.motivo == "bid_actual_ausente"
    r_pause = _decide(
        None,
        _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=50),
        bid_actual=None,
    )
    assert r_pause.kind == "pause"


def test_ad_revenue_cero_con_cost_positivo_dispara_baja_sin_division():
    """La comparacion sellada por MULTIPLICACION exacta evita la division
    por cero: ad_revenue=0 con cost>0 dispara la banda de baja (-25 si
    orders>=1; -12 no exige orders)."""
    r = _decide(_bids(cost=Decimal("10"), ad_revenue=Decimal("0"), orders=1), None)
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")
    r2 = _decide(_bids(cost=Decimal("10"), ad_revenue=Decimal("0"), orders=None), None)
    assert r2.factor == Decimal("-0.12")


def test_cortes_incompleto_no_bloquea_bandas_sobre_bids():
    """ASIMETRIA SELLADA (ventanas independientes): cortes con 6 fechas no
    puede decidir un pause, pero NO impide que la ventana de bids (completa)
    decida su banda."""
    r = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        _cortes(cost=Decimal("15"), ad_revenue=Decimal("0"), orders=0, clicks=50, n_fechas=6),
    )
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")
    assert (r.window_start, r.window_end) == (INICIO_BIDS, FIN_BIDS)


# ---------------------------------------------------------------------------
# Defensa de vocabulario / argumentos (ValueError ruidoso y temprano)
# ---------------------------------------------------------------------------


def test_plataforma_fuera_de_vocabulario_raise():
    with pytest.raises(ValueError, match="vocabulario"):
        _decide(None, None, platform="meli")


def test_floor_mayor_que_ceiling_raise():
    with pytest.raises(ValueError, match="floor"):
        _decide(None, None, floor="2.50", ceiling="0.40")
