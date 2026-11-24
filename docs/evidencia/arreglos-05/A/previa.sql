-- ARREGLOS 05 A: lectura previa de produccion del bloque A, SOLO SELECT.
-- Se corre con bash docs/evidencia/arreglos-05/A/previa.sh (rol de solo
-- lectura, transaccion read_only, por docs/evidencia/arreglos-01/lee_prod.sh).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. Solo conteos,
-- fechas, modos y nombres de objetos: ningun bloque vuelca settings, cuentas
-- ni datos de entidades.
--
-- Fija, antes de la migracion 0094, los invariantes que dependen de datos:
-- 1. que todo envelope cerrado por TX3 trae la evidencia para clasificarlo
--    sin sello (bloques envelopes_* y decisiones_*);
-- 2. cuantas decisiones viven hoy sin desenlace y con o sin intento, que es
--    la poblacion que la vigencia ordinaria y el ejecutor de recuperacion
--    podrian tocar (bloques huerfanas_*);
-- 3. que privilegios y disparadores existen hoy sobre optimizer_cycle y
--    decision_sin_aplicar (bloques privilegios_*, disparadores_*,
--    check_sin_aplicar y motivos_sin_aplicar).

-- @momento_utc
SELECT (now() AT TIME ZONE 'UTC')::text AS momento_utc, current_user AS rol,
       current_setting('transaction_read_only') AS solo_lectura,
       current_setting('server_version_num') AS version_pg;

-- @columnas_optimizer_cycle
SELECT string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position)
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'optimizer_cycle';

-- @indices_optimizer_cycle
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'optimizer_cycle'
ORDER BY indexname;

-- @disparadores_optimizer_cycle
SELECT tgname, pg_get_triggerdef(oid)
FROM pg_trigger
WHERE tgrelid = 'public.optimizer_cycle'::regclass AND NOT tgisinternal
ORDER BY tgname;

-- @roles_servicio
SELECT rolname, rolcanlogin
FROM pg_roles
WHERE rolname LIKE 'orbit_%' OR rolname LIKE 'app_%'
ORDER BY rolname;

-- @membresia_roles
SELECT r.rolname AS rol_login, m.rolname AS miembro_de
FROM pg_roles r
JOIN pg_auth_members am ON am.member = r.oid
JOIN pg_roles m ON m.oid = am.roleid
WHERE r.rolname LIKE 'orbit_%' OR r.rolname LIKE 'app_%'
ORDER BY 1, 2;

-- @privilegios_tabla_optimizer_cycle
SELECT r.rol, p.priv, has_table_privilege(r.rol, 'public.optimizer_cycle', p.priv)
FROM (VALUES ('app_decide'), ('app_read'), ('app_admin'), ('app_ingest'),
             ('orbit_decide'), ('orbit_read'), ('orbit_admin'), ('orbit_ingest')) r(rol)
CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) p(priv)
WHERE has_table_privilege(r.rol, 'public.optimizer_cycle', p.priv)
ORDER BY 1, 2;

-- @privilegios_columna_update_app_decide
SELECT string_agg(a.attname, ',' ORDER BY a.attnum)
FROM pg_attribute a
WHERE a.attrelid = 'public.optimizer_cycle'::regclass AND a.attnum > 0
  AND NOT a.attisdropped
  AND has_column_privilege('app_decide', a.attrelid, a.attnum, 'UPDATE');

-- @envelopes_por_estado_y_cierre
SELECT c.mode, c.status, (c.finished_at IS NOT NULL) AS cerrado, count(*) AS n,
       count(*) FILTER (
           WHERE c.decisions_count IS DISTINCT FROM
                 (SELECT count(*) FROM decision d WHERE d.cycle_id = c.id)
       ) AS conteo_distinto
FROM optimizer_cycle c
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- @envelopes_clasificacion_tx3
SELECT CASE
         WHEN c.status IN ('done', 'degraded') AND c.finished_at IS NOT NULL
              AND c.decisions_count IS NOT DISTINCT FROM
                  (SELECT count(*) FROM decision d WHERE d.cycle_id = c.id)
           THEN 'tx3_con_evidencia'
         WHEN c.status IN ('done', 'degraded') THEN 'cerrado_sin_evidencia_completa'
         WHEN c.status = 'skipped' THEN 'skipped'
         WHEN c.status = 'failed' THEN 'failed'
         WHEN c.status = 'running' THEN 'running'
       END AS clase,
       count(*) AS n,
       min((c.finished_at AT TIME ZONE 'UTC')::date) AS primero,
       max((c.finished_at AT TIME ZONE 'UTC')::date) AS ultimo
FROM optimizer_cycle c
GROUP BY 1
ORDER BY 1;

