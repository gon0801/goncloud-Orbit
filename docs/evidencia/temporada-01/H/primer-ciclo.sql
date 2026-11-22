-- TEMPORADA 01 H.3: vigilancia de los ciclos de la temporada en vivo. SOLO LECTURA.
-- Un disparo por fecha (los trabajos de `at` estan en docs/evidencia/temporada-01/H/transiciones.txt).
-- Lo corre primer-ciclo.sh desde /mnt/data/appdata/orbit/recordatorios/ con el rol orbit_read
-- y manda el resultado por Telegram.
--
-- Referencia: la PRIMERA config que dejo las cuatro claves en live (la de H.2).
-- Solo mira ciclos TERMINADOS que EMPEZARON despues de esa config; si no hay
-- ninguno, la seccion (2) lo dice con esas palabras.
--
-- Casos de paro (todos deben dar 0):
--   1) un bid enviado que baja mas que la reduccion de la entrada
--      (solo motivo entra_temporada_baja: un ajusta_temporada_baja puede bajar
--       mas cuando el dueno sube el porcentaje a media baja, y eso es correcto)
--   2) un regreso que baja un bid
--   3) un PUT despues de una lectura fresca distinta
--   4) dos entradas confirmadas para una keyword en el mismo episodio
--      (una decision que no se aplico y se vuelve a derivar manana es lo esperado)
--   5) un presupuesto escrito sin APLICAR AJUSTE
--
-- Los casos 1 y 2 juzgan lo que se ENVIO, no lo que se confirmo: el ledger
-- (apply_attempt) nace ANTES del PUT con el importe exacto
-- (request_payload->>'bid'), y una verificacion fallida deja verify_ok = false
-- o ninguna fila en decision_application. Mirar solo lo confirmado escondería
-- justo el PUT indebido.
BEGIN READ ONLY;

-- La racha vigente: la primera config que sigue live hasta la ultima. Sin
-- racha, `ref` queda en el futuro y ninguna fila cuenta: la seccion (0) lo dice
-- con esas palabras en vez de juzgar una practica como si fuera live.
SELECT coalesce(
         (SELECT cv.created_at
            FROM config_version cv
           WHERE cv.settings->>'ads_temporada_amazon_mx' = 'live'
             AND cv.settings->>'ads_temporada_amazon_us' = 'live'
             AND cv.settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
             AND cv.settings->>'ads_bid_recuperacion_amazon_us' = 'live'
             AND NOT EXISTS (
                   SELECT 1 FROM config_version nx
                    WHERE nx.id > cv.id
                      AND (nx.settings->>'ads_temporada_amazon_mx' IS DISTINCT FROM 'live'
                        OR nx.settings->>'ads_temporada_amazon_us' IS DISTINCT FROM 'live'
                        OR nx.settings->>'ads_bid_recuperacion_amazon_mx' IS DISTINCT FROM 'live'
                        OR nx.settings->>'ads_bid_recuperacion_amazon_us' IS DISTINCT FROM 'live'))
           ORDER BY cv.id
           LIMIT 1),
         '2099-01-01 00:00:00+00'::timestamptz) AS ref
\gset

\echo '(0) config de referencia: la PRIMERA de la racha vigente con las cuatro claves en live'
SELECT CASE WHEN :'ref' = '2099-01-01 00:00:00+00'
            THEN 'temporada apagada: sin racha viva con las cuatro claves en live; no hay ciclos en vivo que vigilar'
            ELSE 'racha viva con las cuatro claves en live desde ' || :'ref'
       END AS estado;

\echo '(1) ciclos terminados que empezaron despues de esa config'
SELECT c.id, c.platform, c.started_at, c.finished_at, c.decisions_count
  FROM optimizer_cycle c
 WHERE c.status = 'done'
   AND c.started_at > :'ref'
 ORDER BY c.id;

\echo '(2) aviso cuando todavia no hay ninguno'
SELECT CASE WHEN count(*) = 0
            THEN 'todavia no hay ningun ciclo terminado despues del encendido'
            ELSE count(*) || ' ciclo(s) terminado(s) despues del encendido'
       END AS aviso
  FROM optimizer_cycle c
 WHERE c.status = 'done'
   AND c.started_at > :'ref';

\echo '(3) entradas a la baja: confirmadas | enviadas por el ledger | de esas, bajo la reduccion (paro) | enviadas sin sello'
SELECT count(DISTINCT d.id) FILTER (
           WHERE EXISTS (SELECT 1 FROM decision_application da
                          WHERE da.decision_id = d.id AND da.verify_ok IS TRUE)
       ) AS entradas_confirmadas,
       count(DISTINCT d.id) AS entradas_enviadas,
       count(DISTINCT d.id) FILTER (
           WHERE d.old_value IS NOT NULL
             AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
             AND (aa.request_payload->>'bid')::numeric
                 < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
       ) AS paro_baja_mas_que_la_reduccion,
       count(DISTINCT d.id) FILTER (WHERE aa.resultado IS NULL) AS enviadas_sin_sello
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                       AND aa.request_payload ? 'bid'
  JOIN config_version cv ON cv.id = d.config_version_id
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'entra_temporada_baja'
   AND c.status = 'done'
   AND c.started_at > :'ref';

