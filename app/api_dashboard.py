"""Router del dashboard del optimizador (ORBIT 16 - DASHBOARD 01).

Endpoints GET de LECTURA bajo `/api/dashboard` (rol `orbit_read` via
ORBIT_DSN_READ; sin DSN -> 503 fail-closed, misma dependencia que api.py).
Solo GET en toda la superficie (candado de introspeccion OpenAPI).

Task 1.3 — series temporales (brief §3.1/§3.2/§3.6):

- COLAPSO BITEMPORAL (regla 5): las series leen SIEMPRE `v_metric_latest`
  (ultima observacion por (entidad, fecha)), jamas la tabla cruda.
- GRANO EXPLICITO (anti-doble-conteo): JOIN a ad_entity con
  `e.kind = 'campaign'` en AMBAS queries; el kind vive en ad_entity, no en la
  observacion; las hojas (keyword/product_target) duplicarian el dinero
  (evidencia: campaign 63.96 = keyword 24.94 + product_target 39.02).
- VENTANA [D-30, D-1] UTC: default relativo a hoy; el dia en curso EXCLUIDO
  (vintage parcial, regla 6): un `hasta` que lo pida se RECORTA a D-1 y la
  respuesta declara el rango efectivo.
- INMADUREZ: D-8..D-1 marcados `inmaduro: true` (la atribucion madura 5-8d y
  el costo hasta D+15: mostrarlos sin marca mentiria).
- NULL != 0 (regla 3): spine completo del rango; fecha sin fila -> valores
  null (hueco visible); metrica NULL en alguna campana del dia -> agregado
  envenenado (bool_and, mismo criterio que windows.py) -> null, jamas 0.
- SIN_VENTAS: ad_revenue == 0 (conocido) -> acos null + sin_ventas true (caso
  real: amazon_us 2026-08-22, cost 66.6300, revenue 0.0000).
- DINERO COMO STRING (regla 4): cost/ad_revenue salen str() del NUMERIC(14,4)
  tal cual ("363.1400"); clicks entero; acos string de 2 decimales o null.
  El cliente parsea con Number() (decision 7 del header). MONEDA por serie
  desde PLATAFORMAS_MONEDA (la misma fuente del motor), jamas un total que
  mezcle monedas.

Task 1.4 — campanas + feed de decisiones (brief §3.3/§3.4):

- `/campanas`: resumen 30d por campana (grano campaign) + target EFECTIVO con
  PROCEDENCIA de 5 peldanos REUTILIZANDO `cascada_target_acos_con_procedencia`
  de goals.py (1.2) + estado VIVO del goal resuelto (campaña > plataforma,
  decision 17). ANTI-MEZCLA (regla 4): cada fila lleva su moneda, NO existe
  total al pie.
- `/decisiones`: feed por CURSOR (`id <`, ORDER BY id DESC; PROHIBIDO
  limit/offset — decision 8) con JOIN a ad_entity para el nombre (nullable).
  El target mostrado se lee de `inputs.target_acos_pct_usado`, JAMAS de
  `inputs.goal.target_acos_pct` (NULL cuando gano el default — trampa sellada
  grok r2). Motivo en español via dict que IMPORTA los MOTIVO_* de
  bid/hygiene; motivo desconocido -> fallback al id crudo sin crash. Los
  pause traen old/new/value_currency NULL (CHECK del schema): se renderizan
  null, jamas 0.

DETERMINISMO: `hoy` sale de `_hoy_utc()` (UTC), inyectable en tests; las
ventanas se calculan en Python, no en SQL (nada depende de la TimeZone de
sesion).
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row

from app import cycle as ciclo
from app.api import KINDS_DECISION, ConexionLectura
from app.api_common import (
    _SQL_ULTIMO_CICLO_POR_PLATAFORMA,
    _dec_str,
    _fila_ciclo,
    _parse_notes,
    bloque_target_margen,
)
from app.apply import KINDS_QUOTA, estado_quota
from app.dashboard_contribucion import contribucion_campanas as _contribucion_campanas
from app.dashboard_pagina import (
    _CAMPANA_ANCESTRO,
    _JOINS_ANCESTROS,
    _SQL_DECISIONES_FEED,
    _SQL_DECISIONES_PAGINA,
    _SQL_DECISIONES_TOTAL,
    PageWindow,
)
from app.etiqueta_entidad import etiqueta_entidad, linea_entidad
from app.optimizer import bid, hygiene
from app.optimizer import goals as g
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.windows import _SQL_SYNC_PLATAFORMA, _SQL_WATERMARK_PLATAFORMA
from app.redaction import install_scrub_filter, scrub

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Tope duro de la ventana pedible: un anio. El default es [D-30, D-1]; sin
# tope, un `desde` arbitrariamente antiguo es un SELECT que escala con la
# historia.
MAX_DIAS_VENTANA = 366

# Dias inmaduros marcados: [D-8, D-1] (la atribucion madura 5-8d y el costo
# hasta D+15; el dia en curso esta EXCLUIDO de la serie, regla 6).
DIAS_INMADUROS = 8

DIAS_VENTANA_DEFAULT = 30

# Ventana FIJA del resumen de campanas (brief §3.3): [D-30, D-1] UTC.
DIAS_VENTANA_CAMPANAS = 30

# Paginacion del feed por CURSOR: tope duro de 200 filas por pagina (patron
# /audit; el feed jamas usa offset, decision 8 del header).
LIMITE_FEED_MAX = 200
LIMITE_FEED_DEFAULT = 50

_SQL_ENTIDAD = """
SELECT platform, name, kind FROM ad_entity WHERE id = %s
"""

_SQL_SERIE_PLATAFORMA = """
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""

_SQL_SERIE_CAMPANA = """
SELECT v.metric_date,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.kind = 'campaign'
   AND e.id = %s
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY v.metric_date
 ORDER BY v.metric_date
"""

# Resumen 30d de campanas: una fila por campana con sus sumas colapsadas
# (v_metric_latest) + el cache del acos_target publicado (ad_entity_state,
# 4to peldano de la cascada; LEFT JOIN: sin estado = None, regla 3). El
# LEFT JOIN NO rompe el grano: el WHERE e.kind = 'campaign' sigue siendo
# conjuncion obligatoria (candado AST).
_SQL_CAMPANAS_30D = """
SELECT e.id, e.name, e.platform, s.acos_target, s.status,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY e.id, e.name, e.platform, s.acos_target, s.status
 ORDER BY e.id
"""

