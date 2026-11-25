-- Foto de produccion previa al bloque G de ARREGLOS 05 (solo SELECT).
-- Cada bloque empieza con "-- @<etiqueta>" y trae UNA sentencia. El lanzador
-- lee cada bloque por docs/evidencia/arreglos-01/lee_prod.sh (rol orbit_read,
-- transaccion read_only). Mide lotes, lotes mixtos y reobservaciones.

-- @momento
SELECT now() AT TIME ZONE 'UTC' AS ahora_utc,
       current_setting('server_version_num') AS version_servidor;

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
           bool_or(kind IN ('bid', 'budget', 'pause', 'resume')) AS con_entidad,
           count(*) AS decisiones
      FROM decision
     GROUP BY cycle_id
)
SELECT count(*) FILTER (WHERE con_termino AND con_entidad) AS lotes_mixtos,
       count(*) FILTER (WHERE con_termino) AS lotes_con_termino,
       count(*) FILTER (WHERE con_entidad) AS lotes_con_entidad,
       count(*) AS lotes_con_decisiones
  FROM por_ciclo;

-- @decisiones_por_kind
SELECT kind, count(*) AS decisiones
  FROM decision
 GROUP BY kind
 ORDER BY count(*) DESC;

-- @reobservaciones_posteriores
-- Observaciones de un termino que entraron DESPUES de la decision de ese
-- termino en el mismo ad_group: el lookahead que G debe acotar en el sello.
SELECT count(*) AS pares_observacion_posterior,
       count(DISTINCT d.id) AS decisiones_con_observacion_posterior
  FROM decision d
  JOIN search_term_observation o
    ON o.ad_entity_id = d.ad_entity_id
   AND lower(o.search_term) = lower(d.search_term)
 WHERE d.kind IN ('negative', 'harvest')
   AND o.observed_at > d.decided_at;

-- @decisiones_harvest_dato_mayor
-- Harvest cuyo insumo ya venia con observed_at_max posterior a decided_at
-- (lo que el sello debe clampear sin tocar el insumo).
SELECT count(*) AS harvest_total,
       count(*) FILTER (
           WHERE CASE WHEN d.inputs ? 'termino'
                      AND d.inputs #>> '{termino,observed_at_max}' IS NOT NULL
                      THEN (d.inputs #>> '{termino,observed_at_max}')::timestamptz
                           > d.decided_at
                      ELSE false END
       ) AS harvest_insumo_posterior,
       count(*) FILTER (WHERE d.data_observed_at > d.decided_at) AS harvest_sello_en_futuro
  FROM decision d
 WHERE d.kind = 'harvest';

-- @sello_violaciones_historicas
-- El CHECK decision_dato_no_del_futuro deberia dejar esto en cero.
SELECT count(*) AS decisiones_dato_en_futuro
  FROM decision
 WHERE data_observed_at > decided_at;
