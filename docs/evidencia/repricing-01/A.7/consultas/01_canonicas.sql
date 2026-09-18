-- A.7 consulta 1/6: activas canonicas (misma que `fuentes._SQL_CANO`).
--
-- Columnas (`psql -tA`, separador `|`):
--   listing_id|seller_sku|platform|asin|status|observed_at
--
-- «Activa» = la ultima observacion por (seller_sku, platform) cuyo status
-- contiene BUYABLE, cruzada con `listing` (dos listings con el mismo SKU
-- cuentan uno: DISTINCT ON + desempate por listing_id minimo).
\set platform amazon_mx
SELECT listing_id, seller_sku, platform, asin, status, observed_at FROM (
    SELECT DISTINCT ON (e.seller_sku, e.platform)
        l.id AS listing_id, e.seller_sku, e.platform, e.asin, e.status, e.observed_at
    FROM spapi_listing_estado_observation e
    LEFT JOIN listing l ON l.seller_sku = e.seller_sku AND l.platform = e.platform
    WHERE e.platform = :'platform'
    ORDER BY e.seller_sku, e.platform, e.observed_at DESC, l.id NULLS LAST
) u WHERE u.status LIKE '%BUYABLE%';