# Goals completos (tabla chica; la precedencia la resuelve goals.py, jamas
# aqui). Mismo shape de columnas que _lee_goals de cycle.py.
_SQL_GOALS_DASHBOARD = """
SELECT scope, ad_entity_id, platform, target_acos_pct, bid_floor, bid_ceiling,
       bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode
  FROM ads_optimizer_goal
"""

_SQL_CONFIG_VIGENTE = """
SELECT settings FROM config_version ORDER BY id DESC LIMIT 1
"""

# Motivos de DECISION -> espanol (decisión 11 del header): dict que IMPORTA
# las constantes MOTIVO_* de bid/hygiene (los que persisten en inputs.motivo
# de filas en `decision`). Si el vocabulario cambia, falla RUIDOSO en import
# (KeyError), jamas silencioso. Motivo desconocido -> el id crudo (fallback
# sin crash: no se pierde informacion).
MOTIVOS_ES_DECISIONES: dict[str, str] = {
    bid.MOTIVO_PAUSE: "Pausa: sin ventas con clicks y costo sobre el umbral",
    bid._MOTIVO_BANDA[bid.FACTOR_BAJA_FUERTE]: "ACoS sobre 1.35x del target: -25%",
    bid._MOTIVO_BANDA[bid.FACTOR_BAJA_SUAVE]: "ACoS sobre 1.15x del target: -12%",
    bid.MOTIVO_BANDA_MENOS_25_CERO_VENTAS: (
        "Cero ventas con los clicks de una venta y gasto sobre el piso: -25%"
    ),
    bid._MOTIVO_BANDA[bid.FACTOR_SUBIDA]: "ACoS bajo 0.85x del target: +15%",
    hygiene.MOTIVO_NEGATIVE: "Negativo: termino sin ventas con clicks y costo sobre el umbral",
    hygiene.MOTIVO_HARVEST: "Harvest: termino con ACoS bajo el tope hacia campana manual",
}

# ---------------------------------------------------------------------------
# 1.5 - /salud: historico 14d y motivos de skip del ORQUESTADOR
# ---------------------------------------------------------------------------

# Ultimos 14 ciclos de la plataforma (acotado; el candado del test fija el
# LIMIT). El snapshot usa la MISMA fuente del /status de 3.2
# (_SQL_ULTIMO_CICLO_POR_PLATAFORMA, extraido a api_common).
_SQL_HISTORICO_14D = """
SELECT id, mode, platform, started_at, finished_at, decisions_count,
       applied_count, status, notes
  FROM optimizer_cycle
 WHERE platform = %s::platform
   AND motor = 'ads_optimizer'
 ORDER BY id DESC
 LIMIT 14
"""

# Motivos de SKIP -> espanol (decision 11): el vocabulario que el ORQUESTADOR
# persiste en notes.skips — los MOTIVO_* propios de cycle.py + los guarda_*
# (windows.py) + los MOTIVO_* de bid/hygiene que cycle importa a sus
# contadores (evidencia real 2026-08-24: estado_no_enabled, bids_sin_observaciones,
# rango_bloquea_ajuste, sin_banda, pause_cortes_incompleto, bid_actual_ausente,
# sin_umbral_negative, asin_like, acos_sobre_tope, harvest_sin_config,
# entidad_incompleta). DOS diccionarios (este y el del feed), cada uno
# importando su fuente. Motivo desconocido -> id crudo (fallback sin crash).
MOTIVOS_ES_SALUD: dict[str, str] = {
    # motivos del ORQUESTADOR (cycle.py; su fuente)
    ciclo.MOTIVO_SIN_GOAL: "Entidad sin goal (la campana no esta configurada)",
    ciclo.MOTIVO_GOAL_DISABLED: "Goal de campana deshabilitado (opt-out)",
    ciclo.MOTIVO_GOAL_MODE_OFF: "Goal en modo off",
    ciclo.MOTIVO_ESTADO_NO_ENABLED: "Entidad sin estado o no habilitada",
    ciclo.MOTIVO_CAMPANA_NO_ENABLED: "Campana no habilitada (pausada/archivada o sin estado)",
    ciclo.MOTIVO_GRUPO_NO_ENABLED: "Ad group no habilitado (pausado/archivado o sin estado)",
    ciclo.MOTIVO_ENTIDAD_INERTE: (
        "Entidad sin trafico reciente (sin impresiones en 14 dias): sin ajuste"
    ),
    ciclo.MOTIVO_COOLDOWN_7D: "Cooldown 7d: apply verificado reciente",
    ciclo.MOTIVO_ESCALERA_OFF: "Escalera global off",
    # guardas de plataforma (windows.py; el envelope las persiste como
    # motivo_skip = guarda_<guarda>)
    "guarda_watermark": "Watermark de la plataforma vencido (> 7 dias)",
    "guarda_synced_at": "Estructura sincronizada hace > 48h",
    "guarda_sin_datos": "Plataforma sin metricas ni estado",
    # MOTIVO_* de bid (skips que el orquestador importa a sus contadores)
    bid.MOTIVO_PAUSE: "Pausa: sin ventas con clicks y costo sobre el umbral",
    bid.MOTIVO_PAUSE_CORTES_INCOMPLETO: "Pausa bloqueada: ventana de cortes incompleta",
    bid.MOTIVO_PAUSE_MONEDA_INVALIDA: "Pausa bloqueada: moneda del agregado invalida",
    bid.MOTIVO_PAUSE_ORDERS_DESCONOCIDO: "Pausa bloqueada: orders desconocidos",
    bid.MOTIVO_PAUSE_CLICKS_COST_DESCONOCIDOS: "Pausa bloqueada: clicks o costo desconocidos",
    bid.MOTIVO_BIDS_SIN_OBSERVACIONES: "Bid sin observaciones en la ventana",
    bid.MOTIVO_BIDS_INCOMPLETO: "Bid bloqueado: ventana incompleta (< 7 fechas)",
    bid.MOTIVO_BIDS_MONEDA_INVALIDA: "Bid bloqueado: moneda del agregado invalida",
    bid.MOTIVO_ACOS_DESCONOCIDO: "ACoS desconocido (cost o revenue faltante)",
    bid.MOTIVO_RANGO_BLOQUEA_AJUSTE: "Rango [floor, ceiling] bloquea el ajuste",
    bid.MOTIVO_BID_ACTUAL_INVALIDO: "Bid actual invalido",
    bid.MOTIVO_BID_ACTUAL_AUSENTE: "Bid actual ausente",
    bid.MOTIVO_BID_MONEDA_INVALIDA: "Bid con moneda invalida",
    bid.MOTIVO_SIN_BANDA: "Sin banda de ajuste (ACoS dentro del rango)",
    bid.MOTIVO_DELTA_BAJO_UMBRAL: "Cambio menor a 0.01: no-op",
    # MOTIVO_* de hygiene (idem)
    hygiene.MOTIVO_ENTIDAD_INCOMPLETA: "Entidad incompleta: sin umbral de fechas",
    hygiene.MOTIVO_ASIN_LIKE: "Termino ASIN-like: se salta siempre",
    hygiene.MOTIVO_ORDERS_DESCONOCIDO: "Orders desconocidos: sin umbral",
    hygiene.MOTIVO_DATO_FALTANTE: "Dato faltante del termino",
    hygiene.MOTIVO_SIN_UMBRAL_NEGATIVE: "Sin umbral de negative (clicks o costo bajo)",
    hygiene.MOTIVO_ACOS_SOBRE_TOPE: "ACoS sobre el tope de harvest",
    hygiene.MOTIVO_HARVEST_SIN_CONFIG: "Harvest sin config de campana manual",
    hygiene.MOTIVO_HARVEST_DUPLICADO: "Harvest duplicado: ya existe la keyword",
    hygiene.MOTIVO_HARVEST_MONEDA_INCOHERENTE: "Harvest con moneda incoherente",
    hygiene.MOTIVO_MONEDA_INCOHERENTE: "Moneda incoherente",
}


