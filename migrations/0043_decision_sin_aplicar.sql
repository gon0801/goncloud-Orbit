-- ADS D.1: el desenlace "no aplicado" deja de ser invisible.
-- decision_sin_aplicar es append-only: una fila por (decision, ciclo
-- EJECUTOR) que intento y no aplico, con el motivo de un vocabulario CERRADO
-- (espejo de app.apply.MOTIVOS_SIN_APLICAR — el test estatico y el de la
-- constraint viva los mantienen sincronizados, mismo patron que
-- KINDS_QUOTA <-> trigger de quota). `ya_aplicada` esta en el vocabulario
-- pero JAMAS se escribe: su desenlace ya es decision_application.
-- v_decision_huerfana distingue el hueco HISTORICO (ciclos cerrados antes
-- de esta migracion: 'sin_registro') de la decision live que DEBIO quedar
-- registrada y no quedo ('huerfana': auditables una a una).
BEGIN;

CREATE TABLE decision_sin_aplicar (
    decision_id BIGINT NOT NULL REFERENCES decision(id),
    cycle_id    BIGINT NOT NULL REFERENCES optimizer_cycle(id),
    motivo TEXT NOT NULL CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'sin_respuesta', 'perdida'
    )),
    detalle       JSONB,
    registrado_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (decision_id, cycle_id)
);

CREATE INDEX decision_sin_aplicar_por_ciclo ON decision_sin_aplicar (cycle_id);

COMMENT ON TABLE decision_sin_aplicar IS
  'Un no-apply por (decision, ciclo ejecutor): el ciclo live la evaluo y no '
  'la aplico, con el motivo cerrado. Append-only por GRANTs (nadie de la app '
  'tiene UPDATE/DELETE) e idempotente por PK: un re-run del mismo ciclo no '
  'duplica (ON CONFLICT DO NOTHING) y otro ciclo ejecutor SI agrega su fila. '
  'Los desenlaces que YA tienen rastro propio no se duplican aqui: applied '
  '(decision_application), discarded con discard_motivo (apply_queue '
  'terminal) y vetoed/failed de la cola.';
COMMENT ON COLUMN decision_sin_aplicar.cycle_id IS
  'Ciclo EJECUTOR (el live que corrio el apply), NO el ciclo que decidio: '
  'una decision nacida en shadow y evaluada por el ciclo live se registra '
  'contra el live.';
COMMENT ON COLUMN decision_sin_aplicar.motivo IS
  'Vocabulario cerrado, espejo de app.apply.MOTIVOS_SIN_APLICAR. '
  'ya_aplicada existe para el espejo pero nunca se escribe (esa decision ya '
  'tiene decision_application).';
COMMENT ON COLUMN decision_sin_aplicar.detalle IS
  'Contexto minimo del motivo (p.ej. {"kind": "pause"} en sin_quota de la '
  'cola); NULL en las ramas de bids, que ya lo llevan en el resumen.';

-- Append-only por GRANTs (patron 0002/0041): escribe SOLO el aplicador;
-- leen auditoria (app_read) y el dueno (app_admin). NADIE actualiza ni borra.
GRANT INSERT ON decision_sin_aplicar TO app_decide;
GRANT SELECT ON decision_sin_aplicar TO app_read, app_admin;

-- La vista nace con el timestamp de la migracion HORNEADO (format %L): es la
-- frontera entre el hueco historico (sin_registro) y la huerfana real.
DO $$
BEGIN
    EXECUTE format($v$
CREATE VIEW v_decision_huerfana AS
SELECT d.id AS decision_id,
       d.cycle_id,
       d.kind,
       d.ad_entity_id,
       oc.platform,
       oc.finished_at AS ciclo_cerrado_at,
       CASE WHEN oc.finished_at < %L::timestamptz THEN 'sin_registro' ELSE 'huerfana' END
           AS origen
  FROM decision d
  JOIN optimizer_cycle oc ON oc.id = d.cycle_id
 WHERE d.kind IN ('bid', 'pause', 'negative', 'harvest')
   AND oc.mode = 'live'
   AND oc.finished_at IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM decision_application da WHERE da.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM decision_sin_aplicar sa WHERE sa.decision_id = d.id)
   AND NOT EXISTS (SELECT 1 FROM apply_queue q
                    WHERE q.decision_id = d.id
                      AND q.estado IN ('applied', 'failed', 'vetoed', 'discarded'))
$v$, now()::text);
END $$;

COMMENT ON VIEW v_decision_huerfana IS
  'Decisiones aplicables de ciclos live YA CERRADOS sin NINGUN desenlace '
  '(ni aplicada, ni no-apply registrado, ni fila terminal en la cola). '
  'origen = sin_registro para los ciclos cerrados ANTES de la migracion 0043 '
  '(el registro no existia: hueco historico conocido) y huerfana para los '
  'posteriores (debio quedar registrada: auditar una a una). Una fila de '
  'cola EN VUELO (pending_veto/released/applying) no es desenlace: la '
  'decision aparece hasta que la fila termine o el no-apply se registre.';

GRANT SELECT ON v_decision_huerfana TO app_read, app_admin;

-- Candado de privilegios (patron 0036/0042, solo has_* para no exigir
-- membresia de rol al migrar: la prueba de SET ROLE vive en
-- test_apply_schema).
DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'decision_sin_aplicar', 'INSERT') THEN
        RAISE EXCEPTION '0043: app_decide debe poder registrar no-applies';
    END IF;
    IF has_table_privilege('app_decide', 'decision_sin_aplicar', 'UPDATE')
       OR has_table_privilege('app_decide', 'decision_sin_aplicar', 'DELETE') THEN
        RAISE EXCEPTION '0043: decision_sin_aplicar es append-only (app_decide)';
    END IF;
    IF has_table_privilege('app_admin', 'decision_sin_aplicar', 'UPDATE')
       OR has_table_privilege('app_admin', 'decision_sin_aplicar', 'DELETE') THEN
        RAISE EXCEPTION '0043: decision_sin_aplicar es append-only (app_admin)';
    END IF;
    IF has_table_privilege('app_ingest', 'decision_sin_aplicar', 'INSERT') THEN
        RAISE EXCEPTION '0043: app_ingest no debe registrar no-applies';
    END IF;
    IF NOT has_table_privilege('app_read', 'decision_sin_aplicar', 'SELECT') THEN
        RAISE EXCEPTION '0043: app_read debe leer decision_sin_aplicar';
    END IF;
    IF NOT has_table_privilege('app_read', 'v_decision_huerfana', 'SELECT') THEN
        RAISE EXCEPTION '0043: app_read debe leer v_decision_huerfana';
    END IF;
END $$;

COMMIT;
