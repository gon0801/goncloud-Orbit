-- Continuacion 0.1: ad_entity no tiene state; el cache es ad_entity_state.status.
SET TIME ZONE 'UTC';

\echo '=== ELEGIBLES ACTUALES (IDs) ==='
WITH cat AS (
  SELECT p.id AS product_id, p.odoo_sku, l.platform::text AS platform,
         count(l.id) AS n_listings, min(l.seller_sku) AS seller_sku,
         min(l.id) AS listing_id, min(l.external_id) AS asin,
         m.margen_neto_pct, m.dias_con_venta
    FROM product p
    JOIN listing l ON l.product_id = p.id AND l.platform IN ('amazon_mx','amazon_us')
    LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = l.platform
   GROUP BY p.id, p.odoo_sku, l.platform, m.margen_neto_pct, m.dias_con_venta
)
SELECT * FROM cat
 WHERE n_listings = 1 AND margen_neto_pct IS NOT NULL
   AND seller_sku IS NOT NULL AND btrim(seller_sku) <> ''
 ORDER BY platform, odoo_sku;

\echo '=== PRODUCT ADS SIN LISTING ==='
SELECT e.platform::text AS platform,
       coalesce(s.status, 'SIN_STATE') AS status,
       count(*) AS product_ads,
       count(*) FILTER (WHERE e.listing_id IS NULL) AS sin_listing_id,
       count(*) FILTER (WHERE e.listing_id IS NOT NULL) AS con_listing_id
  FROM ad_entity e
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = e.id
 WHERE e.kind = 'product_ad' AND e.platform IN ('amazon_mx','amazon_us')
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '=== LEDGER SALES SIN PRODUCTO ==='
SELECT platform::text AS platform,
       count(*) FILTER (WHERE product_id IS NULL) AS sales_sin_product_id,
       count(*) FILTER (WHERE product_id IS NOT NULL) AS sales_con_product_id
  FROM ledger_event
 WHERE kind = 'sale' AND platform IN ('amazon_mx','amazon_us')
   AND event_date >= DATE '2026-02-20'
   AND event_date < (now() AT TIME ZONE 'UTC')::date - 15
 GROUP BY 1
 ORDER BY 1;

\echo '=== LOTES FABRICA ==='
SELECT l.lote, l.platform::text, l.estado, l.created_at, l.finished_at,
       (SELECT count(*) FROM fabrica_lote_paso p WHERE p.lote = l.lote) AS pasos,
       (SELECT count(*) FROM fabrica_lote_paso p WHERE p.lote = l.lote AND p.estado = 'applied') AS applied,
       (SELECT count(*) FROM fabrica_lote_paso p WHERE p.lote = l.lote AND p.estado = 'failed') AS failed,
       (SELECT count(*) FROM fabrica_lote_paso p WHERE p.lote = l.lote AND p.estado = 'planeado') AS planeado,
       EXISTS (
         SELECT 1 FROM fabrica_lote_paso p
          WHERE p.lote = l.lote AND p.estado <> 'applied' AND p.external_id IS NULL
       ) AS incierto_sin_id,
       EXISTS (
         SELECT 1 FROM fabrica_lote_paso p
          WHERE p.lote = l.lote AND p.external_id IS NOT NULL
       ) AS tiene_ids_externos
  FROM fabrica_lote l
 ORDER BY l.created_at;

\echo '=== CONTEO MULTILISTING ==='
SELECT platform, count(*) AS productos_multilisting, sum(n) AS listings_en_ellos
  FROM (
    SELECT l.platform::text AS platform, p.id, count(*) AS n
      FROM product p
      JOIN listing l ON l.product_id = p.id AND l.platform IN ('amazon_mx','amazon_us')
     GROUP BY l.platform, p.id
    HAVING count(*) > 1
  ) t
 GROUP BY 1
 ORDER BY 1;
