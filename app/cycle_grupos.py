"""Lectura de goals, gates y recorrido de ad groups (terminos/harvest) del ciclo Ads.

Extraido de `app/cycle.py` (Ola D1) para mantener un DAG aciclico
donde cada modulo del ciclo queda por debajo del techo de 900 lineas.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal

import psycopg

from app import (
    apply,
    apply_cola,
    notifica,
    propuestas_campana,
    proteccion_venta,
    temporada_io,
)
from app.cycle_serializacion import _Pendiente, _pendiente_termino
from app.cycle_sombra import _ProductosCiclo
from app.cycle_target import _FamiliasCiclo, _margen_familia_de_hoja
from app.optimizer import cortes, harvest_destino, hygiene, temporada, windows
from app.optimizer import goals as g

MOTIVO_SIN_GOAL = "sin_goal"
MOTIVO_GOAL_DISABLED = "goal_disabled"
MOTIVO_GOAL_MODE_OFF = "goal_mode_off"
MOTIVO_ESTADO_NO_ENABLED = "estado_no_enabled"
MOTIVO_CAMPANA_NO_ENABLED = apply.MOTIVO_CAMPANA_NO_ENABLED
MOTIVO_GRUPO_NO_ENABLED = apply.MOTIVO_GRUPO_NO_ENABLED
MOTIVO_BID_EN_VUELO = apply.MOTIVO_BID_EN_VUELO
MOTIVO_ENTIDAD_INERTE = "entidad_inerte"
MOTIVO_COOLDOWN_7D = "cooldown_7d"
MOTIVO_SIN_TARGET = "sin_target"
MOTIVO_POLITICA_APAGADA = "politica_apagada"
MOTIVO_ESCALERA_OFF = "escalera_off"
MOTIVO_VETO_PENDIENTE = apply_cola.MOTIVO_VETO_PENDIENTE
MOTIVO_PAUSA_PRODUCTO_PENDIENTE = "pausa_producto_pendiente"

_SQL_GOALS = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
 WHERE platform = %s::platform OR ad_entity_id = ANY(%s::bigint[])
"""

_SQL_GRUPOS = """
SELECT ag.id, ag.parent_id AS campaign_id, s.status, sc.status AS status_campana,
       ag.external_id
  FROM ad_entity ag
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = ag.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = ag.parent_id
 WHERE ag.platform = %s::platform AND ag.kind = 'ad_group'
 ORDER BY ag.id
"""

_SQL_MEMBRESIA_GRUPO = """
SELECT r.grupo_id, r.rol::text, c.external_id, c.name
  FROM campana_grupo_rol r
  JOIN ad_entity c ON c.id = r.ad_entity_id
 WHERE r.ad_entity_id = %s
"""

_MOTIVOS_AVISO_DESTINO = frozenset(
    {hygiene.MOTIVO_DESTINO_INCONSISTENTE, hygiene.MOTIVO_SIN_DESTINO_HARVEST}
)

_SQL_SALTOS_PREVIOS = """
SELECT notes
  FROM optimizer_cycle
 WHERE platform = %s::platform AND motor = 'ads_optimizer' AND id < %s
   AND status IN ('done', 'degraded')
 ORDER BY id DESC
 LIMIT 20
"""


