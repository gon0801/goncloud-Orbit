\echo == P1 candidatas hoy: top por gasto (sin venta, >=3 clics, ventana de cortes, no ASIN, sin clave en cola/veto)
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, metric_date, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks, sum(orders) AS orders, count(*) AS dias
        FROM ult GROUP BY 1,2,3 HAVING sum(cost) > 0 AND sum(orders) = 0 AND sum(clicks) >= 3),
c AS (SELECT t.* FROM t WHERE NOT EXISTS (SELECT 1 FROM apply_queue q WHERE q.platform = t.platform AND q.ad_entity_id = t.ad_entity_id
            AND q.search_term = t.search_term AND (q.estado IN ('pending_veto','released','applying','applied') OR (q.estado='vetoed' AND q.vence_el > now())))),
r AS (SELECT platform, search_term, count(*) AS grupos, sum(cost) AS cost, sum(clicks) AS clicks, max(clicks) AS max_clicks_grupo,
             row_number() OVER (PARTITION BY platform ORDER BY sum(cost) DESC) AS rn FROM c GROUP BY 1,2)
SELECT platform, rn, search_term, grupos, clicks, max_clicks_grupo, round(cost,2) AS gasto FROM r WHERE rn <= 22 ORDER BY 1,2;
\echo == resumen candidatas
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, metric_date, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks FROM ult GROUP BY 1,2,3 HAVING sum(cost) > 0 AND sum(orders) = 0 AND sum(clicks) >= 3)
SELECT platform, count(*) AS pares, count(DISTINCT search_term) AS busquedas, round(sum(cost),0) AS gasto,
       count(*) FILTER (WHERE clicks >= 20) AS pares_20_clics, count(*) FILTER (WHERE clicks BETWEEN 3 AND 5) AS pares_3a5,
       round(sum(cost) FILTER (WHERE clicks >= 10),0) AS gasto_10_clics FROM t GROUP BY 1 ORDER BY 1;
