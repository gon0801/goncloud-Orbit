-- Lectura RO de produccion tras desplegar el bloque G (solo SELECT).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. El lanzador
-- lee cada bloque por docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read,
-- transaccion read_only). G no trae migracion: comprueba que 0098 sigue siendo
-- la ultima y que el CHECK del sello y la columna existen.

-- @momento
SELECT now() AT TIME ZONE 'UTC' AS ahora_utc,
       current_setting('server_version_num') AS version_servidor;

-- @esquema_arreglos05
SELECT
  EXISTS (SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'optimizer_cycle'
             AND column_name = 'decision_committed_at') AS m0094_sello,
  EXISTS (SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'optimizer_cycle'
             AND column_name = 'resume_of') AS m0094_resume,
  (SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'apply_attempt'
      AND column_name IN ('bid_previo', 'ausencia_previa')) = 2 AS m0095_evidencia,
  EXISTS (SELECT 1 FROM pg_views
           WHERE schemaname = 'public' AND viewname = 'v_cambio_bid'
             AND position('request_payload' in definition) > 0) AS m0096_historial,
  to_regclass('public.keyword_reposicion_intento') IS NOT NULL AS m0097_reposicion,
  EXISTS (SELECT 1 FROM pg_indexes
           WHERE schemaname = 'public' AND indexname = 'apply_queue_live_efecto_unico')
    AS m0098_indice_live,
  NOT EXISTS (SELECT 1 FROM pg_indexes
               WHERE schemaname = 'public' AND indexname = 'apply_queue_clave_efecto_en_vuelo')
    AS m0098_mixto_ausente,
  EXISTS (SELECT 1 FROM pg_trigger
           WHERE tgrelid = to_regclass('public.apply_queue')
             AND tgname = 'apply_queue_clave_vigente') AS m0098_trigger;

-- @sello_harvest
-- El CHECK del sello bitemporal y la columna que G usa siguen vigentes; G no
-- relaja el trigger de madurez.
SELECT
  EXISTS (SELECT 1 FROM pg_constraint
           WHERE conname = 'decision_dato_no_del_futuro') AS check_dato_no_futuro,
  EXISTS (SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'decision'
             AND column_name = 'data_observed_at') AS col_data_observed_at,
  EXISTS (SELECT 1 FROM pg_trigger
           WHERE tgrelid = to_regclass('public.decision')
             AND tgname = 'decision_madurez_corte') AS trigger_madurez;

-- @lotes_por_estado_modo
SELECT mode, status, count(*) AS lotes
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer'
 GROUP BY mode, status
 ORDER BY mode, status;

-- @lotes_mixtos
WITH por_ciclo AS (
    SELECT cycle_id,
           bool_or(kind IN ('negative', 'harvest')) AS con_termino,
           bool_or(kind IN ('bid', 'budget', 'pause', 'resume')) AS con_entidad
      FROM decision
     GROUP BY cycle_id
)
SELECT count(*) FILTER (WHERE con_termino AND con_entidad) AS lotes_mixtos,
       count(*) FILTER (WHERE con_termino) AS lotes_con_termino,
       count(*) FILTER (WHERE con_entidad) AS lotes_con_entidad,
       count(*) AS lotes_con_decisiones
  FROM por_ciclo;

-- @decisiones_harvest_sello
-- Medida correcta del sello: harvest cuyo INSUMO leido venia con observed_at_max
-- posterior a decided_at (lo que el sello clamp), y harvest con data_observed_at
-- en el futuro (debe ser 0 por el CHECK).
SELECT count(*) AS harvest_total,
       count(*) FILTER (
           WHERE CASE WHEN d.inputs ? 'termino'
                      AND d.inputs #>> '{termino,observed_at_max}' IS NOT NULL
                      THEN (d.inputs #>> '{termino,observed_at_max}')::timestamptz
                           > d.decided_at
                      ELSE false END
       ) AS harvest_insumo_posterior,
       count(*) FILTER (WHERE d.data_observed_at > d.decided_at) AS harvest_sello_en_futuro,
       count(*) FILTER (WHERE d.data_observed_at IS NULL OR d.data_observed_at > d.decided_at)
           AS sello_invalido
  FROM decision d
 WHERE d.kind = 'harvest';

-- @observaciones_ingeridas_posteriores
-- Contexto (no prueba del sello): observaciones del mismo termino INGERIDAS
-- despues de la decision. Puede incluir dias posteriores, asi que no mide el
-- insumo leido; la medida del sello es @decisiones_harvest_sello.
SELECT count(*) AS pares_observacion_posterior,
       count(DISTINCT d.id) AS decisiones_con_observacion_posterior
  FROM decision d
  JOIN search_term_observation o
    ON o.ad_entity_id = d.ad_entity_id
   AND lower(o.search_term) = lower(d.search_term)
 WHERE d.kind IN ('negative', 'harvest')
   AND o.observed_at > d.decided_at;

-- @sello_violaciones_historicas
SELECT count(*) AS decisiones_dato_en_futuro
  FROM decision
 WHERE data_observed_at > decided_at;
