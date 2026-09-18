-- A.7 consulta 5/6: cuenta del puente (misma que `fuentes._SQL_PUENTE`).
--
-- Columnas (`psql -tA`, separador `|`):
--   count
--
-- La cuenta del puente (`listing` por plataforma) se muestra al lado de la
-- canonica; diferencia mayor al 5 % se avisa.
\set platform amazon_mx
SELECT count(*) FROM listing WHERE platform = :'platform';
