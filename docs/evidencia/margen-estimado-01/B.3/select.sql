\pset pager off
-- MARGEN ESTIMADO 01 / B.3 — conteos de universo y escenarios.
-- Solo lectura. REPEATABLE READ. Sin datos personales.
--
-- Correr contra Orbit produccion con orbit_read:
--   ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit_read -d orbit \
--     -v ON_ERROR_STOP=1 -P pager=off' \
--     < docs/evidencia/margen-estimado-01/B.3/select.sql
--
-- La seccion B exige migrations/0028_estimacion_venta.sql aplicada.
-- Si to_regclass('public.estimacion_escenario') es NULL, copiar solo la
-- seccion A y declarar B bloqueada en conteos.json / reporte.md.

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

-- A. Universo FBA MX y economia observada (vive sin 0028)
SELECT now() AT TIME ZONE 'UTC' AS consulted_at_utc;

SELECT to_regclass('public.estimacion_escenario') AS estimacion_escenario,
       to_regclass('public.estimacion_oferta_observation') AS estimacion_oferta,
       to_regclass('public.estimacion_politica_version') AS estimacion_politica,
       to_regclass('public.disponibilidad_observation') AS disponibilidad,
       to_regclass('public.v_margen_producto') AS v_margen_producto;

SELECT l.platform::text,
       count(*) AS listings,
       count(*) FILTER (
           WHERE l.seller_sku IS NOT NULL AND btrim(l.seller_sku) <> ''
       ) AS con_sku,
       count(DISTINCT l.product_id) AS productos
  FROM listing l
 WHERE l.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY l.platform
 ORDER BY l.platform;

WITH fba AS (
    SELECT DISTINCT ON (platform, seller_sku)
           platform, seller_sku, quantity, fetched_at, observed_at
      FROM disponibilidad_observation
     WHERE fuente = 'fba'
     ORDER BY platform, seller_sku, observed_at DESC, id DESC
)
SELECT l.platform::text,
       count(*) AS listings_con_fila_fba,
       count(*) FILTER (WHERE fba.quantity IS NOT NULL) AS quantity_conocida,
       count(*) FILTER (WHERE fba.quantity = 0) AS quantity_cero,
       count(*) FILTER (WHERE fba.quantity > 0) AS quantity_positiva,
       max(fba.fetched_at) AS fetched_at_max,
       max(fba.observed_at) AS observed_at_max
  FROM listing l
  JOIN fba ON fba.platform = l.platform AND fba.seller_sku = l.seller_sku
 WHERE l.platform = 'amazon_mx'
 GROUP BY l.platform;

SELECT m.platform::text,
       count(*) AS filas_margen,
       count(*) FILTER (WHERE m.dias_con_venta IS NULL) AS dias_null,
       count(*) FILTER (WHERE m.dias_con_venta = 0) AS dias_cero,
       count(*) FILTER (WHERE m.dias_con_venta > 0) AS dias_con_venta,
       count(*) FILTER (WHERE m.margen_neto_pct IS NULL) AS margen_null,
       count(*) FILTER (WHERE m.margen_neto_pct IS NOT NULL) AS margen_presente
  FROM v_margen_producto m
 WHERE m.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY m.platform
 ORDER BY m.platform;

SELECT l.platform::text,
       count(*) AS listings,
       count(*) FILTER (WHERE m.product_id IS NULL) AS sin_fila_margen,
       count(*) FILTER (
           WHERE m.dias_con_venta IS NULL OR m.dias_con_venta = 0
       ) AS sin_dias_venta
  FROM listing l
  LEFT JOIN v_margen_producto m
    ON m.product_id = l.product_id AND m.platform = l.platform
 WHERE l.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY l.platform
 ORDER BY l.platform;

WITH fba AS (
    SELECT DISTINCT ON (platform, seller_sku)
           platform, seller_sku
      FROM disponibilidad_observation
     WHERE fuente = 'fba' AND platform = 'amazon_mx'
     ORDER BY platform, seller_sku, observed_at DESC, id DESC
)
SELECT count(*) AS fba_mx_listings,
       count(*) FILTER (WHERE m.product_id IS NULL) AS fba_mx_sin_fila_margen,
       count(*) FILTER (WHERE m.margen_neto_pct IS NOT NULL) AS fba_mx_con_margen,
       count(*) FILTER (
           WHERE m.product_id IS NOT NULL AND m.margen_neto_pct IS NULL
       ) AS fba_mx_fila_sin_margen
  FROM listing l
  JOIN fba ON fba.platform = l.platform AND fba.seller_sku = l.seller_sku
  LEFT JOIN v_margen_producto m
    ON m.product_id = l.product_id AND m.platform = l.platform;

