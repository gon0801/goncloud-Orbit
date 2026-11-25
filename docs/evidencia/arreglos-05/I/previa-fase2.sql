-- Foto de produccion de la fase 2 del bloque I (solo SELECT).
-- Verifica el esquema que sostiene la configuracion, la sonda de la 0099
-- (ultima migracion aplicada, de H) y las cuotas vigentes. I no trae DDL.

-- @momento
SELECT now() AT TIME ZONE 'UTC' AS ahora_utc,
       current_setting('server_version_num') AS version_servidor;

-- @esquema_config_version
SELECT column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'config_version'
 ORDER BY ordinal_position;

-- @triggers_config_version
SELECT tgname, pg_get_triggerdef(oid) AS definicion
  FROM pg_trigger
 WHERE tgrelid = 'public.config_version'::regclass AND NOT tgisinternal
 ORDER BY tgname;

-- @sonda_0099
SELECT CASE
         WHEN NOT EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.decision_sin_aplicar'::regclass
              AND conname = 'decision_sin_aplicar_motivo_check'
         ) THEN false
         ELSE EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.decision_sin_aplicar'::regclass
              AND conname = 'decision_sin_aplicar_motivo_check'
              AND pg_get_constraintdef(oid) LIKE '%tope_ciclo%'
         )
       END AS sonda_0099;

-- @cuotas_vigentes
WITH ultima AS (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
plataformas(p) AS (VALUES ('amazon_us'), ('amazon_mx')),
kinds(k) AS (VALUES ('bid'), ('pause'), ('negative'), ('harvest'))
SELECT p AS plataforma,
       k AS kind,
       (u.settings ? ('ads_apply_cap_' || p || '_' || k)) AS tiene_clave,
       u.settings ->> ('ads_apply_cap_' || p || '_' || k) AS valor_texto,
       jsonb_typeof(u.settings -> ('ads_apply_cap_' || p || '_' || k)) AS tipo_json
  FROM ultima u
 CROSS JOIN plataformas
 CROSS JOIN kinds
 ORDER BY p, k;

-- @cuotas_por_tipo
WITH ultima AS (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
plataformas(p) AS (VALUES ('amazon_us'), ('amazon_mx')),
kinds(k) AS (VALUES ('bid'), ('pause'), ('negative'), ('harvest')),
cuotas AS (
    SELECT u.settings ->> ('ads_apply_cap_' || p || '_' || k) AS valor_texto,
           jsonb_typeof(u.settings -> ('ads_apply_cap_' || p || '_' || k)) AS tipo_json
      FROM ultima u
     CROSS JOIN plataformas
     CROSS JOIN kinds
)
SELECT count(*) FILTER (WHERE tipo_json = 'number') AS numericas,
       count(*) FILTER (WHERE tipo_json = 'string' AND valor_texto ~ '^[0-9]+$') AS texto_digitos,
       count(*) FILTER (WHERE tipo_json = 'string' AND valor_texto !~ '^[0-9]+$') AS texto_no_entero,
       count(*) FILTER (WHERE tipo_json = 'null') AS nulas,
       count(*) FILTER (WHERE tipo_json IS NULL) AS ausentes
  FROM cuotas;

-- @versiones
SELECT count(*) AS versiones,
       min(id) AS primer_id,
       max(id) AS ultimo_id
  FROM config_version;
