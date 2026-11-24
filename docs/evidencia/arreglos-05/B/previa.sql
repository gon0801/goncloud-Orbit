-- ARREGLOS 05 B: lectura previa de produccion del bloque B, SOLO SELECT.
-- Se corre con bash docs/evidencia/arreglos-05/B/previa.sh (rol de solo
-- lectura, transaccion read_only, por docs/evidencia/arreglos-01/lee_prod.sh).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. Solo conteos,
-- fechas y nombres de objetos: ningun bloque vuelca settings, cuentas, ACKs
-- ni datos de entidades.
--
-- Mide, antes de la migracion 0095, lo que el bloque supone:
-- 1. que no hay intento abierto: la reversa de la migracion aborta si hay
--    alguno con evidencia previa, y la evidencia nace sola con intentos nuevos
--    (bloques intentos_*, lock);
-- 2. cuantos negatives fueron confirmados por conciliacion y cuantas
--    aplicaciones de negative no respaldan su id con un ACK del ledger: es la
--    poblacion a la que alcanzan `efecto_reconciliado` y el respaldo del
--    resolver del negative_id (bloques negatives_*, aplicaciones_*);
-- 3. cuantas decisiones de puja aplicadas no traen old_value: esas no tienen
--    valor al que volver en la historia anterior a 0095, y cuantos regresos
--    del dueno se han hecho, que es el flujo que revierte la primera decision
--    de una racha (bloques pujas_* y cambios_bid);
-- 4. que privilegios, disparadores y restricciones existen hoy sobre
--    apply_attempt, y que la 0094 esta aplicada y la 0095 todavia no
--    (bloques privilegios_*, disparadores_*, restricciones_* y sonda_*).

-- @momento_utc
SELECT (now() AT TIME ZONE 'UTC')::text AS momento_utc, current_user AS rol,
       current_setting('transaction_read_only') AS solo_lectura,
       current_setting('server_version_num') AS version_pg;

-- @columnas_apply_attempt
SELECT string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position) AS columnas
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'apply_attempt';

-- @intentos_por_estado
SELECT count(*) AS intentos,
       count(*) FILTER (WHERE finished_at IS NULL) AS abiertos,
       count(*) FILTER (WHERE ack IS NULL) AS sin_ack,
       count(*) FILTER (WHERE finished_at IS NOT NULL AND ack IS NULL) AS cerrados_sin_ack
FROM apply_attempt;

-- @intentos_por_tipo_y_resultado
SELECT tipo,
       -- La etiqueta sin el cuerpo del error de Amazon (va despues de ": ").
       split_part(resultado, ': ', 1) AS resultado,
       count(*) AS n,
       count(*) FILTER (WHERE finished_at IS NULL) AS abiertos
FROM apply_attempt
GROUP BY 1, 2
ORDER BY 1, 2;

-- @negatives_reconciliados
SELECT count(*) AS reconciliados
FROM apply_attempt a
JOIN decision d ON d.id = a.decision_id AND d.kind = 'negative'
WHERE a.resultado = 'ok:reconciliado';

-- @negatives_reconciliados_por_cualquier_camino
SELECT count(*) AS aplicaciones_negative,
       count(*) FILTER (WHERE da.platform_ack ->> 'fuente' = 'list') AS confirmadas_por_lista,
       count(*) FILTER (WHERE da.platform_ack ? 'fuente') AS con_clave_fuente
FROM decision_application da
JOIN decision d ON d.id = da.decision_id AND d.kind = 'negative'
WHERE da.verify_ok IS TRUE;

-- @aplicaciones
SELECT count(*) AS aplicaciones,
       count(*) FILTER (WHERE platform_ack IS NOT NULL) AS con_ack,
       count(*) FILTER (WHERE verify_ok IS TRUE) AS verificadas,
       count(*) FILTER (WHERE applied_cycle_id IS NOT NULL) AS con_ciclo
FROM decision_application;

-- @aplicaciones_negative_sin_ack_en_el_ledger
SELECT count(*) AS aplicadas_sin_ack_en_ledger
FROM decision_application da
JOIN decision d ON d.id = da.decision_id AND d.kind = 'negative'
WHERE da.verify_ok IS TRUE
  AND NOT EXISTS (
      SELECT 1 FROM apply_attempt a
      WHERE a.decision_id = da.decision_id AND a.tipo = 'normal'
        AND a.resultado = 'ok' AND a.ack IS NOT NULL
  );

-- @pujas_aplicadas_sin_old_value
SELECT count(*) AS decisiones_bid_aplicadas,
       count(*) FILTER (WHERE d.old_value IS NULL) AS sin_old_value,
       count(*) FILTER (WHERE d.old_value IS NOT NULL AND d.value_currency IS NULL) AS sin_moneda
FROM decision_application da
JOIN decision d ON d.id = da.decision_id AND d.kind = 'bid'
WHERE da.verify_ok IS TRUE;

