"""Lectura de contribucion pre-cargos por campana (ORBIT 06 · 1.4).

Rollup en la capa de lectura (D2): SUM de hijas keyword|product_target desde
v_contribucion_entidad; motivo dominante desde v_contribucion_cobertura.
Consumido por app/api_dashboard.py y app/ui.py (un camino, regla 2).
"""

from __future__ import annotations

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

_SQL_CONTRIBUCION_CAMPANAS = """
WITH ventana AS (
    SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89) AS d_from,
           ((now() AT TIME ZONE 'UTC')::date - 15)      AS d_to
),
-- Las vistas se materializan UNA vez por consulta (0007 las dejo en ~2.5s).
-- Sin MATERIALIZED el planner las inlinea en esta cadena, misestima rows=1
-- y cae en Nested Loop: >240s medido en prod (2026-09-01).
ent AS MATERIALIZED (
    SELECT v.ad_entity_id,
           v.contrib_sin_halo,
           v.contrib_con_halo,
           v.metric_currency::text AS metric_currency,
           v.metric_date_from,
           v.metric_date_to,
           v.fx_source::text AS fx_source,
           v.precio_min_multilisting
      FROM v_contribucion_entidad v
     WHERE v.platform = %s::platform
),
cob AS MATERIALIZED (
    SELECT v.ad_entity_id, v.motivo::text AS motivo
      FROM v_contribucion_cobertura v
     WHERE v.platform = %s::platform
),
-- Grano ENTIDAD HOJA: UNA fila por leaf con actividad madura en la ventana.
-- Sin DISTINCT el JOIN a v_metric_mature deja una fila por (leaf, dia) y el
-- rollup SUMaba la contribucion de cada leaf una vez POR DIA (~x65-90 en
-- prod; la vista ya suma la ventana completa).
hijos AS (
    SELECT DISTINCT cam.id   AS campaign_id,
           cam.name AS campaign_name,
           cam.platform,
           leaf.id  AS ad_entity_id
      FROM ad_entity cam
      JOIN ad_entity ag ON ag.parent_id = cam.id AND ag.kind = 'ad_group'
      JOIN ad_entity leaf ON leaf.parent_id = ag.id
       AND leaf.kind IN ('keyword', 'product_target')
      JOIN v_metric_mature m ON m.ad_entity_id = leaf.id
      CROSS JOIN ventana v
     WHERE cam.kind = 'campaign'
       AND cam.platform = %s::platform
       AND m.metric_date BETWEEN v.d_from AND v.d_to
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
        "precio_min_multilisting": bool(fila[12]) if fila[12] is not None else False,
        "etiqueta": ETIQUETA_CONTRIBUCION,
    }


def _contribucion_plataforma(conn: ConexionLectura, plataforma: str) -> dict:
    filas = conn.execute(
        _SQL_CONTRIBUCION_CAMPANAS, (plataforma, plataforma, plataforma)
    ).fetchall()
    if filas:
        ventana = {
            "desde": filas[0][10].isoformat(),
            "hasta": filas[0][11].isoformat(),
        }
    else:
        vent = conn.execute(
            "SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89),"
            "       ((now() AT TIME ZONE 'UTC')::date - 15)"
        ).fetchone()
        ventana = {"desde": vent[0].isoformat(), "hasta": vent[1].isoformat()}
    return {"ventana": ventana, "filas": [_fila_contribucion_campana(f) for f in filas]}


def contribucion_campanas(conn: ConexionLectura) -> dict:
    """Payload de /api/dashboard/contribucion y /contribucion (UI)."""
    plataformas = {
        plataforma: _contribucion_plataforma(conn, plataforma) for plataforma in PLATAFORMAS_MONEDA
    }
    return {"plataformas": plataformas}
