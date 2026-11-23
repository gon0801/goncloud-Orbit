-- ARREGLOS 05 BASE.2: lectura previa de produccion, SOLO SELECT.
-- Se corre con bash docs/evidencia/arreglos-05/BASE/previa.sh (rol orbit_read,
-- transaccion read_only, por docs/evidencia/arreglos-01/lee_prod.sh).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. Solo conteos,
-- modos y fechas: ningun bloque vuelca settings ni datos de cuentas.

-- @momento_utc
SELECT (now() AT TIME ZONE 'UTC')::text AS momento_utc, current_user AS rol,
       current_setting('transaction_read_only') AS solo_lectura,
       current_setting('server_version_num') AS version_pg;

-- @esquema_columnas
SELECT table_name, string_agg(column_name, ',' ORDER BY ordinal_position)
FROM information_schema.columns
WHERE table_schema='public' AND table_name IN
 ('optimizer_cycle','apply_attempt','apply_queue','keyword_archivo_manual',
  'decision_application','decision_sin_aplicar','harvest_job','ads_optimizer_goal',
  'sku_cost','estimacion_venta','decision','temporada_episodio','temporada_efecto',
  'temporada_exclusion','temporada_fin_reduccion')
GROUP BY table_name ORDER BY table_name;

-- Sondas de las migraciones de la base integrada (ARREGLOS 01, 02 y 03, y
-- TEMPORADA 01): confirman que estan aplicadas en produccion antes de fijar el
-- numero libre. ORDEN pedia acreditar 0086 por sonda.

-- @sonda 0083_arreglos01_costos_sin_compuerta.sql
-- @sonda 0084_arreglos01_impulso_desarmado.sql
-- @sonda 0085_arreglos01_ledger_reatribucion.sql
-- @sonda 0086_arreglos02_cargos.sql
-- @sonda 0087_arreglos03_devoluciones_meli.sql
-- @sonda 0088_arreglos03_ledger_original.sql
-- @sonda 0089_arreglos03_reputacion_avisada.sql
-- @sonda 0090_arreglos03_error_cierra_por_observacion.sql
-- @sonda 0091_arreglos03_sin_precio_cap.sql
-- @sonda 0092_temporada01_margen_vigente.sql
-- @sonda 0093_estimacion_s4_caja_real_mx.sql
-- @sonda 0093_temporada01_episodio.sql

-- Bloque A: poblacion historica de envelopes antes de la migracion de origen
-- y progreso. Un ciclo 'done' con decisiones pero sin sello es lo que A no
-- puede clasificar por status a ciegas. Todo se atribuye al ciclo que tomo la
-- decision (decision.cycle_id). decision_sin_aplicar guarda el ciclo EJECUTOR y
-- decision_application.applied_cycle_id tambien: desenlaces_con_ciclo_distinto
-- mide cuantos difieren del ciclo de la decision.

-- @ciclos_por_modo_estado
SELECT mode, status, count(*) AS n FROM optimizer_cycle GROUP BY 1,2 ORDER BY 1,2;

-- @ciclos_decisiones_y_aplicacion
SELECT c.status, c.mode, (c.finished_at IS NOT NULL) AS cerrado,
       (c.decisions_count > 0) AS declara_decisiones,
       EXISTS (SELECT 1 FROM decision d WHERE d.cycle_id = c.id) AS tiene_decisiones,
       EXISTS (SELECT 1 FROM decision_application da JOIN decision d ON d.id = da.decision_id
               WHERE d.cycle_id = c.id) AS tiene_aplicacion,
       EXISTS (SELECT 1 FROM decision_sin_aplicar s JOIN decision d ON d.id = s.decision_id
               WHERE d.cycle_id = c.id) AS tiene_sin_aplicar,
       count(*) AS n
FROM optimizer_cycle c GROUP BY 1,2,3,4,5,6,7 ORDER BY 1,2,3,4,5,6,7;

