-- @momento_utc
SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.MS'), current_user,
       current_setting('transaction_read_only')
-- @tabla_madre_existe
SELECT to_regclass('public.keyword_archivo_manual') IS NOT NULL
-- @tabla_madre_columnas
SELECT string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position)
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'keyword_archivo_manual'
-- @tabla_madre_conteos
SELECT count(*),
       count(*) FILTER (WHERE estado = 'planeado'),
       count(*) FILTER (WHERE estado = 'applied'),
       count(*) FILTER (WHERE estado = 'failed'),
       count(*) FILTER (WHERE estado = 'repuesto')
  FROM keyword_archivo_manual
-- @tabla_madre_ids
SELECT min(id), max(id), count(DISTINCT id), count(*) FILTER (WHERE id IS NULL)
  FROM keyword_archivo_manual
-- @reposiciones_ya_marcadas
SELECT count(*) FILTER (WHERE repuesto_at IS NOT NULL),
       count(*) FILTER (WHERE repuesto_external IS NOT NULL),
       count(*) FILTER (WHERE estado = 'repuesto' AND repuesto_external IS NULL),
       count(*) FILTER (WHERE repuesto_at IS NOT NULL AND repuesto_external IS NOT NULL)
  FROM keyword_archivo_manual
-- @lotes
SELECT count(DISTINCT lote), count(*) FILTER (WHERE btrim(lote) = '')
  FROM keyword_archivo_manual
-- @tabla_nueva_ausente
SELECT to_regclass('public.keyword_reposicion_intento') IS NULL
-- @indices_y_triggers
SELECT string_agg(clase || ':' || nombre, ', ' ORDER BY clase, nombre)
  FROM (
        SELECT 'indice' AS clase, indexname AS nombre
          FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'keyword_archivo_manual'
        UNION ALL
        SELECT 'trigger', tgname
          FROM pg_trigger
         WHERE tgrelid = 'public.keyword_archivo_manual'::regclass AND NOT tgisinternal
       ) x
-- @privilegios_tabla
SELECT r.rolname,
       has_table_privilege(r.rolname, 'public.keyword_archivo_manual', 'SELECT'),
       has_table_privilege(r.rolname, 'public.keyword_archivo_manual', 'INSERT'),
       has_table_privilege(r.rolname, 'public.keyword_archivo_manual', 'UPDATE'),
       has_table_privilege(r.rolname, 'public.keyword_archivo_manual', 'DELETE')
  FROM pg_roles r
 WHERE r.rolname IN ('app_read', 'app_ingest', 'app_decide', 'app_admin')
 ORDER BY r.rolname
-- @roles_servicio
SELECT r.rolname,
       pg_has_role(r.rolname, 'app_read', 'MEMBER'),
       pg_has_role(r.rolname, 'app_admin', 'MEMBER')
  FROM pg_roles r
 WHERE r.rolname LIKE 'orbit_%' AND r.rolcanlogin
 ORDER BY r.rolname
-- @secuencia
SELECT count(*), coalesce(max(last_value), 0), coalesce(max(start_value), 0)
  FROM pg_sequences
 WHERE schemaname = 'public' AND sequencename = 'keyword_archivo_manual_id_seq'
-- @sonda_0096
SELECT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'apply_attempt'
            AND column_name = 'ausencia_previa'
       )
-- @referencias_a_la_madre
SELECT count(*)
  FROM pg_constraint c
 WHERE c.contype = 'f' AND c.confrelid = 'public.keyword_archivo_manual'::regclass
