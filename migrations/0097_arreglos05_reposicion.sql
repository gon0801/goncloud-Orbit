-- sonda-aplicada: SELECT to_regclass('public.keyword_reposicion_intento') IS NOT NULL AND to_regclass('public.keyword_reposicion_intento_id_seq') IS NOT NULL AND (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'keyword_reposicion_intento' AND indexname = 'keyword_reposicion_intento_uno_abierto') = 1 AND EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid WHERE t.tgrelid = to_regclass('public.keyword_reposicion_intento') AND t.tgname = 'keyword_reposicion_intento_invariantes' AND position('lease_hasta' in p.prosrc) > 0)
-- =============================================================================
--  ARREGLOS 05 C - reposicion durable por fila - PostgreSQL 16
--
--  Ledger de intentos de reponer una keyword archivada (crear otra PAUSED) y
--  de revertir esa reposicion (archivar solo el ID repuesto propio). Una fila
--  por intento, escrita ANTES del HTTP, con el payload exacto inmutable, el
--  ACK y el ID durables antes del readback, y sello unico de resultado y
--  tiempos. La identidad y el payload no se reescriben: corregir es insertar
--  el intento siguiente (seq+1), igual que apply_attempt (0002).
--
--  Concurrencia: UNIQUE (archivo_id, seq) ordena la historia de la fila y el
--  indice unico parcial (archivo_id, tipo) WHERE finished_at IS NULL impide
--  dos intentos abiertos de la misma operacion. El lease por fila
--  (owner, lease_hasta, heartbeat_at) permite que un sucesor TOME un intento
--  abandonado y lo CONCILIE por lectura, sin emitir otro POST.
--
--  Invariantes temporales en trigger con now() (absoluto UTC), nunca en CHECK:
--  started_at/lease_hasta/heartbeat_at/finished_at no se inventan ni se
--  des-sellan. CHECK solo expresa la forma del sello (resultado y finished_at
--  viajan juntos; un 'ok' exige ACK e ID).
--
--  La lectura previa de produccion (docs/evidencia/arreglos-05/C/previa.txt,
--  2026-11-24 UTC, rol orbit_read, transaccion read_only) midio la tabla madre
--  con 0 filas, la secuencia en 0, ningun FK apuntando a ella y la tabla nueva
--  ausente. No hay backfill.
--
--  Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
--  transaccion (psql -1). No re-ejecutable. Reversa:
--  0097_reversa_arreglos05_reposicion.sql (aborta si ya hay intentos: el down
--  jamas borra el historial).
-- =============================================================================

CREATE TABLE keyword_reposicion_intento (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        archivo_id      BIGINT      NOT NULL REFERENCES keyword_archivo_manual (id),
        seq             INT         NOT NULL CHECK (seq > 0),
        tipo            TEXT        NOT NULL CHECK (tipo IN ('reponer', 'reversa')),
        owner           TEXT        NOT NULL CHECK (btrim(owner) <> ''),
        lease_hasta     TIMESTAMPTZ NOT NULL,
        request_payload JSONB       NOT NULL,
        ack             JSONB,
        external_id     TEXT,
        resultado       TEXT,
        started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        heartbeat_at    TIMESTAMPTZ,
        finished_at     TIMESTAMPTZ,
        UNIQUE (archivo_id, seq),
        CONSTRAINT intento_sello_completo
            CHECK ((finished_at IS NULL) = (resultado IS NULL)),
        CONSTRAINT intento_ok_con_ack_e_id
            CHECK (resultado IS DISTINCT FROM 'ok'
                   OR (ack IS NOT NULL AND external_id IS NOT NULL))
);

CREATE UNIQUE INDEX keyword_reposicion_intento_uno_abierto
    ON keyword_reposicion_intento (archivo_id, tipo)
 WHERE finished_at IS NULL;

