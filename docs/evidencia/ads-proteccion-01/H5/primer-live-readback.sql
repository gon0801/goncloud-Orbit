-- H5.4: primer ciclo live despues del flip 2026-09-30 03:04:56 UTC.
-- SOLO LECTURA. Ejecutar despues de 08:41 UTC del 30-sep desde este repo:
-- ssh -T goncloud 'docker exec -i orbit-db-1 psql "$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)" -X -A -F " | " -P pager=off -v ON_ERROR_STOP=1' \
--   < docs/evidencia/ads-proteccion-01/H5/primer-live-readback.sql
-- Esperado: dos ciclos (US/MX), status=done; mode es el envelope y tambien
-- fue live durante H5 shadow. Investigar toda PAUSE
-- contra B.3 y toda mutacion contra Amazon por platform/kind/external_id.
-- Si un ciclo falla, no queda done o hay PAUSE nueva inexplicable: parada H5.
-- (4)/(5) muestran solo decisiones posteriores al flip; (7)/(8) cubren toda
-- la ventana desde INICIO_SHADOW, incluso un apply tardio de una decision
-- shadow anterior. (8) usa la era de la decision y el modo real de la cola.
BEGIN READ ONLY;

\echo '(1) ciclos desde el flip H5.4'
SELECT id, platform, mode, status,
       started_at AT TIME ZONE 'UTC' AS inicio_utc,
       finished_at AT TIME ZONE 'UTC' AS fin_utc,
       decisions_count, applied_count, notes
  FROM optimizer_cycle
 WHERE started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00'
 ORDER BY id;

\echo '(2) decisiones por kind y ciclo'
SELECT c.id AS cycle_id, c.platform, d.kind, count(*) AS decisiones
  FROM optimizer_cycle c
  JOIN decision d ON d.cycle_id = c.id
 WHERE c.started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00'
 GROUP BY c.id, c.platform, d.kind
 ORDER BY c.id, d.kind;

\echo '(3) toda PAUSE nueva para comparar con replay B.3'
SELECT c.id AS cycle_id, c.platform, d.id AS decision_id,
       e.id AS ad_entity_id, e.kind AS entity_kind, e.external_id,
       d.window_end, d.data_observed_at, d.inputs
  FROM optimizer_cycle c
  JOIN decision d ON d.cycle_id = c.id AND d.kind = 'pause'
  JOIN ad_entity e ON e.id = d.ad_entity_id
 WHERE c.started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00'
 ORDER BY c.id, d.id;

\echo '(4) cola y aplicaciones de decisiones de estos ciclos'
SELECT c.id AS cycle_id, d.id AS decision_id, d.kind,
       e.platform, e.kind AS entity_kind, e.external_id,
       q.id AS queue_id, q.estado AS queue_estado,
       q.applied_at AT TIME ZONE 'UTC' AS applied_utc,
       a.confirmed_at AT TIME ZONE 'UTC' AS confirmado_utc, a.verify_ok
  FROM optimizer_cycle c
  JOIN decision d ON d.cycle_id = c.id
  JOIN ad_entity e ON e.id = d.ad_entity_id
  LEFT JOIN apply_queue q ON q.decision_id = d.id
  LEFT JOIN decision_application a ON a.decision_id = d.id
 WHERE c.started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00'
   AND (q.id IS NOT NULL OR a.decision_id IS NOT NULL)
 ORDER BY c.id, d.id, q.id;

\echo '(5) intentos HTTP reales y reversas de estos ciclos'
SELECT c.id AS cycle_id, d.id AS decision_id, d.kind,
       e.platform, e.kind AS entity_kind, e.external_id,
       t.id AS attempt_id, t.tipo, t.resultado,
       t.started_at AT TIME ZONE 'UTC' AS inicio_utc,
       t.finished_at AT TIME ZONE 'UTC' AS fin_utc
  FROM optimizer_cycle c
  JOIN decision d ON d.cycle_id = c.id
  JOIN ad_entity e ON e.id = d.ad_entity_id
  JOIN apply_attempt t ON t.decision_id = d.id
 WHERE c.started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00'
 ORDER BY c.id, d.id, t.seq;

\echo '(6) guardas actuales y trabajo en vuelo'
SELECT mode, count(*) AS goals FROM ads_optimizer_goal GROUP BY mode ORDER BY mode;
SELECT id, settings->'ads_pause_sin_cooldown_bid' AS flag_b2a
  FROM config_version ORDER BY id DESC LIMIT 1;
