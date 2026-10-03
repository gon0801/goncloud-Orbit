"""Censo read-only para JEV ADS 01 fila 0.2.

Solo agregados por plataforma: ninguna SKU, ASIN, nombre de producto ni texto
de busqueda sale por la salida. Transaccion REPEATABLE READ READ ONLY que
termina en ROLLBACK, misma receta de docs/evidencia/jev-ads-01/README.md.
"""

import json
import os
from datetime import UTC, datetime

from app.db import connect

CONSULTAS = {
    "negativos_biblioteca": """
        SELECT platform, count(*) AS negativos,
               count(DISTINCT tipo_producto) AS tipos_producto,
               min(first_seen_at)::text AS primera_alta,
               max(first_seen_at)::text AS ultima_alta
        FROM negative_biblioteca GROUP BY platform ORDER BY platform""",
    "semillas_biblioteca": """
        SELECT platform, count(*) AS semillas,
               count(DISTINCT tipo_producto) AS tipos_producto
        FROM keyword_biblioteca GROUP BY platform ORDER BY platform""",
    "search_terms": """
        SELECT platform,
               count(DISTINCT search_term) AS terminos_distintos,
               count(DISTINCT search_term) FILTER (WHERE is_asin_like) AS asin_like,
               count(DISTINCT search_term) FILTER (
                   WHERE NOT is_asin_like AND coalesce(orders, 0) >= 1) AS con_orders,
               count(DISTINCT search_term) FILTER (
                   WHERE NOT is_asin_like AND coalesce(cost, 0) > 0) AS con_costo,
               count(DISTINCT search_term) FILTER (
                   WHERE metric_date >= current_date - 30) AS ultimos_30d,
               min(metric_date)::text AS desde,
               max(metric_date)::text AS hasta
        FROM search_term_observation GROUP BY platform ORDER BY platform""",
    "roles_grupo": """
        SELECT g.platform, r.rol, count(*) AS roles,
               count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM ad_entity a
                   WHERE a.parent_id = r.ad_group_ad_entity_id
                     AND a.kind = 'product_ad')) AS roles_sin_anuncios_observados
        FROM campana_grupo_rol r
        JOIN campana_grupo g ON g.id = r.grupo_id
        GROUP BY g.platform, r.rol ORDER BY g.platform, r.rol""",
    "harvest_excepciones": """
        SELECT e.platform, count(*) AS excepciones
        FROM harvest_excepcion h
        JOIN ad_entity e ON e.id = h.ad_entity_id
        GROUP BY e.platform ORDER BY e.platform""",
    "publicaciones_roster": """
        SELECT platform, count(*) AS publicaciones,
               count(DISTINCT product_id) AS productos
        FROM listing GROUP BY platform ORDER BY platform""",
    "keywords_en_grupos": """
        SELECT e.platform,
               count(*) FILTER (WHERE e.kind = 'keyword') AS keywords_totales,
               count(*) FILTER (WHERE e.kind = 'keyword'
                                AND s.status IN ('ENABLED', 'PAUSED')) AS keywords_ep,
               count(*) FILTER (WHERE e.kind = 'product_target') AS targets_totales
        FROM ad_entity e
        LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
        GROUP BY e.platform ORDER BY e.platform""",
    "censo_grupos": """
        WITH por_grupo AS (
         SELECT g.platform, g.id,
          count(a.id) AS anuncios_conocidos,
          count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS anuncios_ep,
          count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')
                                                   AND l.id IS NULL) AS ep_sin_listing,
          count(DISTINCT l.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS publicaciones_ep
         FROM ad_entity g
         LEFT JOIN ad_entity a ON a.parent_id=g.id AND a.platform=g.platform AND a.kind='product_ad'
         LEFT JOIN ad_entity_state s ON s.ad_entity_id=a.id
         LEFT JOIN listing l ON l.id=a.listing_id AND l.platform=a.platform
         WHERE g.kind='ad_group'
         GROUP BY g.platform,g.id
        )
        SELECT platform, count(*) AS grupos_conocidos,
         count(*) FILTER (WHERE anuncios_ep>0) AS grupos_con_ep,
         count(*) FILTER (WHERE anuncios_ep>0 AND publicaciones_ep=0) AS grupos_ep_sin_catalogo,
         count(*) FILTER (WHERE anuncios_ep>0 AND ep_sin_listing>0) AS grupos_ep_catalogo_incompleto
        FROM por_grupo GROUP BY platform ORDER BY platform""",
}

conn = connect(os.environ["ORBIT_DSN_READ"])
salida = {
    "read_only": "on",
    "transaction": "REPEATABLE READ READ ONLY, ROLLBACK final",
    "observed_at": None,
    "consultas": {},
}
try:
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.execute("SET LOCAL statement_timeout = '15s'")
    for nombre, sql in CONSULTAS.items():
        cur = conn.execute(sql)
        columnas = [d.name for d in cur.description]
        salida["consultas"][nombre] = [
            dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()
        ]
    conn.rollback()
finally:
    conn.close()

salida["observed_at"] = datetime.now(UTC).isoformat()
print(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
