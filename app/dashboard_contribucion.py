"""Lectura de contribucion pre-cargos por campana (ORBIT 06 · 1.4).

Rollup en la capa de lectura (D2): SUM de hijas keyword|product_target desde
v_contribucion_entidad; motivo dominante desde v_contribucion_cobertura.
Consumido por app/api_dashboard.py y app/ui.py (un camino, regla 2).
"""

from __future__ import annotations

import copy
import threading
import time

from app.api import ConexionLectura
from app.api_common import _dec_str
from app.optimizer.bid import PLATAFORMAS_MONEDA

ETIQUETA_CONTRIBUCION = "contribucion pre-cargos · no decisoria"

MOTIVOS_ES_CONTRIBUCION: dict[str, str] = {
    "kind_fuera": "kind fuera",
    "sin_padre": "sin padre",
    "serie_incompleta": "serie incompleta",
    "catalogo_parcial": "catalogo parcial",
    "sin_precio": "sin precio",
    "sin_mezcla_ledger": "sin mezcla ledger",
    "sin_fx": "sin FX",
}

# Una sola pasada, ambas plataformas. Filtrar por plataforma aqui re-evaluaba
# las vistas 4 veces (ent + cob, que JOINea entidad, x MX + US). Medido
# 2026-09-01: ~87s la pagina en fixture tipo-prod; una pasada ~43s.
# Las vistas se materializan UNA vez (0007 las dejo en ~2.5s CON Hash Join).
# Sin MATERIALIZED el planner las inlinea, misestima rows=1 y cae en Nested
# Loop: >240s medido en prod (2026-09-01).
# hijos sale de ent U cob (grano entidad). JOIN a v_metric_mature dejaba
# grano (leaf, dia) y el rollup SUMaba x dias (~x65-90 en prod).
_SQL_CONTRIBUCION_CAMPANAS = """
WITH ventana AS (
    SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89) AS d_from,
           ((now() AT TIME ZONE 'UTC')::date - 15)      AS d_to
),
ent AS MATERIALIZED (
    SELECT v.ad_entity_id,
           v.contrib_sin_halo,
           v.contrib_con_halo,
           v.metric_currency::text AS metric_currency,
           v.metric_date_from,
           v.metric_date_to,
           v.fx_source::text AS fx_source,
           v.precio_min_multilisting,
           v.platform
      FROM v_contribucion_entidad v
),
cob AS MATERIALIZED (
    SELECT v.ad_entity_id, v.motivo::text AS motivo, v.platform
      FROM v_contribucion_cobertura v
),
hojas AS (
    SELECT ad_entity_id FROM ent
    UNION
    SELECT ad_entity_id FROM cob
),
hijos AS (
    SELECT cam.id   AS campaign_id,
           cam.name AS campaign_name,
           cam.platform,
           leaf.id  AS ad_entity_id
      FROM hojas h
      JOIN ad_entity leaf ON leaf.id = h.ad_entity_id
       AND leaf.kind IN ('keyword', 'product_target')
      JOIN ad_entity ag ON ag.id = leaf.parent_id AND ag.kind = 'ad_group'
      JOIN ad_entity cam ON cam.id = ag.parent_id AND cam.kind = 'campaign'
),
rollup AS (
    SELECT h.campaign_id,
           SUM(v.contrib_sin_halo) AS contrib_sin,
           SUM(v.contrib_con_halo) AS contrib_con,
           MAX(v.metric_currency) AS metric_currency,
           MIN(v.metric_date_from)    AS metric_date_from,
           MAX(v.metric_date_to)      AS metric_date_to,
           MAX(v.fx_source) FILTER (WHERE v.fx_source IS NOT NULL) AS fx_source,
           BOOL_OR(v.precio_min_multilisting) AS multilisting
      FROM hijos h
      LEFT JOIN ent v ON v.ad_entity_id = h.ad_entity_id
     GROUP BY h.campaign_id
),
cobertura_cnt AS (
    SELECT h.campaign_id, cob.motivo, COUNT(*) AS n
      FROM hijos h
      JOIN cob ON cob.ad_entity_id = h.ad_entity_id
     GROUP BY h.campaign_id, cob.motivo
),
motivo_dom AS (
    SELECT DISTINCT ON (campaign_id) campaign_id, motivo
      FROM cobertura_cnt
     ORDER BY campaign_id, n DESC, motivo
)
SELECT DISTINCT ON (h.campaign_id)
       h.campaign_id,
       h.campaign_name,
       h.platform::text,
       r.contrib_sin,
       r.contrib_con,
       r.metric_currency,
       r.metric_date_from,
       r.metric_date_to,
       r.fx_source,
       m.motivo,
       v.d_from,
       v.d_to,
       r.multilisting
  FROM hijos h
  CROSS JOIN ventana v
  LEFT JOIN rollup r ON r.campaign_id = h.campaign_id
  LEFT JOIN motivo_dom m ON m.campaign_id = h.campaign_id
 ORDER BY h.campaign_id
"""

