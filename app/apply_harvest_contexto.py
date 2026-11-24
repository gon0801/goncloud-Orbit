"""Resolucion del contexto de una cosecha: origen, destino y bid por defecto.

Extraido de `app.apply_harvest` (ARREGLOS 05 B, presupuesto de tamano). Una
sola pieza de conocimiento: de donde sale el destino de la keyword y con que
piso y techo se clampa su bid. Del congelado F2 de la decision (grupo,
excepcion o terna) cuando lo hay; si no, del goal fresco del grupo origen.
Toda falta de dato es un motivo, nunca un valor inventado (regla 3).
"""

from __future__ import annotations

from decimal import Decimal

import psycopg

from app import apply
from app.apply_harvest_list import (
    MOTIVO_BID_DEFAULT_FALTANTE,
    MOTIVO_ENTIDAD_INCOMPLETA,
    MOTIVO_MONEDA_INCOHERENTE,
    MOTIVO_SIN_CONFIG,
    _Contexto,
    _Job,
)
from app.apply_harvest_reversa import _SQL_DECISION, _SQL_EXTERNALES, _SQL_PADRE
from app.optimizer import goals as g
from app.optimizer import harvest_destino, hygiene

_SQL_DESTINO_PERTENECE = """
SELECT 1
  FROM ad_entity ag
  JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
 WHERE ag.kind = 'ad_group'
   AND ag.external_id = %s AND c.external_id = %s
   AND ag.platform = %s::platform AND c.platform = %s::platform
"""


def _goal_del_grupo(conn: psycopg.Connection, platform: str, campaign_pk: int) -> g.Goal | None:
    """Goal resuelto del GRUPO origen (precedencia campana > plataforma)."""
    goals = [g.Goal(*f) for f in conn.execute(apply._SQL_GOALS_ENTIDAD, (platform, campaign_pk))]
    goal_campana = next((x for x in goals if x.scope == "campaign"), None)
    goal_plataforma = next((x for x in goals if x.scope != "campaign"), None)
    return g.resuelve_goal(goal_campana, goal_plataforma)


def _contexto(conn: psycopg.Connection, job: _Job) -> _Contexto:
    """Origen, destino y default: del congelado F2 o del goal fresco."""
    externos = conn.execute(_SQL_EXTERNALES, (job.ad_entity_id,)).fetchone()
    if externos is None:
        raise ValueError(MOTIVO_ENTIDAD_INCOMPLETA)
    padre = conn.execute(_SQL_PADRE, (job.ad_entity_id,)).fetchone()
    new_value, value_currency, inputs = conn.execute(_SQL_DECISION, (job.decision_id,)).fetchone()
    congelado = ((inputs or {}).get("goal") or {}).get("harvest") or {}
    if congelado.get("resuelto_por") in (
        harvest_destino.RESUELTO_GRUPO,
        harvest_destino.RESUELTO_EXCEPCION,
        harvest_destino.RESUELTO_TERNA,
    ):
        return _contexto_congelado(
            conn, job, padre, externos, new_value, value_currency, inputs, congelado
        )
    goal = _goal_del_grupo(conn, job.plataforma, padre[0]) if padre is not None else None
    if goal is None or goal.harvest_campaign_id is None or goal.harvest_ad_group_id is None:
        raise ValueError(MOTIVO_SIN_CONFIG)
    if new_value is None:
        raise ValueError(MOTIVO_BID_DEFAULT_FALTANTE)
    if value_currency != goal.bid_currency:
        raise ValueError(MOTIVO_MONEDA_INCOHERENTE)
    if goal.harvest_ad_group_id == externos[0]:
        raise ValueError(hygiene.MOTIVO_ORIGEN_ES_DESTINO)
    floor, ceiling = g.resuelve_floor_ceiling(goal, goal.bid_currency)
    return _Contexto(
        plataforma=job.plataforma,
        grupo_ext=externos[0],
        campana_ext=externos[1],
        destino_grupo=goal.harvest_ad_group_id,
        destino_campana=goal.harvest_campaign_id,
        default_bid=new_value,
        moneda=value_currency,
        floor=floor,
        ceiling=ceiling,
        resuelto_por=None,
        grupo_id=None,
    )


def _contexto_congelado(
    conn: psycopg.Connection,
    job: _Job,
    padre,
    externos,
    new_value,
    value_currency,
    inputs: dict,
    congelado: dict,
) -> _Contexto:
    if new_value is None:
        raise ValueError(MOTIVO_BID_DEFAULT_FALTANTE)
    destino_campana = congelado.get("campaign_id")
    destino_grupo = congelado.get("ad_group_id")
    if not destino_campana or not destino_grupo:
        raise ValueError(MOTIVO_SIN_CONFIG)
    if value_currency != congelado.get("moneda"):
        raise ValueError(MOTIVO_MONEDA_INCOHERENTE)
    if (
        congelado.get("resuelto_por")
        in (
            harvest_destino.RESUELTO_EXCEPCION,
            harvest_destino.RESUELTO_TERNA,
        )
        and destino_grupo == externos[0]
    ):
        raise ValueError(hygiene.MOTIVO_ORIGEN_ES_DESTINO)
    if congelado.get("resuelto_por") == harvest_destino.RESUELTO_GRUPO and padre is not None:
        vigente = harvest_destino.resolver_destino(conn, job.plataforma, padre[0])
        if not (
            isinstance(vigente, harvest_destino.DestinoHarvest)
            and vigente.resuelto_por == harvest_destino.RESUELTO_GRUPO
            and vigente.campaign_external == destino_campana
            and vigente.ad_group_external == destino_grupo
        ):
            raise ValueError(hygiene.MOTIVO_DESTINO_DESINCRONIZADO)
    pertenece = conn.execute(
        _SQL_DESTINO_PERTENECE,
        (destino_grupo, destino_campana, job.plataforma, job.plataforma),
    ).fetchone()
    if pertenece is None:
        raise ValueError(hygiene.MOTIVO_DESTINO_DESINCRONIZADO)
    goal_congelado = inputs.get("goal") or {}
    return _Contexto(
        plataforma=job.plataforma,
        grupo_ext=externos[0],
        campana_ext=externos[1],
        destino_grupo=destino_grupo,
        destino_campana=destino_campana,
        default_bid=new_value,
        moneda=value_currency,
        floor=Decimal(str(goal_congelado.get("bid_floor"))),
        ceiling=Decimal(str(goal_congelado.get("bid_ceiling"))),
        resuelto_por=congelado.get("resuelto_por"),
        grupo_id=congelado.get("grupo_id"),
    )
