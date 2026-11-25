-- Lectura RO de produccion tras desplegar el bloque F (solo SELECT).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. El lanzador
-- lee cada bloque por docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read,
-- transaccion read_only).

-- @momento
SELECT now() AT TIME ZONE 'UTC' AS ahora_utc,
       current_setting('server_version_num') AS version_servidor;

-- @esquema_0094_0098
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

-- @snapshots_target
SELECT mode, status,
       count(*) AS ciclos,
       count(*) FILTER (
           WHERE CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
                      THEN notes::jsonb ? 'target' ELSE false END
       ) AS con_target,
       count(*) FILTER (
           WHERE CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
                      THEN notes::jsonb #>> '{target,version}' IS NOT NULL ELSE false END
       ) AS con_version
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer'
 GROUP BY mode, status
 ORDER BY mode, status;

-- @bases_live_por_plataforma
SELECT platform, count(*) AS live_comprometidos,
       count(*) FILTER (WHERE status = 'degraded') AS degraded,
       max(id) AS ultimo_id
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND mode = 'live' AND decision_committed_at IS NOT NULL
 GROUP BY platform
 ORDER BY platform;

-- @ultimas_bases
SELECT id, platform, status,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,version}' END AS version,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,base,valor}' END AS base,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,base,origen}' END AS base_origen,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,overlay,meta_alta_pct}' END AS overlay_meta_alta,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,procedencia}' END AS procedencia,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,target_aplicado}' END AS aplicado
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND mode = 'live'
 ORDER BY id DESC
 LIMIT 8;

-- @temporada_config
SELECT key, value
  FROM config_version, jsonb_each(settings)
 WHERE id = (SELECT max(id) FROM config_version)
   AND key LIKE 'ads_temporada%'
 ORDER BY key;
