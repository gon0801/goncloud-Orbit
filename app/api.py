"""Router de SOLO LECTURA del optimizador (ORBIT 03, task 3.2).

Tres endpoints GET bajo `/api/ads-optimizer`: `status`, `audit` y `goals`.
CERO escrituras en PR1 (hallazgo Security del plan: un endpoint sin auth en
host compartido no puede portar `orbit_admin`; la escalera y los goals se
escriben por el camino humano de 4.3; `/run` y `/goals` write llegan en PR2
con auth propia). El test de superficie (`test_api.py`) sella por
introspeccion de rutas que aqui no se registra ningun metodo distinto de GET.

Conexion como rol de lectura: `ORBIT_DSN_READ` (usuario LOGIN `orbit_read`
-> rol `app_read`, SELECT en todo el esquema; docs/DEPLOY.md). Sin DSN ->
503 fail-closed (jamas un DSN en errores: `app.db.connect` lo redacta).

Contrato de lectura sellado (task 3.2 + diseno v2):

- `/status`: ultimo ciclo POR PLATAFORMA + watermarks + notes del ciclo. Los
  watermarks salen de la MISMA fuente del motor (regla 2, un numero una
  fuente): `v_metric_latest` y `ad_entity_state.synced_at` -- las constantes
  SQL se IMPORTAN de `app.optimizer.windows` (la puerta de datos), no se
  copian. El notes es de FORMATO MIXTO (residual declarado del plan): JSON
  en ciclos normales, texto plano `rastro: ...` en ciclos muertos reclamados
  -- el endpoint tolera AMBOS sin reventar (JSON parseado; texto bajo la
  clave `texto`).
- `/audit`: decisiones paginadas (limit/offset, ORDER BY estable `id DESC`),
  filtros por cycle_id/ad_entity_id/kind, 404 para ids inexistentes (un id
  que no existe es error de consulta; un id existente sin decisiones es
  lista vacia, jamas 404).
- `/goals`: lectura de goals con filtros opcionales (platform/scope/enabled).

Dinero como STRING (regla 4): los NUMERIC salen `str()` tal cual (la escala
del string es artefacto deterministico del NUMERIC de origen; mismo criterio
que el congelado de inputs en app/cycle).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from app.db import OrbitDbError, connect
from app.optimizer.bid import PLATAFORMAS_MONEDA
from app.optimizer.windows import _SQL_SYNC_PLATAFORMA, _SQL_WATERMARK_PLATAFORMA
from app.redaction import install_scrub_filter

logger = logging.getLogger(__name__)
install_scrub_filter(logger)

router = APIRouter(prefix="/api/ads-optimizer", tags=["ads-optimizer"])

# Vocabulario cerrado de kinds de decision (enum decision_kind del esquema):
# un valor ajeno es 422, jamas un filtro vacio que mienta.
KINDS_DECISION = Literal["bid", "budget", "pause", "resume", "negative", "harvest"]

# Paginacion de /audit: tope duro de 200 filas por pagina (la tabla decision
# es append-only y crece; una pagina sin tope es un SELECT secuencial enorme).
LIMITE_MAX = 200
LIMITE_DEFAULT = 50

_SQL_ULTIMO_CICLO_POR_PLATAFORMA = """
SELECT DISTINCT ON (platform) id, mode, platform, started_at, finished_at,
       decisions_count, applied_count, status, notes
  FROM optimizer_cycle
 WHERE platform IS NOT NULL
 ORDER BY platform, id DESC
"""

_SQL_CICLO_EXISTE = "SELECT 1 FROM optimizer_cycle WHERE id = %s"
_SQL_ENTIDAD_EXISTE = "SELECT 1 FROM ad_entity WHERE id = %s"

_SQL_AUDIT_BASE = """
SELECT id, cycle_id, ad_entity_id, kind, decided_at, config_version_id,
       data_observed_at, window_start, window_end, search_term,
       old_value, new_value, value_currency, inputs
  FROM decision
 WHERE {filtros}
 ORDER BY id DESC
 LIMIT %s OFFSET %s
"""

_SQL_AUDIT_TOTAL = """
SELECT count(*) AS total FROM decision WHERE {filtros}
"""

_SQL_GOALS = """
SELECT id, scope, ad_entity_id, platform, target_acos_pct, bid_floor,
       bid_ceiling, bid_currency, harvest_campaign_id, harvest_ad_group_id,
       harvest_default_bid, enabled, mode, created_at, updated_at
  FROM ads_optimizer_goal
 WHERE {filtros}
 ORDER BY id
