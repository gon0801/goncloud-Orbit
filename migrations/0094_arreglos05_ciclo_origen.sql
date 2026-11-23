-- sonda-aplicada: SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'optimizer_cycle' AND column_name IN ('decision_committed_at', 'resume_of') HAVING count(*) = 2) THEN false ELSE has_column_privilege('app_decide', 'public.optimizer_cycle', 'decision_committed_at', 'UPDATE') AND NOT has_column_privilege('app_decide', 'public.optimizer_cycle', 'resume_of', 'UPDATE') AND EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'public.optimizer_cycle'::regclass AND tgname = 'optimizer_cycle_0094_progreso') AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('public.decision_sin_aplicar') AND conname = 'decision_sin_aplicar_motivo_check' AND pg_get_constraintdef(oid) LIKE '%vigencia_vencida%') END
-- 0094 (ARREGLOS 05 A.2, diseno seccion A «Recuperacion del ciclo y aislamiento
-- de fallos»): el progreso durable del ciclo y la identidad de su ejecutor.
--
-- 1. optimizer_cycle.decision_committed_at: el sello UNICO de la TX3 del ciclo
--    (la transaccion que inserta sus decisiones), aunque el lote valido traiga
--    cero. NULL significa que ese envelope nunca comprometio decisiones: murio
--    antes de TX3, fallo, o salio `skipped` (escalera off). Se escribe en la
--    MISMA sentencia que lo cierra como done o degraded y admite una sola
--    transicion (NULL a valor), por disparador. Un ciclo cerrado sin sello
--    pertenece al binario anterior a esta migracion.
-- 2. optimizer_cycle.resume_of: el ciclo de ORIGEN de un ejecutor de
--    recuperacion. NULL = el envelope es dueno de sus decisiones. Solo se fija
--    en el INSERT, apunta a un origen de la misma plataforma con TX3
--    comprometida y nunca a otro ejecutor: no hay cadenas de recuperaciones.
-- 3. decision_sin_aplicar admite `vigencia_vencida`: lo ordinario que nunca se
--    intento deja de ser elegible al cambiar el dia de decision, con un
--    desenlace auditable en vez de quedar como huerfano.
-- 4. Permisos minimos: app_decide actualiza SOLO decision_committed_at. No
--    hay grant sobre resume_of (se fija en el INSERT) ni ampliacion del UPDATE
--    general de la tabla.
--
-- Sello del dia del despliegue. Si el ciclo de hoy ya corrio con el binario
-- anterior, ese envelope no lleva sello y el horario siguiente lo tomaria por
-- una caida antes de TX3: volveria a decidir el mismo dia. Se sellan, con la
-- hora de su cierre, UNICAMENTE los ciclos cerrados HOY (UTC) que traen la
-- evidencia inequivoca de TX3: estado done o degraded, finished_at, y
-- decisions_count igual a las filas de decision. La historia anterior al dia
-- de la migracion NO se sella: conserva su clasificacion (docs/evidencia/
-- arreglos-05/A/previa.txt, bloques envelopes_*) y las reglas nuevas solo
-- gobiernan ciclos con sello.
--
-- Consulta previa (solo lectura, 2026-11-23 08:23 UTC, rol de lectura):
--   207 envelopes: 205 cerrados con evidencia de TX3 (175 live y 30 shadow) y 2
--   `skipped` del 2026-08-23; 0 abiertos y 0 con conteo distinto. En las 2,923
--   decisiones, decided_at cae el mismo dia UTC que el cierre de su ciclo.
--   Motivos de decision_sin_aplicar en uso: modo_no_live (190) y fuera_de_cap
--   (3); ningun vigencia_vencida.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0094_reversa_arreglos05_ciclo_origen.sql.

ALTER TABLE optimizer_cycle
    ADD COLUMN decision_committed_at TIMESTAMPTZ NULL,
    ADD COLUMN resume_of             BIGINT NULL REFERENCES optimizer_cycle(id);

DO $$
DECLARE
    sellados integer;
