-- @momento
SELECT now() AS ahora_utc, current_setting('server_version_num') AS version_pg;

-- @sonda_0099
SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.decision_sin_aplicar'::regclass
              AND conname = 'decision_sin_aplicar_motivo_check')
       THEN false
       ELSE EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.decision_sin_aplicar'::regclass
              AND conname = 'decision_sin_aplicar_motivo_check'
              AND pg_get_constraintdef(oid) LIKE '%tope_ciclo%')
       END AS sonda_0099,
       (SELECT convalidated FROM pg_constraint
         WHERE conrelid = 'public.decision_sin_aplicar'::regclass
           AND conname = 'decision_sin_aplicar_motivo_check') AS convalidada;

-- @motivos_check
SELECT pg_get_constraintdef(oid) AS definicion
  FROM pg_constraint
 WHERE conrelid = 'decision_sin_aplicar'::regclass
   AND conname = 'decision_sin_aplicar_motivo_check';

-- @conteo_tablas
-- pg_class cuenta el esquema real; information_schema.tables oculta a orbit_read
-- la tabla privada de 0087 (arreglos03_devoluciones_meli_ids), que no le da
-- privilegios. Ambas columnas quedan a la vista para documentar la diferencia.
SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')) AS tablas_pg_class,
       (SELECT count(*) FROM information_schema.tables
         WHERE table_schema = 'public' AND table_type = 'BASE TABLE') AS tablas_information_schema,
       (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'v') AS vistas;

-- @historia_totales
-- Referencia previa al despliegue (H/previa.txt, 04:34 UTC): 211 ciclos,
-- 503 intentos bid normales, 11 reversas sobre decisiones bid y 193
-- desenlaces sin_aplicar. Las columnas *_bid son las que se comparan con esa
-- foto; el resto es contexto (incluye intentos de cortes/cosecha).
SELECT (SELECT count(*) FROM optimizer_cycle) AS ciclos,
       (SELECT count(*) FROM optimizer_cycle WHERE mode = 'live' AND status = 'done') AS ciclos_live_done,
       (SELECT count(*) FROM optimizer_cycle WHERE mode = 'shadow' AND status = 'done') AS ciclos_shadow_done,
       (SELECT count(*) FROM optimizer_cycle WHERE mode = 'off' AND status = 'skipped') AS ciclos_off_skipped,
       (SELECT count(*) FROM decision) AS decisiones,
       (SELECT count(*) FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
         WHERE a.tipo = 'normal' AND d.kind = 'bid') AS intentos_bid_normal,
       (SELECT count(*) FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
         WHERE a.tipo = 'reversa' AND d.kind = 'bid') AS intentos_bid_reversa,
       (SELECT count(*) FROM apply_attempt WHERE tipo = 'normal') AS intentos_normal,
       (SELECT count(*) FROM apply_attempt WHERE tipo = 'reversa') AS intentos_reversa,
       (SELECT count(*) FROM decision_sin_aplicar) AS desenlaces_sin_aplicar,
       (SELECT count(*) FROM decision_application) AS aplicaciones;

-- @historia_motivos
SELECT motivo, count(*) AS filas
  FROM decision_sin_aplicar
 GROUP BY motivo
 ORDER BY 2 DESC, 1;

-- @historia_maximos
SELECT (SELECT max(id) FROM optimizer_cycle) AS max_ciclo,
       (SELECT max(started_at) FROM optimizer_cycle) AS ultimo_ciclo_iniciado,
       (SELECT max(id) FROM decision) AS max_decision,
       (SELECT max(decided_at) FROM decision) AS ultima_decision,
       (SELECT max(id) FROM apply_attempt) AS max_intento,
       (SELECT max(started_at) FROM apply_attempt) AS ultimo_intento,
       (SELECT max(registrado_at) FROM decision_sin_aplicar) AS ultimo_sin_aplicar,
       (SELECT max(confirmed_at) FROM decision_application) AS ultima_aplicacion;

-- @historia_despues_del_despliegue
-- El despliegue empezo a las 06:08 UTC del 2026-11-25 (predeploy-20261125-060839).
-- Cero filas NUEVAS en las tablas de historia desde ese momento.
SELECT (SELECT count(*) FROM optimizer_cycle
         WHERE started_at >= timestamptz '2026-11-25 06:08:00+00') AS ciclos_nuevos,
       (SELECT count(*) FROM decision
         WHERE decided_at >= timestamptz '2026-11-25 06:08:00+00') AS decisiones_nuevas,
       (SELECT count(*) FROM apply_attempt
         WHERE started_at >= timestamptz '2026-11-25 06:08:00+00') AS intentos_nuevos,
       (SELECT count(*) FROM decision_sin_aplicar
         WHERE registrado_at >= timestamptz '2026-11-25 06:08:00+00') AS sin_aplicar_nuevos,
       (SELECT count(*) FROM decision_application
         WHERE confirmed_at >= timestamptz '2026-11-25 06:08:00+00') AS aplicaciones_nuevas;

-- @tope_ciclo
SELECT count(*) AS desenlaces_tope_ciclo,
       count(DISTINCT cycle_id) AS ciclos_con_tope,
       max(registrado_at) AS ultimo
  FROM decision_sin_aplicar
 WHERE motivo = 'tope_ciclo';

-- @permisos_historia
SELECT has_table_privilege('app_decide', 'public.optimizer_cycle', 'DELETE') AS decide_delete_ciclo,
       has_table_privilege('app_decide', 'public.decision', 'DELETE') AS decide_delete_decision,
       has_table_privilege('app_decide', 'public.apply_attempt', 'DELETE') AS decide_delete_intento,
       has_table_privilege('app_decide', 'public.decision_sin_aplicar', 'DELETE') AS decide_delete_desenlace,
       has_table_privilege('app_decide', 'public.decision_sin_aplicar', 'UPDATE') AS decide_update_desenlace;