\echo '(4) regresos de la baja: confirmados | enviados por el ledger | de esos, bajan un bid (paro) | enviados sin sello'
SELECT count(DISTINCT d.id) FILTER (
           WHERE EXISTS (SELECT 1 FROM decision_application da
                          WHERE da.decision_id = d.id AND da.verify_ok IS TRUE)
       ) AS regresos_confirmados,
       count(DISTINCT d.id) AS regresos_enviados,
       count(DISTINCT d.id) FILTER (
           WHERE d.old_value IS NOT NULL
             AND (aa.request_payload->>'bid')::numeric < d.old_value
       ) AS paro_regreso_que_baja,
       count(DISTINCT d.id) FILTER (WHERE aa.resultado IS NULL) AS enviados_sin_sello
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                       AND aa.request_payload ? 'bid'
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'sale_temporada_baja'
   AND c.status = 'done'
   AND c.started_at > :'ref';

\echo '(5) lectura fresca distinta: sin aplicar | con un PUT registrado a pesar de ella (paro)'
SELECT count(*) AS sin_aplicar_por_lectura,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
       ) AS paro_put_tras_lectura_fresca
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE s.motivo = 'bid_cambiado_fuera'
   AND c.status = 'done'
   AND c.started_at > :'ref';

\echo '(6) keywords con mas de una entrada CONFIRMADA en el mismo episodio (paro) | entradas confirmadas sin episodio estampado'
SELECT (SELECT count(*) FROM (
          SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
            FROM decision d
            JOIN optimizer_cycle c ON c.id = d.cycle_id
            JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
           WHERE d.kind = 'bid'
             AND d.inputs->>'motivo' = 'entra_temporada_baja'
             AND d.inputs->>'temporada_episodio_id' IS NOT NULL
             AND c.status = 'done'
             AND c.started_at > :'ref'
           GROUP BY 1, 2
          HAVING count(*) > 1) AS dos) AS paro_dos_entradas_en_un_episodio,
       (SELECT count(*)
          FROM decision d
          JOIN optimizer_cycle c ON c.id = d.cycle_id
          JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
         WHERE d.kind = 'bid'
           AND d.inputs->>'motivo' = 'entra_temporada_baja'
           AND d.inputs->>'temporada_episodio_id' IS NULL
           AND c.status = 'done'
           AND c.started_at > :'ref') AS entradas_sin_episodio;

\echo '(7) presupuestos confirmados sin APLICAR AJUSTE ni REGRESAR AJUSTE (paro)'
SELECT count(*) AS paro_presupuesto_sin_aplicar_ajuste
  FROM campana_ajuste a
 WHERE a.clase = 'presupuesto'
   AND a.confirmado_el IS NOT NULL
   AND a.creado_el > :'ref'
   AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE');

\echo '(8) resumen de casos de paro (cada linea debe dar 0)'
SELECT 'baja_mas_que_la_reduccion' AS caso, count(DISTINCT d.id) AS n
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                       AND aa.request_payload ? 'bid'
  JOIN config_version cv ON cv.id = d.config_version_id
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'entra_temporada_baja'
   AND d.old_value IS NOT NULL
   AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
   AND (aa.request_payload->>'bid')::numeric
       < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
   AND c.status = 'done'
   AND c.started_at > :'ref'
UNION ALL
SELECT 'regreso_que_baja', count(DISTINCT d.id)
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                       AND aa.request_payload ? 'bid'
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'sale_temporada_baja'
   AND d.old_value IS NOT NULL
   AND (aa.request_payload->>'bid')::numeric < d.old_value
   AND c.status = 'done'
   AND c.started_at > :'ref'
UNION ALL
SELECT 'put_tras_lectura_fresca',
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
       )
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE s.motivo = 'bid_cambiado_fuera'
   AND c.status = 'done'
   AND c.started_at > :'ref'
UNION ALL
SELECT 'dos_entradas_en_un_episodio',
       (SELECT count(*) FROM (
          SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
            FROM decision d
            JOIN optimizer_cycle c ON c.id = d.cycle_id
            JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
           WHERE d.kind = 'bid'
             AND d.inputs->>'motivo' = 'entra_temporada_baja'
             AND d.inputs->>'temporada_episodio_id' IS NOT NULL
             AND c.status = 'done'
             AND c.started_at > :'ref'
           GROUP BY 1, 2
          HAVING count(*) > 1) AS dos)
