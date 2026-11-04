\echo == P7 gasto sin venta por par: la misma busqueda vendio en OTRO grupo de la plataforma en la ventana?
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks, sum(orders) AS orders FROM ult GROUP BY 1,2,3 HAVING sum(cost) > 0),
s AS (SELECT platform, search_term, sum(orders) AS orders_total, count(*) AS grupos, count(*) FILTER (WHERE orders > 0) AS grupos_con_venta FROM t GROUP BY 1,2)
SELECT t.platform,
       round(sum(t.cost),0) AS gasto_total,
       round(sum(t.cost) FILTER (WHERE t.orders = 0),0) AS gasto_pares_sin_venta,
       round(sum(t.cost) FILTER (WHERE t.orders = 0 AND s.orders_total > 0),0) AS sin_venta_aqui_pero_vende_en_otro_grupo,
       round(sum(t.cost) FILTER (WHERE t.orders = 0 AND s.orders_total = 0),0) AS no_vende_en_ningun_grupo,
       round(sum(t.cost) FILTER (WHERE t.orders = 0 AND s.orders_total = 0 AND t.clicks >= 3),0) AS no_vende_en_ninguno_3_clics,
       count(DISTINCT t.search_term) FILTER (WHERE s.orders_total = 0) AS busquedas_que_no_venden_en_ninguno,
       count(DISTINCT t.search_term) FILTER (WHERE t.orders = 0 AND s.orders_total > 0) AS busquedas_que_venden_en_otro
  FROM t JOIN s USING (platform, search_term) GROUP BY 1 ORDER BY 1;
\echo == top busquedas que no venden en NINGUN grupo, por gasto
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
s AS (SELECT platform, search_term, sum(cost) AS cost, sum(clicks) AS clicks, sum(orders) AS orders, count(DISTINCT ad_entity_id) AS grupos FROM ult GROUP BY 1,2 HAVING sum(cost) > 0),
r AS (SELECT *, row_number() OVER (PARTITION BY platform ORDER BY cost DESC) AS rn FROM s WHERE orders = 0)
SELECT platform, rn, search_term, grupos, clicks, round(cost,2) AS gasto FROM r WHERE rn <= 18 ORDER BY 1,2;
\echo == top busquedas que venden en un grupo y queman en otros: gasto quemado y ordenes
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks, sum(orders) AS orders FROM ult GROUP BY 1,2,3 HAVING sum(cost) > 0),
s AS (SELECT platform, search_term, count(*) AS grupos, count(*) FILTER (WHERE orders > 0) AS grupos_venden, sum(orders) AS ordenes,
             sum(cost) FILTER (WHERE orders = 0) AS quemado, sum(cost) FILTER (WHERE orders > 0) AS gasto_donde_vende FROM t GROUP BY 1,2),
r AS (SELECT *, row_number() OVER (PARTITION BY platform ORDER BY quemado DESC NULLS LAST) AS rn FROM s WHERE grupos_venden > 0 AND quemado > 0)
SELECT platform, rn, search_term, grupos, grupos_venden, ordenes, round(quemado,2) AS quemado, round(gasto_donde_vende,2) AS gasto_donde_vende FROM r WHERE rn <= 10 ORDER BY 1,2;
