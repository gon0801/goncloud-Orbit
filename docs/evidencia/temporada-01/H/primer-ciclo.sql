-- TEMPORADA 01 H.3: vigilancia de los ciclos de la temporada en vivo. SOLO LECTURA.
-- Un disparo por fecha (los trabajos de `at` estan en docs/evidencia/temporada-01/H/transiciones.txt).
-- Lo corre primer-ciclo.sh desde /mnt/data/appdata/orbit/recordatorios/ y manda el resultado por Telegram.
--
-- Referencia: la PRIMERA config que dejo las cuatro claves en live (la de H.2).
-- Solo mira ciclos TERMINADOS que EMPEZARON despues de esa config; si no hay
-- ninguno, la seccion (2) lo dice con esas palabras.
--
-- Casos de paro (todos deben dar 0):
--   1) un bid enviado que baja mas que la reduccion de la entrada
--   2) un regreso que baja un bid
--   3) un PUT despues de una lectura fresca distinta
--   4) dos entradas confirmadas para una keyword en el mismo episodio
--      (una decision que no se aplico y se vuelve a derivar manana es lo esperado)
--   5) un presupuesto escrito sin APLICAR AJUSTE
BEGIN READ ONLY;

\echo '(0) config de referencia: la primera con las cuatro claves en live'
SELECT id AS config_version_id, created_at
  FROM config_version
 WHERE settings->>'ads_temporada_amazon_mx' = 'live'
   AND settings->>'ads_temporada_amazon_us' = 'live'
   AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
   AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
 ORDER BY id
 LIMIT 1;

\echo '(1) ciclos terminados que empezaron despues de esa config'
SELECT c.id, c.platform, c.started_at, c.finished_at, c.decisions_count
  FROM optimizer_cycle c
 WHERE c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1)
 ORDER BY c.id;

\echo '(2) aviso cuando todavia no hay ninguno'
SELECT CASE WHEN count(*) = 0
            THEN 'todavia no hay ningun ciclo terminado despues del encendido'
            ELSE count(*) || ' ciclo(s) terminado(s) despues del encendido'
       END AS aviso
  FROM optimizer_cycle c
 WHERE c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1);

\echo '(3) entradas a la baja: confirmadas | bajaron mas que la reduccion (paro)'
SELECT count(*) AS entradas_confirmadas,
       count(*) FILTER (
           WHERE d.new_value IS NOT NULL
             AND d.old_value IS NOT NULL
             AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
             AND d.new_value
                 < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
       ) AS paro_baja_mas_que_la_reduccion
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
  JOIN config_version cv ON cv.id = d.config_version_id
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'entra_temporada_baja'
   AND c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1);

\echo '(4) regresos de la baja: confirmados | bajaron un bid (paro)'
SELECT count(*) AS regresos_confirmados,
       count(*) FILTER (WHERE d.new_value IS NOT NULL
                          AND d.old_value IS NOT NULL
                          AND d.new_value < d.old_value) AS paro_regreso_que_baja
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'sale_temporada_baja'
   AND c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1);

\echo '(5) lectura fresca distinta: sin aplicar | aplicadas a pesar de ella (paro)'
SELECT count(*) AS sin_aplicar_por_lectura,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = s.decision_id)
       ) AS paro_put_tras_lectura_fresca
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE s.motivo = 'bid_cambiado_fuera'
   AND c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1);

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
             AND c.started_at > (
                   SELECT created_at FROM config_version
                    WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                      AND settings->>'ads_temporada_amazon_us' = 'live'
                      AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                      AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                    ORDER BY id
                    LIMIT 1)
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
           AND c.started_at > (
                 SELECT created_at FROM config_version
                  WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                    AND settings->>'ads_temporada_amazon_us' = 'live'
                    AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                    AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                  ORDER BY id
                  LIMIT 1)) AS entradas_sin_episodio;

\echo '(7) presupuestos confirmados sin APLICAR AJUSTE ni REGRESAR AJUSTE (paro)'
SELECT count(*) AS paro_presupuesto_sin_aplicar_ajuste
  FROM campana_ajuste a
 WHERE a.clase = 'presupuesto'
   AND a.confirmado_el IS NOT NULL
   AND a.creado_el > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1)
   AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE');

\echo '(8) resumen de casos de paro (cada linea debe dar 0)'
SELECT 'baja_mas_que_la_reduccion' AS caso,
       count(*) FILTER (
           WHERE d.new_value IS NOT NULL
             AND d.old_value IS NOT NULL
             AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
             AND d.new_value
                 < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
       ) AS n
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
  JOIN config_version cv ON cv.id = d.config_version_id
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'entra_temporada_baja'
   AND c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1)
