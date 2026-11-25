-- Foto de solo lectura para el bloque E (indice mixto de apply_queue).
-- Se ejecuta con docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read,
-- transaccion read_only). No escribe ni inventa backfill.

-- @momento
SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS ahora_utc,
       current_user AS rol,
       current_setting('transaction_read_only') AS solo_lectura,
       (SELECT count(*) FROM apply_queue) AS filas_apply_queue;

-- @indice_actual
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = 'public' AND tablename = 'apply_queue'
 ORDER BY indexname;

-- @colas_por_modo_estado
SELECT modo, estado, count(*) AS n
  FROM apply_queue
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- @colas_en_vuelo
SELECT modo, estado, count(*) AS n,
       count(*) FILTER (WHERE vence_el <= now()) AS vencidas,
       count(*) FILTER (WHERE vence_el > now()) AS vigentes
  FROM apply_queue
 WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- @claves_live_no_terminales_duplicadas
-- El indice live de 0098 no se puede crear si hay mas de una fila live no terminal por clave.
SELECT count(*) AS claves
  FROM (
        SELECT platform, ad_entity_id, familia, search_term
          FROM apply_queue
         WHERE modo = 'live'
           AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1
       ) x;

-- @claves_no_terminales_duplicadas
-- La reversa que restaura el indice mixto rehusa si hay mas de una fila no terminal por clave.
SELECT count(*) AS claves
  FROM (
        SELECT platform, ad_entity_id, familia, search_term
          FROM apply_queue
         WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1
       ) x;

-- @claves_practica_vencida_con_efecto_live
-- Practicas vencidas que hoy congelan una clave con efecto live no terminal.
SELECT count(*) AS claves
  FROM (
        SELECT platform, ad_entity_id, familia, search_term
          FROM apply_queue
         GROUP BY 1, 2, 3, 4
        HAVING bool_or(modo = 'shadow'
                       AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
                       AND vence_el <= now())
           AND bool_or(modo = 'live'
                       AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded'))
       ) x;

-- @practicas_shadow
SELECT count(*) AS total,
       count(*) FILTER (WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')) AS en_vuelo,
       count(*) FILTER (WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
                          AND vence_el <= now()) AS en_vuelo_vencidas,
       count(*) FILTER (WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
                          AND vence_el > now()) AS en_vuelo_vigentes
  FROM apply_queue
 WHERE modo = 'shadow';