-- @desenlaces_con_ciclo_distinto
SELECT (SELECT count(*) FROM decision_sin_aplicar) AS sin_aplicar,
       (SELECT count(*) FROM decision_sin_aplicar s JOIN decision d ON d.id = s.decision_id
         WHERE s.cycle_id <> d.cycle_id) AS sin_aplicar_en_otro_ciclo,
       (SELECT count(*) FROM decision_application WHERE applied_cycle_id IS NOT NULL) AS aplicaciones,
       (SELECT count(*) FROM decision_application da JOIN decision d ON d.id = da.decision_id
         WHERE da.applied_cycle_id IS NOT NULL AND da.applied_cycle_id <> d.cycle_id)
         AS aplicaciones_en_otro_ciclo;

-- @ciclos_sin_cerrar
SELECT platform, status, count(*) AS n, min(started_at) AS primero, max(started_at) AS ultimo
FROM optimizer_cycle WHERE finished_at IS NULL GROUP BY 1,2 ORDER BY 1,2;

-- @lock_optimizador
SELECT job_key, (heartbeat_at + make_interval(secs => ttl_seconds) <= now()) AS vencido,
       (now() - claimed_at)::text AS edad
FROM ads_optimizer_lock ORDER BY 1;

-- Bloques A y B: intentos del ledger por estado de sello.

-- @intentos_por_estado
SELECT d.kind, a.tipo, a.resultado, (a.finished_at IS NOT NULL) AS sellado,
       (a.ack IS NOT NULL) AS con_ack, count(*) AS n,
       min(a.started_at) AS primero, max(a.started_at) AS ultimo
FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5;

-- @aplicacion_por_ack_y_verificacion
SELECT d.kind, (da.platform_ack IS NOT NULL) AS con_ack, da.verify_ok, count(*) AS n
FROM decision_application da JOIN decision d ON d.id = da.decision_id
GROUP BY 1,2,3 ORDER BY 1,2,3;

-- @negatives_reconciliados
SELECT count(*) AS n, count(*) FILTER (WHERE a.ack IS NULL) AS sin_ack,
       count(*) FILTER (WHERE da.platform_ack IS NULL) AS sin_resumen_ack
FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
LEFT JOIN decision_application da ON da.decision_id = d.id
WHERE d.kind = 'negative' AND a.resultado = 'ok:reconciliado';

-- @harvest_por_fase
SELECT platform, fase, count(*) AS n FROM harvest_job GROUP BY 1,2 ORDER BY 1,2;

-- Bloque C: la tabla madre de la reposicion y la ausencia de la nueva.

-- @reposicion_estados
SELECT estado, count(*) AS n, count(repuesto_external) AS con_id
FROM keyword_archivo_manual GROUP BY 1 ORDER BY 1;

-- @reposicion_ids_duplicados
SELECT count(*) AS n FROM
 (SELECT platform, repuesto_external FROM keyword_archivo_manual
  WHERE repuesto_external IS NOT NULL GROUP BY 1,2 HAVING count(*) > 1) x;

-- @reposicion_intentos_existe
SELECT to_regclass('public.keyword_reposicion_intento') IS NOT NULL AS existe,
       (SELECT count(*) FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name LIKE 'keyword_reposicion%') AS tablas;

-- Bloque E: filas en vuelo de apply_queue, por modo, y las shadow ya vencidas
-- que hoy bloquean la clave (platform, ad_entity_id, familia, search_term).

-- @colas_por_modo_estado
SELECT modo, estado, count(*) AS n FROM apply_queue GROUP BY 1,2 ORDER BY 1,2;

-- @colas_en_vuelo_y_vencidas
SELECT modo, estado, count(*) AS n, count(*) FILTER (WHERE vence_el < now()) AS vencidas,
       min(vence_el) AS vence_primero, max(vence_el) AS vence_ultimo
FROM apply_queue WHERE estado NOT IN ('applied','failed','vetoed','discarded')
GROUP BY 1,2 ORDER BY 1,2;

