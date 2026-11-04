-- A.7 consulta 6/6: dia UTC de la base (misma que `fuentes._SQL_HOY`).
--
-- Columnas (`psql -tA`, separador `|`):
--   date
SELECT (now() AT TIME ZONE 'UTC')::date;
