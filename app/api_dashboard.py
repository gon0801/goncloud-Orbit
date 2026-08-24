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
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api import KINDS_DECISION, ConexionLectura
from app.api_common import _dec_str
from app.optimizer import bid, hygiene
from app.optimizer import goals as g
from app.optimizer.bid import PLATAFORMAS_MONEDA

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
SELECT e.id, e.name, e.platform, s.acos_target,
       CASE WHEN bool_and(v.cost IS NOT NULL) THEN sum(v.cost) END,
       CASE WHEN bool_and(v.ad_revenue IS NOT NULL) THEN sum(v.ad_revenue) END,
       CASE WHEN bool_and(v.clicks IS NOT NULL) THEN sum(v.clicks)::bigint END
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.kind = 'campaign'
   AND e.platform = %s::platform
   AND v.metric_date BETWEEN %s AND %s
 GROUP BY e.id, e.name, e.platform, s.acos_target
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

# Feed de decisiones por CURSOR: ORDER BY id DESC, id < cursor, LIMIT. Sin
# OFFSET (decision 8): offset sobre la tabla append-only produce
# huecos/duplicados entre paginas. JOIN a ad_entity para el nombre (nullable
# por schema) y la plataforma.
_SQL_DECISIONES_FEED = """
SELECT d.id, d.cycle_id, d.ad_entity_id, e.name, e.platform, d.kind,
       d.decided_at, d.search_term, d.old_value, d.new_value, d.value_currency,
       d.inputs
  FROM decision d
  JOIN ad_entity e ON e.id = d.ad_entity_id
 WHERE {filtros}
 ORDER BY d.id DESC
 LIMIT %s
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
    bid._MOTIVO_BANDA[bid.FACTOR_SUBIDA]: "ACoS bajo 0.85x del target: +15%",
    hygiene.MOTIVO_NEGATIVE: "Negativo: termino sin ventas con clicks y costo sobre el umbral",
    hygiene.MOTIVO_HARVEST: "Harvest: termino con ACoS bajo el tope hacia campana manual",
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
    no historia): enabled + floor/ceiling EFECTIVOS (defaults de goals.py) +
    mode + scope. Sin goal -> None (regla 3: faltante = null, no inventado)."""
    if goal is None:
        return None
    floor, ceiling = g.resuelve_floor_ceiling(goal)
    return {
        "enabled": goal.enabled,
        "floor": _dec_str(floor),
        "ceiling": _dec_str(ceiling),
        "mode": goal.mode,
        "scope": goal.scope,
    }


def _fila_campana(
    fila,
    goals_campana: dict[int, g.Goal],
    goal_plataforma: g.Goal | None,
    settings: dict,
    plataforma: str,
) -> dict:
    """Fila del resumen: metricas 30d (grano campaign, dinero string) + target
    EFECTIVO con PROCEDENCIA (cascada de 1.2 REUTILIZADA, jamas
    reimplementada) + goal resuelto. Sin total al pie (regla 4)."""
    camp_id, nombre, _plataforma, acos_cache, cost, revenue, clicks = fila
    goal_campana = goals_campana.get(camp_id)
    valor, peldano = g.cascada_target_acos_con_procedencia(
        goal_campana, goal_plataforma, settings, acos_cache, plataforma
    )
    acos, sin_ventas = _acoso(cost, revenue)
    return {
        "ad_entity_id": camp_id,
        "nombre": nombre,
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
        for fila in conn.execute(_SQL_CAMPANAS_30D, (plataforma, desde, hasta)).fetchall():
            items.append(
                _fila_campana(
                    fila, goals_campana, goals_plataforma.get(plataforma), settings, plataforma
                )
            )
    return {"items": items}


# ---------------------------------------------------------------------------
# 1.4 - /decisiones: feed por cursor con motivo en espanol
# ---------------------------------------------------------------------------


def _filtros_feed(platform, kind) -> tuple[str, list]:
    """Fragmentos SQL FIJOS + parametros de los filtros del feed (ningun texto
    del usuario se interpola: solo clausulas literales de este codigo)."""
    clausulas: list[str] = []
    params: list = []
    if platform is not None:
        clausulas.append("e.platform = %s::platform")
        params.append(platform)
    if kind is not None:
        clausulas.append("d.kind = %s::decision_kind")
        params.append(kind)
    return (" AND ".join(clausulas), params)


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
        "nombre": fila[3],  # ad_entity.name nullable -> null, no revienta
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