BEGIN
    UPDATE optimizer_cycle c
       SET decision_committed_at = c.finished_at
     WHERE c.decision_committed_at IS NULL
       AND c.status IN ('done', 'degraded')
       AND c.finished_at IS NOT NULL
       AND c.decisions_count IS NOT DISTINCT FROM
           (SELECT count(*) FROM decision d WHERE d.cycle_id = c.id)
       AND (c.finished_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date;
    GET DIAGNOSTICS sellados = ROW_COUNT;
    RAISE NOTICE '0094: ciclos de hoy cerrados por TX3, sellados con su hora de cierre: %', sellados;
END $$;

CREATE FUNCTION optimizer_cycle_0094_progreso() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    origen optimizer_cycle%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.decision_committed_at IS NOT NULL THEN
            RAISE EXCEPTION
                'optimizer_cycle_0094: el sello decision_committed_at no nace en el '
                'INSERT; se fija al cerrar la TX3 del ciclo'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.resume_of IS NOT NULL THEN
            SELECT * INTO origen FROM optimizer_cycle WHERE id = NEW.resume_of;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'optimizer_cycle_0094: resume_of apunta a un ciclo que no existe (%)',
                    NEW.resume_of
                    USING ERRCODE = 'check_violation';
            END IF;
            IF origen.resume_of IS NOT NULL THEN
                RAISE EXCEPTION
                    'optimizer_cycle_0094: resume_of apunta al origen (resume_of NULL), '
                    'nunca a otro ejecutor: no hay cadenas de recuperaciones'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF origen.platform IS DISTINCT FROM NEW.platform THEN
                RAISE EXCEPTION
                    'optimizer_cycle_0094: el ejecutor es de la misma plataforma que su origen'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF origen.decision_committed_at IS NULL THEN
                RAISE EXCEPTION
                    'optimizer_cycle_0094: solo se recupera un origen con la TX3 comprometida'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.resume_of IS DISTINCT FROM OLD.resume_of THEN
        RAISE EXCEPTION 'optimizer_cycle_0094: resume_of se fija en el INSERT y no cambia'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.decision_committed_at IS DISTINCT FROM OLD.decision_committed_at THEN
        IF OLD.decision_committed_at IS NOT NULL THEN
            RAISE EXCEPTION
                'optimizer_cycle_0094: decision_committed_at tiene una sola transicion '
                '(NULL a valor)'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status NOT IN ('done', 'degraded') OR NEW.finished_at IS NULL THEN
            RAISE EXCEPTION
                'optimizer_cycle_0094: el sello se escribe en el cierre de TX3 '
                '(status done o degraded, con finished_at)'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.decision_committed_at > now() THEN
            RAISE EXCEPTION 'optimizer_cycle_0094: el sello de TX3 no puede ser del futuro'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.decision_committed_at < NEW.started_at THEN
            RAISE EXCEPTION
                'optimizer_cycle_0094: el sello de TX3 no puede ser anterior al inicio del ciclo'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER optimizer_cycle_0094_progreso
    BEFORE INSERT OR UPDATE ON optimizer_cycle
    FOR EACH ROW EXECUTE FUNCTION optimizer_cycle_0094_progreso();

GRANT UPDATE (decision_committed_at) ON optimizer_cycle TO app_decide;

-- El vocabulario cerrado de desenlaces (espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR,
-- mismo orden) admite vigencia_vencida al final: el mismo DROP/ADD CONSTRAINT
-- de 0081 y 0093.
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
            '0094: se esperaba el CHECK nombrado decision_sin_aplicar_motivo_check';
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
        'espera_proteccion', 'bid_cambiado_fuera', 'vigencia_vencida'
    ))
$check$;
END $$;

COMMENT ON COLUMN optimizer_cycle.decision_committed_at IS
    'Sello unico de la TX3 del ciclo: la transaccion que inserta sus decisiones, '
    'aunque el lote valido traiga cero. NULL = el envelope nunca comprometio '
    'decisiones (muerte antes de TX3, fallo, skipped, o binario anterior a 0094). '
    'Una sola transicion NULL a valor, siempre en la sentencia que cierra el '
    'ciclo como done o degraded (disparador optimizer_cycle_0094_progreso). Es '
    'la fuente unica de «hoy ya se decidio»: el horario siguiente no abre otro '
    'origen, y si quedan decisiones sin desenlace abre un ejecutor (resume_of).';
COMMENT ON COLUMN optimizer_cycle.resume_of IS
    'Ciclo de ORIGEN de un ejecutor de recuperacion (ARREGLOS 05 A.2). NULL = el '
    'envelope es dueno de sus decisiones. Solo en el INSERT; el origen es de la '
    'misma plataforma, con TX3 comprometida y sin su propio resume_of: nunca una '
    'cadena de recuperaciones. El ejecutor no decide (decisions_count 0) y '
    'decision_application.applied_cycle_id conserva al ejecutor que confirmo. '
    'La referencia tambien viaja en notes.resume_of, que es donde sobrevive a la '
    'reversa de la migracion.';
COMMENT ON FUNCTION optimizer_cycle_0094_progreso() IS
    'Invariantes de progreso del ciclo (ARREGLOS 05 A.2): el sello de TX3 no nace '
    'en el INSERT, tiene una sola transicion, se escribe solo al cerrar como done '
    'o degraded y no es del futuro ni anterior al inicio; resume_of solo en el '
    'INSERT, hacia un origen de la misma plataforma con TX3 comprometida. El '
    'binario anterior a 0094 cierra sus ciclos sin sello y sigue pasando: un '
    'rollback de codigo no lo rompe.';
COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0094 agrega vigencia_vencida: la decision ordinaria que nunca se '
    'intento deja de ser elegible al cambiar el dia en que se decidio. No se '
    'borra la decision ni se toca la que ya tiene un intento: esa solo la '
    'resuelve la conciliacion, leyendo.';

DO $$
BEGIN
    IF NOT has_column_privilege(
        'app_decide', 'public.optimizer_cycle', 'decision_committed_at', 'UPDATE'
    ) THEN
        RAISE EXCEPTION '0094: app_decide debe poder sellar decision_committed_at';
    END IF;
    IF has_column_privilege('app_decide', 'public.optimizer_cycle', 'resume_of', 'UPDATE') THEN
        RAISE EXCEPTION '0094: resume_of se fija en el INSERT (app_decide no lo actualiza)';
    END IF;
    IF has_table_privilege('app_decide', 'public.optimizer_cycle', 'UPDATE') THEN
        RAISE EXCEPTION '0094: el UPDATE de optimizer_cycle sigue siendo por columna';
    END IF;
END $$;