def _hoy_utc() -> dt.date:
    """Fecha UTC de hoy (unico reloj del modulo; los tests la inyectan)."""
    return dt.datetime.now(dt.UTC).date()


def _ventana_efectiva(
    desde: dt.date | None, hasta: dt.date | None, hoy: dt.date
) -> tuple[dt.date, dt.date]:
    """Ventana efectiva de la serie: default [D-30, D-1]; el dia en curso
    EXCLUIDO (un `hasta` que lo pida se recorta a D-1, sellado); `desde`
    posterior a `hasta` tras el recorte -> 422; ventana de mas de
    MAX_DIAS_VENTANA -> 422. La respuesta SIEMPRE declara el rango efectivo."""
    ultimo = hoy - dt.timedelta(days=1)  # D-1
    if desde is None:
        desde = ultimo - dt.timedelta(days=DIAS_VENTANA_DEFAULT - 1)  # D-30
    if hasta is None or hasta > ultimo:
        hasta = ultimo
    if desde > hasta:
        raise HTTPException(
            status_code=422,
            detail="desde no puede ser posterior a hasta (el dia en curso esta excluido)",
        )
    if (hasta - desde).days + 1 > MAX_DIAS_VENTANA:
        raise HTTPException(
            status_code=422,
            detail=f"ventana de mas de {MAX_DIAS_VENTANA} dias: acota el rango",
        )
    return desde, hasta


def _ventana_inmaduros(hoy: dt.date) -> dict[str, str]:
    """Ventana de inmadurez [D-8, D-1] como contrato de la respuesta."""
    return {
        "desde": (hoy - dt.timedelta(days=DIAS_INMADUROS)).isoformat(),
        "hasta": (hoy - dt.timedelta(days=1)).isoformat(),
    }


