-- Fase D: primer ciclo despues del deploy (2026-09-28 00:31 UTC). SOLO LECTURA.
-- Uso (desde el repo):
--   ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1' \
--     < docs/evidencia/ads-proteccion-01/fase-d/primer-ciclo.sql
-- Esperado: (1) ciclos done; (2) filas de target por ciclo; (3) 0 y 0;
-- (4) contador D.2 (puede ser 0); (5) bids con inversion_n10_v1;
-- (6) motivos sin choque_clave salvo choque real; (7) huerfana = 0.
BEGIN READ ONLY;

\echo '(1) ciclos desde el deploy'
SELECT id, platform, mode, status, started_at, finished_at, decisions_count
  FROM optimizer_cycle
 WHERE started_at > '2026-09-28 00:31:00+00'
 ORDER BY id;

\echo '(2) C.2a: filas de target_acos_ciclo por ciclo y procedencia'
SELECT t.cycle_id, t.procedencia, count(*) AS hojas
  FROM target_acos_ciclo t
  JOIN optimizer_cycle c ON c.id = t.cycle_id
 WHERE c.started_at > '2026-09-28 00:31:00+00'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '(3) C.2a: decisiones de hoja sin fila de target | con target distinto al de inputs (esperado 0 | 0)'
SELECT count(*) FILTER (WHERE t.cycle_id IS NULL) AS sin_fila,
       count(*) FILTER (WHERE t.cycle_id IS NOT NULL
                         AND (d.inputs->>'target_acos_pct_usado')::numeric
                             IS DISTINCT FROM t.target_acos_pct) AS distinto
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
  JOIN ad_entity e ON e.id = d.ad_entity_id AND e.kind IN ('keyword', 'product_target')
  LEFT JOIN target_acos_ciclo t ON t.cycle_id = d.cycle_id AND t.ad_entity_id = d.ad_entity_id
 WHERE c.started_at > '2026-09-28 00:31:00+00'
   AND d.kind IN ('bid', 'pause');

\echo '(4) D.2: saltos inversion_sin_evidencia por ciclo (notes.skips.entidad)'
SELECT id,
       CASE WHEN notes ~ '^\s*\{'
            THEN coalesce((notes::jsonb -> 'skips' -> 'entidad' ->> 'inversion_sin_evidencia')::int, 0)
       END AS inversion_sin_evidencia
  FROM optimizer_cycle
 WHERE started_at > '2026-09-28 00:31:00+00'
 ORDER BY id;

\echo '(5) D.2: decisiones bid por politica de inversion congelada'
SELECT d.cycle_id, d.inputs->>'inversion_policy_version' AS politica, count(*)
  FROM decision d
  JOIN optimizer_cycle c ON c.id = d.cycle_id
 WHERE c.started_at > '2026-09-28 00:31:00+00' AND d.kind = 'bid'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '(6) D.1b: decision_sin_aplicar por motivo desde el deploy'
SELECT s.motivo, count(*)
  FROM decision_sin_aplicar s
  JOIN optimizer_cycle c ON c.id = s.cycle_id
 WHERE c.started_at > '2026-09-28 00:31:00+00'
 GROUP BY 1
 ORDER BY 1;

\echo '(7) v_decision_huerfana por origen (esperado: huerfana = 0)'
SELECT origen, count(*) FROM v_decision_huerfana GROUP BY 1 ORDER BY 1;

ROLLBACK;
