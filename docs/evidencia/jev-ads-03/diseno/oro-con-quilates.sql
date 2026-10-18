-- JEV ADS 03 diseno: SOLO LECTURA (orbit_read). Busquedas de oro con quilates.
-- Ultima observacion por dia y entidad; sin terminos ASIN. Cada consulta trae su CTE.
BEGIN READ ONLY;
\echo == 1. por mercado y tipo
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, search_term, metric_date, cost, clicks, orders, ad_revenue
  FROM search_term_observation WHERE NOT is_asin_like
  ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
cls AS (
  SELECT *, CASE
    WHEN lower(search_term) ~ '(\m(10|14|18|24)\s*(k|kt|ct|kilates?|quilates?|karats?|carats?)\M)|white gold|oro blanco|solid gold|oro (macizo|solido|sólido|puro)|\m(10|14|18|24)k\M' THEN '1 oro con quilates'
    WHEN lower(search_term) ~ '\m(oro|gold|golden|dorad[oa]s?)\M' THEN '2 oro o dorado sin quilates'
    ELSE '3 resto' END AS grupo
  FROM ult)
SELECT platform, grupo, count(DISTINCT search_term) busquedas, min(metric_date) desde, max(metric_date) hasta,
  sum(clicks) clics, round(sum(cost),2) gasto, sum(orders) ventas, round(sum(ad_revenue),2) ingreso,
  round(100.0*sum(orders)/nullif(sum(clicks),0),2) conv_pct
FROM cls GROUP BY 1,2 ORDER BY 1,2;
\echo == 2. oro con quilates: con venta o mas de 20 de gasto
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, search_term, metric_date, cost, clicks, orders, ad_revenue
  FROM search_term_observation WHERE NOT is_asin_like
  ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
q AS (SELECT * FROM ult WHERE lower(search_term) ~ '(\m(10|14|18|24)\s*(k|kt|ct|kilates?|quilates?|karats?|carats?)\M)|white gold|oro blanco|solid gold|oro (macizo|solido|sólido|puro)|\m(10|14|18|24)k\M')
SELECT platform, search_term, sum(clicks) clics, round(sum(cost),2) gasto, coalesce(sum(orders),0) ventas, round(sum(ad_revenue),2) ingreso
FROM q GROUP BY 1,2 HAVING coalesce(sum(orders),0)>0 OR sum(cost) > 20 ORDER BY ventas DESC, gasto DESC;
ROLLBACK;
