-- Prototipo: busquedas que gastan y NO venden en ningun grupo (ventana de cortes), top por gasto,
-- cada una en el grupo donde mas gasto. SOLO LECTURA.
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks, sum(orders) AS orders FROM ult GROUP BY 1,2,3 HAVING sum(cost) > 0),
s AS (SELECT platform, search_term, sum(cost) AS cost_total, sum(clicks) AS clicks_total, sum(orders) AS orders_total, count(*) AS grupos FROM t GROUP BY 1,2),
top AS (SELECT s.*, row_number() OVER (PARTITION BY platform ORDER BY cost_total DESC) AS rn FROM s WHERE orders_total = 0),
mejor AS (SELECT DISTINCT ON (t.platform, t.search_term) t.platform, t.search_term, t.ad_entity_id AS grupo
            FROM t JOIN top ON top.platform = t.platform AND top.search_term = t.search_term ORDER BY t.platform, t.search_term, t.cost DESC),
prod AS (SELECT pa.platform, pa.parent_id AS grupo, count(DISTINCT l.product_id) AS productos
           FROM ad_entity pa JOIN ad_entity_state st ON st.ad_entity_id = pa.id AND st.status IN ('ENABLED','PAUSED')
           JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform WHERE pa.kind='product_ad' GROUP BY 1,2)
SELECT 'S', top.platform, top.rn, m.grupo, coalesce(p.productos,0), top.grupos, top.clicks_total, round(top.cost_total,2), top.search_term
  FROM top JOIN mejor m ON m.platform = top.platform AND m.search_term = top.search_term
  LEFT JOIN prod p ON p.platform = top.platform AND p.grupo = m.grupo
 WHERE top.rn <= 60 ORDER BY 2, 3;
