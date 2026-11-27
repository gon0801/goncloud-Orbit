-- sonda-aplicada: SELECT to_regclass('public.pausa_economica_evaluacion') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'decision_sin_aplicar_motivo_check' AND pg_get_constraintdef(oid) LIKE '%espera_reciente_economica%')
-- 0105 (ADS PROTECCION P03): pausa economica v2 (`economic_pause_v2`).
--
-- Que hace:
--   1) Crea `pausa_economica_evaluacion`, tabla append-only espejo de
--      `ubicacion_evaluacion` (0104): una fila por hoja juzgada y ciclo, cruce
--      o no la puerta madura. `entrada` congela los insumos de la regla pura
--      para el replay determinista de `tools/pausa_economica_verifica.py`.
--      `decision_id` puede ser NULL: en `shadow` es practica y nunca decide;
--      en `live` la fila existe aunque la pausa se retire por el cortacircuito
--      de 5, la gane `pause_umbral` o aplique cooldown (auditoria completa).
--   2) Agrega `espera_reciente_economica` al CHECK cerrado
--      `decision_sin_aplicar_motivo_check` (apendice al final, mismo orden que
--      `app.apply_cuota.MOTIVOS_SIN_APLICAR`).
--
-- Reglas de diseno:
--   - Append-only de verdad: triggers `prohibir_mutacion()` en
--     `BEFORE UPDATE OR DELETE` y `BEFORE TRUNCATE` (regla 5).
--   - Montos `NUMERIC(14,4)` + moneda `currency` (regla 4); `target_acos_pct`
--     es `NUMERIC(7,4)`.
--   - Vocabulario cerrado: un `pausar` es siempre `pause_economica`; un
--     `mantener` lleva uno de los cinco motivos de no-pausa de la regla pura.
--   - Roles de grupo reales (`app_read`, `app_decide`, `app_ingest`,
--     `app_admin`), nunca `orbit_*` ni `GRANT ALL`.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0105_reversa_pausa_economica_v2.sql (excluida del despliegue por
-- `*_reversa_*`, verify/Launch.md).

CREATE TABLE pausa_economica_evaluacion (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle_id          BIGINT      NOT NULL REFERENCES optimizer_cycle (id),
    ad_entity_id      BIGINT      NOT NULL REFERENCES ad_entity (id),
    platform          platform    NOT NULL CHECK (platform IN ('amazon_mx', 'amazon_us')),
    modo              TEXT        NOT NULL CHECK (modo IN ('shadow', 'live')),
    politica          TEXT        NOT NULL CHECK (politica = 'economic_pause_v2'),
    config_version_id BIGINT      NOT NULL REFERENCES config_version (id),
    decided_at        TIMESTAMPTZ NOT NULL,
    ventana_reciente_desde DATE   NOT NULL,
    ventana_reciente_hasta DATE   NOT NULL,
    moneda            currency    NOT NULL,
    target_acos_pct   NUMERIC(7,4) NOT NULL,
    cortes_completa   BOOLEAN     NOT NULL,
    cost_maduro       NUMERIC(14,4) NULL,
    revenue_maduro    NUMERIC(14,4) NULL,
    cost_reciente     NUMERIC(14,4) NULL,
    revenue_reciente  NUMERIC(14,4) NULL,
    exceso_combinado  NUMERIC(14,4) NULL,
    exceso_minimo     NUMERIC(14,4) NOT NULL,
    entrada           JSONB       NOT NULL,
    veredicto         TEXT        NOT NULL CHECK (veredicto IN ('mantener', 'pausar')),
    motivo            TEXT        NOT NULL CHECK (motivo <> ''),
    decision_id       BIGINT      NULL REFERENCES decision (id),
    CONSTRAINT pausa_economica_evaluacion_ventana
        CHECK (ventana_reciente_desde <= ventana_reciente_hasta),
    -- Vocabulario cerrado: un `pausar` es siempre `pause_economica`; un
    -- `mantener` lleva uno de los cinco motivos de no-pausa (Regla 3: dato
    -- faltante, umbral no cruzado, salvada, recuperando e incompleto son
    -- distintos). Espejo de app.optimizer.pausa_economica.MOTIVOS_MANTENER.
    CONSTRAINT pausa_economica_evaluacion_motivo_veredicto
        CHECK ((veredicto = 'pausar' AND motivo = 'pause_economica')
               OR (veredicto = 'mantener' AND motivo IN (
                       'pause_economica_salvada_reciente',
                       'pause_economica_recuperando_reciente',
                       'pause_economica_reciente_incompleto',
                       'pause_economica_no_cruza_madura',
                       'pause_economica_cortes_incompletos'))),
    -- Regla 3: un `mantener` no lleva decision; un `pausar` live puede no
    -- tenerla (cortacircuito, cooldown, otra pausa del ciclo).
    CONSTRAINT pausa_economica_evaluacion_veredicto_coherente
        CHECK ((veredicto = 'mantener' AND decision_id IS NULL)
               OR veredicto = 'pausar'),
    CONSTRAINT pausa_economica_evaluacion_modo_decision
        CHECK ((modo = 'shadow' AND decision_id IS NULL) OR modo = 'live'),
    UNIQUE (cycle_id, ad_entity_id)
);

