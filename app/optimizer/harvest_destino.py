"""Resolutor unico del destino de harvest (FABRICA 02, A.1).

Patron EXACTO de `hygiene.py`: `psycopg` solo bajo `TYPE_CHECKING`, la
decision pura (`decide`) testeable sin base y el SQL en UNA sola funcion
lectora (`_lee`). `test_motor_puro_sin_io` prohíbe `psycopg` en runtime en
todo `app/optimizer`.

Firma: `resolver_destino(conn, platform, campaign_ad_entity_id)`. El
parametro es la CAMPANA (`campana_grupo_rol.ad_entity_id` y
`harvest_excepcion.ad_entity_id` son campanas; `decision.ad_entity_id` y
`harvest_job.ad_entity_id` son el ad group: cada caller resuelve el padre,
como hace `_SQL_PADRE` en `apply_harvest.py`). Si se pasa el ad group, los
SELECT devuelven cero filas y todo resuelve `sin_destino_de_harvest`.

Orden (plan, seccion "Diseno"):
1. `campana_grupo_rol` por campana: rol `category_exact` como origen ->
   `Skip("origen_es_destino")`; si no, la hermana exacta del mismo grupo
   con `ad_entity.platform = platform` asertado.
2. `harvest_excepcion` por campana.
3. La terna VIGENTE por el MISMO camino que `resuelve_goal` (goal de scope
   `campaign` si existe; si no, el de `platform`), con motivo informativo
   `migracion_pendiente`. Incluye la terna de plataforma a proposito
   (E/0.2: acotarla a `scope = campaign` dejaba 241 de 246 campanas sin
   cosechar el dia del deploy).
4. `Skip("sin_destino_de_harvest")`.

Resolver != comparar: el paso 3 resuelve con la terna vigente venga de
donde venga; la comparacion "terna vs grupo" (`destino_inconsistente`)
mira SOLO `scope = campaign` (la terna de plataforma es el default de la
cuenta, no una decision sobre esa campana, y jamas contradice al grupo).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from app.optimizer import goals as g
from app.optimizer import hygiene
from app.optimizer.bid import PLATAFORMAS_MONEDA

if TYPE_CHECKING:
    import psycopg

RESUELTO_GRUPO = "grupo"
RESUELTO_EXCEPCION = "excepcion"
RESUELTO_TERNA = "terna"


@dataclass(frozen=True)
class DestinoHarvest:
    """Destino resuelto: externos de Amazon + procedencia + monto vigente.

    `bid`/`moneda`/`floor`/`ceiling` salen del goal resuelto por el mismo
    camino que `resuelve_goal` (el monto sigue fijandolo el goal, decision
    12 del spec); None sin goal (el motor salta con `harvest_sin_config`,
    como hoy). `motivo` es `migracion_pendiente` solo si `resuelto_por` es
    `terna`, None en otro caso."""

    campaign_external: str
    ad_group_external: str
    resuelto_por: str  # grupo | excepcion | terna
    grupo_id: int | None
    motivo: str | None
    bid: Decimal | None
    moneda: str | None
    floor: Decimal | None
    ceiling: Decimal | None


@dataclass(frozen=True)
class SaltoHarvest:
    """Sin destino: el motor salta con este motivo (vocabulario cerrado de
    `hygiene`)."""

    motivo: str


@dataclass(frozen=True)
class _Lectura:
    """Todo lo que el resolutor necesita, leido de UNA vez (`_lee`)."""

    grupo: tuple[int, str, str, str] | None  # (grupo_id, rol_origen, camp_ext, ag_ext)
    excepcion: tuple[str, str] | None  # (camp_ext, ag_ext)
    goal_campana: g.Goal | None
    goal_plataforma: g.Goal | None


# Campana en grupo + hermana exacta del grupo con plataforma asertada (no
# hay candado de plataforma en `campana_grupo_rol`: el JOIN la exige; sin
# hermana valida no hay destino de grupo, fail-closed al paso 2).
_SQL_GRUPO = """
SELECT r.grupo_id, r.rol::text, ce.external_id, ae.external_id
  FROM campana_grupo_rol r
  JOIN campana_grupo_rol re
    ON re.grupo_id = r.grupo_id AND re.rol = 'category_exact'
  JOIN ad_entity ce
    ON ce.id = re.ad_entity_id AND ce.kind = 'campaign'
   AND ce.platform = %s::platform
  JOIN ad_entity ae
    ON ae.id = re.ad_group_ad_entity_id AND ae.kind = 'ad_group'
   AND ae.parent_id = re.ad_entity_id AND ae.platform = %s::platform
 WHERE r.ad_entity_id = %s
"""

_SQL_EXCEPCION = """
SELECT destino_campaign_external, destino_ad_group_external
  FROM harvest_excepcion
 WHERE ad_entity_id = %s
"""

# Columnas en el ORDEN del dataclass g.Goal (scope, ad_entity_id,
# platform, ...): scope campaign trae platform NULL por
# goal_scope_coherente; scope platform trae ad_entity_id NULL.
_SQL_GOALS = """
SELECT scope::text, ad_entity_id, platform::text, target_acos_pct, bid_floor,
       bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode::text
  FROM ads_optimizer_goal
 WHERE (scope = 'campaign' AND ad_entity_id = %s)
    OR (scope = 'platform' AND platform = %s::platform)
