-- A.7 consulta 3/6: goals vigentes (misma que `fuentes._SQL_GOALS`).
--
-- Columnas (`psql -tA`, separador `|`):
--   listing_id
\set platform amazon_mx
SELECT listing_id FROM precio_goal WHERE platform = :'platform' AND valid_to IS NULL;