UNION ALL
SELECT 'regreso_que_baja',
       count(*) FILTER (WHERE d.new_value IS NOT NULL
                          AND d.old_value IS NOT NULL
                          AND d.new_value < d.old_value)
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' = 'sale_temporada_baja'
   AND c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1)
UNION ALL
SELECT 'put_tras_lectura_fresca',
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = s.decision_id)
       )
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE s.motivo = 'bid_cambiado_fuera'
   AND c.status = 'done'
   AND c.started_at > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1)
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
             AND c.started_at > (
                   SELECT created_at FROM config_version
                    WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                      AND settings->>'ads_temporada_amazon_us' = 'live'
                      AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                      AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                    ORDER BY id
                    LIMIT 1)
           GROUP BY 1, 2
          HAVING count(*) > 1) AS dos)
UNION ALL
SELECT 'presupuesto_sin_aplicar_ajuste', count(*)
  FROM campana_ajuste a
 WHERE a.clase = 'presupuesto'
   AND a.confirmado_el IS NOT NULL
   AND a.creado_el > (
         SELECT created_at FROM config_version
          WHERE settings->>'ads_temporada_amazon_mx' = 'live'
            AND settings->>'ads_temporada_amazon_us' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
            AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
          ORDER BY id
          LIMIT 1)
   AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
ORDER BY 1;

\echo '(9) veredicto'
SELECT CASE
         WHEN (
           (SELECT count(*) FROM (
              SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
                FROM decision d
                JOIN optimizer_cycle c ON c.id = d.cycle_id
                JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
               WHERE d.kind = 'bid'
                 AND d.inputs->>'motivo' = 'entra_temporada_baja'
                 AND d.inputs->>'temporada_episodio_id' IS NOT NULL
                 AND c.status = 'done'
                 AND c.started_at > (
                       SELECT created_at FROM config_version
                        WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                          AND settings->>'ads_temporada_amazon_us' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                        ORDER BY id
                        LIMIT 1)
               GROUP BY 1, 2
              HAVING count(*) > 1) AS dos)
           + (SELECT count(*)
                FROM decision d
                JOIN optimizer_cycle c ON c.id = d.cycle_id
                JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
                JOIN config_version cv ON cv.id = d.config_version_id
               WHERE d.kind = 'bid'
                 AND d.inputs->>'motivo' = 'entra_temporada_baja'
                 AND cv.settings->'ads_temporada'->>'reduccion_baja_pct' IS NOT NULL
                 AND d.new_value IS NOT NULL
                 AND d.old_value IS NOT NULL
                 AND d.new_value
                     < d.old_value * (1 - (cv.settings->'ads_temporada'->>'reduccion_baja_pct')::numeric / 100)
                 AND c.status = 'done'
                 AND c.started_at > (
                       SELECT created_at FROM config_version
                        WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                          AND settings->>'ads_temporada_amazon_us' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                        ORDER BY id
                        LIMIT 1))
           + (SELECT count(*)
                FROM decision d
                JOIN optimizer_cycle c ON c.id = d.cycle_id
                JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
               WHERE d.kind = 'bid'
                 AND d.inputs->>'motivo' = 'sale_temporada_baja'
                 AND d.new_value IS NOT NULL
                 AND d.old_value IS NOT NULL
                 AND d.new_value < d.old_value
                 AND c.status = 'done'
                 AND c.started_at > (
                       SELECT created_at FROM config_version
                        WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                          AND settings->>'ads_temporada_amazon_us' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                        ORDER BY id
                        LIMIT 1))
           + (SELECT count(*)
                FROM decision_sin_aplicar s
                JOIN optimizer_cycle c ON c.id = s.cycle_id
               WHERE s.motivo = 'bid_cambiado_fuera'
                 AND EXISTS (SELECT 1 FROM decision_application da
                              WHERE da.decision_id = s.decision_id)
                 AND c.status = 'done'
                 AND c.started_at > (
                       SELECT created_at FROM config_version
                        WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                          AND settings->>'ads_temporada_amazon_us' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                        ORDER BY id
                        LIMIT 1))
           + (SELECT count(*)
                FROM campana_ajuste a
               WHERE a.clase = 'presupuesto'
                 AND a.confirmado_el IS NOT NULL
                 AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
                 AND a.creado_el > (
                       SELECT created_at FROM config_version
                        WHERE settings->>'ads_temporada_amazon_mx' = 'live'
                          AND settings->>'ads_temporada_amazon_us' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_mx' = 'live'
                          AND settings->>'ads_bid_recuperacion_amazon_us' = 'live'
                        ORDER BY id
                        LIMIT 1))
         ) = 0
         THEN 'SIN CASOS DE PARO'
         ELSE 'CASO DE PARO: revisar la seccion (8)'
       END AS veredicto;

ROLLBACK;
