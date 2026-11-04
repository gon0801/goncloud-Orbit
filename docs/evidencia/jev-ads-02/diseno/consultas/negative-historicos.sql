\echo == los 18 pares negative historicos: la busqueda vende en otros grupos? (ventana de cortes de hoy y todo el historial observado)
WITH neg AS (SELECT DISTINCT e.platform, d.ad_entity_id, d.search_term, min(d.decided_at)::date AS primera, max(d.decided_at)::date AS ultima, count(*) AS veces
               FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id WHERE d.kind='negative' GROUP BY 1,2,3),
ult AS (SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, metric_date, cost, clicks, orders
          FROM search_term_observation WHERE NOT is_asin_like ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks, sum(orders) AS orders FROM ult GROUP BY 1,2,3)
SELECT n.platform, n.ad_entity_id AS grupo, n.search_term, n.veces, n.primera,
       (SELECT estado FROM apply_queue q WHERE q.ad_entity_id = n.ad_entity_id AND q.search_term = n.search_term AND q.kind='negative' ORDER BY q.id DESC LIMIT 1) AS cola,
       (SELECT t.clicks FROM t WHERE t.platform=n.platform AND t.ad_entity_id=n.ad_entity_id AND t.search_term=n.search_term) AS clics_aqui,
       (SELECT t.orders FROM t WHERE t.platform=n.platform AND t.ad_entity_id=n.ad_entity_id AND t.search_term=n.search_term) AS ordenes_aqui,
       (SELECT count(*) FROM t WHERE t.platform=n.platform AND t.search_term=n.search_term AND t.ad_entity_id<>n.ad_entity_id AND t.orders>0) AS otros_grupos_que_venden,
       (SELECT coalesce(sum(t.orders),0) FROM t WHERE t.platform=n.platform AND t.search_term=n.search_term AND t.ad_entity_id<>n.ad_entity_id) AS ordenes_en_otros
  FROM neg n ORDER BY 1, 5, 3;
