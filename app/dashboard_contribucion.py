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
hijos AS (
    SELECT cam.id   AS campaign_id,
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
           MAX(v.metric_currency::text) AS metric_currency,
           MIN(v.metric_date_from)    AS metric_date_from,
           MAX(v.metric_date_to)      AS metric_date_to,
           MAX(v.fx_source) FILTER (WHERE v.fx_source IS NOT NULL) AS fx_source
      FROM hijos h
      LEFT JOIN v_contribucion_entidad v ON v.ad_entity_id = h.ad_entity_id
     GROUP BY h.campaign_id
),
cobertura_cnt AS (
    SELECT h.campaign_id, cob.motivo, COUNT(*) AS n
      FROM hijos h
      JOIN v_contribucion_cobertura cob ON cob.ad_entity_id = h.ad_entity_id
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
       v.d_to
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
    contrib_sin = fila[4]
    contrib_con = fila[5]
    return {
        "ad_entity_id": fila[0],
        "nombre": fila[1],
        "plataforma": fila[2],
        "contrib_sin_halo": _dec_str(contrib_sin) if contrib_sin is not None else None,
        "contrib_con_halo": _dec_str(contrib_con) if contrib_con is not None else None,
        "moneda": fila[6] or PLATAFORMAS_MONEDA.get(fila[2]),
        "metric_date_from": fila[7].isoformat() if fila[7] is not None else None,
        "metric_date_to": fila[8].isoformat() if fila[8] is not None else None,
        "fx_source": fila[9],
        "motivo_ausencia": _motivo_contribucion_es(fila[10]),
        "etiqueta": ETIQUETA_CONTRIBUCION,
    }


def _contribucion_plataforma(conn: ConexionLectura, plataforma: str) -> dict:
    filas = conn.execute(_SQL_CONTRIBUCION_CAMPANAS, (plataforma,)).fetchall()
    if filas:
        ventana = {
            "desde": filas[0][11].isoformat(),
            "hasta": filas[0][12].isoformat(),
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