CREATE FUNCTION keyword_reposicion_intento_invariantes() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento es el ledger de la reposicion: una '
            'fila no se borra; el intento fallido ES la evidencia (regla 5)'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.ack IS NOT NULL OR NEW.external_id IS NOT NULL
           OR NEW.resultado IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
            RAISE EXCEPTION
                'keyword_reposicion_intento %: nace como INTENCION antes del '
                'HTTP; ack, id, resultado y sello llegan despues', NEW.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.heartbeat_at IS NOT NULL THEN
            RAISE EXCEPTION
                'keyword_reposicion_intento %: un intento nuevo no trae heartbeat',
                NEW.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.started_at > now() THEN
            RAISE EXCEPTION
                'keyword_reposicion_intento %: started_at futuro (% > %)',
                NEW.id, NEW.started_at, now()
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.lease_hasta <= NEW.started_at THEN
            RAISE EXCEPTION
                'keyword_reposicion_intento %: el lease nace despues del intento '
                '(% <= %)', NEW.id, NEW.lease_hasta, NEW.started_at
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.finished_at IS NOT NULL THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: sellado, el intento es inmutable',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF ROW(NEW.id, NEW.archivo_id, NEW.seq, NEW.tipo, NEW.request_payload, NEW.started_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.archivo_id, OLD.seq, OLD.tipo, OLD.request_payload, OLD.started_at) THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: ni la identidad ni el payload se '
            'reescriben; corregir es insertar el intento siguiente (seq+1)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.ack IS DISTINCT FROM OLD.ack
       AND (OLD.ack IS NOT NULL OR NEW.ack IS NULL) THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: ack ya sellado o se intenta des-sellar',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.external_id IS DISTINCT FROM OLD.external_id
       AND (OLD.external_id IS NOT NULL OR NEW.external_id IS NULL) THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: external_id ya sellado o des-sellado',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.resultado IS DISTINCT FROM OLD.resultado
       AND (OLD.resultado IS NOT NULL OR NEW.resultado IS NULL) THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: resultado ya sellado o des-sellado',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.finished_at IS DISTINCT FROM OLD.finished_at
       AND (OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL) THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: finished_at ya sellado o des-sellado',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.heartbeat_at IS NOT NULL
       AND (NEW.heartbeat_at < OLD.started_at OR NEW.heartbeat_at > now()) THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: heartbeat fuera de [started_at, now()] '
            '(% < % o > %)', OLD.id, NEW.heartbeat_at, OLD.started_at, now()
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.lease_hasta <= OLD.started_at THEN
        RAISE EXCEPTION
            'keyword_reposicion_intento %: el lease no puede vencer antes de '
            'empezar (% <= %)', OLD.id, NEW.lease_hasta, OLD.started_at
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.finished_at IS NOT NULL THEN
        IF NEW.finished_at < OLD.started_at
           OR NEW.finished_at < COALESCE(NEW.heartbeat_at, OLD.started_at)
           OR NEW.finished_at > now() THEN
            RAISE EXCEPTION
                'keyword_reposicion_intento %: finished_at fuera de [inicio, now()] '
                '(% no cae en [%, now()])', OLD.id, NEW.finished_at, OLD.started_at
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER keyword_reposicion_intento_invariantes
    BEFORE INSERT OR UPDATE OR DELETE ON keyword_reposicion_intento
    FOR EACH ROW EXECUTE FUNCTION keyword_reposicion_intento_invariantes();
CREATE TRIGGER keyword_reposicion_intento_sin_truncate
    BEFORE TRUNCATE ON keyword_reposicion_intento
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE keyword_reposicion_intento IS
    'ARREGLOS 05 C: un intento de reponer una keyword archivada o de revertir '
    'esa reposicion. Nace ANTES del HTTP con su payload exacto; el ACK y el ID '
    'se guardan antes del readback y se sellan una sola vez con el resultado. '
    'Dos intentos abiertos de la misma operacion no pueden coexistir.';
COMMENT ON COLUMN keyword_reposicion_intento.archivo_id IS
    'Fila de keyword_archivo_manual que se repone o cuya reposicion se revierte.';
COMMENT ON COLUMN keyword_reposicion_intento.seq IS
    'Orden de los intentos de la fila, global entre reponer y reversa.';
COMMENT ON COLUMN keyword_reposicion_intento.tipo IS
    'reponer = crear la keyword PAUSED; reversa = archivar el ID repuesto propio.';
COMMENT ON COLUMN keyword_reposicion_intento.request_payload IS
    'Payload exacto enviado a Amazon; inmutable. Sin POST no hay payload de creacion.';
COMMENT ON COLUMN keyword_reposicion_intento.external_id IS
    'keywordId del ACK (reponer) o el ID repuesto que se archiva (reversa).';
COMMENT ON COLUMN keyword_reposicion_intento.lease_hasta IS
    'Vencimiento del lease del owner; un sucesor solo concilia, nunca reenvia.';
COMMENT ON COLUMN keyword_reposicion_intento.resultado IS
    'Sello unico: ok, ok:no_aplicado, fallo:rechazo, fallo:entidad_*, '
    'fallo:identidad_ocupada. NULL mientras el intento esta abierto.';

GRANT SELECT, INSERT ON keyword_reposicion_intento TO app_admin;
GRANT UPDATE (owner, lease_hasta, heartbeat_at, ack, external_id, resultado, finished_at)
    ON keyword_reposicion_intento TO app_admin;
GRANT SELECT ON keyword_reposicion_intento TO app_read;
-- El ALTER DEFAULT PRIVILEGES de 0001 da SELECT a todos los roles; este ledger
-- no es de la ingesta ni del ciclo (mismo REVOKE que 0066/0069/0078).
REVOKE SELECT ON keyword_reposicion_intento FROM app_ingest, app_decide;
GRANT USAGE, SELECT ON SEQUENCE keyword_reposicion_intento_id_seq TO app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_admin', 'keyword_reposicion_intento', 'INSERT')
       OR NOT has_column_privilege(
              'app_admin', 'keyword_reposicion_intento', 'resultado', 'UPDATE')
       OR has_column_privilege(
              'app_admin', 'keyword_reposicion_intento', 'request_payload', 'UPDATE')
       OR has_table_privilege('app_read', 'keyword_reposicion_intento', 'INSERT')
       OR NOT has_table_privilege('app_read', 'keyword_reposicion_intento', 'SELECT')
       OR has_table_privilege('app_ingest', 'keyword_reposicion_intento', 'SELECT')
       OR has_table_privilege('app_decide', 'keyword_reposicion_intento', 'SELECT') THEN
        RAISE EXCEPTION '0097: privilegios de keyword_reposicion_intento distintos de 0078';
    END IF;
END;
$$;