def _acoso(cost: Decimal | None, ad_revenue: Decimal | None) -> tuple[str | None, bool]:
    """(acos_str, sin_ventas) del dia. ad_revenue == 0 CONOCIDO -> sin_ventas
    true y acos null (infinito: jamas division ni 0 enganoso); cost o
    ad_revenue None -> dato faltante (acos null, sin_ventas false); si no,
    ratio Decimal exacto con 2 decimales como STRING (el cliente parsea con
    Number(); decision 7 del header). Redondeo HALF_UP EXPLICITO (convencion
    comercial; hallazgo kimi: el default del contexto es half-even y 11.125
    saldria "11.12"). Solo presentacion: el motor jamas materializa ACoS
    (compara por multiplicacion)."""
    if ad_revenue is None:
        return None, False
    if ad_revenue == 0:
        return None, True
    if cost is None:
        return None, False
    acos = (cost / ad_revenue * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(acos), False


def _fila_serie(
    fecha: dt.date,
    fila: tuple | None,
    inmaduro_desde: dt.date,
    inmaduro_hasta: dt.date,
) -> dict:
    """Una fila de la serie: fecha del spine con sus valores. `fila` None
    (fecha sin datos) -> TODO null (hueco visible, jamas 0)."""
    inmaduro = inmaduro_desde <= fecha <= inmaduro_hasta
    if fila is None:
        return {
            "fecha": fecha.isoformat(),
            "cost": None,
            "ad_revenue": None,
            "clicks": None,
            "acos": None,
            "sin_ventas": False,
            "inmaduro": inmaduro,
        }
    cost, ad_revenue, clicks = fila
    acos, sin_ventas = _acoso(cost, ad_revenue)
    return {
        "fecha": fecha.isoformat(),
        "cost": _dec_str(cost),
        "ad_revenue": _dec_str(ad_revenue),
        "clicks": clicks,
        "acos": acos,
        "sin_ventas": sin_ventas,
        "inmaduro": inmaduro,
    }


def _arma_serie(
    desde: dt.date, hasta: dt.date, por_fecha: dict[dt.date, tuple], hoy: dt.date
) -> list[dict]:
    """Spine completo [desde, hasta]: TODAS las fechas presentes; fecha sin
    fila -> hueco null (regla 3); inmaduro relativo a hoy (D-8..D-1)."""
    inmaduro_desde = hoy - dt.timedelta(days=DIAS_INMADUROS)
    inmaduro_hasta = hoy - dt.timedelta(days=1)
    serie = []
    fecha = desde
    while fecha <= hasta:
        serie.append(_fila_serie(fecha, por_fecha.get(fecha), inmaduro_desde, inmaduro_hasta))
        fecha += dt.timedelta(days=1)
    return serie


@router.get("/series/plataforma")
def serie_plataforma(
    conn: ConexionLectura,
    platform: Literal["amazon_us", "amazon_mx"],
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
) -> dict:
    """Serie diaria de la plataforma (grano kind='campaign'): cost/revenue
    STRING, clicks entero, acos string o null, sin_ventas, inmaduro. Spine
    completo del rango efectivo (declarado en la respuesta)."""
    hoy = _hoy_utc()
    desde_ef, hasta_ef = _ventana_efectiva(desde, hasta, hoy)
    filas = conn.execute(_SQL_SERIE_PLATAFORMA, (platform, desde_ef, hasta_ef)).fetchall()
    por_fecha = {fila[0]: (fila[1], fila[2], fila[3]) for fila in filas}
    return {
        "plataforma": platform,
        "moneda": PLATAFORMAS_MONEDA[platform],
        "desde": desde_ef.isoformat(),
        "hasta": hasta_ef.isoformat(),
        "ventana_inmaduros": _ventana_inmaduros(hoy),
        "series": _arma_serie(desde_ef, hasta_ef, por_fecha, hoy),
    }


@router.get("/series/campana")
def serie_campana(
    conn: ConexionLectura,
    ad_entity_id: Annotated[int, Query(ge=1)],
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
) -> dict:
    """Serie diaria de UNA campana (kind='campaign' EXPLICITO): mismo contrato
    de filas que la serie de plataforma. 404 si el id no existe; 422 si la
    entidad no es kind='campaign' o su plataforma no tiene moneda sellada."""
    fila_entidad = conn.execute(_SQL_ENTIDAD, (ad_entity_id,)).fetchone()
    if fila_entidad is None:
        raise HTTPException(status_code=404, detail=f"ad_entity_id {ad_entity_id} no existe")
    platform, nombre, kind = fila_entidad
    if kind != "campaign":
        raise HTTPException(
            status_code=422,
            detail=(
                f"ad_entity_id {ad_entity_id} es kind={kind}: el endpoint es "
                "solo para kind='campaign'"
            ),
        )
    if platform not in PLATAFORMAS_MONEDA:
        raise HTTPException(
            status_code=422,
            detail=f"plataforma {platform} sin moneda sellada para el dashboard",
        )
    hoy = _hoy_utc()
    desde_ef, hasta_ef = _ventana_efectiva(desde, hasta, hoy)
    filas = conn.execute(_SQL_SERIE_CAMPANA, (ad_entity_id, desde_ef, hasta_ef)).fetchall()
    por_fecha = {fila[0]: (fila[1], fila[2], fila[3]) for fila in filas}
    return {
        "ad_entity_id": ad_entity_id,
        "nombre": nombre,
        "plataforma": platform,
        "moneda": PLATAFORMAS_MONEDA[platform],
        "desde": desde_ef.isoformat(),
        "hasta": hasta_ef.isoformat(),
        "ventana_inmaduros": _ventana_inmaduros(hoy),
        "series": _arma_serie(desde_ef, hasta_ef, por_fecha, hoy),
    }


# ---------------------------------------------------------------------------
# 1.4 - /campanas: resumen 30d con target efectivo y procedencia
# ---------------------------------------------------------------------------


def _goal_desde_fila(fila) -> g.Goal:
    """Reconstruye un Goal (dataclass puro de goals.py) desde una fila de
    ads_optimizer_goal (mismo shape que _lee_goals de cycle.py). La
    precedencia campana > plataforma la resuelve goals.py (resuelve_goal +
    cascada de 1.2), JAMAS se reimplementa en la capa web."""
    (
        scope,
        ad_entity_id,
        platform,
        target_acos_pct,
        bid_floor,
        bid_ceiling,
        bid_currency,
        harvest_campaign_id,
        harvest_ad_group_id,
        harvest_default_bid,
        enabled,
        mode,
    ) = fila
    return g.Goal(
        scope=scope,
        ad_entity_id=ad_entity_id,
        platform=platform,
        target_acos_pct=target_acos_pct,
        bid_floor=bid_floor,
        bid_ceiling=bid_ceiling,
        bid_currency=bid_currency,
        harvest_campaign_id=harvest_campaign_id,
        harvest_ad_group_id=harvest_ad_group_id,
        harvest_default_bid=harvest_default_bid,
        enabled=enabled,
        mode=mode,
    )


def _carga_goals(conn) -> tuple[dict[int, g.Goal], dict[str, g.Goal]]:
    """(goals de campana por ad_entity_id, goals de plataforma por platform)
    de la tabla completa (chica: 2 filas hoy). La precedencia la resuelve
    goals.py, no el SQL ni esta capa."""
    goals_campana: dict[int, g.Goal] = {}
    goals_plataforma: dict[str, g.Goal] = {}
    for fila in conn.execute(_SQL_GOALS_DASHBOARD).fetchall():
        goal = _goal_desde_fila(fila)
        if goal.scope == "campaign" and goal.ad_entity_id is not None:
            goals_campana[goal.ad_entity_id] = goal
        elif goal.scope == "platform" and goal.platform is not None:
            goals_plataforma[goal.platform] = goal
    return goals_campana, goals_plataforma


def _config_vigente(conn) -> dict:
    """settings de la config_version VIGENTE (la de mayor id); sin config ->
    {} (el setting no gana y la cascada sigue a cache/default, regla 3)."""
    fila = conn.execute(_SQL_CONFIG_VIGENTE).fetchone()
    return fila[0] if fila is not None else {}


def _goal_estado(goal: g.Goal | None) -> dict | None:
    """Estado VIVO del goal resuelto (decision 17: el goal es config mutable,
    no historia): enabled + floor/ceiling EFECTIVOS (defaults de goals.py POR
    MONEDA, preflight 1.2 -- la moneda es goal.bid_currency) + mode + scope.
    Sin goal -> None (regla 3: faltante = null, no inventado)."""
    if goal is None:
        return None
    floor, ceiling = g.resuelve_floor_ceiling(goal, goal.bid_currency)
    return {
        "enabled": goal.enabled,
        "floor": _dec_str(floor),
        "ceiling": _dec_str(ceiling),
        "mode": goal.mode,
        "scope": goal.scope,
    }


_SQL_TARGET_MARGEN_ULTIMO = """
SELECT notes
  FROM optimizer_cycle
 WHERE platform = %s::platform AND motor = 'ads_optimizer' AND status = 'done'
 ORDER BY id DESC
 LIMIT 1
"""


def _target_margen_del_ciclo(conn, plataforma: str) -> Decimal | None:
    """Aplicado del peldano `margen_plataforma` segun el ULTIMO ciclo done de
    la plataforma (cross-review grok H3). Fuente UNICA: notes.target, lo que el
    ciclo resolvio — la web NO re-resuelve la vista ni el paso maximo. Sin
    ciclo, sin notes, sin bloque o con el peldano abstenido -> None y la
    cascada sigue como hoy (regla 3)."""
    fila = conn.execute(_SQL_TARGET_MARGEN_ULTIMO, (plataforma,)).fetchone()
    if not fila or not isinstance(fila[0], dict):
        return None
    bloque = fila[0].get("target")
    if not isinstance(bloque, dict) or bloque.get("procedencia") != "margen_plataforma":
        return None
    valor = bloque.get("target_aplicado")
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _fila_campana(
    fila,
    goals_campana: dict[int, g.Goal],
    goal_plataforma: g.Goal | None,
    settings: dict,
    plataforma: str,
    target_margen: Decimal | None = None,
) -> dict:
    """Fila del resumen: metricas 30d (grano campaign, dinero string) + target
    EFECTIVO con PROCEDENCIA (cascada de 1.2 REUTILIZADA, jamas
    reimplementada) + goal resuelto. Sin total al pie (regla 4)."""
    camp_id, nombre, _plataforma, acos_cache, estado, cost, revenue, clicks = fila
    goal_campana = goals_campana.get(camp_id)
    valor, peldano = g.cascada_target_acos_con_procedencia(
        goal_campana, goal_plataforma, settings, acos_cache, plataforma, target_margen
    )
    acos, sin_ventas = _acoso(cost, revenue)
    return {
        "ad_entity_id": camp_id,
        "nombre": nombre,
        # estado publicado en Amazon (ad_entity_state; feedback del smoke
        # 1.7: no todas las campanas con datos 30d siguen activas). Sin
        # estado -> null (regla 3), jamas un estado inventado.
        "status": estado,
        "plataforma": plataforma,
        "moneda": PLATAFORMAS_MONEDA[plataforma],
        "metricas_30d": {
            "cost": _dec_str(cost),
            "ad_revenue": _dec_str(revenue),
            "clicks": clicks,
            "acos": acos,
            "sin_ventas": sin_ventas,
            # la ventana [D-30, D-1] siempre incluye D-8..D-1: el agregado
            # contiene dias que aun maduran y debe DECLARARLO (trampa de los
            # tres relojes), jamas mostrarlos como maduros.
            "inmaduro": True,
        },
        "target_efectivo": {"valor": _dec_str(valor), "peldano": peldano},
        "goal": _goal_estado(g.resuelve_goal(goal_campana, goal_plataforma)),
    }


@router.get("/campanas")
def campanas(
    conn: ConexionLectura,
    platform: Literal["amazon_us", "amazon_mx"] | None = None,
) -> dict:
    """Resumen de campanas de la(s) plataforma(s) (brief §3.3): metricas 30d
    colapsadas (v_metric_latest, kind='campaign') + target EFECTIVO con
    procedencia (cascada de 1.2) + estado VIVO del goal resuelto. Cada fila
    lleva su moneda; NO existe total al pie (anti-mezcla, regla 4)."""
    hoy = _hoy_utc()
    desde = hoy - dt.timedelta(days=DIAS_VENTANA_CAMPANAS)
    hasta = hoy - dt.timedelta(days=1)
    settings = _config_vigente(conn)
    goals_campana, goals_plataforma = _carga_goals(conn)
    items: list[dict] = []
    plataformas = (platform,) if platform is not None else tuple(PLATAFORMAS_MONEDA)
    for plataforma in plataformas:
        # ORBIT 06 2.3 (cross-review grok H3): el peldano `margen_plataforma`
        # lo resuelve el CICLO, no la web (la vista y el paso maximo exigen
        # estado). La tabla lee el aplicado del ultimo ciclo de esa plataforma
        # y se lo pasa a la MISMA cascada que usa el motor. Sin esto el
        # dashboard llamaba con target_margen=None y mostraba el setting (20)
        # mientras el motor ya decidia con el derivado: dos verdades en
        # pantallas distintas, y el dueno creeria que no encendio.
        target_margen = _target_margen_del_ciclo(conn, plataforma)
        for fila in conn.execute(_SQL_CAMPANAS_30D, (plataforma, desde, hasta)).fetchall():
            items.append(
                _fila_campana(
                    fila,
                    goals_campana,
                    goals_plataforma.get(plataforma),
                    settings,
                    plataforma,
                    target_margen,
                )
            )
    return {"items": items}


# ---------------------------------------------------------------------------
# 1.4 - /decisiones: feed por cursor con motivo en espanol
# ---------------------------------------------------------------------------


def _filtros_feed(platform, kind) -> tuple[list[str], list]:
    """Fragmentos SQL FIJOS + parametros de los filtros del feed (ningun texto
    del usuario se interpola: solo clausulas literales de este codigo).
    Devuelve la LISTA de clausulas (el caller agrega el cursor y une una sola
    vez): devolver el string ya unido rompia el feed — bug atrapado por
    test_feed_sql_compuesto_parsea_con_todos_los_filtros."""
    clausulas: list[str] = []
    params: list = []
    if platform is not None:
        clausulas.append("e.platform = %s::platform")
        params.append(platform)
    if kind is not None:
        clausulas.append("d.kind = %s::decision_kind")
        params.append(kind)
    return (clausulas, params)


def decisiones_pagina(
    conn: ConexionLectura, page: int, page_size: int
) -> tuple[list[dict], PageWindow]:
    """Pagina HTML: count -> ventana clampada -> LIMIT/OFFSET. No es ruta."""
    total = conn.execute(_SQL_DECISIONES_TOTAL).fetchone()[0]
    ventana = PageWindow.desde_total(total=total, page=page, page_size=page_size)
    filas = conn.execute(_SQL_DECISIONES_PAGINA, (ventana.page_size, ventana.offset)).fetchall()
    return [_fila_decision(fila) for fila in filas], ventana


def _fila_decision(fila) -> dict:
    """Una decision del feed. Trampas reales de la evidencia: el target se lee
    de inputs.target_acos_pct_usado (JAMAS de inputs.goal.target_acos_pct,
    NULL cuando gano el default — grok r2); los pause traen old/new/currency
    NULL (CHECK del schema): se renderizan null, jamas 0; motivo desconocido
    -> fallback al id crudo sin crash."""
    inputs = fila[11] if isinstance(fila[11], dict) else {}
    motivo = inputs.get("motivo")
    return {
        "id": fila[0],
        "cycle_id": fila[1],
        "ad_entity_id": fila[2],
        "nombre": linea_entidad(
            kind=fila[12],
            name=fila[3],
            keyword_text=fila[13],
            campana=fila[14],
        ),
        "plataforma": fila[4],
        "kind": fila[5],
        "decided_at": fila[6],
        "search_term": fila[7],
        "old_value": _dec_str(fila[8]),
        "new_value": _dec_str(fila[9]),
        "value_currency": fila[10],
        "target_acos_pct_usado": inputs.get("target_acos_pct_usado"),
        "motivo_es": MOTIVOS_ES_DECISIONES.get(motivo, motivo) if motivo is not None else None,
    }


@router.get("/decisiones")
def decisiones(
    conn: ConexionLectura,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=LIMITE_FEED_MAX)] = LIMITE_FEED_DEFAULT,
    platform: Literal["amazon_us", "amazon_mx"] | None = None,
    kind: KINDS_DECISION | None = None,
) -> dict:
    """Feed de decisiones por CURSOR (brief §3.4, decision 8): ORDER BY id
    DESC, `id < cursor` (la fila nueva con id mayor jamas se cuela en una
    pagina cuyo cursor ya quedo atras); PROHIBIDO limit/offset (offset sobre
    append-only produce huecos/duplicados). JOIN a ad_entity para el nombre
    (nullable). Sin filtros el `total` no se computa (patron /audit)."""
    clausulas, params = _filtros_feed(platform, kind)
    if cursor is not None:
        clausulas.append("d.id < %s")
        params.append(cursor)
    # limit+1: la fila extra solo dice si hay mas (jamas se sirve)
    params.append(limit + 1)
    filas = conn.execute(
        _SQL_DECISIONES_FEED.format(filtros=" AND ".join(clausulas) or "true"),
        params,
    ).fetchall()
    hay_mas = len(filas) > limit
    items = [_fila_decision(fila) for fila in filas[:limit]]
    return {
        "items": items,
        "next_cursor": items[-1]["id"] if hay_mas and items else None,
        "has_more": hay_mas,
    }


