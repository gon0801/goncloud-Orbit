-- ---------------------------------------------------------------------------
-- SP-API 01 A.3 revision PR #241 — conteo de llamadas en ingest_run.
--
-- El brief de A.3 exige dejar el conteo (y la duracion, ya cubierta por
-- started/finished_at) en ingest_run, no solo en el stdout. Columna
-- anulable: las fuentes que aun no la reportan (orders) dejan NULL.
-- Es expansiva (solo ADD COLUMN + CHECK + COMMENT). No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE ingest_run ADD COLUMN llamadas INTEGER;

ALTER TABLE ingest_run
    ADD CONSTRAINT ingest_run_llamadas_no_negativas
    CHECK (llamadas IS NULL OR llamadas >= 0);

COMMENT ON COLUMN ingest_run.llamadas IS
    'Intentos HTTP del pase (la tasa se gasta aunque fallen). NULL en '
    'fuentes que aun no lo reportan.';

COMMIT;
