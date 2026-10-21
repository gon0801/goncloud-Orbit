-- ingesta-01 D.2: medicion de 7 dias del ingest diario y de los ciclos. SOLO LECTURA.
-- Desplegado en D.1 el 2026-10-21 ~18:33 UTC; la ventana empieza el 2026-10-22 (primer dia completo).
-- Uso (desde el repo): ssh gonserver 'sudo docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1' < este-archivo
BEGIN READ ONLY;

\echo '(1) por dia y source: corridas, ok, repetidos (ok de mas), intento del primer ok, hora y duracion de ese ok'
WITH r AS (
    SELECT source, ok, started_at, finished_at,
           (started_at AT TIME ZONE 'UTC')::date AS dia,
           row_number() OVER (PARTITION BY source, (started_at AT TIME ZONE 'UTC')::date
                              ORDER BY started_at) AS intento
      FROM ingest_run
     WHERE source IN ('amazon_ads_reports_v3', 'amazon_ads_products_v3', 'amazon_ads_placements_v3')
       AND started_at >= '2026-10-22 00:00+00'
)
SELECT dia, source,
       count(*) AS corridas,
       count(*) FILTER (WHERE ok) AS ok,
       greatest(count(*) FILTER (WHERE ok) - 1, 0) AS repetidos,
       min(intento) FILTER (WHERE ok) AS intento_primer_ok,
       to_char(min(started_at) FILTER (WHERE ok) AT TIME ZONE 'UTC', 'HH24:MI') AS hora_primer_ok_utc,
       to_char((min(finished_at - started_at) FILTER (WHERE ok)), 'HH24:MI:SS') AS duracion_primer_ok
  FROM r
 GROUP BY dia, source
 ORDER BY dia, source;

\echo '(2) por dia: a que hora corrio cada ciclo del optimizador'
SELECT (started_at AT TIME ZONE 'UTC')::date AS dia, platform, mode, status,
       to_char(started_at AT TIME ZONE 'UTC', 'HH24:MI:SS') AS hora_utc
  FROM optimizer_cycle
 WHERE started_at >= '2026-10-22 00:00+00'
 ORDER BY started_at;

\echo '(3) dias de la ventana sin ningun ok por source (huecos)'
SELECT d::date AS dia, s.source
  FROM generate_series('2026-10-22'::date, (now() AT TIME ZONE 'UTC')::date - 1, '1 day') d
 CROSS JOIN (VALUES ('amazon_ads_reports_v3'), ('amazon_ads_products_v3'), ('amazon_ads_placements_v3')) s(source)
 WHERE NOT EXISTS (SELECT 1 FROM ingest_run r
                    WHERE r.source = s.source AND r.ok
                      AND (r.started_at AT TIME ZONE 'UTC')::date = d::date)
 ORDER BY 1, 2;
ROLLBACK;
