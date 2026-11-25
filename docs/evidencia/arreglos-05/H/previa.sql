-- @momento
SELECT now() AS ahora_utc, current_setting('server_version_num') AS version_pg;

-- @esquema_motivos
SELECT pg_get_constraintdef(oid) AS definicion
  FROM pg_constraint
 WHERE conrelid = 'decision_sin_aplicar'::regclass
   AND conname = 'decision_sin_aplicar_motivo_check';

-- @ciclos
SELECT motor, mode, coalesce(status, '(null)') AS status, count(*) AS ciclos,
       max(id) AS id_maximo
  FROM optimizer_cycle
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

-- @intentos_bid_por_ciclo
WITH por_ciclo AS (
    SELECT d.cycle_id, count(*) AS intentos
      FROM apply_attempt a
      JOIN decision d ON d.id = a.decision_id
     WHERE a.tipo = 'normal' AND d.kind = 'bid'
     GROUP BY d.cycle_id
)
SELECT count(*) AS ciclos_con_intentos,
       coalesce(sum(intentos), 0) AS intentos_totales,
       coalesce(max(intentos), 0) AS maximo,
       count(*) FILTER (WHERE intentos > 150) AS ciclos_sobre_150,
       count(*) FILTER (WHERE intentos > 100) AS ciclos_sobre_100,
       count(*) FILTER (WHERE intentos > 50) AS ciclos_sobre_50,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY intentos) AS mediana,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY intentos) AS p90
  FROM por_ciclo;

-- @distribucion_top
WITH por_ciclo AS (
    SELECT d.cycle_id, count(*) AS intentos
      FROM apply_attempt a
      JOIN decision d ON d.id = a.decision_id
     WHERE a.tipo = 'normal' AND d.kind = 'bid'
     GROUP BY d.cycle_id
)
SELECT p.cycle_id, o.platform, o.mode, o.status, p.intentos,
       (o.decision_committed_at IS NOT NULL) AS con_sello_tx3
  FROM por_ciclo p
  JOIN optimizer_cycle o ON o.id = p.cycle_id
 ORDER BY p.intentos DESC, p.cycle_id DESC
 LIMIT 10;

-- @ambiguos
SELECT count(*) FILTER (WHERE a.finished_at IS NULL) AS sin_sello,
       count(*) FILTER (WHERE a.resultado = 'fallo:readback_ambiguo') AS readback_ambiguo,
       count(*) FILTER (WHERE a.finished_at IS NULL
                          OR a.resultado = 'fallo:readback_ambiguo') AS ambiguos_total,
       count(*) AS intentos_normal_bid
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
 WHERE a.tipo = 'normal' AND d.kind = 'bid';

-- @temporada_marcador
SELECT count(*) AS intentos_normal_bid,
       count(*) FILTER (
           WHERE d.inputs->>'motivo' IN ('entra_temporada_baja', 'sale_temporada_baja')
       ) AS con_motivo_transicion,
       count(*) FILTER (
           WHERE d.inputs->>'motivo' IN ('entra_temporada_baja', 'sale_temporada_baja')
             AND d.inputs->>'temporada_episodio_id' IS NOT NULL
       ) AS transicion_con_episodio,
       count(*) FILTER (
           WHERE d.inputs->>'motivo' IN ('entra_temporada_baja', 'sale_temporada_baja')
             AND d.inputs->>'temporada_episodio_id' IS NULL
       ) AS transicion_sin_episodio,
       count(*) FILTER (WHERE d.inputs->>'motivo' = 'ajusta_temporada_baja') AS ajustes
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
 WHERE a.tipo = 'normal' AND d.kind = 'bid';

-- @decisiones_bid_temporada
SELECT d.inputs->>'motivo' AS motivo, count(*) AS decisiones,
       count(*) FILTER (WHERE d.inputs->>'temporada_episodio_id' IS NOT NULL) AS con_episodio,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM apply_attempt a WHERE a.decision_id = d.id AND a.tipo = 'normal'
       )) AS con_intento_normal
  FROM decision d
 WHERE d.kind = 'bid'
   AND d.inputs->>'motivo' IN ('entra_temporada_baja', 'sale_temporada_baja',
                               'ajusta_temporada_baja')
 GROUP BY 1
 ORDER BY 1;

-- @intentos_por_tipo
SELECT a.tipo, count(*) AS intentos
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id
 WHERE d.kind = 'bid'
 GROUP BY 1
 ORDER BY 1;

-- @motivos_en_uso
SELECT motivo, count(*) AS desenlaces
  FROM decision_sin_aplicar
 GROUP BY 1
 ORDER BY 2 DESC, 1;

-- @roles
SELECT has_table_privilege('app_decide', 'public.apply_attempt', 'INSERT') AS decide_insert_ledger,
       has_table_privilege('app_decide', 'public.optimizer_cycle', 'SELECT') AS decide_select_ciclo,
       has_column_privilege(
           'app_decide', 'public.optimizer_cycle', 'decision_committed_at', 'UPDATE'
       ) AS decide_sella_tx3,
       has_table_privilege('app_read', 'public.apply_attempt', 'SELECT') AS read_ledger;
