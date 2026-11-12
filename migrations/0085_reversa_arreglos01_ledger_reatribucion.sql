-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0085 (ARREGLOS 01 Y.2).
--
-- Devuelve ledger_event al candado append-only completo: quita el trigger
-- ledger_solo_reatribuye, vuelve a poner ledger_append_only y revoca el
-- UPDATE (product_id) de app_ingest.
--
-- Si ledger_reatribucion ya tiene filas, la tabla de auditoria SE CONSERVA:
-- es append-only y guarda el rastro de reatribuciones reales; borrarla
-- perderia la evidencia. Si esta vacia, se borra junto con su estructura.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF to_regclass('public.ledger_reatribucion') IS NULL THEN
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ledger_reatribucion) THEN
        DROP TABLE ledger_reatribucion;
    ELSE
        REVOKE INSERT ON ledger_reatribucion FROM app_ingest;
    END IF;
END
$$;

DROP TRIGGER ledger_solo_reatribuye ON ledger_event;
DROP FUNCTION ledger_solo_reatribuye();

CREATE TRIGGER ledger_append_only
    BEFORE UPDATE OR DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

REVOKE UPDATE (product_id) ON ledger_event FROM app_ingest;