"""


def _lee(conn: psycopg.Connection, platform: str, campaign_id: int) -> _Lectura:
    """UNICA funcion lectora del modulo: grupo + excepcion + goals."""
    fila_grupo = conn.execute(_SQL_GRUPO, (platform, platform, campaign_id)).fetchone()
    fila_exc = conn.execute(_SQL_EXCEPCION, (campaign_id,)).fetchone()
    goal_campana: g.Goal | None = None
    goal_plataforma: g.Goal | None = None
    for fila in conn.execute(_SQL_GOALS, (campaign_id, platform)).fetchall():
        goal = g.Goal(*fila)
        if goal.scope == "campaign":
            goal_campana = goal
        else:
            goal_plataforma = goal
    return _Lectura(
        grupo=tuple(fila_grupo) if fila_grupo is not None else None,  # type: ignore[arg-type]
        excepcion=tuple(fila_exc) if fila_exc is not None else None,  # type: ignore[arg-type]
        goal_campana=goal_campana,
        goal_plataforma=goal_plataforma,
    )


def _terna_completa(goal: g.Goal | None) -> bool:
    """La terna harvest va junta o no va (espejo del CHECK
    `goal_harvest_completo` y de `hygiene._config_completa`): campana y ad
    group no vacios, bid positivo, moneda presente."""
    return (
        goal is not None
        and bool(goal.harvest_campaign_id and goal.harvest_campaign_id.strip())
        and bool(goal.harvest_ad_group_id and goal.harvest_ad_group_id.strip())
        and goal.harvest_default_bid is not None
        and goal.harvest_default_bid > 0
        and bool(goal.bid_currency)
    )


def _monto(lectura: _Lectura) -> tuple[Decimal | None, str | None, Decimal | None, Decimal | None]:
    """(bid, moneda, floor, ceiling) del goal resuelto por el mismo camino
    que `resuelve_goal`; todo None sin goal."""
    goal = g.resuelve_goal(lectura.goal_campana, lectura.goal_plataforma)
    if goal is None:
        return (None, None, None, None)
    floor, ceiling = g.resuelve_floor_ceiling(goal, goal.bid_currency)
    return (goal.harvest_default_bid, goal.bid_currency, floor, ceiling)


def decide(lectura: _Lectura) -> DestinoHarvest | SaltoHarvest:
    """Los cuatro pasos, puros y sin IO (testeables sin base)."""
    bid, moneda, floor, ceiling = _monto(lectura)
    if lectura.grupo is not None:
        grupo_id, rol_origen, camp_ext, ag_ext = lectura.grupo
        if rol_origen == "category_exact":
            return SaltoHarvest(motivo=hygiene.MOTIVO_ORIGEN_ES_DESTINO)
        if _terna_completa(lectura.goal_campana) and (
            lectura.goal_campana.harvest_campaign_id != camp_ext
            or lectura.goal_campana.harvest_ad_group_id != ag_ext
        ):
            # Solo scope campaign contradice: la terna de plataforma es el
            # default de la cuenta, no una decision sobre esta campana.
            return SaltoHarvest(motivo=hygiene.MOTIVO_DESTINO_INCONSISTENTE)
        return DestinoHarvest(
            campaign_external=camp_ext,
            ad_group_external=ag_ext,
            resuelto_por=RESUELTO_GRUPO,
            grupo_id=grupo_id,
            motivo=None,
            bid=bid,
            moneda=moneda,
            floor=floor,
            ceiling=ceiling,
        )
    if lectura.excepcion is not None:
        camp_ext, ag_ext = lectura.excepcion
        return DestinoHarvest(
            campaign_external=camp_ext,
            ad_group_external=ag_ext,
            resuelto_por=RESUELTO_EXCEPCION,
            grupo_id=None,
            motivo=None,
            bid=bid,
            moneda=moneda,
            floor=floor,
            ceiling=ceiling,
        )
    goal = g.resuelve_goal(lectura.goal_campana, lectura.goal_plataforma)
    if _terna_completa(goal):
        assert goal is not None
        assert goal.harvest_campaign_id is not None
        assert goal.harvest_ad_group_id is not None
        return DestinoHarvest(
            campaign_external=goal.harvest_campaign_id,
            ad_group_external=goal.harvest_ad_group_id,
            resuelto_por=RESUELTO_TERNA,
            grupo_id=None,
            motivo=hygiene.MOTIVO_MIGRACION_PENDIENTE,
            bid=bid,
            moneda=moneda,
            floor=floor,
            ceiling=ceiling,
        )
    return SaltoHarvest(motivo=hygiene.MOTIVO_SIN_DESTINO_HARVEST)


def resolver_destino(
    conn: psycopg.Connection, platform: str, campaign_ad_entity_id: int
) -> DestinoHarvest | SaltoHarvest:
    """Destino de harvest de una CAMPANA (ver docstring del modulo)."""
    if platform not in PLATAFORMAS_MONEDA:
        raise ValueError(
            f"plataforma fuera del vocabulario sellado {{amazon_us, amazon_mx}}: {platform!r}"
        )
    return decide(_lee(conn, platform, campaign_ad_entity_id))
