-- A.7 consulta 5/6: filas de `listing` por plataforma (misma que
-- `fuentes` cuenta como identidad).
--
-- Columnas (`psql -tA`, separador `|`):
--   count
--
-- IDENTIDAD, NO ACTIVAS: `listing` no guarda ciclo de vida (el estado del
-- puente vive en su SQLite, no en Orbit). Esta cuenta se muestra al lado
-- de la canonica como contexto, sin aviso. El aviso del 5 % solo se
-- calcula con `--puente-activas N` (activas reales del puente).
\set platform amazon_mx
SELECT count(*) FROM listing WHERE platform = :'platform';
