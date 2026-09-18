-- A.7 consulta 4/6: decisiones del dia (misma que `fuentes._SQL_DECISIONES`).
--
-- Columnas (`psql -tA`, separador `|`):
--   listing_id|resultado|motivo
--
-- `decision_date` es el dia UTC de la base (lo fija el trigger).
\set platform amazon_mx
\set hoy 2026-09-18
SELECT listing_id, resultado, motivo FROM precio_decision
WHERE platform = :'platform' AND decision_date = :'hoy';
