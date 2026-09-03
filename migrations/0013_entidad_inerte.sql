-- =============================================================================
--  ORBIT · MIGRACION 0013 · vista v_entidad_inerte (BIDS 01) · PostgreSQL 16
--
--  La vista es la UNICA fuente de "inerte" (D2 sellada): el ciclo, la pagina
--  /inertes, el digest y la herramienta de archivo leen AQUI (regla 2: un
--  numero, una fuente). Hoja (keyword/product_target) con status ENABLED
--  propio, del ad group y de la campana (cache ad_entity_state, no el LIST)
--  sin impresiones en los ultimos N = 14 dias contados desde el WATERMARK
--  de SU plataforma (max(metric_date) en v_metric_latest, misma consulta
--  que windows.py). Desde now() seria mentira con ingesta D-1: un retraso
--  de datos pareceria inactividad. Clasificacion sobre 90 dias desde el
--  watermark: con_ventas_previas (ordenes > 0) / gasto_sin_ventas (gasto >
--  0, cero ordenes) / peso_muerto (nada en 90d).
--
--  Ausencia de fila = NO inerte (la guarda del ciclo solo salta lo que la
--  vista devuelve). La vista NO decide nada: solo expone el diagnostico.
-- =============================================================================

CREATE OR REPLACE VIEW v_entidad_inerte AS
WITH watermark AS (
  SELECT e.platform, max(v.metric_date) AS wm
    FROM v_metric_latest v JOIN ad_entity e ON e.id = v.ad_entity_id
   GROUP BY e.platform
), hojas AS (
  SELECT e.id, e.platform, e.kind, e.keyword_text, e.name, e.external_id,
         g.id AS ad_group_id, g.name AS ad_group_name,
         c.id AS campaign_id, c.name AS campaign_name
    FROM ad_entity e
    JOIN ad_entity g ON g.id = e.parent_id AND g.kind = 'ad_group'
    JOIN ad_entity c ON c.id = g.parent_id AND c.kind = 'campaign'
    JOIN ad_entity_state se ON se.ad_entity_id = e.id AND se.status = 'ENABLED'
    JOIN ad_entity_state sg ON sg.ad_entity_id = g.id AND sg.status = 'ENABLED'
    JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
   WHERE e.kind IN ('keyword', 'product_target')
), reciente AS (
  -- N = 14 dias desde el watermark de SU plataforma (sellado BIDS 01).
  -- Regla 3 (revision PR #133): impressions NULL es DESCONOCIDO, no cero:
  -- filas_14d cuenta observaciones (0 sin filas: el count ignora la fila
  -- NULL-extendida del LEFT JOIN) y nulas_14d las que traen NULL (OJO:
  -- count(columna), no count(*): con cero filas el filtrado daria 1).
  SELECT h.id,
         count(v.ad_entity_id) AS filas_14d,
         count(v.ad_entity_id) FILTER (WHERE v.impressions IS NULL) AS nulas_14d,
         coalesce(sum(v.impressions), 0) AS impresiones_14d
    FROM hojas h JOIN watermark w ON w.platform = h.platform
    LEFT JOIN v_metric_latest v ON v.ad_entity_id = h.id AND v.metric_date > w.wm - 14
   GROUP BY h.id
), historia AS (
  -- 90 dias desde el watermark. Regla 4 (revision PR #133): el gasto se
  -- expone con su moneda (mon_min/mon_max iguales) y se ANULA con mezcla
  -- (fail-loud, jamas una suma mezclada); la clasificacion usa los crudos.
  SELECT h.id,
         count(v.ad_entity_id) AS filas_90d,
         coalesce(sum(v.cost), 0) AS gasto_suma_90d,
         coalesce(sum(v.orders), 0) AS ordenes_90d,
         min(v.metric_currency) AS mon_min_90d,
         max(v.metric_currency) AS mon_max_90d,
         max(v.metric_date) FILTER (WHERE v.impressions > 0) AS ultima_impresion
    FROM hojas h JOIN watermark w ON w.platform = h.platform
    LEFT JOIN v_metric_latest v ON v.ad_entity_id = h.id AND v.metric_date > w.wm - 90
   GROUP BY h.id
)
SELECT h.*,
       w.wm AS watermark,
       hi.ultima_impresion,
       -- date - date = integer; NULL = nunca hubo impresion en 90d
       (w.wm - hi.ultima_impresion) AS dias_sin_impresiones,
       CASE WHEN hi.filas_90d > 0 AND hi.mon_min_90d IS DISTINCT FROM hi.mon_max_90d
            THEN NULL ELSE hi.gasto_suma_90d END AS gasto_90d,
       CASE WHEN hi.mon_min_90d IS NOT DISTINCT FROM hi.mon_max_90d
            THEN hi.mon_min_90d END AS moneda,
       hi.ordenes_90d,
       CASE WHEN hi.ordenes_90d > 0 THEN 'con_ventas_previas'
            WHEN hi.gasto_suma_90d > 0 THEN 'gasto_sin_ventas'
            ELSE 'peso_muerto' END AS clasificacion
  FROM hojas h
  JOIN watermark w ON w.platform = h.platform
  JOIN reciente r ON r.id = h.id
     AND (r.filas_14d = 0 OR (r.nulas_14d = 0 AND r.impresiones_14d = 0))
  JOIN historia hi ON hi.id = h.id;

COMMENT ON VIEW v_entidad_inerte IS
  'BIDS 01 (D2, spec 2026-08-26 aprobada 2026-09-03): diagnostico de hojas '
  'sin trafico. N = 14 dias contados desde el watermark de metricas de la '
  'plataforma (max(metric_date) en v_metric_latest), jamas desde now(): con '
  'ingesta D-1 un retraso de datos pareceria inactividad. Fuente UNICA de '
  '"inerte" para el ciclo (guarda entidad_inerte), la pagina /inertes, el '
  'digest y la herramienta de archivo (regla 2). Solo hojas '
  'keyword/product_target con status ENABLED propio, del ad group y de la '
  'campana (cache ad_entity_state). Regla 3 (revision PR #133): impressions '
  'NULL es DESCONOCIDO, no cero — inerte solo sin filas en 14d o con filas '
  'todas conocidas sumando 0; una sola observacion reciente NULL deja la '
  'hoja FUERA (sigue optimizandose). Regla 4: gasto_90d viaja con su moneda '
  '(unica en 90d); con mezcla, moneda NULL y gasto_90d NULL (fail-loud). '
  'Clasificacion sobre 90 dias desde el watermark (con los crudos, sin '
  'depender de la moneda): con_ventas_previas / gasto_sin_ventas / '
  'peso_muerto. Ausencia de fila = NO inerte.';

GRANT SELECT ON v_entidad_inerte
    TO app_read, app_ingest, app_decide, app_admin;