CREATE INDEX pausa_economica_evaluacion_entidad_idx
    ON pausa_economica_evaluacion (platform, ad_entity_id, id DESC);

CREATE TRIGGER pausa_economica_evaluacion_append_only
    BEFORE UPDATE OR DELETE ON pausa_economica_evaluacion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER pausa_economica_evaluacion_append_only_truncate
    BEFORE TRUNCATE ON pausa_economica_evaluacion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE pausa_economica_evaluacion IS
  'ADS PROTECCION P03 (0105): evaluacion diaria de la pausa economica v2 por '
  'hoja. Append-only (regla 5). `entrada` congela los insumos completos '
  '(AgregadoMetricas de cortes y ventana reciente) para el replay '
  'determinista. v2 solo puede disparar donde v1 ya disparaba: la ventana '
  'inmadura unicamente QUITA pausas. `decision_id` es NULL en shadow y en un '
  'pausar live retirado por el cortacircuito de 5 o por cooldown.';

GRANT SELECT ON pausa_economica_evaluacion TO app_read, app_decide, app_ingest, app_admin;
GRANT INSERT ON pausa_economica_evaluacion TO app_decide;
GRANT USAGE, SELECT ON SEQUENCE pausa_economica_evaluacion_id_seq TO app_decide;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'pausa_economica_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'INSERT')
       OR has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'UPDATE')
       OR has_table_privilege('app_decide', 'pausa_economica_evaluacion', 'DELETE')
       OR has_table_privilege('app_read', 'pausa_economica_evaluacion', 'INSERT')
       OR has_table_privilege('app_ingest', 'pausa_economica_evaluacion', 'INSERT')
       OR NOT has_sequence_privilege('app_decide',
              'pausa_economica_evaluacion_id_seq', 'USAGE') THEN
        RAISE EXCEPTION '0105: privilegios de pausa_economica_evaluacion incorrectos';
    END IF;
END $$;

-- El CHECK cerrado de `decision_sin_aplicar` suma la espera de la pausa
-- economica. Apendice al final: mismo orden que MOTIVOS_SIN_APLICAR.
DO $$
DECLARE
    candidatos TEXT[];
BEGIN
    SELECT array_agg(conname) INTO candidatos
      FROM pg_constraint
     WHERE conrelid = 'decision_sin_aplicar'::regclass
       AND contype = 'c'
       AND conname = 'decision_sin_aplicar_motivo_check';
    IF candidatos IS NULL OR array_length(candidatos, 1) <> 1 THEN
        RAISE EXCEPTION
            '0105: se esperaba el CHECK nombrado decision_sin_aplicar_motivo_check';
    END IF;
    EXECUTE $check$
ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check
$check$;
    EXECUTE $check$
ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
        'espera_proteccion', 'bid_cambiado_fuera', 'vigencia_vencida',
        'tope_ciclo', 'espera_reciente_economica'
    ))
$check$;
END $$;

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0105 agrega espera_reciente_economica al final: la decision es una '
    'pausa economica cuya ventana reciente todavia no es medible; espera sin '
    'gastar cuota y se descarta al vencer TOPE_ESPERA_RECIENTE_DIAS (7).';

DO $$
DECLARE
    definicion TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO definicion
      FROM pg_constraint
     WHERE conrelid = 'decision_sin_aplicar'::regclass
       AND conname = 'decision_sin_aplicar_motivo_check';
    IF definicion IS NULL OR definicion NOT LIKE '%espera_reciente_economica%' THEN
        RAISE EXCEPTION '0105: el CHECK de motivos no admite espera_reciente_economica';
    END IF;
    IF definicion NOT LIKE '%tope_ciclo%' OR definicion NOT LIKE '%espera_proteccion%' THEN
        RAISE EXCEPTION '0105: el CHECK perdio motivos previos';
    END IF;
END $$;
