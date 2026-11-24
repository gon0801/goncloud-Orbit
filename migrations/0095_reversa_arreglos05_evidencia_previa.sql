-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0095 (ARREGLOS 05 B).
--
-- Devuelve el esquema al estado de 0094: reemplaza el disparador del ledger
-- por el texto de 0002 (sin las dos columnas en su parte inmutable), quita el
-- CHECK de evidencia unica y las columnas bid_previo y ausencia_previa de
-- apply_attempt.
--
-- Que conserva y que no:
--   1) ABORTA si hay un intento ABIERTO que ya lleva evidencia previa: al
--      quitar las columnas la conciliacion perdria lo unico que le permite
--      atribuir el efecto (un negative confirmado por LIST quedaria pendiente
--      para siempre). El mensaje dice como salir.
--   2) NO borra ni reescribe intentos: los cerrados siguen, solo sin su
--      evidencia previa. Una puja aplicada con lectura previa pierde asi su
--      valor comprobado y el binario anterior revierte con decision.old_value,
--      que es lo que hacia antes de 0095.
--
-- Binario compatible: el codigo anterior a 0095 no conoce las dos columnas y
-- nunca las nombra, asi que corre contra este esquema mientras la reversa no
-- se aplica. Por eso rollback.sh restaura el codigo ANTES de revertir las
-- migraciones. Limites conocidos con el binario anterior: confirma un negative
-- por identidad sin evidencia previa, sobrescribe el ID del ACK con el que
-- encuentre en la lectura de vuelta y revierte una puja al valor de la cache.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    abiertos integer;
BEGIN
    SELECT count(*) INTO abiertos
      FROM apply_attempt
     WHERE finished_at IS NULL
       AND (bid_previo IS NOT NULL OR ausencia_previa IS NOT NULL);
    IF abiertos > 0 THEN
        RAISE EXCEPTION
            'reversa 0095: hay % intento(s) abierto(s) con evidencia previa; sin la '
            'columna la conciliacion no podria atribuir su efecto. Deten las '
            'mutaciones, dejalos conciliar (ciclo con el binario de 0095) y '
            'repite la reversa', abiertos;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION apply_attempt_solo_sella_resultado() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'apply_attempt es el ledger de TODO lo que salio a Amazon: una '
            'fila no se borra. El intento fallido ES la evidencia de la '
            'secuencia sellada (regla 5).'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.ack IS DISTINCT FROM OLD.ack
       AND (OLD.ack IS NOT NULL OR NEW.ack IS NULL) THEN
        RAISE EXCEPTION
            'apply_attempt %: ack ya sellado o se intenta des-sellar: el '
            'sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.resultado IS DISTINCT FROM OLD.resultado
       AND (OLD.resultado IS NOT NULL OR NEW.resultado IS NULL) THEN
        RAISE EXCEPTION
            'apply_attempt %: resultado ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.finished_at IS DISTINCT FROM OLD.finished_at
       AND (OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL) THEN
        RAISE EXCEPTION
            'apply_attempt %: finished_at ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF ROW(NEW.id, NEW.decision_id, NEW.seq, NEW.tipo, NEW.request_payload,
           NEW.quota_cobrada, NEW.started_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.decision_id, OLD.seq, OLD.tipo, OLD.request_payload,
           OLD.quota_cobrada, OLD.started_at) THEN
        RAISE EXCEPTION
            'apply_attempt %: de una fila del ledger SOLO se sellan '
            'ack/resultado/finished_at (NULL -> valor, una vez). El intento '
            'nace ANTES del HTTP y no se reescribe: corregir es insertar la '
            'fila del reintento (seq siguiente).', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE apply_attempt DROP CONSTRAINT attempt_evidencia_previa_una;
ALTER TABLE apply_attempt DROP COLUMN ausencia_previa;
ALTER TABLE apply_attempt DROP COLUMN bid_previo;
