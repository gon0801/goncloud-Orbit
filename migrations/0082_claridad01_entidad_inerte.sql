-- CLARIDAD 01 G.2: v_entidad_inerte en una sola pasada (la pantalla Inertes tardaba 235 s).
--
-- sonda-aplicada: SELECT pg_get_viewdef('public.v_entidad_inerte'::regclass) LIKE '%ultimas_90d%'
--
-- Mismas columnas, mismo orden y mismas filas que la 0013. El EXPLAIN en una copia del
-- respaldo (docs/evidencia/claridad-01/G/explain-antes.txt) mostro que el costo no eran
-- los recorridos de v_metric_latest (unos 100 ms cada uno): el planificador estima 1 fila
-- para `hojas` (son cientos) y con bucles anidados re-ejecutaba el agregado de 14 dias
-- por hoja y el de 90 dias por cada par. Aqui hay un solo agregado: el watermark sale de
-- ads_metric_observation (mismo max por plataforma que v_metric_latest, que tiene una fila
-- por cada par entidad-fecha observado), la ultima observacion por (hoja, dia) se toma una
-- vez con DISTINCT ON sobre los 90 dias del watermark, y 14 y 90 dias salen de ese CTE con
-- FILTER. Filtrar por metric_date antes del DISTINCT ON no cambia la ultima observacion de
-- un dia: metric_date es parte de la clave. No es vista materializada: el estado de hoja,
-- grupo y campana se lee en vivo, como en la 0013, porque la vista alimenta el ciclo y el
-- archivado.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_entidad_inerte AS
WITH watermark AS (
  SELECT e.platform, max(o.metric_date) AS wm
    FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
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
), ultimas_90d AS (
  SELECT DISTINCT ON (o.ad_entity_id, o.metric_date)
         o.ad_entity_id, o.metric_date, o.impressions, o.cost, o.orders, o.metric_currency
    FROM ads_metric_observation o
    JOIN hojas h ON h.id = o.ad_entity_id
    JOIN watermark w ON w.platform = h.platform
   WHERE o.metric_date > w.wm - 90
   ORDER BY o.ad_entity_id, o.metric_date, o.observed_at DESC
), metricas AS (
  SELECT h.id, h.platform, h.kind, h.keyword_text, h.name, h.external_id,
         h.ad_group_id, h.ad_group_name, h.campaign_id, h.campaign_name, w.wm,
         count(u.ad_entity_id) FILTER (WHERE u.metric_date > w.wm - 14) AS filas_14d,
         count(u.ad_entity_id) FILTER (WHERE u.metric_date > w.wm - 14
                                         AND u.impressions IS NULL) AS nulas_14d,
         coalesce(sum(u.impressions) FILTER (WHERE u.metric_date > w.wm - 14), 0)
           AS impresiones_14d,
         count(u.ad_entity_id) AS filas_90d,
         coalesce(sum(u.cost), 0) AS gasto_suma_90d,
         coalesce(sum(u.orders), 0) AS ordenes_90d,
         min(u.metric_currency) AS mon_min_90d,
         max(u.metric_currency) AS mon_max_90d,
         max(u.metric_date) FILTER (WHERE u.impressions > 0) AS ultima_impresion
    FROM hojas h
    JOIN watermark w ON w.platform = h.platform
    LEFT JOIN ultimas_90d u ON u.ad_entity_id = h.id
   GROUP BY h.id, h.platform, h.kind, h.keyword_text, h.name, h.external_id,
            h.ad_group_id, h.ad_group_name, h.campaign_id, h.campaign_name, w.wm
)
SELECT m.id, m.platform, m.kind, m.keyword_text, m.name, m.external_id,
       m.ad_group_id, m.ad_group_name, m.campaign_id, m.campaign_name,
       m.wm AS watermark,
       m.ultima_impresion,
       (m.wm - m.ultima_impresion) AS dias_sin_impresiones,
       CASE WHEN m.filas_90d > 0 AND m.mon_min_90d IS DISTINCT FROM m.mon_max_90d
            THEN NULL ELSE m.gasto_suma_90d END AS gasto_90d,
       CASE WHEN m.mon_min_90d IS NOT DISTINCT FROM m.mon_max_90d
            THEN m.mon_min_90d END AS moneda,
       m.ordenes_90d,
       CASE WHEN m.ordenes_90d > 0 THEN 'con_ventas_previas'
            WHEN m.gasto_suma_90d > 0 THEN 'gasto_sin_ventas'
            ELSE 'peso_muerto' END AS clasificacion
  FROM metricas m
 WHERE m.filas_14d = 0 OR (m.nulas_14d = 0 AND m.impresiones_14d = 0);

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
  'peso_muerto. Ausencia de fila = NO inerte.'
  ' CLARIDAD 01 G.2 (0082): misma definicion en una sola pasada (un DISTINCT ON '
  'sobre 90 dias del watermark; 14 y 90 dias con FILTER).';
