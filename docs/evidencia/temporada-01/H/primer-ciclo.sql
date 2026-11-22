-- TEMPORADA 01 H.3: vigilancia de los ciclos de la temporada en vivo. SOLO LECTURA.
-- Un disparo por fecha (los trabajos de `at` estan en docs/evidencia/temporada-01/H/transiciones.txt).
-- Lo corre primer-ciclo.sh desde /mnt/data/appdata/orbit/recordatorios/ con el rol orbit_read
-- y manda el resultado por Telegram.
--
-- Referencia: la PRIMERA config de la racha vigente de cada plataforma, es decir,
-- la primera en la que esa plataforma tenia temporada o subida rapida en live y a
-- la que ninguna config posterior le apago las dos. Solo mira ciclos TERMINADOS
-- que EMPEZARON despues de esa config; si no hay ninguno, la seccion (2) lo dice
-- con esas palabras, y si una plataforma no tiene ambito activo el veredicto es
-- NO EVALUADO.
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

-- La racha vigente, POR PLATAFORMA: la primera config en la que la plataforma
-- tenia temporada o subida rapida en live y a la que ninguna config posterior le
-- apago las dos. Una edicion que solo cambia una plataforma no apaga el
-- chequeo de la otra (Devin, PR 730).
SELECT coalesce(
         (SELECT cv.created_at FROM config_version cv
           WHERE (cv.settings->>'ads_temporada_amazon_mx' = 'live'
               OR cv.settings->>'ads_bid_recuperacion_amazon_mx' = 'live')
             AND NOT EXISTS (
                   SELECT 1 FROM config_version nx
                    WHERE nx.id > cv.id
                      AND nx.settings->>'ads_temporada_amazon_mx' IS DISTINCT FROM 'live'
                      AND nx.settings->>'ads_bid_recuperacion_amazon_mx' IS DISTINCT FROM 'live')
           ORDER BY cv.id LIMIT 1),
         '2099-01-01 00:00:00+00'::timestamptz) AS ref_mx
\gset
SELECT coalesce(
         (SELECT cv.created_at FROM config_version cv
           WHERE (cv.settings->>'ads_temporada_amazon_us' = 'live'
               OR cv.settings->>'ads_bid_recuperacion_amazon_us' = 'live')
             AND NOT EXISTS (
                   SELECT 1 FROM config_version nx
                    WHERE nx.id > cv.id
                      AND nx.settings->>'ads_temporada_amazon_us' IS DISTINCT FROM 'live'
                      AND nx.settings->>'ads_bid_recuperacion_amazon_us' IS DISTINCT FROM 'live')
           ORDER BY cv.id LIMIT 1),
         '2099-01-01 00:00:00+00'::timestamptz) AS ref_us
\gset

\echo '(0) referencia por plataforma: la PRIMERA de su racha viva (temporada o subida rapida en live)'
SELECT CASE WHEN :'ref_mx'::timestamptz = '2099-01-01T00:00:00Z'::timestamptz
            THEN 'amazon_mx: sin ambito activo (temporada y subida rapida apagadas o en practica)'
            ELSE 'amazon_mx: racha viva desde ' || :'ref_mx'
       END AS mx,
       CASE WHEN :'ref_us'::timestamptz = '2099-01-01T00:00:00Z'::timestamptz
            THEN 'amazon_us: sin ambito activo (temporada y subida rapida apagadas o en practica)'
            ELSE 'amazon_us: racha viva desde ' || :'ref_us'
       END AS us;

\echo '(1) ciclos terminados que empezaron despues de esa config'
SELECT c.id, c.platform, c.started_at, c.finished_at, c.decisions_count
  FROM optimizer_cycle c
 WHERE c.status = 'done'
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
 ORDER BY c.id;

\echo '(2) aviso cuando todavia no hay ninguno'
SELECT CASE WHEN count(*) = 0
            THEN 'todavia no hay ningun ciclo terminado despues del encendido'
            ELSE count(*) || ' ciclo(s) terminado(s) despues del encendido'
       END AS aviso
  FROM optimizer_cycle c
 WHERE c.status = 'done'
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'));

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
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'));

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
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'));

\echo '(5) lectura fresca distinta: sin aplicar | con un PUT registrado a pesar de ella (paro)'
SELECT count(*) AS sin_aplicar_por_lectura,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
       ) AS paro_put_tras_lectura_fresca
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE s.motivo = 'bid_cambiado_fuera'
   AND c.status = 'done'
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'));

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
             AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
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
           AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))) AS entradas_sin_episodio;

