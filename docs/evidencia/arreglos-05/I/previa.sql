-- Foto de produccion previa al bloque I de ARREGLOS 05 (solo SELECT).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. El lanzador
-- lee cada bloque por docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read,
-- transaccion read_only). Mide las cuotas vigentes por plataforma y si alguna
-- quedo guardada como texto, null o fraccion.

-- @momento
SELECT now() AT TIME ZONE 'UTC' AS ahora_utc,
       current_setting('server_version_num') AS version_servidor;

-- @config_vigente
SELECT id, created_at, label
  FROM config_version
 ORDER BY id DESC
 LIMIT 1;

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

-- @cuotas_fuera_de_rango
WITH ultima AS (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
plataformas(p) AS (VALUES ('amazon_us'), ('amazon_mx')),
kinds(k) AS (VALUES ('bid'), ('pause'), ('negative'), ('harvest')),
cuotas AS (
    SELECT p, k,
           u.settings ->> ('ads_apply_cap_' || p || '_' || k) AS valor_texto,
           jsonb_typeof(u.settings -> ('ads_apply_cap_' || p || '_' || k)) AS tipo_json
      FROM ultima u
     CROSS JOIN plataformas
     CROSS JOIN kinds
)
SELECT count(*) FILTER (
           WHERE (tipo_json = 'number'
                  AND (valor_texto::numeric < 0 OR valor_texto::numeric > 2147483647))
              OR (tipo_json = 'string' AND valor_texto ~ '^-?[0-9]+$'
                  AND (valor_texto::numeric < 0 OR valor_texto::numeric > 2147483647))
       ) AS cuotas_fuera_de_rango
  FROM cuotas;

-- @cuotas_historicas_rotas
-- Cualquier fila de config_version (no solo la vigente) con una cuota que el
-- lector cap_desde_settings rechazaria. Una fila historica rota no gobierna,
-- pero confirma la forma que el bloque evita crear.
SELECT count(*) FILTER (WHERE jsonb_typeof(e.value) = 'boolean') AS booleanas,
       count(*) FILTER (WHERE jsonb_typeof(e.value) = 'null') AS nulas,
       count(*) FILTER (WHERE jsonb_typeof(e.value) = 'number'
                          AND e.value::text LIKE '%.%') AS fracciones,
       count(*) FILTER (WHERE jsonb_typeof(e.value) = 'string'
                          AND e.value #>> '{}' !~ '^[0-9]+$') AS texto_no_entero,
       count(*) AS claves_de_cuota
  FROM config_version c
 CROSS JOIN LATERAL jsonb_each(c.settings) AS e
 WHERE e.key LIKE 'ads_apply_cap_%';

-- @versiones
SELECT count(*) AS versiones,
       min(id) AS primer_id,
       max(id) AS ultimo_id
  FROM config_version;
