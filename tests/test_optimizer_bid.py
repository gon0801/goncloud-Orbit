"""Tests del bloque PAUSE puro (`app.optimizer.bid`, task 2.2).

Todos UNITARIOS y PUROS: cero IO, cero DB. Las pruebas de bandas v1 viven en
tests/test_eras.py (BIDS 02 M.3). El bloque pause decide sobre agregados de
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
    cost_min: str | None = None,
    umbral_pause: int | None = None,
) -> b.ResultadoBid:
    return b.decide_pause(
        platform=platform,
        bids=bids,
        cortes=cortes,
        target_acos_pct=Decimal(target),
        cost_min=None if cost_min is None else Decimal(cost_min),
        umbral_pause=100 if umbral_pause is None else umbral_pause,
    )


# ---------------------------------------------------------------------------
# DoD 1: ejemplo sellado del diseno
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DoD 2: bordes EXACTOS de cada umbral por mercado
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DoD 4: |delta| < 0.01 tras clamps -> no-op
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DoD 6: test documental del clamp por decision
# ---------------------------------------------------------------------------


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


def test_pause_no_necesita_bid_actual():
    """PAUSE no necesita bid_actual y SI decide."""
    r_pause = _decide(
        None,
        _cortes(cost=Decimal("40"), ad_revenue=Decimal("0"), orders=0, clicks=100),
    )
    assert r_pause.kind == "pause"


# ---------------------------------------------------------------------------
# Defensa de vocabulario / argumentos (ValueError ruidoso y temprano)
# ---------------------------------------------------------------------------


def test_plataforma_fuera_de_vocabulario_raise():
    with pytest.raises(ValueError, match="vocabulario"):
        _decide(None, None, platform="meli")


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
    )
    assert r.kind is None


# ---------------------------------------------------------------------------
# BIDS 01 (regla A'): cero ventas con los clicks de una venta y gasto sobre
# el piso -> -25% con motivo propio (antes solo podia dar -12%: la banda
# -25 exigia orders >= 1 por construccion)
# ---------------------------------------------------------------------------


def test_pause_gana_sobre_la_regla_de_cero_ventas():
    """Al 1.5x (umbral_pause) con cost >= piso en la ventana de CORTES manda el
    PAUSE (sin cambio): la regla nueva vive DESPUES del pause."""
    r = _decide(
        _bids(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        _cortes(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        cost_min="40",
        umbral_pause=180,
    )
    # Cross-review grok-2: motivo pineado (kind solo no distingue el pause
    # que gana del que se bloquea).
    assert (r.kind, r.motivo) == ("pause", b.MOTIVO_PAUSE)


# ---------------------------------------------------------------------------
# BIDS 02 M.3: entrada que decide solo la PAUSE
# ---------------------------------------------------------------------------


def test_decide_pause_solo_evalua_cortes_y_reporta_bloqueo_de_bids():
    """M.3: decide_pause decide solo la PAUSE con _decide_pause: pausa
    real cuando califica (ventana de cortes), y no-op con el motivo de
    bloqueo de bids cuando no (paridad con decide_bid sin bandas)."""
    pausa = b.decide_pause(
        platform="amazon_us",
        bids=None,
        cortes=_cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), orders=0, clicks=105),
        target_acos_pct=Decimal("25"),
        umbral_pause=100,
        cost_min=Decimal("40"),
    )
    assert pausa.kind == "pause"
    assert pausa.motivo == "pause_umbral"
    assert pausa.window_start == INICIO_CORTES
    assert pausa.window_end == FIN_CORTES

    sin_banda = b.decide_pause(
        platform="amazon_us",
        bids=None,
        cortes=_cortes(cost=Decimal("10"), ad_revenue=Decimal("0"), orders=0, clicks=10),
        target_acos_pct=Decimal("25"),
        umbral_pause=100,
        cost_min=Decimal("40"),
    )
    assert sin_banda.kind is None
    assert sin_banda.motivo == "bids_sin_observaciones"