# ---------------------------------------------------------------------------
# 1.5 - /salud: snapshot + historico 14d + watermarks + skips
# ---------------------------------------------------------------------------


def _motivo_ciclo(status: str, notes) -> str | None:
    """Motivo visible de un ciclo degraded/failed/skipped (grok r2: skipped
    trae motivo_skip=escalera_off y quedaba fuera del filtro): de
    notes.motivo_skip traducido, o notes.degradacion_live, o notes.error (el
    sello de _sello_fallido persiste el error scrubbeado AHI — grok r2), o el
    texto plano del notes (formato mixto); None en ciclos done sin motivo.
    Jamas un ciclo roto por un notes raro (regla 3: faltante = null)."""
    if status not in ("degraded", "failed", "skipped"):
        return None
    if isinstance(notes, dict):
        motivo_skip = notes.get("motivo_skip")
        if motivo_skip is not None:
            return MOTIVOS_ES_SALUD.get(motivo_skip, motivo_skip)
        degradacion = notes.get("degradacion_live")
        if degradacion:
            return degradacion
        # ciclos failed reales: _sello_fallido persiste el error scrubbeado
        # bajo notes.error (grok r2: no se leia y el historico lo mostraba
        # sin motivo)
        error = notes.get("error")
        if error:
            return error
        # _parse_notes envuelve el texto plano como {"texto": ...}: la rama
        # vivia FUERA de este if y era inalcanzable (motivo perdido en ciclos
        # failed con notes de texto — lo atrapa test_salud_notes_mixto_* en CI)
        return notes.get("texto")
    return None


