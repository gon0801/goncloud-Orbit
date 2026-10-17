-- JEV ADS 03 diseno: SOLO LECTURA (orbit_read).
BEGIN READ ONLY;
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, search_term, metric_date, cost, clicks, orders, ad_revenue
  FROM search_term_observation WHERE NOT is_asin_like
  ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
cls AS (
  SELECT *, CASE
    WHEN lower(search_term) ~ '(\m(10|14|18|24)\s*(k|kt|ct|kilates?|quilates?|karat)\M)|white gold|oro blanco|solid gold|oro (macizo|solido|sólido|puro)|\m(10|14|18|24)k\M' THEN '1 oro con quilates'
    WHEN lower(search_term) ~ '\m(oro|gold|golden|dorad[oa]s?)\M' THEN '2 oro o dorado sin quilates'
    ELSE '3 resto' END AS grupo
  FROM ult)
SELECT platform, grupo, count(DISTINCT search_term) busquedas, min(metric_date) desde, max(metric_date) hasta,
  sum(clicks) clics, round(sum(cost),2) gasto, sum(orders) ventas, round(sum(ad_revenue),2) ingreso,
  round(100.0*sum(orders)/nullif(sum(clicks),0),2) conv_pct,
  round(100.0*sum(cost)/nullif(sum(ad_revenue),0),1) acos_pct
FROM cls GROUP BY 1,2 ORDER BY 1,2;
\echo -- quilates con venta
SELECT platform, search_term, sum(clicks) clics, round(sum(cost),2) gasto, sum(orders) ventas, round(sum(ad_revenue),2) ingreso
FROM cls WHERE grupo='1 oro con quilates' GROUP BY 1,2 HAVING sum(orders)>0 ORDER BY ventas DESC LIMIT 25;
\echo -- quilates mas gasto sin venta
SELECT platform, search_term, sum(clicks) clics, round(sum(cost),2) gasto, coalesce(sum(orders),0) ventas
FROM cls WHERE grupo='1 oro con quilates' GROUP BY 1,2 HAVING coalesce(sum(orders),0)=0 ORDER BY gasto DESC LIMIT 10;
ROLLBACK;
