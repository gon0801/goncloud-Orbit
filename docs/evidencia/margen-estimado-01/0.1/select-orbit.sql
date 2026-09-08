\pset pager off
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT l.platform, count(*), count(*) FILTER (WHERE l.listing_price IS NOT NULL),
       count(DISTINCT l.seller_sku), count(DISTINCT l.product_id)
  FROM listing AS l
 WHERE l.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY l.platform;

SELECT l.platform, count(DISTINCT l.id),
       count(DISTINCT l.id) FILTER (WHERE c.id IS NOT NULL),
       count(DISTINCT c.cost_currency),
       bool_and(c.includes_tax IS FALSE) FILTER (WHERE c.id IS NOT NULL)
  FROM listing AS l
  LEFT JOIN LATERAL (
      SELECT id, cost_currency, includes_tax FROM sku_cost
       WHERE product_id = l.product_id
         AND valid_from <= (now() AT TIME ZONE 'UTC')::date
         AND (valid_to IS NULL OR valid_to > (now() AT TIME ZONE 'UTC')::date)
       ORDER BY valid_from DESC LIMIT 1
  ) AS c ON true
 WHERE l.platform IN ('amazon_mx', 'amazon_us')
 GROUP BY l.platform;

SELECT base_currency, quote_currency, min(rate_date), max(rate_date), count(*)
  FROM fx_rate GROUP BY base_currency, quote_currency;

SELECT source, max(finished_at), count(*) FILTER (WHERE ok),
       count(*) FILTER (WHERE NOT ok)
  FROM ingest_run
 WHERE source IN ('bridge_listings', 'accounting_sku_costs',
                  'accounting_currency_rates', 'bridge_disponibilidad')
 GROUP BY source;

SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position) AS columnas
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name IN ('product', 'listing', 'sku_cost')
 GROUP BY table_name
 ORDER BY table_name;

ROLLBACK;
