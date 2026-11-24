-- sonda-aplicada: SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'apply_attempt' AND column_name IN ('bid_previo', 'ausencia_previa') HAVING count(*) = 2) THEN false ELSE NOT has_column_privilege('app_decide', 'public.apply_attempt', 'bid_previo', 'UPDATE') AND NOT has_column_privilege('app_decide', 'public.apply_attempt', 'ausencia_previa', 'UPDATE') AND has_column_privilege('app_decide', 'public.apply_attempt', 'ack', 'UPDATE') AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.apply_attempt'::regclass AND conname = 'attempt_evidencia_previa_una') AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_trigger t ON t.tgfoid = p.oid WHERE t.tgrelid = 'public.apply_attempt'::regclass AND t.tgname = 'apply_attempt_solo_sella_resultado' AND position('ausencia_previa' in p.prosrc) > 0) END
-- 0095 (ARREGLOS 05 B, diseno seccion B «Identidad, confirmacion y reversas»):
-- la evidencia de lo que Orbit leyo en Amazon justo antes de cada envio.
--
-- 1. apply_attempt.bid_previo NUMERIC(14,4): el bid que Orbit leyo vivo en la
--    hoja ANTES del PUT de una puja (la lectura que aporta TEMPORADA 01 E). Es
--    el unico valor al que la reversa de bid puede volver. La moneda es la de
--    la decision (decision.value_currency): una sola fuente.
-- 2. apply_attempt.ausencia_previa JSONB: la identidad (adGroupId, keywordText,
--    matchType) cuya ausencia demostro una lectura COMPLETA de lista antes del
--    POST de un negative. Sin ella, un negative que aparece despues de un POST
--    de respuesta perdida no se puede atribuir a Orbit.
-- 3. Las dos nacen con la fila del ledger y no cambian: el disparador
--    apply_attempt_solo_sella_resultado las agrega a la parte inmutable.
--    app_decide no las actualiza (su UPDATE sigue por columna: ack, resultado,
--    finished_at). A lo sumo una de las dos viaja en cada fila: un PUT de puja
--    no crea objetos y un POST de creacion no pisa una puja.
--
-- No hay backfill: en la lectura previa de produccion (docs/evidencia/
-- arreglos-05/B/previa.txt, 2026-11-24 UTC, rol de solo lectura) los 576
-- intentos estan cerrados, ningun negative fue confirmado por conciliacion y
-- ninguna decision de puja aplicada carece de old_value. Las filas anteriores
-- a esta migracion nacen con ambas NULL, que es lo que se sabe de ellas. Una
-- puja aplicada sin lectura previa guardada vuelve, al revertirse, a
-- decision.old_value: el unico valor registrado entonces (limite declarado
-- en docs/APPLY.md).
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0095_reversa_arreglos05_evidencia_previa.sql.

ALTER TABLE apply_attempt
    ADD COLUMN bid_previo      NUMERIC(14,4) NULL,
    ADD COLUMN ausencia_previa JSONB NULL,
    ADD CONSTRAINT attempt_evidencia_previa_una
        CHECK (bid_previo IS NULL OR ausencia_previa IS NULL);

-- El candado del ledger (0002) se reemplaza por el mismo texto mas las dos
-- columnas nuevas en la parte que nunca cambia.
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
           NEW.quota_cobrada, NEW.started_at, NEW.bid_previo, NEW.ausencia_previa)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.decision_id, OLD.seq, OLD.tipo, OLD.request_payload,
           OLD.quota_cobrada, OLD.started_at, OLD.bid_previo, OLD.ausencia_previa) THEN
        RAISE EXCEPTION
            'apply_attempt % (0095): de una fila del ledger SOLO se sellan '
            'ack/resultado/finished_at (NULL -> valor, una vez). El intento '
            'nace ANTES del HTTP, con su evidencia previa, y no se reescribe: '
            'corregir es insertar la fila del reintento (seq siguiente).', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN apply_attempt.bid_previo IS
    'ARREGLOS 05 B: el bid que Orbit leyo vivo en Amazon justo ANTES del PUT de '
    'este intento (lectura de TEMPORADA 01 E), NUMERIC(14,4) en la moneda de la '
    'decision. Unica fuente del valor al que vuelve la reversa de bid; no se usa '
    'el old_value de la cache, que puede ser NULL o distinto en el redondeo. '
    'NULL en un PUT anterior a 0095 o en un intento que no es de puja. Nace con '
    'la fila y no cambia (disparador apply_attempt_solo_sella_resultado).';
COMMENT ON COLUMN apply_attempt.ausencia_previa IS
    'ARREGLOS 05 B: evidencia durable de que una lectura COMPLETA de lista no '
    'encontro viva esta identidad (lectura, plataforma y adGroupId/keywordText/'
    'matchType) inmediatamente antes del POST de este intento. Un negative que '
    'aparece despues de un POST de respuesta perdida solo se confirma como '
    'efecto de Orbit si el intento abierto la trae. Ausencia previa mas '
    'aparicion posterior no prueba autoria absoluta frente a un creador externo '
    'concurrente: por eso la confirmacion se marca efecto_reconciliado, distinta '
    'de creacion_propia. Nace con la fila y no cambia.';
COMMENT ON CONSTRAINT attempt_evidencia_previa_una ON apply_attempt IS
    'ARREGLOS 05 B: a lo sumo una evidencia previa por intento. Un PUT de puja '
    'conserva el bid leido; un POST de creacion conserva la identidad ausente.';

DO $$
BEGIN
    IF NOT has_column_privilege('app_decide', 'public.apply_attempt', 'ack', 'UPDATE') THEN
        RAISE EXCEPTION '0095: app_decide debe seguir sellando ack';
    END IF;
    IF has_column_privilege('app_decide', 'public.apply_attempt', 'bid_previo', 'UPDATE')
       OR has_column_privilege('app_decide', 'public.apply_attempt', 'ausencia_previa', 'UPDATE') THEN
        RAISE EXCEPTION '0095: la evidencia previa nace con la fila (app_decide no la actualiza)';
    END IF;
    IF has_table_privilege('app_decide', 'public.apply_attempt', 'UPDATE') THEN
        RAISE EXCEPTION '0095: el UPDATE de apply_attempt sigue siendo por columna';
    END IF;
END $$;