def _fila_historico(fila) -> dict:
    """Una fila del historico 14d: cycle_id, fecha (started_at), status,
    decisions_count y, cuando aplique, el motivo (degradado/failed visible
    con motivo — DoD de 1.5)."""
    (cycle_id, _mode, _platform, started_at, _fin, decisions_count, _applied, status, notes) = fila
    return {
        "cycle_id": cycle_id,
        "fecha": started_at,
        "status": status,
        "decisions_count": decisions_count,
        "motivo": _motivo_ciclo(status, _parse_notes(notes)),
    }


def _skips_traducidos(contadores: dict) -> dict:
    """{motivo: {count, motivo_es}}; motivo desconocido -> id crudo (fallback
    sin crash: la evidencia del skip jamas se pierde)."""
    return {
        motivo: {"count": count, "motivo_es": MOTIVOS_ES_SALUD.get(motivo, motivo)}
        for motivo, count in contadores.items()
    }


def _skips_de(ultimo_ciclo: dict | None) -> dict:
    """Skips agregados por motivo del ULTIMO ciclo (vocabulario del
    orquestador + MOTIVO_* de bid/hygiene que importa a sus contadores),
    traducidos (decision 11). Sin ciclo o sin notes -> vacio, jamas null
    inventado."""
    if ultimo_ciclo is None or ultimo_ciclo.get("notes") is None:
        return {"entidad": {}, "termino": {}}
    skips = ultimo_ciclo["notes"].get("skips") or {}
    return {
        "entidad": _skips_traducidos(skips.get("entidad") or {}),
        "termino": _skips_traducidos(skips.get("termino") or {}),
    }


