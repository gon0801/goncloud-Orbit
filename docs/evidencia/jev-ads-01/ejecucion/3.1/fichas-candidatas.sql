WITH candidatas AS (
  SELECT d.id, d.kind::text AS kind, d.search_term, d.ad_entity_id, e.platform::text AS plataforma, d.window_end
    FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id
   WHERE d.kind IN ('negative', 'harvest') AND d.decided_at >= now() - interval '30 days'
     AND d.window_end <= current_date - 10
)
SELECT c.plataforma, c.kind, count(DISTINCT c.id) AS decisiones, count(DISTINCT c.search_term) AS terminos,
       count(DISTINCT l.product_id) AS productos_en_origen
  FROM candidatas c
  LEFT JOIN ad_entity pa ON pa.parent_id = c.ad_entity_id AND pa.kind = 'product_ad'
  LEFT JOIN listing l ON l.id = pa.listing_id
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT c.plataforma, l.product_id, p.odoo_sku, left(p.name, 60), string_agg(DISTINCT c.id::text, ',')
  FROM (SELECT d.id, d.ad_entity_id, e.platform::text AS plataforma FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id
         WHERE d.kind IN ('negative','harvest') AND d.decided_at >= now() - interval '30 days' AND d.window_end <= current_date - 10) c
  JOIN ad_entity pa ON pa.parent_id = c.ad_entity_id AND pa.kind = 'product_ad'
  JOIN listing l ON l.id = pa.listing_id JOIN product p ON p.id = l.product_id
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2;
