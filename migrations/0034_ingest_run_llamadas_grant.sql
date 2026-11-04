-- ---------------------------------------------------------------------------
-- SP-API 01 A.6 — GRANT faltante: app_ingest sella ingest_run.llamadas
--
-- Bug de produccion (primer pase spapi_pricing, 2026-09-10 UTC): 0001 otorga
-- el UPDATE de ingest_run a app_ingest POR COLUMNA (finished_at,
-- rows_written, rows_skipped, skip_reason, ok; linea 1471 de 0001). Las
-- columnas nuevas no heredan grants por columna: 0033 agrego `llamadas` sin
-- su GRANT y el sello del pase (UPDATE ... llamadas = ...) reviento con
-- "permission denied for table ingest_run" (corridas 145 y 147 quedaron
-- abiertas con los datos ya escritos).
--
-- Expansiva, no toca datos. CI: test_grant_update_llamadas_app_ingest en
-- tests/test_spapi_pricing.py (demostrado en rojo sin esta migracion).
-- ---------------------------------------------------------------------------

BEGIN;

GRANT UPDATE (llamadas) ON ingest_run TO app_ingest;

-- Verificacion: el sello de pricing necesita UPDATE en las seis columnas.
DO $$
BEGIN
    IF NOT has_column_privilege('app_ingest', 'ingest_run', 'llamadas', 'UPDATE') THEN
        RAISE EXCEPTION '0034: app_ingest sigue sin UPDATE en ingest_run.llamadas';
    END IF;
END $$;

COMMIT;
