-- A.7 consulta 3/6: goals vigentes (misma que `fuentes._SQL_GOALS`).
--
-- Columnas (`psql -tA`, separador `|`):
--   listing_id
\set platform amazon_mx
\set hoy 2026-09-18
SELECT listing_id FROM precio_goal WHERE platform = :'platform'
AND valid_from <= :'hoy' AND (valid_to IS NULL OR valid_to > :'hoy');
