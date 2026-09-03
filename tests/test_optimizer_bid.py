"""Tests del bid engine puro (`app.optimizer.bid`, task 2.2).

Todos UNITARIOS y PUROS: cero IO, cero DB. El motor decide sobre agregados de
`app.optimizer.windows` construidos a mano (AgregadoMetricas con >=7 fechas =>
completa). Acoplamiento numerico sellado contra el diseno v2
(docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md, reglas 1-5):

- Ejemplo sellado del diseno: target 25, bid 1.00, ACoS 36 -> -25% -> 0.75;
  floor 0.80 -> 0.80 (clamp por floor).
- Bordes EXACTOS por mercado: 1.35x, 1.15x y 0.85x (estrictos), orders 1/3,
  clicks 100 y cost 40 USD / 500 MXN (inclusivos >=; CORTES 03; antes
  25 / 12 / 200).
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
    cost_min: str | None = None,
    expected_clicks: str | None = None,
    umbral_pause: int | None = None,
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
        cost_min=None if cost_min is None else Decimal(cost_min),
        expected_clicks=None if expected_clicks is None else Decimal(expected_clicks),
        umbral_pause=b.LEGACY_PAUSE if umbral_pause is None else umbral_pause,
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


def test_pause_exacto_us_clicks_100_cost_40():
    """Bordes inclusivos exactos en amazon_us: clicks=100 y cost=40 USD
    PAUSEAN (>=). kind 'pause' no lleva dinero: value_currency NULL (esquema
    sellado), old/new None, factor None. La ventana que decide es la de
    CORTES, con SU observed_at. CORTES 03 (dueno 2026-08-28): los bordes
    eran 25 clicks / 12 USD."""
    r = _decide(None, _cortes(cost=Decimal("40"), ad_revenue=Decimal("0"), orders=0, clicks=100))
    assert r.kind == "pause"
    assert r.motivo == "pause_umbral"
    assert r.old_value is None
    assert r.new_value is None
    assert r.value_currency is None
    assert r.factor is None
    assert (r.window_start, r.window_end) == (INICIO_CORTES, FIN_CORTES)
    assert r.data_observed_at == OBS_CORTES


def test_pause_exacto_mx_cost_500_mxn():
    """Cost justo 500 MXN en amazon_mx PAUSEA (>=, umbral por plataforma).
    CORTES 03 (dueno 2026-08-28): el borde era 200 MXN."""
    r = _decide(
        None,
        _cortes(cost=Decimal("500"), ad_revenue=Decimal("0"), orders=0, clicks=100, moneda="MXN"),
        platform="amazon_mx",
        bid_moneda="MXN",
    )
    assert r.kind == "pause"
    assert r.value_currency is None


def test_cortes_03_fila_30_no_pausa_72_clicks_25_usd():
    """CASO DISCRIMINANTE estrella de CORTES 03 (dueno 2026-08-28, origen
    spot-check ORBIT 04 4.4 fila 30, decision 774): 72 clics / 25.21 USD /
    0 ventas NO debe pausar. Con el codigo viejo (fallback 50 / 12 USD) ESTA
    MISMA geometria SI pausaba -- este test la veta."""
    r = _decide(None, _cortes(cost=Decimal("25.21"), ad_revenue=Decimal("0"), orders=0, clicks=72))
    assert r.kind is None
    assert r.motivo == "bids_sin_observaciones"


def test_clicks_99_cost_40_no_pause():
    """Borde inclusivo por clicks CORTES 03: 99 < 100 NO pausa aunque el cost
    40 >= 40. Con el codigo viejo (piso 25) 99 clicks SI pausaban."""
    r = _decide(None, _cortes(cost=Decimal("40"), ad_revenue=Decimal("0"), orders=0, clicks=99))
    assert r.kind is None


def test_clicks_100_cost_39_99_no_pause_us():
    """Borde inclusivo por cost CORTES 03: 39.99 < 40 USD NO pausa aunque
    clicks 100 >= 100. Con el codigo viejo (12 USD) SI pausaba."""
    r = _decide(None, _cortes(cost=Decimal("39.99"), ad_revenue=Decimal("0"), orders=0, clicks=100))
    assert r.kind is None


# ---------------------------------------------------------------------------
# DoD 3: precedencias explicitas
# ---------------------------------------------------------------------------


def test_cost_min_parametro_manda_sobre_el_default():
    """El parametro cost_min (cierre CORTES 03) ES consumido por la guarda
    de pause, en ambas direcciones: con cost_min 999 el costo 45 NO pausa
    (el default vigente 40 si lo haria); con cost_min 5 el costo 11.99 SI
    pausa (el default no). Si alguien volviera a leer el dict directo e
    ignorara el parametro, estas dos aserciones reventan. El replay es el
    caller que depende de esto (pasa el congelado/historico)."""
    r_alto = _decide(
        None,
        _cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), orders=0, clicks=100),
        cost_min="999",
    )
    assert r_alto.kind is None
    r_bajo = _decide(
        None,
        _cortes(cost=Decimal("11.99"), ad_revenue=Decimal("0"), orders=0, clicks=100),
        cost_min="5",
    )
    assert r_bajo.kind == "pause"
    assert r_bajo.motivo == "pause_umbral"


def test_precedencia_pause_gana_a_banda_menos_25():
    """Entidad que cumple PAUSE por cortes Y banda -25 por bids: PAUSE gana,
    y la ventana reportada es la de CORTES (la que decidio el pause).
    Geometria CORTES 03: 105 clicks / 45 USD (>= 100 / >= 40)."""
    r = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        _cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), orders=0, clicks=105),
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


def test_clamp_floor_no_puede_invertir_una_baja():
    """Hallazgo codex [alta], cross-review ronda 1: banda -25% con bid 0.05 y
    floor 0.10 -> el clamp de resultado SUBIRIA el bid a 0.10 (+100%) con un
    motivo que dice -25%. El cambio FINAL tambien debe obedecer el clamp por
    decision [-30%, +20%]: no existe valor que cumpla ambos -> no-op."""
    r = _decide(
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
    r = _decide(
        _bids(cost=Decimal("20"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual="3.00",
        ceiling="2.50",
    )
    assert r.kind is None
    assert r.motivo == "rango_bloquea_ajuste"


def test_clamp_ceiling_con_baja_dentro_del_rango_si_decide():
    """No todo bid fuera de rango bloquea: -12% con bid 3.00 y ceiling 2.50
    clampa a 2.50 con delta -16.7%, MISMA direccion y dentro de [-30%, +20%]
    -> si se emite (el ajuste es ejecutable)."""
    r = _decide(
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
    r = _decide(
        _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
        None,
        bid_actual="0",
    )
    assert r.kind is None
    assert r.motivo == "bid_actual_invalido"


def test_target_acos_invalido_value_error():
    """Hallazgo codex+grok (ronda 1): target 0 haria que cualquier cost>0
    dispare una baja (la multiplicacion queda costo > 0); negativo invierte
    las bandas. La cascada de 2.4 valida sus peldanos, pero el motor no
    confia en eso: ValueError ruidoso y temprano."""
    for target in ("0", "-25", "nan"):
        with pytest.raises(ValueError, match="target_acos_pct"):
            _decide(
                _bids(cost=Decimal("36"), ad_revenue=Decimal("100"), orders=5),
                None,
                target=target,
            )


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
    """orders=None es DESCONOCIDO, no 0: con clicks 105 y cost 45 (>= umbrales
    us CORTES 03) NO hay pause; el motivo declara el dato faltante (auditable
    en 3.1)."""
    r = _decide(None, _cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), orders=None, clicks=105))
    assert r.kind is None
    assert r.motivo == "pause_orders_desconocido"


def test_clicks_o_cost_none_no_pause():
    """clicks/cost None en cortes -> no PAUSE (umbrales no evaluables)."""
    r_clicks = _decide(
        None, _cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), orders=0, clicks=None)
    )
    assert r_clicks.kind is None
    r_cost = _decide(None, _cortes(cost=None, ad_revenue=Decimal("0"), orders=0, clicks=105))
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
        _cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), orders=0, clicks=105, moneda="MXN"),
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
        _cortes(cost=Decimal("40"), ad_revenue=Decimal("0"), orders=0, clicks=100),
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


# ---------------------------------------------------------------------------
# Cobertura extra (review 2.2-2.4): bordes MX del lado de abajo y orders=0
# ---------------------------------------------------------------------------


def test_cost_499_99_no_pause_mx():
    """Un centavo bajo el umbral MX (499.99, umbral 500): NO pausea
    (simetria con test_clicks_100_cost_39_99_no_pause_us). Con el codigo
    viejo (umbral 200) SI pausaba."""
    r = _decide(
        None,
        _cortes(
            cost=Decimal("499.99"), ad_revenue=Decimal("0"), orders=0, clicks=100, moneda="MXN"
        ),
        platform="amazon_mx",
        bid_moneda="MXN",
    )
    assert r.kind is None


def test_orders_0_literal_no_habilita_menos_25_cae_a_menos_12():
    """orders=0 LITERAL (no None) con ACoS sobre 1.35x: la banda -25 exige
    orders minimo 1, asi que cae a -12. El 0 literal merece su propio pin
    en vez de inferirse de la rama None (review 2.2-2.4)."""
    r = _decide(_bids(cost=Decimal("40"), ad_revenue=Decimal("100"), orders=0), None)
    assert r.kind == "bid"
    assert r.motivo == "banda_menos_12"
    assert r.factor == Decimal("-0.12")


# ---------------------------------------------------------------------------
# BIDS 01 (regla A'): cero ventas con los clicks de una venta y gasto sobre
# el piso -> -25% con motivo propio (antes solo podia dar -12%: la banda
# -25 exigia orders >= 1 por construccion)
# ---------------------------------------------------------------------------


def test_cero_ventas_con_clics_esperados_y_gasto_sobre_piso_baja_25():
    """BIDS 01 (regla 9): orders=0 y ad_revenue=0 con clicks >= expected_clicks
    del grupo y cost >= cost_min -> -25% con motivo propio. Antes del fix el
    motor solo podia dar -12% a cero ventas (-25% exigia orders >= 1)."""
    r = _decide(
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
    r = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=119, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_gasto_bajo_el_piso_sigue_menos_12():
    r = _decide(
        _bids(cost=Decimal("39.99"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_sin_expected_clicks_no_aplica():
    """Grupo sin evidencia (expected None): regla 3, nada inventado -> -12%."""
    r = _decide(
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
    r1 = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=None),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    r2 = _decide(
        _bids(cost=Decimal("45"), ad_revenue=None, clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r1.kind, r1.motivo, r1.factor) == ("bid", "banda_menos_12", Decimal("-0.12"))
    assert (r2.kind, r2.motivo, r2.factor) == (None, b.MOTIVO_ACOS_DESCONOCIDO, None)


def test_pause_gana_sobre_la_regla_de_cero_ventas():
    """Al 1.5x (umbral_pause) con cost >= piso en la ventana de CORTES manda el
    PAUSE (sin cambio): la regla nueva vive DESPUES del pause."""
    r = _decide(
        _bids(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        _cortes(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        cost_min="40",
        expected_clicks="120",
        umbral_pause=180,
    )
    assert r.kind == "pause"