_SQL_VENTANA = (
    "SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89),"
    "       ((now() AT TIME ZONE 'UTC')::date - 15)"
)


# HTTP no-store obliga un GET nuevo al volver al tab. Sin snapshot cada
# reopen reevalua las vistas (~10s en prod). Si el primer GET sigue vivo
# (el browser aborta, el SQL no) dos Hash Join se pisan. Un calculo a la
# vez + TTL corto: el segundo GET es el mismo payload, no otra query.
_CACHE_TTL_S = 60.0
_cache_lock = threading.Lock()
_cache: tuple[str, float, dict] | None = None


def _dbname(conn: ConexionLectura | None) -> str:
    info = getattr(conn, "info", None)
    if info is None:
        return ""
    return getattr(info, "dbname", "") or ""


def invalidar_cache_contribucion() -> None:
    """Tira el snapshot. Los tests lo llaman para no heredar estado."""
    global _cache
    with _cache_lock:
        _cache = None


def _preparar_lectura_contribucion(conn: ConexionLectura) -> None:
    """Hash Join solo en ESTA transaccion (SET LOCAL, no session).

    El planner estima los CTE de v_contribucion_entidad / cobertura en
    rows=1 y elige Nested Loop. Medido 2026-09-01 (200 hojas x 90d):
    cogs_diario hace 18k x 18k = 324M filas (~60s la vista). Con
    enable_nestloop=off la misma vista baja a ~2.5s (Hash Join). USERSET:
    orbit_read puede cambiarlo. SET LOCAL muere al COMMIT: un pool no
    hereda nestloop=off.
    """
    conn.execute("SET LOCAL enable_nestloop = off")


def _motivo_contribucion_es(motivo: str | None) -> str | None:
    if motivo is None:
        return None
    return MOTIVOS_ES_CONTRIBUCION.get(motivo, motivo)


def _fila_contribucion_campana(fila) -> dict:
    contrib_sin = fila[3]
    contrib_con = fila[4]
    return {
        "ad_entity_id": fila[0],
        "nombre": fila[1],
        "plataforma": fila[2],
        "contrib_sin_halo": _dec_str(contrib_sin) if contrib_sin is not None else None,
        "contrib_con_halo": _dec_str(contrib_con) if contrib_con is not None else None,
        "moneda": fila[5] or PLATAFORMAS_MONEDA.get(fila[2]),
        "metric_date_from": fila[6].isoformat() if fila[6] is not None else None,
        "metric_date_to": fila[7].isoformat() if fila[7] is not None else None,
        "fx_source": fila[8],
        "motivo_ausencia": _motivo_contribucion_es(fila[9]),
        # 0008: la campana uso el precio MENOR de algun producto multilisting
        # (bool_or de sus hojas). None (campana sin rango) -> False.
        "precio_min_multilisting": bool(fila[12]),
        "etiqueta": ETIQUETA_CONTRIBUCION,
    }


def _ventana_de(conn: ConexionLectura, filas) -> dict:
    if filas:
        return {
            "desde": filas[0][10].isoformat(),
            "hasta": filas[0][11].isoformat(),
        }
    vent = conn.execute(_SQL_VENTANA).fetchone()
    return {"desde": vent[0].isoformat(), "hasta": vent[1].isoformat()}


def _contribucion_todas(conn: ConexionLectura) -> dict:
    """Una consulta: ambas plataformas, vistas evaluadas una vez cada una."""
    with conn.transaction():
        _preparar_lectura_contribucion(conn)
        filas = conn.execute(_SQL_CONTRIBUCION_CAMPANAS).fetchall()
    ventana = _ventana_de(conn, filas)
    por_plat: dict[str, list] = {p: [] for p in PLATAFORMAS_MONEDA}
    for fila in filas:
        item = _fila_contribucion_campana(fila)
        por_plat[item["plataforma"]].append(item)
    return {
        "plataformas": {
            plataforma: {"ventana": ventana, "filas": por_plat[plataforma]}
            for plataforma in PLATAFORMAS_MONEDA
        }
    }


def _contribucion_plataforma(conn: ConexionLectura, plataforma: str) -> dict:
    return _contribucion_todas(conn)["plataformas"][plataforma]


def contribucion_campanas(conn: ConexionLectura) -> dict:
    """Payload de /api/dashboard/contribucion y /contribucion (UI).

    Snapshot de proceso + un solo calculo a la vez. El HTML sigue
    no-store (el browser no cachea); el reopen no vuelve a pagar la vista.
    """
    global _cache
    db = _dbname(conn)
    with _cache_lock:
        now = time.monotonic()
        if _cache is not None and _cache[0] == db and now - _cache[1] < _CACHE_TTL_S:
            return copy.deepcopy(_cache[2])
        data = _contribucion_todas(conn)
        _cache = (db, time.monotonic(), data)
        return copy.deepcopy(data)