SELECT count(*) FILTER (WHERE fuente = 'fba') AS obs_fba,
       count(*) FILTER (WHERE fuente = 'fbm') AS obs_fbm,
       max(observed_at) AS observed_at_max
  FROM disponibilidad_observation
 WHERE platform = 'amazon_mx';

SELECT (to_regclass('public.estimacion_escenario') IS NOT NULL)::int AS hay_escenario;
\gset

\if :hay_escenario
-- B. Escenarios persistidos (requiere 0028)
WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.id,
           e.listing_id,
           e.platform,
           e.canal,
           e.estado,
           e.motivos,
           e.contribucion,
           e.contribucion_pct,
           e.moneda,
           e.observed_at,
           e.valoracion_date
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.estado::text,
       count(*) AS n
  FROM vigente v
 GROUP BY v.estado
 ORDER BY v.estado;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.listing_id, e.estado, e.motivos
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.estado::text,
       motivo,
       count(*) AS n
  FROM vigente v
  LEFT JOIN LATERAL jsonb_array_elements_text(v.motivos) AS motivo ON true
 GROUP BY v.estado, motivo
 ORDER BY v.estado, n DESC, motivo;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.platform, e.canal, e.estado, e.listing_id
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.platform::text,
       v.canal::text,
       v.estado::text,
       count(*) AS n
  FROM vigente v
 GROUP BY v.platform, v.canal, v.estado
 ORDER BY v.platform, v.canal, v.estado;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.listing_id,
           e.platform,
           e.canal,
           e.estado,
           e.motivos,
           e.contribucion,
           e.observed_at
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
),
fba AS (
    SELECT DISTINCT ON (platform, seller_sku)
           platform, seller_sku
      FROM disponibilidad_observation
     WHERE fuente = 'fba' AND platform = 'amazon_mx'
     ORDER BY platform, seller_sku, observed_at DESC, id DESC
)
SELECT v.estado::text,
       count(*) AS n,
       count(*) FILTER (WHERE v.contribucion IS NOT NULL) AS con_principal
  FROM vigente v
  JOIN listing l ON l.id = v.listing_id
  JOIN fba ON fba.platform = l.platform AND fba.seller_sku = l.seller_sku
 WHERE v.platform = 'amazon_mx' AND v.canal = 'fba'
 GROUP BY v.estado
 ORDER BY v.estado;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.listing_id, e.estado, e.motivos, e.canal, e.platform, e.observed_at
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.listing_id,
       v.platform::text,
       v.canal::text,
       v.estado::text,
       v.motivos,
       v.observed_at
  FROM vigente v
 WHERE v.estado = 'incompleta'
 ORDER BY v.listing_id
 LIMIT 5;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.listing_id, e.estado, e.motivos, e.canal, e.platform, e.observed_at
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.listing_id,
       v.platform::text,
       v.canal::text,
       v.estado::text,
       v.motivos,
       v.observed_at
  FROM vigente v
 WHERE v.estado = 'desactualizada'
 ORDER BY v.listing_id
 LIMIT 5;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.listing_id, e.estado, e.motivos, e.canal, e.platform, e.observed_at
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.listing_id,
       v.platform::text,
       v.canal::text,
       v.estado::text,
       v.motivos,
       v.observed_at
  FROM vigente v
 WHERE v.estado = 'identidad_ambigua'
 ORDER BY v.listing_id
 LIMIT 5;

WITH vigente AS (
    SELECT DISTINCT ON (e.listing_id)
           e.listing_id,
           e.platform,
           e.canal,
           e.estado,
           e.contribucion,
           e.contribucion_pct,
           e.moneda,
           e.observed_at
      FROM estimacion_escenario e
     ORDER BY e.listing_id, e.observed_at DESC, e.id DESC
)
SELECT v.listing_id,
       v.platform::text,
       v.canal::text,
       v.contribucion,
       v.contribucion_pct,
       v.moneda,
       m.dias_con_venta,
       m.margen_neto_pct
  FROM vigente v
  JOIN listing l ON l.id = v.listing_id
  LEFT JOIN v_margen_producto m
    ON m.product_id = l.product_id AND m.platform = l.platform
 WHERE v.estado = 'disponible'
   AND v.platform = 'amazon_mx'
   AND v.canal = 'fba'
   AND (m.product_id IS NULL OR m.dias_con_venta IS NULL OR m.dias_con_venta = 0)
 ORDER BY v.listing_id
 LIMIT 10;

\else
SELECT 'estimacion_escenario ausente: seccion B omitida' AS aviso;
\endif

ROLLBACK;