@dataclass
class _Contadores:
    """Contadores mutables llenados durante TX2 (parcial honesto si falla el ciclo)."""

    skips_entidad: Counter = field(default_factory=Counter)
    skips_termino: Counter = field(default_factory=Counter)
    decisiones: Counter = field(default_factory=Counter)
    entidades: int = 0
    ad_groups: int = 0
    terminos: int = 0
    destinos: Counter = field(default_factory=Counter)
    saltos_grupo: dict[int, notifica.SaltoDestinoGrupo] = field(default_factory=dict)
    propuestas_campana: tuple[propuestas_campana.Evaluacion, ...] = ()
    targets: dict[int, tuple[Decimal, str]] = field(default_factory=dict)
    productos: _ProductosCiclo | None = None
    protecciones: list[proteccion_venta.TransicionProteccion] = field(default_factory=list)
    temporada_plat: temporada_io.TemporadaPlataforma | None = None
    reactivaciones: tuple[temporada_io.Reactivacion, ...] = ()
    excluidas_temporada: dict[int, str] = field(default_factory=dict)
    fines: dict[int, temporada.FinReduccion] = field(default_factory=dict)
    exclusiones: dict[int, Decimal] = field(default_factory=dict)
    temporada_sombra_comparadas: int = 0
    temporada_sombra_coinciden: int = 0
    temporada_sombra_diferencias: list[dict] = field(default_factory=list)
    ubicaciones: list[tuple] = field(default_factory=list)
    resumen_ubicaciones: dict | None = None
    # P03: evaluaciones de la pausa economica v2 (fila por hoja juzgada),
    # su bloque de notas y el error de lectura del cache (SAVEPOINT).
    pausa_economica_evals: list = field(default_factory=list)
    pausa_economica_resumen: dict | None = None
    pausa_economica_error: str | None = None
    pausa_economica_anomalia_tope: bool = False
    pausa_economica_sin_cortes: int = 0


def lee_goals(
    conn: psycopg.Connection, platform: str, ids_campanas: list[int]
) -> tuple[g.Goal | None, dict[int, g.Goal]]:
    fila_goal = conn.execute(_SQL_GOALS, (platform, ids_campanas)).fetchall()
    goal_plataforma: g.Goal | None = None
    por_campana: dict[int, g.Goal] = {}
    for fila in fila_goal:
        goal = g.Goal(
            scope=fila[0],
            ad_entity_id=fila[1],
            platform=fila[2],
            target_acos_pct=fila[3],
            bid_floor=fila[4],
            bid_ceiling=fila[5],
            bid_currency=fila[6],
            harvest_campaign_id=fila[7],
            harvest_ad_group_id=fila[8],
            harvest_default_bid=fila[9],
            enabled=fila[10],
            mode=fila[11],
        )
        if goal.scope == "platform":
            goal_plataforma = goal
        elif goal.ad_entity_id is not None:
            por_campana[goal.ad_entity_id] = goal
    return (goal_plataforma, por_campana)


def _porta_goal_campana(
    por_campana: dict[int, g.Goal], goal_plataforma: g.Goal | None, campaign_id
) -> tuple[g.Goal | None, str | None]:
    resuelto = g.resuelve_goal(por_campana.get(campaign_id), goal_plataforma)
    if resuelto is None:
        return (None, MOTIVO_SIN_GOAL)
    if not resuelto.enabled:
        return (None, MOTIVO_GOAL_DISABLED)
    if resuelto.mode == "off":
        return (None, MOTIVO_GOAL_MODE_OFF)
    return (resuelto, None)


def _gates_entidad(
    conn: psycopg.Connection,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    *,
    campaign_id,
    entidad_id: int,
    status,
    ancestros: tuple[tuple[str, str | None], ...],
    decided_at: dt.datetime,
    comprobar_cooldown: bool = True,
) -> tuple[g.Goal | None, str | None]:
    goal_plataforma, por_campana = goals
    goal, motivo = _porta_goal_campana(por_campana, goal_plataforma, campaign_id)
    for motivo_ancestro, status_ancestro in ancestros:
        if motivo is None and status_ancestro != "ENABLED":
            motivo = motivo_ancestro
    if motivo is None and status != "ENABLED":
        motivo = MOTIVO_ESTADO_NO_ENABLED
    if motivo is None and comprobar_cooldown and g.en_cooldown(conn, entidad_id, ahora=decided_at):
        motivo = MOTIVO_COOLDOWN_7D
    return (goal, motivo)


def _resuelve_target_y_procedencia(
    goal: g.Goal,
    setting_target: Decimal | None,
    margen_plataforma: Decimal | None,
    target_margen_familia: Decimal | None,
    *,
    target_margen_producto: Decimal | None = None,
) -> tuple[Decimal | None, str | None]:
    """Calcula `(target, procedencia)` validando los peldanos una sola vez."""
    target = g.cascada_target_acos(
        goal.target_acos_pct,
        setting_target,
        margen_plataforma,
        goal.scope,
        target_margen_familia,
        target_margen_producto=target_margen_producto,
    )
    if target is None:
        return None, None
    procedencia = g.peldano_target_acos(
        goal.target_acos_pct,
        goal.scope,
        margen_plataforma,
        setting_target,
        target_margen_familia,
        target_margen_producto=target_margen_producto,
    )
    return target, procedencia


