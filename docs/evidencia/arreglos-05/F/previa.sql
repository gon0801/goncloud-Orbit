-- Foto de produccion previa al bloque F de ARREGLOS 05 (solo SELECT).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. El lanzador
-- lee cada bloque por docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read,
-- transaccion read_only). Los CASE evitan castear notas que no son JSON.

-- @momento
SELECT now() AT TIME ZONE 'UTC' AS ahora_utc,
       current_setting('server_version_num') AS version_servidor;

-- @snapshots_por_modo_estado
SELECT mode, status,
       count(*) AS ciclos,
       count(*) FILTER (
           WHERE CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
                      THEN notes::jsonb ? 'target' ELSE false END
       ) AS con_target
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer'
 GROUP BY mode, status
 ORDER BY mode, status;

-- @target_por_procedencia
SELECT coalesce(CASE WHEN left(ltrim(notes), 1) = '{' THEN notes::jsonb #>> '{target,procedencia}' END,
                '(sin clave)') AS procedencia,
       coalesce(CASE WHEN left(ltrim(notes), 1) = '{' THEN notes::jsonb #>> '{target,motivo_abstencion}' END,
                '(sin clave)') AS motivo,
       count(*) AS ciclos,
       count(*) FILTER (
           WHERE CASE WHEN left(ltrim(notes), 1) = '{'
                      THEN notes::jsonb #>> '{target,target_aplicado}' END IS NOT NULL
       ) AS con_aplicado
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer'
   AND CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb ? 'target' ELSE false END
 GROUP BY 1, 2
 ORDER BY 3 DESC;

-- @base_inequivoca_v1
SELECT count(*) AS snapshots_v1_con_base_inequivoca
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND mode = 'live' AND status = 'done'
   AND CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,procedencia}' = 'margen_plataforma'
             AND notes::jsonb #>> '{target,target_aplicado}' IS NOT NULL
            ELSE false END;

-- @snapshots_versionados
SELECT count(*) AS con_clave_version_en_target
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer'
   AND CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,version}' IS NOT NULL ELSE false END;

-- @ciclos_live_con_decision_comprometida
SELECT count(*) AS live_comprometidos,
       count(*) FILTER (WHERE status = 'done') AS done,
       count(*) FILTER (WHERE status = 'degraded') AS degraded,
       count(*) FILTER (WHERE status NOT IN ('done', 'degraded')) AS otros
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND mode = 'live' AND decision_committed_at IS NOT NULL;

-- @ultimas_bases_live
SELECT id, platform, status,
       decision_committed_at IS NOT NULL AS comprometida,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,procedencia}' END AS procedencia,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,target_aplicado}' END AS aplicado,
       CASE WHEN notes IS NOT NULL AND left(ltrim(notes), 1) = '{'
            THEN notes::jsonb #>> '{target,motivo_abstencion}' END AS motivo
  FROM optimizer_cycle
 WHERE motor = 'ads_optimizer' AND mode = 'live' AND platform = 'amazon_us'
 ORDER BY id DESC
 LIMIT 12;

-- @temporada_config
SELECT key, value
  FROM config_version, jsonb_each(settings)
 WHERE id = (SELECT max(id) FROM config_version)
   AND key LIKE 'ads_temporada%'
 ORDER BY key;
