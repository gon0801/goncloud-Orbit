-- ORBIT 19 / 0.3 — metricas actuales (grano campana/keyword) vs product_ad.
-- Solo lectura. UTC fijado.
-- ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit_read -d orbit -v ON_ERROR_STOP=1'
SET TIME ZONE 'UTC';

SELECT now() AT TIME ZONE 'UTC' AS observed_at_utc;

SELECT kind::text, platform::text, count(*)
  FROM ad_entity
 GROUP BY 1, 2
 ORDER BY 1, 2;

SELECT e.kind::text,
       count(*) AS filas,
       count(DISTINCT e.id) AS entidades,
       sum(v.cost) AS cost,
       sum(v.ad_revenue) AS ad_revenue,
       sum(v.orders) AS orders,
       sum(v.impressions) AS impressions
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 WHERE e.platform = 'amazon_mx'
   AND v.metric_date = DATE '2026-09-03'
 GROUP BY 1
 ORDER BY 1;

SELECT count(*) AS product_ads,
       count(listing_id) AS con_listing,
       count(DISTINCT listing_id) AS listings_distintos
  FROM ad_entity
 WHERE kind = 'product_ad' AND platform = 'amazon_mx';

SELECT e.platform::text,
       max(v.metric_date) AS max_metric_date,
       max(v.observed_at) AS max_observed_at
  FROM v_metric_latest v
  JOIN ad_entity e ON e.id = v.ad_entity_id
 GROUP BY 1
 ORDER BY 1;
