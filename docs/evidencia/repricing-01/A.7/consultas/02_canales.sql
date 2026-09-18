-- A.7 consulta 2/6: canal y precio por listing (misma que `fuentes._SQL_CANAL`).
--
-- Columnas (`psql -tA`, separador `|`):
--   listing_id|canal|price_amount|price_currency
--
-- El canal ya viene mapeado (`mapear_canal` en la ingesta): fba|fbm.
\set platform amazon_mx
SELECT DISTINCT ON (listing_id) listing_id, canal, price_amount, price_currency
FROM estimacion_oferta_observation WHERE platform = :'platform'
ORDER BY listing_id, observed_at DESC;
