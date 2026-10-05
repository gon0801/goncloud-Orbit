-- JEV ADS 02, tarea 0.2: comprobacion de la frase corregida. SOLO LECTURA
-- (orbit_read). La palabra no se escribe en el repo: se pasa al correr, con
-- `psql -v patron=<expresion regular>`.
-- "Sin revocar ni vencer" es el mismo universo que cuenta `cobertura.sql` en su
-- tercera seccion. "Elegida" es la ficha que usa el asesor
-- (app/jev_asesor._enriquecer): por cada listing, la mas reciente que lo cubre.
\echo == versiones sin revocar ni vencer: total y las que conservan la frase anterior
WITH v AS (
  SELECT f.id, f.plataforma, (f.hechos::text ~* :'patron') AS con_frase
    FROM jev_ficha_version f
   WHERE f.revisar_antes_de >= now()
     AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id))
SELECT plataforma, count(*) AS versiones, count(*) FILTER (WHERE con_frase) AS con_frase_anterior
  FROM v GROUP BY 1 ORDER BY 1;
\echo == fichas elegidas por el asesor: total y las que conservan la frase anterior
WITH v AS (
  SELECT f.id, f.producto_id, f.plataforma, f.listings, f.observado_at, f.created_at,
         (f.hechos::text ~* :'patron') AS con_frase
    FROM jev_ficha_version f
   WHERE f.revisar_antes_de >= now()
     AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)),
elegida AS (
  SELECT DISTINCT ON (l.id) l.id AS listing_id, v.id AS ficha, v.plataforma, v.con_frase
    FROM listing l
    JOIN v ON v.producto_id = l.product_id AND v.plataforma = l.platform
          AND v.listings @> ARRAY[l.id]::bigint[]
   ORDER BY l.id, v.observado_at DESC, v.created_at DESC)
SELECT plataforma, count(DISTINCT ficha) AS elegidas,
       count(DISTINCT ficha) FILTER (WHERE con_frase) AS con_frase_anterior
  FROM elegida GROUP BY 1 ORDER BY 1;
\echo == revocaciones registradas
SELECT count(*) AS revocaciones FROM jev_ficha_revocacion;