@router.get("/salud")
def salud(conn: ConexionLectura) -> dict:
    """Salud por plataforma (brief §3.5, plan 1.5): snapshot del ULTIMO ciclo
    + historico ACOTADO a 14d + watermarks (las MISMAS fuentes del motor:
    v_metric_latest y synced_at — regla 2) + skips del ultimo ciclo con su
    traduccion. REUTILIZA _parse_notes, _fila_ciclo y el SQL de ultimo ciclo
    extraidos a api_common (jamas dos copias, decision 6).

    Preflight 1.4 (sellado 4): cada plataforma gana "quota" —
    {kind: {used, cap, fuente}} para cada kind de KINDS_QUOTA. DECISION
    DECLARADA: la visibilidad vive AQUI (y no en /api/ads-optimizer/status)
    porque /salud es la fuente que la pantalla /salud ya consume
    (app/ui.py pagina_salud) y donde 0002/notifica ya declaran la
    visibilidad de la quota ("VISIBLE en Salud"); la tarea 1.5 renderiza
    desde aqui sin cablear un fetch nuevo, y NO se agregan rutas (la
    superficie de ambos routers queda sellada). UNA fuente: estado_quota de
    app.apply (el mismo motor_quota y la misma expresion de dia UTC que
    consume_quota); la app JAMAS re-implementa el mapeo de caps."""
    ciclos = {
        fila[2]: _fila_ciclo(fila)  # fila[2] = platform (ver SELECT)
        for fila in conn.execute(_SQL_ULTIMO_CICLO_POR_PLATAFORMA).fetchall()
    }
    plataformas: dict[str, dict] = {}
    for plataforma in PLATAFORMAS_MONEDA:
        watermark = conn.execute(_SQL_WATERMARK_PLATAFORMA, (plataforma,)).fetchone()[0]
        synced_at = conn.execute(_SQL_SYNC_PLATAFORMA, (plataforma,)).fetchone()[0]
        historico = conn.execute(_SQL_HISTORICO_14D, (plataforma,)).fetchall()
        ultimo = ciclos.get(plataforma)
        plataformas[plataforma] = {
            "watermark": watermark.isoformat() if watermark is not None else None,
            "synced_at": synced_at,
            "ultimo_ciclo": ultimo,
            "historico_14d": [_fila_historico(fila) for fila in historico],
            "skips": _skips_de(ultimo),
            "quota": _quota_de(conn, plataforma),
            "target_margen": bloque_target_margen(ultimo),
        }
    return {"plataformas": plataformas}


def _quota_de(conn: ConexionLectura, plataforma: str) -> dict:
    """El bloque quota de UNA plataforma (preflight 1.4). ADV-1 (adversary):
    un cap ROTO en la config (valor no numerico) revienta estado_quota A
    PROPOSITO en el camino de cobro (ruidoso, jamas disfraz de cap infinito)
    — pero la pantalla de LECTURA no muere entera por una forma rota: queda
    VISIBLE con fuente="config_rota" (cap null; no "sin_clave", que
    mentiria: la clave SI existe) y las formas sanas siguen legibles."""
    quota: dict = {}
    for kind in KINDS_QUOTA:
        try:
            quota[kind] = estado_quota(conn, plataforma, kind)
        except ValueError as exc:
            logger.warning(
                "salud: quota %s/%s ilegible (config rota): %s",
                plataforma,
                kind,
                scrub(str(exc)),
            )
            quota[kind] = {"used": 0, "cap": None, "fuente": "config_rota"}
    return quota


@router.get("/contribucion")
def contribucion_campanas(conn: ConexionLectura) -> dict:
    """Contribucion pre-cargos por campana (ORBIT 06 1.4, docs/MARGEN-ENTIDAD.md)."""
    return _contribucion_campanas(conn)


# ---------------------------------------------------------------------------
# ORBIT 04 3.1 - /cortes: pendientes de veto (UI minima de veto, sellado 20)
# ---------------------------------------------------------------------------

# SOLO los estados vetables (sellado 4): pending_veto (en ventana) y released
# (espera quota FIFO y SIGUE vetable, r2 grok); applying es punto de no retorno
# y los terminales no se vetan. ORDER BY vence_el: lo que vence primero se ve
# primero.
#
# CORTES UI 01: el indicador del harvest se lee de decision.inputs->'termino'
# (shape real en app/cycle.py: termino = {search_term, cost, ad_revenue,
# clicks, orders, fechas_distintas, moneda, ...}). Se trae el JSON entero y
# se extrae en Python (NULL = termino ausente -> indicador None, regla 3).
_SQL_CORTES_PENDIENTES = (
    """
SELECT q.id, q.platform::text, q.familia, q.kind, q.ad_entity_id, e.external_id,
       q.search_term, q.estado, q.vence_el, q.encolado_at, q.decision_id,
       e.kind::text AS entidad_kind, e.name, e.keyword_text,
       d.inputs AS decision_inputs,
       """
    + _CAMPANA_ANCESTRO
    + """ AS campana
  FROM apply_queue q
  LEFT JOIN ad_entity e ON e.id = q.ad_entity_id
  JOIN decision d ON d.id = q.decision_id
"""
    + _JOINS_ANCESTROS
    + """
 WHERE q.estado IN ('pending_veto', 'released')
 ORDER BY q.vence_el, q.id
"""
)

# CORTES UI 01 (D2-D4): la pantalla se llama Propuestas y cada fila declara
# su tipo por KIND (pause/negative/harvest: familia solo distingue
# entity_cut/term_cut y no alcanza para tres etiquetas). Espanol llano, sin
# acentos; strings exactos pineados por tests.
ETIQUETA_POR_KIND = {
    "pause": "Apagar palabra",
    "negative": "Bloquear busqueda",
    "harvest": "Capturar termino que vende",
}
DIRECCION_POR_KIND = {
    "pause": "recorta",
    "negative": "recorta",
    "harvest": "crece",
}
EFECTO_RECHAZO_POR_KIND = {
    "pause": "Rechazar: la palabra NO se apagara (seguira gastando)",
    "negative": "Rechazar: la busqueda NO se bloqueara",
    "harvest": "Rechazar: la palabra NO se creara",
}


def _indicador_harvest(kind: str, decision_inputs: dict | None) -> dict | None:
    """Indicador que justifica el harvest (D2): ordenes e ingreso de la
    ventana desde decision.inputs->'termino'. Solo kind='harvest' y solo
    con termino presente; sin el: None (regla 3, jamas inventado).
    Dinero = (valor string, moneda); NULL como null (regla 4)."""
    if kind != "harvest" or not isinstance(decision_inputs, dict):
        return None
    termino = decision_inputs.get("termino")
    if not isinstance(termino, dict):
        return None
    ingreso = termino.get("ad_revenue")
    return {
        "ordenes": termino.get("orders"),
        "ingreso": _dec_str(ingreso) if ingreso is not None else None,
        "clics": termino.get("clicks"),
        "moneda": termino.get("moneda"),
    }


# ---------------------------------------------------------------------------
# BIDS 01 1.3 - /inertes: diagnostico de hojas sin trafico (solo lectura)
# ---------------------------------------------------------------------------

