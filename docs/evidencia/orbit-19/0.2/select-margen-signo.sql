-- ORBIT 19 / 0.2 — signos de margen por mercado. Solo lectura.
-- No mezclar MX y US. Null no es cero. Cero/negativo son numeros, no ausencia.
SET TIME ZONE 'UTC';

SELECT now() AT TIME ZONE 'UTC' AS observed_at_utc,
       DATE '2026-02-20' AS ventana_desde,
       (now() AT TIME ZONE 'UTC')::date - 15 AS ventana_hasta;

WITH cat AS (
  SELECT p.id AS product_id,
         l.platform::text AS platform,
         count(l.id) AS n_listings,
         m.margen_neto_pct,
         m.dias_con_venta,
         m.venta_total
    FROM product p
    JOIN listing l ON l.product_id = p.id AND l.platform IN ('amazon_mx', 'amazon_us')
    LEFT JOIN v_margen_producto m ON m.product_id = p.id AND m.platform = l.platform
   GROUP BY p.id, l.platform, m.margen_neto_pct, m.dias_con_venta, m.venta_total
)
SELECT platform,
       count(*) AS productos,
       count(*) FILTER (WHERE n_listings > 1) AS multilisting,
       count(*) FILTER (WHERE margen_neto_pct > 0) AS margen_positivo,
       count(*) FILTER (WHERE margen_neto_pct = 0) AS margen_cero,
       count(*) FILTER (WHERE margen_neto_pct < 0) AS margen_negativo,
       count(*) FILTER (WHERE margen_neto_pct IS NULL) AS margen_null,
       count(*) FILTER (WHERE margen_neto_pct IS NULL AND venta_total IS NULL) AS null_sin_ventas,
       count(*) FILTER (
         WHERE margen_neto_pct IS NULL AND dias_con_venta IS NOT NULL AND dias_con_venta < 30
       ) AS null_menos_30,
       count(*) FILTER (WHERE n_listings > 1 AND margen_neto_pct IS NULL) AS multi_sin_margen,
       count(*) FILTER (WHERE n_listings > 1 AND margen_neto_pct > 0) AS multi_margen_pos,
       count(*) FILTER (WHERE n_listings = 1 AND margen_neto_pct > 0) AS uno_margen_pos
  FROM cat
 GROUP BY platform
 ORDER BY 1;

SELECT platform::text,
       count(*) AS filas_vista,
       count(*) FILTER (WHERE margen_neto_pct > 0) AS pos,
       count(*) FILTER (WHERE margen_neto_pct = 0) AS cero,
       count(*) FILTER (WHERE margen_neto_pct < 0) AS neg,
       min(margen_neto_pct) AS min_pct,
       max(margen_neto_pct) AS max_pct
  FROM v_margen_producto
 WHERE platform IN ('amazon_mx', 'amazon_us')
 GROUP BY 1
 ORDER BY 1;