"""


def _conexion_lectura():
    """Dependency: conexion como rol de lectura (ORBIT_DSN_READ).

    Fail-closed: sin DSN -> 503 con mensaje claro (la API no puede operar sin
    config); fallo de conexion -> 503 con el DSN ya redactado por
    `app.db.connect` (jamas el DSN crudo en la respuesta).
    """
    dsn = os.environ.get("ORBIT_DSN_READ")
    if not dsn:
        raise HTTPException(
            status_code=503,
            detail="ORBIT_DSN_READ no esta definido: la API de lectura no puede conectar",
        )
    try:
        conn = connect(dsn)
    except OrbitDbError as exc:
        logger.warning("no se pudo conectar la API de lectura: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from None
    try:
        yield conn
    finally:
        conn.close()


ConexionLectura = Annotated[object, Depends(_conexion_lectura)]


def _dec_str(valor) -> str | None:
    """Decimal -> string TAL CUAL sale de la DB (regla 4: dinero como str,
    jamas float; la escala del string es artefacto deterministico del
    NUMERIC de origen, mismo criterio que _dec_str de app/cycle)."""
    return str(valor) if valor is not None else None


def _parse_notes(notes) -> dict | None:
    """Notes del envelope: FORMATO MIXTO (residual declarado del plan).

    JSON en ciclos normales -> se devuelve el dict estructurado (ahi viven
    los skips que 4.4 cita); texto plano `rastro: ...` en ciclos muertos
    reclamados -> se devuelve bajo la clave `texto`. Ninguna forma puede
    reventar el endpoint.
    """
    if notes is None:
        return None
    try:
        data = json.loads(notes)
    except (ValueError, TypeError):
        return {"texto": notes}
    return data if isinstance(data, dict) else {"texto": notes}


def _filtros_decision(cycle_id, ad_entity_id, kind) -> tuple[str, list]:
    """Fragmentos SQL FIJOS + parametros de los filtros de /audit (ningun
    texto del usuario se interpola: solo clausulas literales de este codigo)."""
    clausulas: list[str] = []
    params: list = []
    if cycle_id is not None:
        clausulas.append("cycle_id = %s")
        params.append(cycle_id)
    if ad_entity_id is not None:
        clausulas.append("ad_entity_id = %s")
        params.append(ad_entity_id)
    if kind is not None:
        clausulas.append("kind = %s::decision_kind")
        params.append(kind)
    return (" AND ".join(clausulas) or "true", params)


def _filtros_goals(platform, scope, enabled) -> tuple[str, list]:
    """Fragmentos SQL FIJOS + parametros de los filtros de /goals.

    `platform` cubre TANTO el goal de scope=platform como los de scope=campaign
    cuya campana pertenece a la plataforma (misma resolucion que el motor:
    app/cycle `_SQL_GOALS` asocia goals de campana por ad_entity) — un goal de
    campana sin `platform` en la columna no puede escaparse del filtro.
    """
    clausulas: list[str] = []
    params: list = []
    if platform is not None:
        clausulas.append(
            "(platform = %s::platform OR ad_entity_id IN"
            " (SELECT id FROM ad_entity WHERE platform = %s::platform))"
        )
        params.extend([platform, platform])
    if scope is not None:
        clausulas.append("scope = %s")
        params.append(scope)
    if enabled is not None:
        clausulas.append("enabled = %s")
        params.append(enabled)
    return (" AND ".join(clausulas) or "true", params)


def _fila_ciclo(fila) -> dict:
    """Tupla de _SQL_ULTIMO_CICLO_POR_PLATAFORMA -> dict de la respuesta."""
    return {
        "id": fila[0],
        "mode": fila[1],
        "platform": fila[2],
        "started_at": fila[3],
        "finished_at": fila[4],
        "decisions_count": fila[5],
        "applied_count": fila[6],
        "status": fila[7],
        "notes": _parse_notes(fila[8]),
    }


@router.get("/status")
def status(conn: ConexionLectura) -> dict:
    """Ultimo ciclo por plataforma + watermarks + skips estructurados del notes.

    `watermark` es max(metric_date) colapsado (v_metric_latest) de la
    plataforma y `synced_at` es max(ad_entity_state.synced_at): exactamente
    las dos fuentes que evalua `windows.guarda_plataforma` (regla 2). Una
    plataforma sin datos aparece con nulls, no con 404: el status es un
    panorama de TODAS las plataformas del vocabulario sellado.

    NOTA: las consultas de watermark/synced importan las constantes de
    windows.py (columna sin ALIAS: `max(...)`), asi que aqui se leen por
    INDICE con row_factory por defecto (tuplas); las consultas de audit/goals
    listan sus columnas por nombre y usan dict_row.
    """
    ciclos = {
        fila[2]: _fila_ciclo(fila)  # fila[2] = platform (ver SELECT)
        for fila in conn.execute(_SQL_ULTIMO_CICLO_POR_PLATAFORMA).fetchall()
    }
    plataformas: dict[str, dict] = {}
    for plataforma in PLATAFORMAS_MONEDA:
        watermark = conn.execute(_SQL_WATERMARK_PLATAFORMA, (plataforma,)).fetchone()[0]
        synced_at = conn.execute(_SQL_SYNC_PLATAFORMA, (plataforma,)).fetchone()[0]
        plataformas[plataforma] = {
            "ultimo_ciclo": ciclos.get(plataforma),
            "watermark": watermark.isoformat() if watermark is not None else None,
            "synced_at": synced_at,
        }
    return {"plataformas": plataformas}


@router.get("/audit")
def audit(
    conn: ConexionLectura,
    cycle_id: Annotated[int | None, Query(ge=1)] = None,
    ad_entity_id: Annotated[int | None, Query(ge=1)] = None,
    kind: KINDS_DECISION | None = None,
    limit: Annotated[int, Query(ge=1, le=LIMITE_MAX)] = LIMITE_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Decisiones paginadas con filtros por ciclo/entidad/kind.

    ORDER BY estable (`id DESC`): la paginacion por offset es determinista.
    404 para ids inexistentes (cycle_id/ad_entity_id que no existen en el
    esquema); un id existente sin decisiones devuelve items vacios.
    """
    conn.row_factory = dict_row
    if cycle_id is not None and conn.execute(_SQL_CICLO_EXISTE, (cycle_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"cycle_id {cycle_id} no existe")
    if (
        ad_entity_id is not None
        and conn.execute(_SQL_ENTIDAD_EXISTE, (ad_entity_id,)).fetchone() is None
    ):
        raise HTTPException(status_code=404, detail=f"ad_entity_id {ad_entity_id} no existe")

    filtros, params = _filtros_decision(cycle_id, ad_entity_id, kind)
    # dict_row activo: la columna se lee por nombre (AS total), jamas por indice
    total = conn.execute(_SQL_AUDIT_TOTAL.format(filtros=filtros), params).fetchone()["total"]
    filas = conn.execute(
        _SQL_AUDIT_BASE.format(filtros=filtros),
        (*params, limit, offset),
    ).fetchall()
    items = []
    for fila in filas:
        items.append(
            {
                "id": fila["id"],
                "cycle_id": fila["cycle_id"],
                "ad_entity_id": fila["ad_entity_id"],
                "kind": fila["kind"],
                "decided_at": fila["decided_at"],
                "config_version_id": fila["config_version_id"],
                "data_observed_at": fila["data_observed_at"],
                "window_start": fila["window_start"],
                "window_end": fila["window_end"],
                "search_term": fila["search_term"],
                "old_value": _dec_str(fila["old_value"]),
                "new_value": _dec_str(fila["new_value"]),
                "value_currency": fila["value_currency"],
                "inputs": fila["inputs"],
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/goals")
def goals(
    conn: ConexionLectura,
    platform: Literal["amazon_us", "amazon_mx"] | None = None,
    scope: Literal["campaign", "platform"] | None = None,
    enabled: bool | None = None,
) -> list[dict]:
    """Lectura de goals (la escritura es decision humana: app_admin via el
    camino de 4.3; /goals write llega en PR2 con auth propia)."""
    conn.row_factory = dict_row
    filtros, params = _filtros_goals(platform, scope, enabled)
    filas = conn.execute(_SQL_GOALS.format(filtros=filtros), params).fetchall()
    return [
        {
            "id": fila["id"],
            "scope": fila["scope"],
            "ad_entity_id": fila["ad_entity_id"],
            "platform": fila["platform"],
            "target_acos_pct": _dec_str(fila["target_acos_pct"]),
            "bid_floor": _dec_str(fila["bid_floor"]),
            "bid_ceiling": _dec_str(fila["bid_ceiling"]),
            "bid_currency": fila["bid_currency"],
            "harvest_campaign_id": fila["harvest_campaign_id"],
            "harvest_ad_group_id": fila["harvest_ad_group_id"],
            "harvest_default_bid": _dec_str(fila["harvest_default_bid"]),
            "enabled": fila["enabled"],
            "mode": fila["mode"],
            "created_at": fila["created_at"],
            "updated_at": fila["updated_at"],
        }
        for fila in filas
    ]