-- @decisiones_mismo_dia_que_el_cierre
SELECT count(*) AS decisiones,
       count(*) FILTER (
           WHERE (d.decided_at AT TIME ZONE 'UTC')::date
                 <> (c.finished_at AT TIME ZONE 'UTC')::date
       ) AS otro_dia_que_el_cierre,
       count(*) FILTER (
           WHERE abs(extract(epoch FROM (c.finished_at - d.decided_at))) > 600
       ) AS cierre_a_mas_de_10_min,
       count(*) FILTER (WHERE c.finished_at IS NULL) AS ciclo_abierto
FROM decision d
JOIN optimizer_cycle c ON c.id = d.cycle_id;

-- @origenes_que_deciden_por_dia
SELECT x.platform::text, count(*) AS dias_con_decisiones,
       max(x.n) AS max_ciclos_decidiendo_en_un_dia,
       count(*) FILTER (WHERE x.n > 1) AS dias_con_mas_de_un_ciclo,
       max(x.decisiones) AS max_decisiones_de_un_dia
FROM (SELECT c.platform, (c.finished_at AT TIME ZONE 'UTC')::date AS dia,
             count(DISTINCT c.id) AS n, count(*) AS decisiones
      FROM optimizer_cycle c
      JOIN decision d ON d.cycle_id = c.id
      WHERE c.finished_at IS NOT NULL
      GROUP BY 1, 2) x
GROUP BY 1
ORDER BY 1;

-- @huerfanas_por_origen
SELECT h.origen, h.kind, h.platform::text, count(*) AS n,
       min(h.ciclo_cerrado_at)::date AS ciclo_primero,
       max(h.ciclo_cerrado_at)::date AS ciclo_ultimo,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = h.decision_id AND a.tipo = 'normal')
       ) AS con_intento_normal,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = h.decision_id)
       ) AS con_intento
FROM v_decision_huerfana h
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- @huerfanas_posteriores_a_0044
SELECT h.cycle_id, h.origen,
       string_agg(DISTINCT h.kind::text, ',' ORDER BY h.kind::text) AS kinds,
       count(*) AS n, min(h.ciclo_cerrado_at)::date AS cerrado,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt a
                          WHERE a.decision_id = h.decision_id AND a.tipo = 'normal')
       ) AS con_intento_normal
FROM v_decision_huerfana h
WHERE h.origen <> 'sin_registro'
GROUP BY 1, 2
ORDER BY 1;

-- @huerfanas_sin_intento_sin_cola
-- La poblacion que la vigencia ordinaria registraria como vencida si corriera hoy
-- sobre toda la historia: sin aplicacion, sin no-apply, sin fila en la cola y sin
-- intento normal.
SELECT count(*) AS n,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_queue q WHERE q.decision_id = h.decision_id)
       ) AS con_fila_en_cola,
       min(h.ciclo_cerrado_at)::date AS ciclo_primero,
       max(h.ciclo_cerrado_at)::date AS ciclo_ultimo
FROM v_decision_huerfana h
WHERE (h.ciclo_cerrado_at AT TIME ZONE 'UTC')::date < (now() AT TIME ZONE 'UTC')::date
  AND NOT EXISTS (SELECT 1 FROM apply_attempt a
                   WHERE a.decision_id = h.decision_id AND a.tipo = 'normal');

-- @motivos_sin_aplicar
SELECT motivo, count(*) AS n,
       min((registrado_at AT TIME ZONE 'UTC')::date) AS primero,
       max((registrado_at AT TIME ZONE 'UTC')::date) AS ultimo
FROM decision_sin_aplicar
GROUP BY 1
ORDER BY 1;

-- @check_sin_aplicar
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'public.decision_sin_aplicar'::regclass AND contype = 'c'
ORDER BY conname;

-- @disparadores_sin_aplicar
SELECT tgname
FROM pg_trigger
WHERE tgrelid = 'public.decision_sin_aplicar'::regclass AND NOT tgisinternal
ORDER BY tgname;

-- @funciones_solo_cierre
SELECT proname
FROM pg_proc
WHERE proname LIKE '%solo_cierre%' OR proname LIKE '%solo_cierra%'
ORDER BY proname;

-- @applied_count_cuadra_por_ejecutor
SELECT count(*) AS ciclos_con_aplicadas,
       count(*) FILTER (
           WHERE c.applied_count IS DISTINCT FROM
                 (SELECT count(*) FROM decision_application da
                   WHERE da.applied_cycle_id = c.id AND da.verify_ok IS TRUE)
       ) AS no_cuadra
FROM optimizer_cycle c
WHERE coalesce(c.applied_count, 0) > 0;

-- @ciclos_aplicaron_sin_decidir
SELECT count(*) AS n
FROM optimizer_cycle
WHERE coalesce(applied_count, 0) > 0 AND coalesce(decisions_count, 0) = 0;

-- @ciclos_de_hoy
SELECT id, platform::text, mode, status,
       to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS iniciado_utc,
       to_char(finished_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS cerrado_utc,
       decisions_count, applied_count
FROM optimizer_cycle
WHERE (started_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date
ORDER BY id;
