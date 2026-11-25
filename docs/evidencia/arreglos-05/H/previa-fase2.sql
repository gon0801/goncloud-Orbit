-- @momento
SELECT now() AS ahora_utc, current_setting('server_version_num') AS version_pg;

-- @sonda_0099
SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.decision_sin_aplicar'::regclass
              AND conname = 'decision_sin_aplicar_motivo_check')
       THEN false
       ELSE EXISTS (
           SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.decision_sin_aplicar'::regclass
              AND conname = 'decision_sin_aplicar_motivo_check'
              AND pg_get_constraintdef(oid) LIKE '%tope_ciclo%')
       END AS sonda_0099;

-- @motivos_check
SELECT pg_get_constraintdef(oid) AS definicion
  FROM pg_constraint
 WHERE conrelid = 'decision_sin_aplicar'::regclass
   AND conname = 'decision_sin_aplicar_motivo_check';

-- @conteo_tablas
-- pg_class, no information_schema.tables: la tabla privada de 0087
-- (arreglos03_devoluciones_meli_ids) no tiene privilegios para orbit_read y
-- information_schema la oculta; pg_class la cuenta igual (la sonda de 0087 usa
-- to_regclass por lo mismo).
SELECT count(*) FILTER (WHERE c.relkind IN ('r', 'p')) AS tablas_base,
       count(*) FILTER (WHERE c.relkind = 'v') AS vistas
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relkind IN ('r', 'p', 'v');

-- @tope_ciclo_desenlaces
SELECT count(*) AS desenlaces_tope_ciclo,
       count(DISTINCT cycle_id) AS ciclos_con_tope,
       max(registrado_at) AS ultimo
  FROM decision_sin_aplicar
 WHERE motivo = 'tope_ciclo';

-- @intentos_por_ciclo
WITH por_ciclo AS (
    SELECT d.cycle_id, count(*) AS intentos
      FROM apply_attempt a
      JOIN decision d ON d.id = a.decision_id
     WHERE a.tipo = 'normal' AND d.kind = 'bid'
     GROUP BY d.cycle_id
)
SELECT count(*) AS ciclos_con_intentos,
       coalesce(max(intentos), 0) AS maximo,
       count(*) FILTER (WHERE intentos > 150) AS ciclos_sobre_150,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY intentos) AS p90
  FROM por_ciclo;

-- @ciclos
SELECT motor, mode, coalesce(status, '(null)') AS status, count(*) AS ciclos, max(id) AS id_maximo
  FROM optimizer_cycle
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

-- @roles
SELECT has_table_privilege('app_decide', 'public.decision_sin_aplicar', 'INSERT') AS decide_insert_desenlace,
       has_table_privilege('app_read', 'public.decision_sin_aplicar', 'INSERT') AS read_insert_desenlace,
       has_table_privilege('app_decide', 'public.apply_attempt', 'INSERT') AS decide_insert_ledger,
       has_table_privilege('app_read', 'public.decision_sin_aplicar', 'SELECT') AS read_select_desenlace;