def _config_harvest_de(
    conn: psycopg.Connection,
    goal: g.Goal,
    platform: str,
    destino: harvest_destino.DestinoHarvest | harvest_destino.SaltoHarvest | None,
) -> tuple[hygiene.ConfigHarvest | None, frozenset[str], str | None]:
    if destino is None:
        if (
            goal.harvest_campaign_id is None
            or goal.harvest_ad_group_id is None
            or goal.harvest_default_bid is None
        ):
            return (None, frozenset(), None)
        config = hygiene.ConfigHarvest(
            campaign_id=goal.harvest_campaign_id,
            ad_group_id=goal.harvest_ad_group_id,
            default_bid=goal.harvest_default_bid,
            moneda=goal.bid_currency,
        )
        return (
            config,
            hygiene.keywords_campana_destino(conn, platform, goal.harvest_campaign_id),
            None,
        )
    if not isinstance(destino, harvest_destino.DestinoHarvest):
        return (None, frozenset(), destino.motivo)
    if destino.bid is None or destino.moneda is None:
        return (None, frozenset(), hygiene.MOTIVO_HARVEST_SIN_CONFIG)
    config = hygiene.ConfigHarvest(
        campaign_id=destino.campaign_external,
        ad_group_id=destino.ad_group_external,
        default_bid=destino.bid,
        moneda=destino.moneda,
    )
    return (
        config,
        hygiene.keywords_campana_destino(conn, platform, destino.campaign_external),
        None,
    )


def _registra_destino(
    conn: psycopg.Connection,
    platform: str,
    campaign_id: int,
    destino: harvest_destino.DestinoHarvest | harvest_destino.SaltoHarvest | None,
    contadores: _Contadores,
    *,
    tiene_grupos: bool = True,
) -> None:
    if isinstance(destino, harvest_destino.DestinoHarvest):
        contadores.destinos[destino.resuelto_por] += 1
        return
    if not isinstance(destino, harvest_destino.SaltoHarvest):
        return
    if destino.motivo not in _MOTIVOS_AVISO_DESTINO or not tiene_grupos:
        return
    fila = conn.execute(_SQL_MEMBRESIA_GRUPO, (campaign_id,)).fetchone()
    if fila is None:
        return
    grupo_id, rol, external, nombre = fila
    contadores.saltos_grupo[campaign_id] = notifica.SaltoDestinoGrupo(
        platform=platform,
        grupo_id=grupo_id,
        campaign_ad_entity_id=campaign_id,
        campaign_external=external,
        nombre=nombre,
        rol=rol,
        motivo=destino.motivo,
    )


def _harvest_destino_notes(contadores: _Contadores) -> dict:
    return {
        "resueltos": {
            procedencia: contadores.destinos.get(procedencia, 0)
            for procedencia in ("grupo", "excepcion", "terna")
        },
        "saltos_grupo": {
            str(camp): salto.motivo for camp, salto in contadores.saltos_grupo.items()
        },
    }


def _saltos_previos(
    conn: psycopg.Connection, platform: str, cycle_id: int
) -> dict[str, str] | None:
    for (notes,) in conn.execute(_SQL_SALTOS_PREVIOS, (platform, cycle_id)).fetchall():
        if not isinstance(notes, str):
            continue
        try:
            cuerpo = json.loads(notes)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(cuerpo, dict):
            continue
        bloque = cuerpo.get("harvest_destino")
        if not isinstance(bloque, dict):
            continue
        saltos = bloque.get("saltos_grupo")
        return dict(saltos) if isinstance(saltos, dict) else {}
    return None


