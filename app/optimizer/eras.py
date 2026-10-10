"""Historia congelada de bandas_v1 (BIDS 02 M.3).

La politica bandas_v1 tal como decidio hasta el corte, MUDADA aqui sin
cambios desde bid.py, cortes.py y replay.py (bandas, regla A' de cero
ventas, pisos historicos REPLAY_* y reconstruccion de agregados). Solo la
importa replay.py para re-decidir filas viejas (sin inputs.politica): el
candado de tests/test_architecture.py prohibe que app/cycle.py la importe.
Si un ajuste futuro cambia este archivo, la prueba dorada de replay grita.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.optimizer import windows
from app.optimizer.bid import (
    CLAMP_FACTOR_MAX,
    CLAMP_FACTOR_MIN,
    FACTOR_BAJA_FUERTE,
    FACTOR_BAJA_SUAVE,
    FACTOR_SUBIDA,
    MIN_DELTA_ABSOLUTO,
    MOTIVO_ACOS_DESCONOCIDO,
    MOTIVO_BID_ACTUAL_AUSENTE,
    MOTIVO_BID_ACTUAL_INVALIDO,
    MOTIVO_BID_MONEDA_INVALIDA,
    MOTIVO_BIDS_INCOMPLETO,
    MOTIVO_BIDS_MONEDA_INVALIDA,
    MOTIVO_BIDS_SIN_OBSERVACIONES,
    MOTIVO_DELTA_BAJO_UMBRAL,
    MOTIVO_RANGO_BLOQUEA_AJUSTE,
    MOTIVO_SIN_BANDA,
    MULT_BAJA_FUERTE,
    MULT_BAJA_SUAVE,
    MULT_SUBIDA,
    ORDERS_MIN_BAJA_FUERTE,
    ORDERS_MIN_SUBIDA,
    PAUSE_COST_MIN,
    PLATAFORMAS_MONEDA,
    POLITICA_PAUSE_ECONOMICA,
    ResultadoBid,
    _decide_pause,
)
from app.optimizer.caso import POLITICA_BID
from app.optimizer.cortes import LEGACY_PAUSE
from app.optimizer.windows import AgregadoMetricas

_CIEN = Decimal("100")
_UNO = Decimal("1")

# BIDS 01 (regla A', spec 2026-08-26 aprobada 2026-09-03): cero ventas con
# los clicks de una venta y gasto sobre el piso -> -25% con motivo propio.
# (Mudado de bid.py sin cambios.)
MOTIVO_BANDA_MENOS_25_CERO_VENTAS = "banda_menos_25_cero_ventas"
_MOTIVO_BANDA: dict[Decimal, str] = {
    FACTOR_BAJA_FUERTE: "banda_menos_25",
    FACTOR_BAJA_SUAVE: "banda_menos_12",
    FACTOR_SUBIDA: "banda_mas_15",
}

# Historia congelada (decision del lead 2026-08-28): el piso de costo que
# VIGIA antes de CORTES 03. SOLO lo consume _replay_bid para filas sin
# inputs.corte.cost_min_usado (toda la era anterior; ninguna fila de
# produccion tenia aun la clave al medir 2026-08-28); JAMAS lo usa un
# camino vivo. (Mudado de bid.py sin cambios.)
REPLAY_PAUSE_COST_PRE_CORTES03: dict[str, Decimal] = {
    "amazon_us": Decimal("12"),
    "amazon_mx": Decimal("200"),
}

# Historia congelada (cierre CORTES 03, decision del lead 2026-08-28): el
# umbral de clicks del pause que VIGIA antes de CORTES 01. SOLO lo consume
# el replay para filas sin inputs.corte; JAMAS un camino vivo (el piso
# VIGENTE es LEGACY_PAUSE: 100). (Mudado de cortes.py sin cambios.)
REPLAY_PAUSE_CLICKS_PRE_CORTES01 = 25


def _factor_banda(agregado: AgregadoMetricas, target_acos_pct: Decimal) -> Decimal | None:
    """Factor de banda que dispara sobre el agregado de BIDS (o None).

    ACoS COMPLETO (cost / ad_revenue; `revenue_same_sku` jamas) comparado por
    multiplicacion exacta: PROHIBIDO dividir (evita division por cero y
    mantiene Decimal exacto). orders None no satisface ningun orders>=N
    (regla 3). Precedencia explicita: -25 antes que -12; +15 excluido de -12
    por aritmetica exacta (0.85 < 1.15)."""
    costo = agregado.cost
    ingreso = agregado.ad_revenue
    orders = agregado.orders
    target = target_acos_pct / _CIEN
    if (
        costo > MULT_BAJA_FUERTE * target * ingreso
        and orders is not None
        and orders >= ORDERS_MIN_BAJA_FUERTE
    ):
        return FACTOR_BAJA_FUERTE
    if costo > MULT_BAJA_SUAVE * target * ingreso:
        return FACTOR_BAJA_SUAVE
    if (
        costo < MULT_SUBIDA * target * ingreso
        and orders is not None
        and orders >= ORDERS_MIN_SUBIDA
    ):
        return FACTOR_SUBIDA
    return None


def _factor_cero_ventas(
    agregado: AgregadoMetricas, expected_clicks: Decimal | None, cost_min: Decimal | None
) -> Decimal | None:
    """BIDS 01 (spec 2026-08-26 aprobada 2026-09-03, regla A'): gasto sin UNA
    venta habiendo alcanzado los clicks que en ese grupo cuesta una venta y
    el piso de costo de pausa -> -25%. Relativa al producto (expected_clicks
    de CORTES 01), jamas un numero absoluto. Cualquier insumo None = no aplica
    (regla 3). Se evalua DESPUES del pause y ANTES de las bandas."""
    if expected_clicks is None or cost_min is None:
        return None
    if agregado.orders is None or agregado.ad_revenue is None:
        return None
    if agregado.clicks is None or agregado.cost is None:
        return None
    if agregado.orders != 0 or agregado.ad_revenue != 0:
        return None
    if Decimal(agregado.clicks) >= expected_clicks and agregado.cost >= cost_min:
        return FACTOR_BAJA_FUERTE
    return None


def _factor_ventana_v1(
    bids: AgregadoMetricas,
    target_acos_pct: Decimal,
    expected_clicks: Decimal | None,
    costo_piso: Decimal,
) -> tuple[str | None, Decimal | None]:
    """Seleccion de factor v1 (BIDS 01 regla A' + bandas de ventana)."""
    factor = _factor_cero_ventas(bids, expected_clicks, costo_piso)
    motivo_banda = MOTIVO_BANDA_MENOS_25_CERO_VENTAS if factor is not None else None
    if factor is None:
        factor = _factor_banda(bids, target_acos_pct)
        motivo_banda = _MOTIVO_BANDA.get(factor) if factor is not None else None
    return (motivo_banda, factor)


def _dec_de_json(valor) -> Decimal | None:
    """Decimal de vuelta desde el string congelado."""
    return Decimal(str(valor)) if valor is not None else None


def _fechas_sinteticas(window_end: dt.date, n: int) -> tuple[dt.date, ...]:
    """n fechas dentro de la ventana terminando en window_end: el CONTEO es lo
    que replayea `completa` (>= 7 fechas); el replay sintetiza las fechas."""
    return tuple(window_end - dt.timedelta(days=n - 1 - i) for i in range(n))


def _agregado_sintetico(d: dict | None) -> windows.AgregadoMetricas | None:
    if d is None:
        return None
    fin = dt.date.fromisoformat(d["window_end"])
    observed = d["observed_at_max"]
    return windows.AgregadoMetricas(
        window_start=dt.date.fromisoformat(d["window_start"]),
        window_end=fin,
        fechas=_fechas_sinteticas(fin, d["fechas"]),
        metric_currency=d["moneda"],
        cost=_dec_de_json(d["cost"]),
        ad_revenue=_dec_de_json(d["ad_revenue"]),
        revenue_same_sku=_dec_de_json(d["revenue_same_sku"]),
        impressions=None,  # el motor de bids no lo consume; no se congelo
        clicks=d["clicks"],
        orders=d["orders"],
        observed_at_max=dt.datetime.fromisoformat(observed) if observed else None,
    )


def _args_replay_bid(inputs: dict, target: Decimal | None = None) -> dict:
    """Args v1 de decide_bid desde inputs congelados (fuente unica para
    _replay_bid y reproduce_bandas_v1: el contrafactual v1 espeja al vivo
    arg-por-arg, cero drift)."""
    goal = inputs["goal"]
    # CORTES 01 (spec) + cierre CORTES 03: umbral de clicks =
    # inputs.corte.umbral_clicks_usado; piso de costo = inputs.corte.cost_min_usado
    # (clave que congela el ciclo desde CORTES 03). Fila historica sin la clave
    # rejuega con la HISTORIA de su era -- REPLAY_PAUSE_CLICKS_PRE_CORTES01 (25) y
    # REPLAY_PAUSE_COST_PRE_CORTES03 (12/200) --, nunca con el vigente
    # (100 / 40/500). Medicion en produccion (SELECT read-only 2026-08-28):
    # las 34/34 pauses historicas reproducen fieles (4 con freeze usan su
    # umbral congelado; 30 sin freeze usan 25/12; ninguna fila tenia aun
    # cost_min_usado, incluida la 774 -> pause).
    corte = inputs.get("corte")
    umbral_pause = (
        corte["umbral_clicks_usado"] if corte is not None else REPLAY_PAUSE_CLICKS_PRE_CORTES01
    )
    cost_min = (
        Decimal(corte["cost_min_usado"])
        if corte is not None and "cost_min_usado" in corte
        else REPLAY_PAUSE_COST_PRE_CORTES03[inputs["platform"]]
    )
    # BIDS 01 revision (regla 4.4): el replay lee SOLO el marcador
    # cero_ventas_expected_usado (lo que decide_bid consumio). expected_clicks
    # se congela desde CORTES 01 en TODAS las bids: leerlo contaminaria las
    # filas pre-BIDS (17 decisiones US medidas rejuegan -25% indebido). Fila
    # sin la clave (toda la era anterior) o con null -> None -> la regla no
    # aplica y rejuega IGUAL que lo persistido (regla 3).
    expected = (
        _dec_de_json(corte.get("cero_ventas_expected_usado"))
        if corte is not None and corte.get("cero_ventas_expected_usado") is not None
        else None
    )
    return {
        "platform": inputs["platform"],
        "bids": _agregado_sintetico(inputs["ventanas"]["bids"]),
        "cortes": _agregado_sintetico(inputs["ventanas"]["cortes"]),
        "target_acos_pct": (
            target if target is not None else Decimal(inputs["target_acos_pct_usado"])
        ),
        "bid_actual": _dec_de_json(inputs["bid_actual"]),
        "bid_moneda": inputs["bid_moneda"],
        "floor": Decimal(goal["bid_floor"]),
        "ceiling": Decimal(goal["bid_ceiling"]),
        "umbral_pause": umbral_pause,
        "cost_min": cost_min,
        "expected_clicks": expected,
        "policy_version": (inputs.get("economic_policy") or {}).get("version"),
    }


def _decide_v1(
    *,
    platform: str,
    bids: AgregadoMetricas | None,
    cortes: AgregadoMetricas | None,
    target_acos_pct: Decimal,
    bid_actual: Decimal | None,
    bid_moneda: str | None,
    floor: Decimal,
    ceiling: Decimal,
    umbral_pause: int = LEGACY_PAUSE,
    cost_min: Decimal | None = None,
    expected_clicks: Decimal | None = None,
    policy_version: str | None = None,
) -> ResultadoBid:
    """El camino v1 PURO de decide_bid (bandas de ventana + regla A' +
    cola de clamps), tal como decidio hasta el corte. Los params v2
    (evidencia, politica_bandas, confianzas, fallback) no existen aqui:
    murieron con el vivo."""
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(
            f"plataforma fuera del vocabulario sellado {{amazon_us, amazon_mx}}: {platform!r}"
        )
    if not target_acos_pct.is_finite() or target_acos_pct <= 0:
        raise ValueError(f"target_acos_pct invalido: {target_acos_pct!r} (debe ser > 0)")
    if floor > ceiling:
        raise ValueError(f"floor {floor} > ceiling {ceiling}: rango de bid invalido")
    if policy_version not in (None, POLITICA_PAUSE_ECONOMICA):
        raise ValueError(f"politica pause desconocida: {policy_version!r}")
    moneda = PLATAFORMAS_MONEDA[platform]
    costo_piso = cost_min if cost_min is not None else PAUSE_COST_MIN[platform]
    pausa, motivo_pause_bloqueado = _decide_pause(
        cortes, moneda, umbral_pause, costo_piso, target_acos_pct, policy_version
    )
    if pausa is not None:
        return pausa
    motivo_bids_bloqueado: str | None = None
    if bids is None:
        motivo_bids_bloqueado = MOTIVO_BIDS_SIN_OBSERVACIONES
    elif not bids.completa:
        motivo_bids_bloqueado = MOTIVO_BIDS_INCOMPLETO
    elif bids.metric_currency != moneda:
        motivo_bids_bloqueado = MOTIVO_BIDS_MONEDA_INVALIDA
    elif bids.cost is None or bids.ad_revenue is None:
        motivo_bids_bloqueado = MOTIVO_ACOS_DESCONOCIDO
    else:
        motivo_banda, factor = _factor_ventana_v1(
            bids, target_acos_pct, expected_clicks, costo_piso
        )
        if factor is None:
            motivo_bids_bloqueado = MOTIVO_SIN_BANDA
        elif bid_actual is None:
            motivo_bids_bloqueado = MOTIVO_BID_ACTUAL_AUSENTE
        elif bid_actual <= 0:
            motivo_bids_bloqueado = MOTIVO_BID_ACTUAL_INVALIDO
        elif bid_moneda != moneda:
            motivo_bids_bloqueado = MOTIVO_BID_MONEDA_INVALIDA
        else:
            factor_clamped = min(max(factor, CLAMP_FACTOR_MIN), CLAMP_FACTOR_MAX)
            nuevo = bid_actual * (_UNO + factor_clamped)
            nuevo = min(max(nuevo, floor), ceiling)
            if abs(nuevo - bid_actual) < MIN_DELTA_ABSOLUTO:
                motivo_bids_bloqueado = MOTIVO_DELTA_BAJO_UMBRAL
            else:
                delta = nuevo - bid_actual
                direccion_ok = delta < 0 if factor_clamped < 0 else delta > 0
                magnitud_ok = delta <= CLAMP_FACTOR_MAX * bid_actual and (
                    -delta <= -CLAMP_FACTOR_MIN * bid_actual
                )
                if not (direccion_ok and magnitud_ok):
                    motivo_bids_bloqueado = MOTIVO_RANGO_BLOQUEA_AJUSTE
                else:
                    assert motivo_banda is not None  # factor no None implica motivo
                    return ResultadoBid(
                        kind="bid",
                        motivo=motivo_banda,
                        old_value=bid_actual,
                        new_value=nuevo,  # sin quantize: presentacion la decide el apply (PR2)
                        value_currency=bid_moneda,
                        factor=factor,
                        window_start=bids.window_start,
                        window_end=bids.window_end,
                        data_observed_at=bids.observed_at_max,
                    )
    return ResultadoBid(
        kind=None,
        motivo=motivo_pause_bloqueado or motivo_bids_bloqueado,
        old_value=None,
        new_value=None,
        value_currency=None,
        factor=None,
        window_start=None,
        window_end=None,
        data_observed_at=None,
    )


def _replay_bid(inputs: dict, target: Decimal | None = None) -> ResultadoBid:
    """Re-decide una fila vieja (era bandas, sin inputs.politica) con el camino v1 puro."""
    assert inputs.get("politica") != POLITICA_BID, "fila nueva por el camino viejo"
    return _decide_v1(**_args_replay_bid(inputs, target))


def decide_bid_era_bandas(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """La politica bandas_v1 tal como decidio hasta el corte, MUDADA aqui
    sin cambios (bandas, regla A' de cero ventas, pisos historicos
    REPLAY_*). Devuelve (kind, new_value, value_currency)."""
    resultado = _replay_bid(inputs)
    return (resultado.kind, resultado.new_value, resultado.value_currency)
