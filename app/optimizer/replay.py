"""Replay puro de decisiones desde inputs congelados."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.optimizer import bid, cortes, hygiene, windows


def _dec_de_json(valor) -> Decimal | None:
    return Decimal(str(valor)) if valor is not None else None


def _fechas_sinteticas(window_end: dt.date, n: int) -> tuple[dt.date, ...]:
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
        impressions=None,
        clicks=d["clicks"],
        orders=d["orders"],
        observed_at_max=dt.datetime.fromisoformat(observed) if observed else None,
    )


def _replay_bid(inputs: dict) -> bid.ResultadoBid:
    goal = inputs["goal"]
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
    )


def _replay_hygiene(inputs: dict) -> hygiene.ResultadoTermino:
    vt = inputs["ventana_terminos"]
    td = inputs["termino"]
    fin = dt.date.fromisoformat(vt["window_end"])
    observed = td["observed_at_max"]
    termino = windows.AgregadoTermino(
        ad_entity_id=0,
        search_term=td["search_term"],
        metric_currency=td["moneda"],
        cost=_dec_de_json(td["cost"]),
        ad_revenue=_dec_de_json(td["ad_revenue"]),
        clicks=td["clicks"],
        orders=td["orders"],
        fechas_distintas=td["fechas_distintas"],
        is_asin_like=False,
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
        keywords_existentes=frozenset(),
        umbral_negative=umbral_negative,
        piso_negative=piso,
    )
    return resultado


def reproduce(inputs: dict) -> tuple[str | None, Decimal | None, str | None]:
    """Re-decide una decision desde sus inputs congelados."""
    motor = inputs.get("motor")
    if motor == "bid":
        resultado = _replay_bid(inputs)
    elif motor == "hygiene":
        resultado = _replay_hygiene(inputs)
    else:
        raise ValueError(f"inputs.motor fuera del vocabulario {{bid, hygiene}}: {motor!r}")
    return (resultado.kind, resultado.new_value, resultado.value_currency)