UNION ALL
SELECT 'presupuesto_sin_aplicar_ajuste', count(*)
  FROM campana_ajuste a
 WHERE a.clase = 'presupuesto'
   AND a.confirmado_el IS NOT NULL
   AND a.creado_el > :'ref'
   AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
ORDER BY 1;

\echo '(9) casos de paro en total y veredicto (una sola linea)'
SELECT (SELECT count(DISTINCT d.id)
          FROM decision d
          JOIN optimizer_cycle c ON c.id = d.cycle_id
          JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                               AND aa.request_payload ? 'bid'
          JOIN config_version cv ON cv.id = d.config_version_id
         WHERE d.kind = 'bid'
           AND d.inputs->>'motivo' = 'entra_temporada_baja'
           AND d.old_value IS NOT NULL
           AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
           AND (aa.request_payload->>'bid')::numeric
               < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
           AND c.status = 'done'
           AND c.started_at > :'ref')
     + (SELECT count(DISTINCT d.id)
          FROM decision d
          JOIN optimizer_cycle c ON c.id = d.cycle_id
          JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                               AND aa.request_payload ? 'bid'
         WHERE d.kind = 'bid'
           AND d.inputs->>'motivo' = 'sale_temporada_baja'
           AND d.old_value IS NOT NULL
           AND (aa.request_payload->>'bid')::numeric < d.old_value
           AND c.status = 'done'
           AND c.started_at > :'ref')
     + (SELECT count(*)
          FROM decision_sin_aplicar s
          JOIN optimizer_cycle c ON c.id = s.cycle_id
         WHERE s.motivo = 'bid_cambiado_fuera'
           AND EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
           AND c.status = 'done'
           AND c.started_at > :'ref')
     + (SELECT count(*) FROM (
          SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
            FROM decision d
            JOIN optimizer_cycle c ON c.id = d.cycle_id
            JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
           WHERE d.kind = 'bid'
             AND d.inputs->>'motivo' = 'entra_temporada_baja'
             AND d.inputs->>'temporada_episodio_id' IS NOT NULL
             AND c.status = 'done'
             AND c.started_at > :'ref'
           GROUP BY 1, 2
          HAVING count(*) > 1) AS dos)
     + (SELECT count(*)
          FROM campana_ajuste a
         WHERE a.clase = 'presupuesto'
           AND a.confirmado_el IS NOT NULL
           AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
           AND a.creado_el > :'ref') AS casos_de_paro,
       CASE WHEN (
           (SELECT count(DISTINCT d.id)
              FROM decision d
              JOIN optimizer_cycle c ON c.id = d.cycle_id
              JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                                   AND aa.request_payload ? 'bid'
              JOIN config_version cv ON cv.id = d.config_version_id
             WHERE d.kind = 'bid'
               AND d.inputs->>'motivo' = 'entra_temporada_baja'
               AND d.old_value IS NOT NULL
               AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
               AND (aa.request_payload->>'bid')::numeric
                   < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
               AND c.status = 'done'
               AND c.started_at > :'ref')
           + (SELECT count(DISTINCT d.id)
                FROM decision d
                JOIN optimizer_cycle c ON c.id = d.cycle_id
                JOIN apply_attempt aa ON aa.decision_id = d.id AND aa.tipo = 'normal'
                                     AND aa.request_payload ? 'bid'
               WHERE d.kind = 'bid'
                 AND d.inputs->>'motivo' = 'sale_temporada_baja'
                 AND d.old_value IS NOT NULL
                 AND (aa.request_payload->>'bid')::numeric < d.old_value
                 AND c.status = 'done'
                 AND c.started_at > :'ref')
           + (SELECT count(*)
                FROM decision_sin_aplicar s
                JOIN optimizer_cycle c ON c.id = s.cycle_id
               WHERE s.motivo = 'bid_cambiado_fuera'
                 AND EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
                 AND c.status = 'done'
                 AND c.started_at > :'ref')
           + (SELECT count(*) FROM (
                SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
                  FROM decision d
                  JOIN optimizer_cycle c ON c.id = d.cycle_id
                  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
                 WHERE d.kind = 'bid'
                   AND d.inputs->>'motivo' = 'entra_temporada_baja'
                   AND d.inputs->>'temporada_episodio_id' IS NOT NULL
                   AND c.status = 'done'
                   AND c.started_at > :'ref'
                 GROUP BY 1, 2
                HAVING count(*) > 1) AS dos)
           + (SELECT count(*)
                FROM campana_ajuste a
               WHERE a.clase = 'presupuesto'
                 AND a.confirmado_el IS NOT NULL
                 AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
                 AND a.creado_el > :'ref')
         ) = 0
         THEN 'SIN CASOS DE PARO'
         ELSE 'CASO DE PARO: revisar la seccion (8)'
       END AS veredicto;

ROLLBACK;