# Lee v_entidad_inerte (migracion 0013, UNICA fuente D2): nada se diagnostica
# aqui. NULLS LAST (gasto NULL = mezcla de monedas, fail-loud) e id de
# desempate estable.
# BIDS 01 2.6: JOIN a ad_entity por first_seen_at (migracion 0017) para
# mostrar la puerta de antiguedad del archivado. `en_espera` es ESPEJO EXACTO
# del predicado del tool (tools/archiva_inertes.py, _SQL_PLAN /
# _SQL_EXCLUIDOS_JOVENES) con el DEFAULT --min-antiguedad-dias=30: ese default
# es la fuente del numero (regla 2); si cambia, ESTE 30 se toca aqui.
# `archivable_desde` es informativo (primera vista + 30 dias, fecha UTC).
_SQL_INERTES = """
SELECT v.platform::text, v.kind::text, v.keyword_text, v.name, v.external_id,
       v.campaign_name, v.ad_group_name, v.clasificacion, v.dias_sin_impresiones,
       v.ultima_impresion, v.gasto_90d, v.moneda::text, v.ordenes_90d,
       (e.first_seen_at AT TIME ZONE 'UTC')::date AS first_seen_fecha,
       e.first_seen_at > (now() AT TIME ZONE 'UTC')::date - 30 AS en_espera,
       (e.first_seen_at AT TIME ZONE 'UTC')::date + 30 AS archivable_desde
  FROM v_entidad_inerte v
  JOIN ad_entity e ON e.id = v.id
 ORDER BY v.platform, v.clasificacion, v.gasto_90d DESC NULLS LAST, v.id
"""

# BIDS 01 2.6: resumen del ledger de lotes (keyword_archivo_manual, migracion
# 0014): por lote, la fecha del primer intento y conteos por estado; los 5
# mas recientes. Tabla vacia -> cero filas (sin fila inventada, regla 3).
_SQL_LOTES_INERTES = """
SELECT lote, (min(intentado_at) AT TIME ZONE 'UTC')::date AS primer_intento,
       count(*) FILTER (WHERE estado = 'planeado') AS planeado,
       count(*) FILTER (WHERE estado = 'applied') AS applied,
       count(*) FILTER (WHERE estado = 'failed') AS failed,
       count(*) FILTER (WHERE estado = 'repuesto') AS repuesto
  FROM keyword_archivo_manual
 GROUP BY lote
 ORDER BY min(intentado_at) DESC
 LIMIT 5
"""


@router.get("/cortes")
def cortes(conn: ConexionLectura) -> dict:
    """Cortes pendientes de veto con su vencimiento (regla 22: la UI CONSUME
    este endpoint, no reimplementa queries). El POST del veto vive en
    app/api_write.py (auth de escritura, sellado 18); esta lectura no
    necesita token (es la misma conexion de lectura del dashboard)."""
    conn.row_factory = dict_row
    filas = conn.execute(_SQL_CORTES_PENDIENTES).fetchall()
    return {
        "items": [
            {
                "id": fila["id"],
                "plataforma": fila["platform"],
                "familia": fila["familia"],
                "kind": fila["kind"],
                "ad_entity_id": fila["ad_entity_id"],
                "external_id": fila["external_id"],
                "nombre": linea_entidad(
                    kind=fila["entidad_kind"],
                    name=fila["name"],
                    keyword_text=fila["keyword_text"],
                    campana=fila["campana"],
                ),
                "search_term": fila["search_term"],
                "estado": fila["estado"],
                "vence_el": fila["vence_el"].isoformat(),
                "encolado_at": fila["encolado_at"].isoformat(),
                "decision_id": fila["decision_id"],
                "etiqueta": ETIQUETA_POR_KIND.get(fila["kind"]),
                "direccion": DIRECCION_POR_KIND.get(fila["kind"]),
                "efecto_rechazo": EFECTO_RECHAZO_POR_KIND.get(fila["kind"]),
                "indicador": _indicador_harvest(fila["kind"], fila["decision_inputs"]),
            }
            for fila in filas
        ]
    }


@router.get("/inertes")
def inertes(conn: ConexionLectura) -> dict:
    """Hojas sin trafico con su clasificacion (regla 22: la UI CONSUME este
    endpoint). Dinero con moneda y NULL como null (reglas 3-4).

    BIDS 01 2.6: ademas first_seen_at / en_espera / archivable_desde por item
    (puerta de antiguedad del archivado, espejo del default del tool) y
    `lotes`: resumen de keyword_archivo_manual (tabla vacia -> [])."""
    conn.row_factory = dict_row
    items = [
        {
            "plataforma": fila["platform"],
            "kind": fila["kind"],
            "texto": etiqueta_entidad(
                kind=fila["kind"],
                name=fila["name"],
                keyword_text=fila["keyword_text"],
                campana=fila["campaign_name"],
            ).hoja,
            "external_id": fila["external_id"],
            "campana": fila["campaign_name"],
            "ad_group": fila["ad_group_name"],
            "clasificacion": fila["clasificacion"],
            "dias_sin_impresiones": fila["dias_sin_impresiones"],
            "ultima_impresion": (
                fila["ultima_impresion"].isoformat() if fila["ultima_impresion"] else None
            ),
            "gasto_90d": _dec_str(fila["gasto_90d"]),
            "moneda": fila["moneda"],
            "ordenes_90d": int(fila["ordenes_90d"]),
            "first_seen_at": (
                fila["first_seen_fecha"].isoformat() if fila["first_seen_fecha"] else None
            ),
            "en_espera": bool(fila["en_espera"]) if fila["en_espera"] is not None else None,
            "archivable_desde": (
                fila["archivable_desde"].isoformat() if fila["archivable_desde"] else None
            ),
        }
        for fila in conn.execute(_SQL_INERTES).fetchall()
    ]
    totales: dict[str, dict[str, int]] = {}
    for item in items:
        por_clase = totales.setdefault(item["plataforma"], {})
        por_clase[item["clasificacion"]] = por_clase.get(item["clasificacion"], 0) + 1
    lotes = [
        {
            "lote": fila["lote"],
            "fecha": fila["primer_intento"].isoformat(),
            "planeado": fila["planeado"],
            "applied": fila["applied"],
            "failed": fila["failed"],
            "repuesto": fila["repuesto"],
        }
        for fila in conn.execute(_SQL_LOTES_INERTES).fetchall()
    ]
    return {"totales": totales, "items": items, "lotes": lotes}
