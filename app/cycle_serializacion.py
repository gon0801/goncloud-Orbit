"""Serializacion congelada de decisiones, proteccion y notas del ciclo (Fase 3.4).

Helpers puros de armado de filas `_Pendiente`, freeze de `inputs` (corte,
agregado, goal, bid, pause, termino), sello bitemporal, proteccion por venta
reciente y armado de `notes`. Sin IO directo de base de datos ni imports de
`app.cycle` (DAG: `cycle -> cycle_serializacion`).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app import proteccion_venta
from app.optimizer import bid, cortes, harvest_destino, hygiene, windows
from app.optimizer import goals as g
from app.optimizer import proteccion as subproteccion
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.caso import (
    POLITICA_BID,
    InsumosPausa,
    InsumosProteccion,
    _proteccion_json,
)
from app.optimizer.politica import Mover, Pausar

if TYPE_CHECKING:
    from app.cycle import _Contadores


@dataclass(frozen=True)
class _Pendiente:
    """Una decision lista para escribir en TX3 (la fila se congela en TX2).
    `evaluacion_producto` es el product_id de la evaluacion que la decidio
    (AUTO-03 F.2); TX3 lo traduce a decision.evaluacion_producto_id."""

    ad_entity_id: int
    kind: str
    data_observed_at: dt.datetime
    window_start: dt.date
    window_end: dt.date
    search_term: str | None
    old_value: Decimal | None
    new_value: Decimal | None
    value_currency: str | None
    inputs: dict
    evaluacion_producto: int | None = None


def _dec_str(valor: Decimal | None) -> str | None:
    """Decimal -> string TAL CUAL llega de la DB: la escala del string
    (ej '1.0000', '25.00') es artefacto deterministico del NUMERIC de origen
    (money_amount NUMERIC(14,4), acos NUMERIC(6,2)) y hay que CONSERVARLA —
    una "normalizacion" futura aqui romperia las comparaciones de auditoria
    y el golden replay (hallazgo reviewer 3.1)."""
    return str(valor) if valor is not None else None


def _ts(momento: dt.datetime | None) -> str | None:
    return momento.isoformat() if momento is not None else None


def _fecha_iso(fecha: dt.date | None) -> str | None:
    return fecha.isoformat() if fecha is not None else None


def _corte_json(
    corte: cortes.UmbralResuelto,
    evidencia: windows.EvidenciaAdGroup | None,
    piso: cortes.PisoResuelto | None = None,
    cost_min: Decimal | None = None,
) -> dict:
    """Freeze de `inputs.corte` TOP-LEVEL (CORTES 01; shape EXACTO del spec
    v3): umbral_clicks_usado es el FINAL con piso aplicado, expected_clicks
    viaja como string Decimal (regla 4) y evidencia es null cuando el grupo
    no esta en el dict (fallback; jamas un numero inventado). Desde 1.4, el
    camino negative congela ADEMAS el piso de cost resuelto (piso_cost_usado
    + aov como string Decimal|null; misma regla 4). Desde el cierre CORTES
    03, el MOTOR DE BIDS congela ADEMAS cost_min_usado (string Decimal)
    cuando le llega `cost_min`."""
    freeze = {
        "umbral_clicks_usado": corte.umbral,
        "elegible": corte.elegible,
        "expected_clicks": _dec_str(corte.expected_clicks),
        "evidencia": (
            {
                "clicks": evidencia.clicks,
                "orders": evidencia.orders,
                "fechas": evidencia.fechas_distintas,
                "ventana_desde": evidencia.ventana_desde.isoformat(),
                "ventana_hasta": evidencia.ventana_hasta.isoformat(),
                "observed_at_max": _ts(evidencia.observed_at_max),
            }
            if evidencia is not None
            else None
        ),
    }
    if piso is not None:
        freeze["piso_cost_usado"] = _dec_str(piso.piso_cost)
        freeze["aov"] = _dec_str(piso.aov)
    if cost_min is not None:
        freeze["cost_min_usado"] = _dec_str(cost_min)
    return freeze


def _sello_bitemporal(
    decided_at: dt.datetime,
    obs_directo: dt.datetime | None,
    evidencia: windows.EvidenciaAdGroup | None,
) -> dt.datetime | None:
    """data_observed_at = LEAST(decided_at, max(obs directo, observed_at_max
    de la evidencia)) -- el CLAMP es OBLIGATORIO: el CHECK
    decision_dato_no_del_futuro exige data_observed_at <= decided_at."""
    base = obs_directo
    if evidencia is not None and evidencia.observed_at_max is not None:
        base = evidencia.observed_at_max if base is None else max(base, evidencia.observed_at_max)
    if base is None:
        return None
    return min(decided_at, base)


def _agregado_json(agg: windows.AgregadoMetricas | None) -> dict | None:
    if agg is None:
        return None
    return {
        "window_start": agg.window_start.isoformat(),
        "window_end": agg.window_end.isoformat(),
        "fechas": len(agg.fechas),
        "cost": _dec_str(agg.cost),
        "ad_revenue": _dec_str(agg.ad_revenue),
        "revenue_same_sku": _dec_str(agg.revenue_same_sku),
        "clicks": agg.clicks,
        "orders": agg.orders,
        "moneda": agg.metric_currency,
        "observed_at_max": _ts(agg.observed_at_max),
    }


def _goal_json(
    goal: g.Goal,
    moneda: str,
    destino: harvest_destino.DestinoHarvest | harvest_destino.SaltoHarvest | None = None,
) -> dict:
    """Goal resuelto congelado (estructura sellada que consume el replay).
    bid_floor/bid_ceiling se congelan EFECTIVOS (resuelve_floor_ceiling)."""
    if isinstance(destino, harvest_destino.DestinoHarvest):
        if destino.bid is None or destino.moneda is None:
            completa = False
            harvest = None
        else:
            completa = True
            harvest = {
                "campaign_id": destino.campaign_external,
                "ad_group_id": destino.ad_group_external,
                "default_bid": _dec_str(destino.bid),
                "moneda": destino.moneda,
                "resuelto_por": destino.resuelto_por,
                "grupo_id": destino.grupo_id,
                "motivo": destino.motivo,
            }
    else:
        completa = (
            goal.harvest_campaign_id is not None
            or goal.harvest_ad_group_id is not None
            or goal.harvest_default_bid is not None
        )
        harvest = (
            {
                "campaign_id": goal.harvest_campaign_id,
                "ad_group_id": goal.harvest_ad_group_id,
                "default_bid": _dec_str(goal.harvest_default_bid),
                "moneda": goal.bid_currency,
            }
            if completa
            else None
        )
    floor, ceiling = g.resuelve_floor_ceiling(goal, moneda)
    return {
        "scope": goal.scope,
        "target_acos_pct": _dec_str(goal.target_acos_pct),
        "bid_floor": _dec_str(floor),
        "bid_ceiling": _dec_str(ceiling),
        "harvest": harvest,
    }


def pendiente_bid(
    caso,
    veredicto,
    *,
    modo: str,
    goal: g.Goal,
    decidido_el: dt.datetime,
    procedencia: str,
    snapshot: dict | None,
    corte: cortes.UmbralResuelto,
    evidencia: windows.EvidenciaAdGroup | None,
    cost_min: Decimal,
) -> _Pendiente:
    """Fila de `decision` lista para insertar (BIDS 02 M.3, bosquejo
    cycle.py). Mover y Regresar son kind bid, Pausar es kind pause y
    Mantener jamas llega aqui."""
    if isinstance(veredicto, Pausar):
        kind = "pause"
        cortes_agg = caso.pausa.cortes
        assert cortes_agg is not None  # R0 solo pausa sobre cortes medidos
        window_start = cortes_agg.window_start
        window_end = cortes_agg.window_end
        obs = cortes_agg.observed_at_max
        old_value = None
        new_value = None
        value_currency = None
        factor = None
        nivel = None
    else:
        kind = "bid"
        window_start = caso.ventana_desde
        window_end = caso.ventana_hasta
        obs = caso.observado_al
        old_value = caso.bid.valor
        new_value = veredicto.bid_nuevo
        value_currency = caso.bid.moneda
        factor = veredicto.factor if isinstance(veredicto, Mover) else None
        nivel = veredicto.nivel if isinstance(veredicto, Mover) else None
    inputs = {
        "motor": "bid",
        "politica": POLITICA_BID,
        "platform": caso.plataforma,
        "modo": modo,
        "motivo": veredicto.motivo,
        "factor": _dec_str(factor),
        "nivel": nivel,
        "veredicto_kind": type(veredicto).__name__,
        "caso": caso.como_json(),
        "target_acos_pct_usado": _dec_str(caso.economia.target_acos_pct),
        "target_procedencia": procedencia,
        **({"target_snapshot": snapshot} if snapshot is not None else {}),
        "goal": _goal_json(goal, PLATAFORMAS_MONEDA[caso.plataforma]),
        "bid_actual": _dec_str(caso.bid.valor),
        "bid_moneda": caso.bid.moneda,
        "corte": _corte_json(corte, evidencia, cost_min=cost_min),
    }
    return _Pendiente(
        ad_entity_id=caso.hoja_id,
        kind=kind,
        data_observed_at=_sello_bitemporal(decidido_el, obs, None),
        window_start=window_start,
        window_end=window_end,
        search_term=None,  # bid/pause deciden sobre la entidad (CHECK del esquema)
        old_value=old_value,
        new_value=new_value,
        value_currency=value_currency,
        inputs=inputs,
    )


def _pendiente_pause_apagada(
    entidad_id: int,
    resultado: bid.ResultadoBid,
    *,
    platform: str,
    modo: str,
    goal: g.Goal,
    cortes: windows.AgregadoMetricas | None,
    target: Decimal,
    procedencia: str,
    snapshot: dict | None,
    bid_actual: Decimal | None,
    bid_moneda: str | None,
    decided_at: dt.datetime,
    corte: cortes.UmbralResuelto,
    evidencia: windows.EvidenciaAdGroup | None,
    cost_min: Decimal,
    policy_version: str | None,
    proteccion: InsumosProteccion | None = None,
) -> _Pendiente:
    """La PAUSE con la politica apagada (BIDS 02 M.3, cambio 4): freeze
    sin caso ni politica, con la forma de la era anterior (ventanas +
    corte + version economica) para que el replay la re-decida por eras."""
    assert resultado.kind == "pause"
    assert cortes is not None
    inputs = {
        "motor": "bid",
        "platform": platform,
        "ventanas": {
            "bids": None,
            "cortes": _agregado_json(cortes),
        },
        "goal": _goal_json(goal, PLATAFORMAS_MONEDA[platform]),
        "target_acos_pct_usado": _dec_str(target),
        "economic_policy": {"version": policy_version},
        "target_procedencia": procedencia,
        **({"target_snapshot": snapshot} if snapshot is not None else {}),
        "bid_actual": _dec_str(bid_actual),
        "bid_moneda": bid_moneda,
        "factor": None,
        "motivo": resultado.motivo,
        "modo": modo,
        "corte": _corte_json(corte, evidencia, cost_min=cost_min),
        "proteccion": _proteccion_json(proteccion),
    }
    return _Pendiente(
        ad_entity_id=entidad_id,
        kind="pause",
        data_observed_at=_sello_bitemporal(decided_at, resultado.data_observed_at, None),
        window_start=resultado.window_start,
        window_end=resultado.window_end,
        search_term=None,
        old_value=None,
        new_value=None,
        value_currency=None,
        inputs=inputs,
    )


def _pendiente_termino(
    grupo_id: int,
    termino: windows.AgregadoTermino,
    resultado: hygiene.ResultadoTermino,
    *,
    platform: str,
    modo: str,
    goal: g.Goal,
    terminos: windows.TerminosCortes,
    target: Decimal,
    procedencia: str,
    snapshot: dict | None,
    decided_at: dt.datetime,
    corte: cortes.UmbralResuelto | None = None,
    evidencia: windows.EvidenciaAdGroup | None = None,
    piso: cortes.PisoResuelto | None = None,
    destino: harvest_destino.DestinoHarvest | harvest_destino.SaltoHarvest | None = None,
) -> _Pendiente:
    """Freeze de decisiones de terminos (negative/harvest)."""
    data_observed = resultado.data_observed_at
    inputs = {
        "motor": "hygiene",
        "platform": platform,
        "ventana_terminos": {
            "window_start": _fecha_iso(terminos.window_start),
            "window_end": _fecha_iso(terminos.window_end),
            "fechas": len(terminos.fechas_entidad),
        },
        "termino": {
            "search_term": termino.search_term,
            "cost": _dec_str(termino.cost),
            "ad_revenue": _dec_str(termino.ad_revenue),
            "clicks": termino.clicks,
            "orders": termino.orders,
            "fechas_distintas": termino.fechas_distintas,
            "moneda": termino.metric_currency,
            "observed_at_max": _ts(termino.observed_at_max),
        },
        "goal": _goal_json(goal, PLATAFORMAS_MONEDA[platform], destino),
        "target_acos_pct_usado": _dec_str(target),
        # ORBIT 06 2.3: peldano ganador + snapshot SOLO si gana el margen.
        "target_procedencia": procedencia,
        **({"target_snapshot": snapshot} if snapshot is not None else {}),
        "motivo": resultado.motivo,
        "modo": modo,
    }
    if corte is not None:
        inputs["corte"] = _corte_json(corte, evidencia, piso)
        # sello bitemporal compartido con el motor de bids (1.3): la
        # evidencia entra al max y el LEAST clampea a decided_at
        data_observed = _sello_bitemporal(decided_at, data_observed, evidencia)
    return _Pendiente(
        ad_entity_id=grupo_id,
        kind=resultado.kind,
        data_observed_at=data_observed,
        window_start=resultado.window_start,
        window_end=resultado.window_end,
        search_term=resultado.search_term,
        old_value=None,  # negative sin dinero; harvest nace con bid nuevo
        new_value=resultado.new_value,
        value_currency=resultado.value_currency,
        inputs=inputs,
    )


def _proteccion_activa(
    *,
    platform: str,
    entidad_id: int,
    target: Decimal,
    corte_pause: cortes.UmbralResuelto,
    cortes_hoja: windows.AgregadoMetricas | None,
    costo_piso: Decimal,
    pausa: InsumosPausa,
    proteccion_cache: proteccion_venta.CacheProteccion | None,
    contadores: _Contadores,
    decided_at: dt.datetime,
) -> bool:
    """ORBIT 07 M1: True si la subpolitica pura frena la pausa por venta
    reciente. Registra la transicion append-only del episodio cuando la abre."""
    if pausa.proteccion is None:
        return False
    if pausa.proteccion.episodio is None and pausa.proteccion.reciente.ancla is None:
        return False
    resultado = bid.decide_pause(
        platform=platform,
        bids=None,
        cortes=cortes_hoja,
        target_acos_pct=target,
        umbral_pause=corte_pause.umbral,
        cost_min=costo_piso,
        policy_version=pausa.politica_economica,
        proteccion=pausa.proteccion,
    )
    if resultado.proteccion is None or resultado.motivo not in (
        subproteccion.MOTIVO_PROTEGIDA,
        subproteccion.MOTIVO_INDETERMINADA,
    ):
        return False
    _registra_proteccion(
        contadores, proteccion_cache, entidad_id, resultado.proteccion, decidido_el=decided_at
    )
    return True


def _registra_proteccion(
    contadores: _Contadores,
    cache: proteccion_venta.CacheProteccion | None,
    entidad_id: int,
    resultado,
    *,
    decidido_el: dt.datetime,
) -> None:
    """Anexa la transicion append-only del episodio (TX3 la inserta)."""
    if cache is None:
        return
    transicion = cache.transicion(entidad_id, resultado, decidido_el)
    if transicion is not None:
        contadores.protecciones.append(transicion)


def _notas_cuerpo(
    contadores: _Contadores,
    ciclos_muertos: list[int],
    degradacion_live: str | None,
    motivo_skip: str | None,
    detalle: str | None,
    target: dict | None = None,
    harvest_destino: dict | None = None,
    temporada: dict | None = None,
) -> dict:
    cuerpo = {
        "skips": {
            "entidad": dict(contadores.skips_entidad),
            "termino": dict(contadores.skips_termino),
        },
        "decisiones": dict(contadores.decisiones),
        "entidades": contadores.entidades,
        "ad_groups": contadores.ad_groups,
        "terminos": contadores.terminos,
        "ciclos_muertos": ciclos_muertos,
        "degradacion_live": degradacion_live,
    }
    if target is not None:
        cuerpo["target"] = target
    if harvest_destino is not None:
        cuerpo["harvest_destino"] = harvest_destino
    # TEMPORADA 01 D: la sombra de la baja comparada caso a caso (solo con
    # ads_temporada_<plat> en shadow; claves ausentes = no se comparo nada).
    if temporada is not None:
        cuerpo["temporada"] = temporada
    if motivo_skip is not None:
        cuerpo["motivo_skip"] = motivo_skip
        cuerpo["detalle"] = detalle
    return cuerpo


def _notas_json(
    contadores: _Contadores,
    ciclos_muertos: list[int],
    degradacion_live: str | None,
    motivo_skip: str | None = None,
    detalle: str | None = None,
) -> str:
    return json.dumps(
        _notas_cuerpo(contadores, ciclos_muertos, degradacion_live, motivo_skip, detalle),
        ensure_ascii=False,
        default=str,
    )