\echo '(7) presupuestos confirmados sin APLICAR AJUSTE ni REGRESAR AJUSTE (paro)'
SELECT count(*) AS paro_presupuesto_sin_aplicar_ajuste
  FROM campana_ajuste a
 WHERE a.clase = 'presupuesto'
   AND a.confirmado_el IS NOT NULL
   AND ((a.platform = 'amazon_mx' AND a.creado_el > :'ref_mx') OR (a.platform = 'amazon_us' AND a.creado_el > :'ref_us'))
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
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
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
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
UNION ALL
SELECT 'put_tras_lectura_fresca',
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
       )
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE s.motivo = 'bid_cambiado_fuera'
   AND c.status = 'done'
   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
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
             AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
           GROUP BY 1, 2
          HAVING count(*) > 1) AS dos)
UNION ALL
SELECT 'presupuesto_sin_aplicar_ajuste', count(*)
  FROM campana_ajuste a
 WHERE a.clase = 'presupuesto'
   AND a.confirmado_el IS NOT NULL
   AND ((a.platform = 'amazon_mx' AND a.creado_el > :'ref_mx') OR (a.platform = 'amazon_us' AND a.creado_el > :'ref_us'))
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
           AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us')))
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
           AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us')))
     + (SELECT count(*)
          FROM decision_sin_aplicar s
          JOIN optimizer_cycle c ON c.id = s.cycle_id
         WHERE s.motivo = 'bid_cambiado_fuera'
           AND EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
           AND c.status = 'done'
           AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us')))
     + (SELECT count(*) FROM (
          SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
            FROM decision d
            JOIN optimizer_cycle c ON c.id = d.cycle_id
            JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
           WHERE d.kind = 'bid'
             AND d.inputs->>'motivo' = 'entra_temporada_baja'
             AND d.inputs->>'temporada_episodio_id' IS NOT NULL
             AND c.status = 'done'
             AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
           GROUP BY 1, 2
          HAVING count(*) > 1) AS dos)
     + (SELECT count(*)
          FROM campana_ajuste a
         WHERE a.clase = 'presupuesto'
           AND a.confirmado_el IS NOT NULL
           AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
           AND ((a.platform = 'amazon_mx' AND a.creado_el > :'ref_mx') OR (a.platform = 'amazon_us' AND a.creado_el > :'ref_us'))) AS casos_de_paro,
       CASE WHEN :'ref_mx'::timestamptz = '2099-01-01T00:00:00Z'::timestamptz
                  AND :'ref_us'::timestamptz = '2099-01-01T00:00:00Z'::timestamptz
            THEN 'NO EVALUADO: temporada y subida rapida sin ambito activo en las dos plataformas'
            WHEN (
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
               AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us')))
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
                 AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us')))
           + (SELECT count(*)
                FROM decision_sin_aplicar s
                JOIN optimizer_cycle c ON c.id = s.cycle_id
               WHERE s.motivo = 'bid_cambiado_fuera'
                 AND EXISTS (SELECT 1 FROM apply_attempt aa WHERE aa.decision_id = s.decision_id)
                 AND c.status = 'done'
                 AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us')))
           + (SELECT count(*) FROM (
                SELECT d.ad_entity_id, d.inputs->>'temporada_episodio_id'
                  FROM decision d
                  JOIN optimizer_cycle c ON c.id = d.cycle_id
                  JOIN decision_application da ON da.decision_id = d.id AND da.verify_ok IS TRUE
                 WHERE d.kind = 'bid'
                   AND d.inputs->>'motivo' = 'entra_temporada_baja'
                   AND d.inputs->>'temporada_episodio_id' IS NOT NULL
                   AND c.status = 'done'
                   AND ((c.platform = 'amazon_mx' AND c.started_at > :'ref_mx') OR (c.platform = 'amazon_us' AND c.started_at > :'ref_us'))
                 GROUP BY 1, 2
                HAVING count(*) > 1) AS dos)
           + (SELECT count(*)
                FROM campana_ajuste a
               WHERE a.clase = 'presupuesto'
                 AND a.confirmado_el IS NOT NULL
                 AND a.go_literal NOT IN ('APLICAR AJUSTE', 'REGRESAR AJUSTE')
                 AND ((a.platform = 'amazon_mx' AND a.creado_el > :'ref_mx') OR (a.platform = 'amazon_us' AND a.creado_el > :'ref_us')))
         ) = 0
         THEN 'SIN CASOS DE PARO'
         ELSE 'CASO DE PARO: revisar la seccion (8)'
       END AS veredicto;

ROLLBACK;