def _procesa_grupo(
    conn: psycopg.Connection,
    *,
    fila,
    platform: str,
    setting_target: Decimal | None,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    modo: str,
    decided_at: dt.datetime,
    contadores: _Contadores,
    pendientes: list[_Pendiente],
    tick,
    evidencia_ad_groups: dict[int, windows.EvidenciaAdGroup],
    keywords_propias: Mapping[int, frozenset[str]],
    bloqueadas: set[tuple[int, str, str | None]],
    margen_plataforma: Decimal | None,
    snapshot_margen: dict,
    familias_ciclo: _FamiliasCiclo,
    tiene_grupos_fabrica: bool = True,
) -> None:
    grupo_id, campaign_id, status, status_campana, grupo_external = fila
    plat_t = contadores.temporada_plat
    en_live = plat_t is not None and plat_t.en_vivo
    cerca_plat = plat_t.estado.cerca_plataforma if en_live else None
    mult_neg = (
        plat_t.estacion.mult_cortes_baja if en_live and plat_t.estacion.fase == "baja" else None
    )
    terminos = windows.terminos_cortes(conn, grupo_id, decided_at, cerca=cerca_plat)
    contadores.terminos += len(terminos.terminos)
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=grupo_id,
        status=status,
        ancestros=((MOTIVO_CAMPANA_NO_ENABLED, status_campana),),
        decided_at=decided_at,
    )
    if motivo is not None:
        contadores.skips_termino[motivo] += len(terminos.terminos)
        tick()
        return
    assert goal is not None
    libres = tuple(
        t for t in terminos.terminos if (grupo_id, "term_cut", t.search_term) not in bloqueadas
    )
    bloqueados = len(terminos.terminos) - len(libres)
    if bloqueados:
        contadores.skips_termino[MOTIVO_VETO_PENDIENTE] += bloqueados
        terminos = replace(terminos, terminos=libres)
    evidencia = evidencia_ad_groups.get(grupo_id)
    corte_negativo = cortes.umbral_corte(evidencia, "negative", mult_baja=mult_neg)
    piso_neg = cortes.piso_corte(evidencia, platform, mult_baja=mult_neg)
    target_margen_familia, snapshot_fam = _margen_familia_de_hoja(
        familias_ciclo, campaign_id, None, ancla_grupo=familias_ciclo.anclas_grupo.get(grupo_id)
    )
    target, procedencia = _resuelve_target_y_procedencia(
        goal, setting_target, margen_plataforma, target_margen_familia
    )
    if target is None:
        contadores.skips_termino[MOTIVO_SIN_TARGET] += len(terminos.terminos)
        tick()
        return
    assert procedencia is not None
    destino = (
        harvest_destino.resolver_destino(
            conn, platform, campaign_id, origen_ad_group_external=grupo_external
        )
        if tiene_grupos_fabrica
        else None
    )
    config_harvest, keywords, motivo_salto = _config_harvest_de(conn, goal, platform, destino)
    _registra_destino(
        conn,
        platform,
        campaign_id,
        destino,
        contadores,
        tiene_grupos=tiene_grupos_fabrica,
    )
    resultados = hygiene.decide_hygiene(
        platform=platform,
        terminos=terminos,
        target_acos_pct=target,
        config_harvest=config_harvest,
        keywords_existentes=keywords,
        keywords_propias=keywords_propias.get(grupo_id, frozenset()),
        umbral_negative=corte_negativo.umbral,
        piso_negative=piso_neg.piso_cost,
    )
    tick()
    for termino, resultado in zip(terminos.terminos, resultados, strict=True):
        if resultado.kind is None:
            motivo = resultado.motivo
            if motivo_salto is not None and motivo == hygiene.MOTIVO_HARVEST_SIN_CONFIG:
                motivo = motivo_salto
            contadores.skips_termino[motivo] += 1
            continue
        pendientes.append(
            _pendiente_termino(
                grupo_id,
                termino,
                resultado,
                platform=platform,
                modo=modo,
                goal=goal,
                terminos=terminos,
                target=target,
                procedencia=procedencia,
                snapshot=(
                    snapshot_margen
                    if procedencia == "margen_plataforma"
                    else (snapshot_fam if procedencia == "margen_familia" else None)
                ),
                decided_at=decided_at,
                corte=corte_negativo if resultado.kind == "negative" else None,
                evidencia=evidencia if resultado.kind == "negative" else None,
                piso=piso_neg if resultado.kind == "negative" else None,
                destino=destino,
            )
        )
        contadores.decisiones[resultado.kind] += 1
