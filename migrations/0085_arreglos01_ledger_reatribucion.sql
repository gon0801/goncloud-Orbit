-- sonda-aplicada: SELECT to_regprocedure('public.ledger_solo_reatribuye()') IS NOT NULL
-- 0085 (ARREGLOS 01 Y.2, hallazgo H-H4): corregir el producto de un ASIN mueve
-- sus ventas pasadas y deja rastro.
--
-- ledger_event es append-only (prohibir_mutacion). La unica mutacion nueva y
-- acotada es corregir product_id de una venta de Amazon cuando el mapa
-- ASIN -> listing cambio: solo de producto a producto, solo ventas y solo
-- Amazon. El trigger escribe la auditoria en ledger_reatribucion, asi que la
-- auditoria no se puede omitir. Monto, fecha y cantidad siguen inmodificables.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0085_reversa_arreglos01_ledger_reatribucion.sql.

CREATE TABLE ledger_reatribucion (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ledger_event_id BIGINT NOT NULL REFERENCES ledger_event(id),
    product_antes   BIGINT NOT NULL REFERENCES product(id),
    product_despues BIGINT NOT NULL REFERENCES product(id),
    ingest_run_id   BIGINT REFERENCES ingest_run(id),
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (product_antes <> product_despues)
);

CREATE INDEX ON ledger_reatribucion (ledger_event_id);
CREATE INDEX ON ledger_reatribucion (ingest_run_id) WHERE ingest_run_id IS NOT NULL;

CREATE TRIGGER ledger_reatribucion_append_only
    BEFORE UPDATE OR DELETE ON ledger_reatribucion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER ledger_reatribucion_append_only_truncate
    BEFORE TRUNCATE ON ledger_reatribucion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Sustituye ledger_append_only: el DELETE sigue prohibido; el UPDATE solo
-- admite product_id de una venta de Amazon, de un producto a otro. La
-- auditoria se escribe aqui mismo, dentro del candado.
CREATE FUNCTION ledger_solo_reatribuye() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_ingest_run_id BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'ledger_event es append-only: una venta se reatribuye, no se borra'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.kind <> 'sale'
       OR OLD.platform NOT IN ('amazon_mx', 'amazon_us')
       OR OLD.product_id IS NULL
       OR NEW.product_id IS NULL
       OR NEW.product_id = OLD.product_id
       OR (to_jsonb(NEW) - 'product_id') IS DISTINCT FROM (to_jsonb(OLD) - 'product_id') THEN
        RAISE EXCEPTION
            'ledger_event: solo se reatribuye product_id de una venta de Amazon,'
            ' de un producto a otro producto'
            USING ERRCODE = 'restrict_violation';
    END IF;
    v_ingest_run_id := nullif(current_setting('orbit.ingest_run_id', true), '')::bigint;
    INSERT INTO ledger_reatribucion
        (ledger_event_id, product_antes, product_despues, ingest_run_id)
    VALUES (OLD.id, OLD.product_id, NEW.product_id, v_ingest_run_id);
    RETURN NEW;
END;
$$;

DROP TRIGGER ledger_append_only ON ledger_event;
CREATE TRIGGER ledger_solo_reatribuye
    BEFORE UPDATE OR DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION ledger_solo_reatribuye();

-- El rol de ingesta puede corregir SOLO product_id; el trigger acota que. Y
-- necesita INSERT en la auditoria: el trigger corre con los permisos de quien
-- hace el UPDATE.
GRANT UPDATE (product_id) ON ledger_event TO app_ingest;
GRANT INSERT ON ledger_reatribucion TO app_ingest;

COMMENT ON TABLE ledger_reatribucion IS
  'Auditoria append-only de las reatribuciones de product_id en ledger_event '
  '(ARREGLOS 01 Y, hallazgo H-H4). La escribe el trigger ledger_solo_reatribuye '
  'en el mismo UPDATE: product_antes / product_despues dicen de donde a donde '
  'se movio la venta.';

COMMENT ON FUNCTION prohibir_mutacion IS
  'Regla 5, hecha cumplir a nivel de motor. La diferencia entre una regla '
  'escrita en un documento -que el repo viejo YA TENIA y no sirvio de nada- y '
  'una que la base hace cumplir aunque el que escriba tenga todos los '
  'permisos. Excepciones deliberadas (documentadas en su tabla): ingest_run, '
  'optimizer_cycle, decision_application, ad_entity_state, ads_optimizer_goal, '
  'ads_optimizer_lock, harvest_job, apply_quota_state, catalogo. sku_cost '
  'tiene su propio candado, mas estrecho: sku_cost_solo_cierra_vigencia. '
  'ledger_event tambien: ledger_solo_reatribuye solo corrige product_id de una '
  'venta de Amazon, de un producto a otro, y audita en ledger_reatribucion.';
