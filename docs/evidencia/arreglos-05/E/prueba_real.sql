-- Lectura RO de produccion despues del despliegue del bloque E.
-- Se ejecuta con docs/evidencia/arreglos-01/lee_prod.sh (orbit_read, read_only).
-- Solo esquema, conteos y banderas; no escribe ni inventa datos.

-- @momento
SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS ahora_utc,
       current_user AS rol,
       current_setting('transaction_read_only') AS solo_lectura,
       (SELECT count(*) FROM apply_queue) AS filas_apply_queue;

-- @indices
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = 'public' AND tablename = 'apply_queue'
 ORDER BY indexname;

-- @triggers
SELECT tgname
  FROM pg_trigger
 WHERE tgrelid = 'public.apply_queue'::regclass AND NOT tgisinternal
 ORDER BY tgname;

-- @funcion_0098
SELECT position('VETO vigente' in p.prosrc) > 0 AS veto_al_alta,
       position('pg_advisory_xact_lock' in p.prosrc) > 0 AS candado_asesor,
       position('vence_el' in p.prosrc) > 0 AS vigencia
  FROM pg_proc p
  JOIN pg_trigger t ON t.tgfoid = p.oid
 WHERE t.tgrelid = 'public.apply_queue'::regclass
   AND t.tgname = 'apply_queue_clave_vigente';

-- @sonda_0098
SELECT to_regclass('public.apply_queue') IS NOT NULL
   AND EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'apply_queue' AND indexname = 'apply_queue_live_efecto_unico')
   AND NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'apply_queue' AND indexname = 'apply_queue_clave_efecto_en_vuelo')
   AND EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = to_regclass('public.apply_queue') AND t.tgname = 'apply_queue_clave_vigente' AND NOT t.tgisinternal);

-- @colas_por_modo_estado
SELECT modo, estado, count(*) AS n
  FROM apply_queue
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- @practicas_shadow
SELECT count(*) AS total,
       count(*) FILTER (WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')) AS en_vuelo,
       count(*) FILTER (WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
                          AND vence_el <= now()) AS en_vuelo_vencidas,
       count(*) FILTER (WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
                          AND vence_el > now()) AS en_vuelo_vigentes
  FROM apply_queue
 WHERE modo = 'shadow';

-- @practicas_vencidas_historia
SELECT count(*) AS filas,
       to_char(min(vence_el) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS vence_primero,
       to_char(max(vence_el) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS vence_ultimo,
       to_char(min(encolado_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS encolado_primero,
       to_char(max(encolado_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS encolado_ultimo
  FROM apply_queue
 WHERE modo = 'shadow' AND estado = 'pending_veto';

-- @claves_no_terminales_duplicadas
SELECT count(*) AS claves
  FROM (
        SELECT platform, ad_entity_id, familia, search_term
          FROM apply_queue
         WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1
       ) x;
