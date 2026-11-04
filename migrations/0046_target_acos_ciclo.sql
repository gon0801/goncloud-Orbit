-- ADS C.2a: el target de cada hoja queda CONGELADO POR CICLO. Una fila por
-- (ciclo, hoja) con el valor y la procedencia EXACTOS que consumio el motor,
-- capturados donde hoy se calcula la cascada — para toda hoja que llega al
-- calculo, incluidas las que luego salen por no-op o cooldown (no solo las
-- que dejan decision). Es el insumo del replay economico: leer el goal
-- vigente mide con el target de HOY un ciclo que decidio con el de ENTONCES.
-- Append-only por GRANTs e idempotente por PK (ON CONFLICT DO NOTHING).
-- NUMERIC sin escala: conserva el valor exacto usado, sin redondeo.
BEGIN;

CREATE TABLE target_acos_ciclo (
    cycle_id        BIGINT NOT NULL REFERENCES optimizer_cycle(id),
    ad_entity_id    BIGINT NOT NULL REFERENCES ad_entity(id),
    decided_at      TIMESTAMPTZ NOT NULL,
    target_acos_pct NUMERIC NOT NULL,
    procedencia     TEXT NOT NULL,
    CONSTRAINT target_acos_ciclo_procedencia_check CHECK (procedencia IN (
        'goal_campana', 'goal_plataforma', 'margen_plataforma',
        'setting_plataforma', 'cache_estado', 'default'
    )),
    PRIMARY KEY (cycle_id, ad_entity_id)
);

COMMENT ON TABLE target_acos_ciclo IS
  'Freeze del target por (ciclo, hoja): el valor y la procedencia que la '
  'cascada entrego al motor EN ese ciclo, aunque la hoja no dejara decision '
  '(no-op y cooldown incluidos; las hojas que no llegaron al calculo —gates, '
  'veto, inerte— no tienen fila, igual que los ad groups). Append-only por '
  'GRANTs; el replay economico lee AQUI, jamas el goal vigente. Ciclos '
  'anteriores a 0046 sin fila quedan sin_target_historico: hueco conocido.';
COMMENT ON COLUMN target_acos_ciclo.decided_at IS
  'Reloj del ciclo (el mismo de sus decisiones): filas del ciclo comparten '
  'instante aunque la hoja no dejara decision.';
COMMENT ON COLUMN target_acos_ciclo.target_acos_pct IS
  'NUMERIC sin escala a proposito: el valor exacto que consumio decide_bid, '
  'sin redondeo de schema (regla 2: un numero, una fuente).';
COMMENT ON COLUMN target_acos_ciclo.procedencia IS
  'Peldano ganador de la cascada, espejo sellado de '
  'app.optimizer.goals.PELDANOS_CASCADA (mismo orden; el test estatico y el '
  'de la constraint viva los mantienen sincronizados).';

-- Append-only por GRANTs (patron 0044): escribe SOLO el decididor (TX3 del
-- ciclo, junto a decision); leen auditoria (app_read) y el dueno (app_admin).
-- NADIE actualiza ni borra.
GRANT INSERT ON target_acos_ciclo TO app_decide;
GRANT SELECT ON target_acos_ciclo TO app_read, app_admin;

-- Candado de privilegios (patron 0044, solo has_* para no exigir membresia
-- de rol al migrar: la prueba de SET ROLE vive en test_apply_schema).
DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'target_acos_ciclo', 'INSERT') THEN
        RAISE EXCEPTION '0046: app_decide debe poder congelar el target del ciclo';
    END IF;
    IF has_table_privilege('app_decide', 'target_acos_ciclo', 'UPDATE')
       OR has_table_privilege('app_decide', 'target_acos_ciclo', 'DELETE') THEN
        RAISE EXCEPTION '0046: target_acos_ciclo es append-only (app_decide)';
    END IF;
    IF has_table_privilege('app_admin', 'target_acos_ciclo', 'UPDATE')
       OR has_table_privilege('app_admin', 'target_acos_ciclo', 'DELETE') THEN
        RAISE EXCEPTION '0046: target_acos_ciclo es append-only (app_admin)';
    END IF;
    IF has_table_privilege('app_ingest', 'target_acos_ciclo', 'INSERT') THEN
        RAISE EXCEPTION '0046: app_ingest no debe congelar targets';
    END IF;
    IF NOT has_table_privilege('app_read', 'target_acos_ciclo', 'SELECT') THEN
        RAISE EXCEPTION '0046: app_read debe leer target_acos_ciclo';
    END IF;
END $$;

COMMIT;