-- @reversas_de_puja
SELECT count(*) AS reversas_bid,
       count(*) FILTER (WHERE a.resultado = 'ok') AS ok,
       count(*) FILTER (WHERE a.finished_at IS NULL) AS abiertas
FROM apply_attempt a
JOIN decision d ON d.id = a.decision_id AND d.kind = 'bid'
WHERE a.tipo = 'reversa';

-- @cambios_bid_por_origen
SELECT origen, count(*) AS n
FROM v_cambio_bid
GROUP BY 1
ORDER BY 1;

-- @harvest_jobs_por_fase
SELECT fase, count(*) AS n
FROM harvest_job
GROUP BY 1
ORDER BY 1;

-- @harvest_jobs_autoria
SELECT coalesce(external_ids ->> 'keyword_creada', '<sin clave>') AS keyword_creada,
       coalesce(external_ids ->> 'negative_creada', '<sin clave>') AS negative_creada,
       count(*) AS n
FROM harvest_job
GROUP BY 1, 2
ORDER BY 1, 2;

-- @lock
SELECT count(*) AS filas FROM ads_optimizer_lock;

-- @disparadores_apply_attempt
SELECT tgname, pg_get_triggerdef(oid) AS definicion
FROM pg_trigger
WHERE tgrelid = 'public.apply_attempt'::regclass AND NOT tgisinternal
ORDER BY tgname;

-- @funcion_ledger_menciona_evidencia
SELECT proname, position('ausencia_previa' in prosrc) > 0 AS ya_trae_0095
FROM pg_proc
WHERE proname = 'apply_attempt_solo_sella_resultado';

-- @restricciones_apply_attempt
SELECT conname, pg_get_constraintdef(oid) AS definicion
FROM pg_constraint
WHERE conrelid = 'public.apply_attempt'::regclass
ORDER BY 1;

-- @privilegios_tabla_apply_attempt
SELECT r.rol,
       has_table_privilege(r.rol, 'public.apply_attempt', 'SELECT') AS puede_select,
       has_table_privilege(r.rol, 'public.apply_attempt', 'INSERT') AS puede_insert,
       has_table_privilege(r.rol, 'public.apply_attempt', 'UPDATE') AS update_de_toda_la_tabla,
       has_table_privilege(r.rol, 'public.apply_attempt', 'DELETE') AS puede_delete
FROM (VALUES ('app_read'), ('app_ingest'), ('app_decide'), ('app_admin')) AS r(rol)
ORDER BY 1;

-- @privilegios_columna_app_decide
SELECT string_agg(
           column_name
           || CASE WHEN has_column_privilege('app_decide', 'public.apply_attempt', column_name, 'UPDATE')
                   THEN ':UPDATE' ELSE '' END
           || CASE WHEN has_column_privilege('app_decide', 'public.apply_attempt', column_name, 'INSERT')
                   THEN ':INSERT' ELSE '' END,
           ', ' ORDER BY ordinal_position
       ) AS columnas
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'apply_attempt';

-- @roles_servicio
SELECT r.rolname AS rol_login, r.rolcanlogin AS puede_entrar, m.rolname AS miembro_de
FROM pg_auth_members am
JOIN pg_roles r ON r.oid = am.member
JOIN pg_roles m ON m.oid = am.roleid
WHERE r.rolname LIKE 'orbit_%'
ORDER BY 1, 3;

-- @sonda_0094
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'optimizer_cycle' AND column_name IN ('decision_committed_at', 'resume_of') HAVING count(*) = 2) THEN false ELSE has_column_privilege('app_decide', 'public.optimizer_cycle', 'decision_committed_at', 'UPDATE') AND NOT has_column_privilege('app_decide', 'public.optimizer_cycle', 'resume_of', 'UPDATE') AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'public.optimizer_cycle'::regclass AND tgname = 'optimizer_cycle_0094_progreso') AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('public.decision_sin_aplicar') AND conname = 'decision_sin_aplicar_motivo_check' AND pg_get_constraintdef(oid) LIKE '%vigencia_vencida%') END AS aplicada;

-- @sonda_0095
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'apply_attempt' AND column_name IN ('bid_previo', 'ausencia_previa') HAVING count(*) = 2) THEN false ELSE NOT has_column_privilege('app_decide', 'public.apply_attempt', 'bid_previo', 'UPDATE') AND NOT has_column_privilege('app_decide', 'public.apply_attempt', 'ausencia_previa', 'UPDATE') AND has_column_privilege('app_decide', 'public.apply_attempt', 'ack', 'UPDATE') AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.apply_attempt'::regclass AND conname = 'attempt_evidencia_previa_una') AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_trigger t ON t.tgfoid = p.oid WHERE t.tgrelid = 'public.apply_attempt'::regclass AND t.tgname = 'apply_attempt_solo_sella_resultado' AND position('ausencia_previa' in p.prosrc) > 0) END AS aplicada;
