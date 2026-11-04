-- ORBIT 19 / 0.1 — inventario de catalogo. Solo lectura. UTC fijado.
-- Ejecutar: ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit_read -d orbit -v ON_ERROR_STOP=1'
SET TIME ZONE 'UTC';

\echo '=== OBSERVED_AT UTC ==='
SELECT now() AT TIME ZONE 'UTC' AS observed_at_utc,
       (now() AT TIME ZONE 'UTC')::date AS hoy_utc,
       DATE '2026-02-20' AS ventana_desde,
       (now() AT TIME ZONE 'UTC')::date - 15 AS ventana_hasta;

\echo '=== LISTINGS Y PRODUCTOS POR MERCADO ==='
SELECT l.platform::text AS platform,
       count(*) AS listings,
       count(DISTINCT l.product_id) AS productos,
       count(*) FILTER (WHERE l.seller_sku IS NULL OR btrim(l.seller_sku) = '') AS sin_seller_sku,
       count(*) FILTER (WHERE l.external_id IS NULL OR btrim(l.external_id) = '') AS sin_asin,
       count(*) FILTER (WHERE l.listing_price IS NULL) AS sin_precio
  FROM listing l
 WHERE l.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY 1
 ORDER BY 1;

\echo '=== CATALOGO DEL SELECTOR (mismo JOIN que app/fabrica_web.py) ==='
WITH cat AS (
  SELECT p.id AS product_id,
         p.odoo_sku,
         l.platform::text AS platform,
         count(l.id) AS n_listings,
         min(l.seller_sku) AS seller_sku_min,
         bool_or(l.seller_sku IS NULL OR btrim(l.seller_sku) = '') AS algun_sku_vacio,
         m.margen_neto_pct,
         m.dias_con_venta,
         m.cobertura,
         m.fees_sin_tipo,
         m.venta_total,
         m.ventana_desde,
         m.ventana_hasta
    FROM product p
    JOIN listing l ON l.product_id = p.id AND l.platform IN ('amazon_mx', 'amazon_us')
    LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = l.platform
   GROUP BY p.id, p.odoo_sku, l.platform, m.margen_neto_pct, m.dias_con_venta,
            m.cobertura, m.fees_sin_tipo, m.venta_total, m.ventana_desde, m.ventana_hasta
)
SELECT platform,
       count(*) AS productos,
       count(*) FILTER (
         WHERE n_listings = 1
           AND margen_neto_pct IS NOT NULL
           AND seller_sku_min IS NOT NULL
           AND btrim(seller_sku_min) <> ''
       ) AS elegibles_selector,
       count(*) FILTER (WHERE n_listings <> 1) AS multilisting,
       count(*) FILTER (
         WHERE n_listings = 1 AND margen_neto_pct IS NULL AND venta_total IS NULL
       ) AS sin_ventas_vinculadas,
       count(*) FILTER (
         WHERE n_listings = 1 AND margen_neto_pct IS NULL
           AND dias_con_venta IS NOT NULL AND dias_con_venta < 30
       ) AS menos_30_fechas,
       count(*) FILTER (
         WHERE n_listings = 1 AND margen_neto_pct IS NULL
           AND dias_con_venta IS NOT NULL AND dias_con_venta >= 30
       ) AS margen_null_otras_guardas,
       count(*) FILTER (
         WHERE n_listings = 1 AND margen_neto_pct IS NOT NULL
           AND (seller_sku_min IS NULL OR btrim(seller_sku_min) = '')
       ) AS sin_seller_sku
  FROM cat
 GROUP BY platform
 ORDER BY 1;

\echo '=== MULTILISTING: IDS AMBIGUOS ==='
SELECT l.platform::text AS platform,
       p.id AS product_id,
       p.odoo_sku,
       count(l.id) AS n_listings,
       array_agg(l.id ORDER BY l.external_id, l.id) AS listing_ids,
       array_agg(l.external_id ORDER BY l.external_id, l.id) AS asins,
       array_agg(l.seller_sku ORDER BY l.external_id, l.id) AS seller_skus
  FROM product p
  JOIN listing l ON l.product_id = p.id AND l.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY l.platform, p.id, p.odoo_sku
HAVING count(l.id) > 1
 ORDER BY l.platform, n_listings DESC, p.odoo_sku;

\echo '=== PRODUCT ADS SIN LISTING (OMISION DE MAPA EN ANUNCIOS) ==='
SELECT e.platform::text AS platform,
       e.state,
       count(*) AS product_ads,
       count(*) FILTER (WHERE e.listing_id IS NULL) AS sin_listing_id
  FROM ad_entity e
 WHERE e.kind = 'product_ad' AND e.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '=== LEDGER SALES SIN PRODUCTO (OMISION CONTABLE) ==='
SELECT platform::text AS platform,
       count(*) FILTER (WHERE product_id IS NULL) AS sales_sin_product_id,
       count(*) FILTER (WHERE product_id IS NOT NULL) AS sales_con_product_id
  FROM ledger_event
 WHERE kind = 'sale' AND platform IN ('amazon_mx', 'amazon_us')
   AND event_date >= DATE '2026-02-20'
   AND event_date < (now() AT TIME ZONE 'UTC')::date - 15
 GROUP BY 1
 ORDER BY 1;

\echo '=== LOTES FABRICA RECUPERABLES ==='
SELECT l.lote,
       l.platform::text AS platform,
       l.estado,
       l.created_at,
       l.finished_at,
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

\echo '=== VENTANA MARGEN (UNA FILA DE MUESTRA) ==='
SELECT DISTINCT ventana_desde, ventana_hasta, ledger_fresco_at
  FROM v_margen_producto
 LIMIT 5;
