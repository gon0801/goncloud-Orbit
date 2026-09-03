"""Replay puro de decisiones desde inputs congelados (corazon de la
auditabilidad; spot-check humano de ORBIT 03 4.4).

Vivio en app/cycle.py hasta ORBIT 05 2.1a: se movio aqui (motor puro, sin
psycopg ni app.ads) para que tools/dossier_adversarial.py pueda replayear sin
cargar app.apply/app.ads. app.cycle lo reexporta (API publica sellada). El
FREEZE (serializacion congelada de inputs) sigue en app/cycle.py: el par
freeze<->replay queda partido a proposito y DECLARADO (allowlist de
test_architecture); cualquier clave nueva en inputs se agrega en los dos.

REPLAY FIEL POR CONSTRUCCION (decision del lead 2026-08-28, cierre CORTES
03): el replay LEE lo congelado, JAMAS recalcula evidencia (el snapshot de la
decision ya no existe) y JAMAS usa un valor vigente. Fila historica sin la
clave rejuega con la HISTORIA de su era (constantes REPLAY_*_PRE_* y
LEGACY_*), nunca con el umbral vigente.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.optimizer import bid, cortes, hygiene, windows


def _dec_de_json(valor) -> Decimal | None:
    """Decimal de vuelta desde el string congelado (regla 4; nunca float)."""
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


def _replay_bid(inputs: dict) -> bid.ResultadoBid:
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
        corte["umbral_clicks_usado"]
        if corte is not None
        else cortes.REPLAY_PAUSE_CLICKS_PRE_CORTES01
    )
    cost_min = (
        Decimal(corte["cost_min_usado"])
        if corte is not None and "cost_min_usado" in corte
        else bid.REPLAY_PAUSE_COST_PRE_CORTES03[inputs["platform"]]
    )
    # BIDS 01 (regla A'): el replay LEE inputs.corte.expected_clicks (el
    # ciclo lo congela desde CORTES 01); fila historica sin la clave -> None
    # -> la regla no aplica y rejuega IGUAL que antes (regla 3).
    expected = (
        _dec_de_json(corte.get("expected_clicks"))
        if corte is not None and corte.get("expected_clicks") is not None
        else None
    )
    return bid.decide_bid(
        platform=inputs["platform"],
        bids=_agregado_sintetico(inputs["ventanas"]["bids"]),
        cortes=_agregado_sintetico(inputs["ventanas"]["cortes"]),
        target_acos_pct=Decimal(inputs["target_acos_pct_usado"]),
        bid_actual=_dec_de_json(inputs["bid_actual"]),
        bid_moneda=inputs["bid_moneda"],
        floor=Decimal(goal["bid_floor"]),
        ceiling=Decimal(goal["bid_ceiling"]),
        umbral_pause=umbral_pause,
        cost_min=cost_min,
        expected_clicks=expected,
    )


def _replay_hygiene(inputs: dict) -> hygiene.ResultadoTermino:
    vt = inputs["ventana_terminos"]
    td = inputs["termino"]
    fin = dt.date.fromisoformat(vt["window_end"])
    observed = td["observed_at_max"]
    termino = windows.AgregadoTermino(
        ad_entity_id=0,  # no consumido por el motor; identidad no congelada
        search_term=td["search_term"],
        metric_currency=td["moneda"],
        cost=_dec_de_json(td["cost"]),
        ad_revenue=_dec_de_json(td["ad_revenue"]),
        clicks=td["clicks"],
        orders=td["orders"],
        fechas_distintas=td["fechas_distintas"],
        is_asin_like=False,  # un termino ASIN-like JAMAS genera decision (2.3)
        observed_at_max=dt.datetime.fromisoformat(observed) if observed else None,
    )
    terminos = windows.TerminosCortes(
        ad_entity_id=0,
        window_start=dt.date.fromisoformat(vt["window_start"]),
        window_end=fin,
        fechas_entidad=_fechas_sinteticas(fin, vt["fechas"]),
        terminos=(termino,),
    )
    harvest = inputs["goal"]["harvest"]
    config = (
        hygiene.ConfigHarvest(
            campaign_id=harvest["campaign_id"],
            ad_group_id=harvest["ad_group_id"],
            default_bid=Decimal(harvest["default_bid"]),
            moneda=harvest["moneda"],
        )
        if harvest
        else None
    )
    # CORTES 01 (spec): el replay LEE inputs.corte.umbral_clicks_usado y
    # piso_cost_usado, JAMAS recalcula evidencia ni AOV (el snapshot de la
    # decision ya no existe). Fila historica sin la clave (pre-CORTES, o
    # congelada en 1.2/1.3 sin piso) -> legacy 20 y 8/130, replay exacto.
    corte = inputs.get("corte")
    umbral_negative = corte["umbral_clicks_usado"] if corte is not None else cortes.LEGACY_NEGATIVE
    piso = (
        Decimal(corte["piso_cost_usado"])
        if corte is not None and "piso_cost_usado" in corte
        else None
    )
    (resultado,) = hygiene.decide_hygiene(
        platform=inputs["platform"],
        terminos=terminos,
        target_acos_pct=Decimal(inputs["target_acos_pct_usado"]),
        config_harvest=config,
        # keywords_existentes vacio: una decision de harvest solo existe si el
        # termino NO estaba duplicado al decidir (replay contra nada bloquea).
        keywords_existentes=frozenset(),
        umbral_negative=umbral_negative,
        piso_negative=piso,
    )
    return resultado


def reproduce(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """Re-decide UNA decision desde sus inputs congelados y devuelve
    (kind, new_value, value_currency). Es la funcion del spot-check humano
    (4.4): reproduce(inputs) debe igualar la decision persistida.

    Reconstruye agregados SINTETICOS (fechas = n fechas dentro de la ventana:
    el conteo es lo que replayea `completa`) y llama al motor puro con los
    valores congelados (Decimal(str) de vuelta, jamas float)."""
    motor = inputs.get("motor")
    if motor == "bid":
        resultado = _replay_bid(inputs)
    elif motor == "hygiene":
        resultado = _replay_hygiene(inputs)
    else:
        raise ValueError(f"inputs.motor fuera del vocabulario {{bid, hygiene}}: {motor!r}")
    return (resultado.kind, resultado.new_value, resultado.value_currency)