-- @colas_clave_con_historia_y_practica
SELECT count(*) AS claves
FROM (SELECT platform, ad_entity_id, familia, search_term
      FROM apply_queue GROUP BY 1,2,3,4
      HAVING bool_or(modo = 'live' AND estado = 'applied')
         AND bool_or(modo = 'shadow' AND estado NOT IN ('applied','failed','vetoed','discarded'))) x;

-- Bloques F y G: que version y que marcas traen los inputs congelados.

-- @inputs_claves_por_tipo
SELECT d.kind, k, count(*) AS n
FROM decision d, LATERAL jsonb_object_keys(d.inputs) k
GROUP BY 1,2 ORDER BY 1, 3 DESC, 2;

-- @inputs_version_del_caso
SELECT d.kind, d.inputs->'caso'->>'version' AS version_caso, count(*) AS n
FROM decision d WHERE d.inputs ? 'caso' GROUP BY 1,2 ORDER BY 1,2;

-- @inputs_target_por_procedencia
SELECT d.inputs->>'target_procedencia' AS procedencia,
       (d.inputs ? 'target_snapshot') AS con_snapshot, count(*) AS n
FROM decision d GROUP BY 1,2 ORDER BY 1,2;

-- Bloque H: cuanto usa hoy el cupo por ciclo y como se marca la temporada.

-- @intentos_bid_por_ciclo
SELECT c.platform, count(*) AS ciclos, max(x.n) AS max_intentos,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY x.n)) AS p99,
       round(avg(x.n)) AS promedio
FROM (SELECT d.cycle_id, count(*) AS n FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
      WHERE d.kind = 'bid' AND a.tipo = 'normal' GROUP BY 1) x
JOIN optimizer_cycle c ON c.id = x.cycle_id GROUP BY 1 ORDER BY 1;

-- @decisiones_de_temporada
SELECT d.inputs->>'motivo' AS motivo, (d.inputs ? 'temporada_episodio_id') AS estampada,
       count(*) AS n
FROM decision d
WHERE d.inputs->>'motivo' IN ('entra_temporada_baja','ajusta_temporada_baja','sale_temporada_baja')
GROUP BY 1,2 ORDER BY 1,2;

-- @temporada_registros
SELECT (SELECT count(*) FROM temporada_episodio) AS episodios,
       (SELECT count(*) FROM temporada_episodio WHERE cerrado_el IS NULL) AS episodios_abiertos,
       (SELECT count(*) FROM temporada_efecto) AS efectos,
       (SELECT count(*) FROM temporada_efecto WHERE cerrado_el IS NULL) AS efectos_abiertos,
       (SELECT count(*) FROM temporada_exclusion) AS exclusiones,
       (SELECT count(*) FROM temporada_fin_reduccion) AS fines;

-- @temporada_efectos_por_desenlace
SELECT tipo, desenlace, (cerrado_el IS NULL) AS abierto, count(*) AS n
FROM temporada_efecto GROUP BY 1,2,3 ORDER BY 1,2,3;

-- Bloques I y J: modos y topes en uso. Solo claves de modo y de tope.

-- @config_versiones
SELECT count(*) AS versiones, min(created_at) AS primera, max(created_at) AS ultima
FROM config_version;

-- @settings_modos_y_topes
SELECT jsonb_object_agg(k, v ORDER BY k)::text
FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) c,
 LATERAL jsonb_each(c.settings) e(k,v)
WHERE k ~ '^(ads_bid_politica_|ads_apply_cap_|ads_temporada_|precio_modo|precio_cap_|ads_producto_|ads_recuperacion_|ads_motor_bid|ads_campana_topada|ads_subproteccion|resumen_acos)'
  AND k !~ '(token|secret|chat|clave|password|key)';

-- @goals
SELECT platform, mode, enabled, count(*) AS n
FROM ads_optimizer_goal GROUP BY 1,2,3 ORDER BY 1,2,3;