SELECT count(*) AS ciclos_running FROM optimizer_cycle WHERE status = 'running';
SELECT count(*) AS harvest_en_vuelo FROM harvest_job WHERE fase NOT IN ('done', 'failed');
SELECT count(*) AS cola_harvest_en_vuelo FROM apply_queue
 WHERE kind = 'harvest' AND estado IN ('pending_veto', 'released', 'applying');

\echo '(7) mutaciones e intentos desde INICIO_SHADOW, sin filtro por ciclo'
SELECT (SELECT count(*) FROM apply_queue
         WHERE applied_at > TIMESTAMPTZ '2026-09-25 06:13:41+00') AS cola_aplicada,
       (SELECT count(*) FROM apply_attempt
         WHERE started_at > TIMESTAMPTZ '2026-09-25 06:13:41+00') AS intentos,
       (SELECT count(*) FROM apply_attempt t JOIN decision d ON d.id = t.decision_id
         WHERE t.started_at > TIMESTAMPTZ '2026-09-25 06:13:41+00'
           AND t.tipo = 'normal' AND d.kind = 'bid') AS intentos_bid,
       (SELECT count(*) FROM decision_application
         WHERE attempted_at > TIMESTAMPTZ '2026-09-25 06:13:41+00') AS aplicaciones,
       (SELECT count(*) FROM decision_application a JOIN decision d ON d.id = a.decision_id
         WHERE a.attempted_at > TIMESTAMPTZ '2026-09-25 06:13:41+00'
           AND a.verify_ok IS TRUE AND d.kind = 'bid') AS bids_confirmados;

\echo '(8) era de cada mutacion desde INICIO_SHADOW (pre-flip aplicado = 0)'
WITH movimientos AS (
    SELECT 'cola_aplicada' AS fuente,
           CASE WHEN q.modo = 'shadow' THEN 'fila_shadow'
                WHEN d.decided_at < TIMESTAMPTZ '2026-09-30 03:04:56+00'
                  THEN 'decision_pre_flip'
                ELSE 'post_flip' END AS origen,
           d.kind::text AS kind
      FROM apply_queue q
      JOIN decision d ON d.id = q.decision_id
     WHERE q.applied_at > TIMESTAMPTZ '2026-09-25 06:13:41+00'
    UNION ALL
    SELECT 'intento_http',
           CASE WHEN d.decided_at < TIMESTAMPTZ '2026-09-30 03:04:56+00'
                  THEN 'decision_pre_flip' ELSE 'post_flip' END, d.kind::text
      FROM apply_attempt t
      JOIN decision d ON d.id = t.decision_id
     WHERE t.started_at > TIMESTAMPTZ '2026-09-25 06:13:41+00'
    UNION ALL
    SELECT 'aplicacion',
           CASE WHEN d.decided_at < TIMESTAMPTZ '2026-09-30 03:04:56+00'
                  THEN 'decision_pre_flip' ELSE 'post_flip' END, d.kind::text
      FROM decision_application a
      JOIN decision d ON d.id = a.decision_id
     WHERE a.attempted_at > TIMESTAMPTZ '2026-09-25 06:13:41+00'
)
SELECT fuente, origen, kind, count(*) AS filas
  FROM movimientos
 GROUP BY fuente, origen, kind
 ORDER BY fuente, origen, kind;

\echo '(9) decisiones sin apply y su motivo; huerfanas reales = 0'
SELECT d.id AS decision_id, d.cycle_id, d.kind,
       sa.cycle_id AS ciclo_ejecutor, sa.motivo, sa.detalle
  FROM decision d
  LEFT JOIN decision_application a ON a.decision_id = d.id
  LEFT JOIN decision_sin_aplicar sa ON sa.decision_id = d.id
 WHERE d.cycle_id IN (SELECT id FROM optimizer_cycle
                       WHERE started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00')
   AND a.decision_id IS NULL
 ORDER BY d.id, sa.cycle_id;
SELECT decision_id, origen FROM v_decision_huerfana
 WHERE cycle_id IN (SELECT id FROM optimizer_cycle
                     WHERE started_at >= TIMESTAMPTZ '2026-09-30 03:04:56+00')
 ORDER BY decision_id;

ROLLBACK;
