-- ---------------------------------------------------------------------------
-- SP-API 01 A.2 — observaciones append-only de Orders 2026-01-01 (acta 0.1).
--
-- Una fila por (orden, ultima actualizacion): la re-corrida con solape de
-- 1 dia no duplica (ON CONFLICT DO NOTHING cuenta como skip). Corregir =
-- insertar fila nueva (regla 5); sin purga (D7). Sin columnas de comprador
-- ni direccion: la ingesta jamas pide includedData=BUYER (cero PII).
--
-- Es expansiva: CREATE TABLE/TRIGGER/FUNCTION/GRANT. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE spapi_order_observation (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    amazon_order_id     TEXT NOT NULL,
    platform            platform NOT NULL,
    marketplace_id      TEXT NOT NULL,
    purchase_date       TIMESTAMPTZ,
    last_updated_time   TIMESTAMPTZ NOT NULL,
    order_status        TEXT,
    fulfillment_channel TEXT,
    sales_channel       TEXT,
    order_total_amount  money_amount,
    order_total_currency currency,
    number_of_items     INTEGER,
    api_version         TEXT NOT NULL DEFAULT '2026-01-01',
    observed_at         TIMESTAMPTZ NOT NULL,
    ingest_run_id       BIGINT REFERENCES ingest_run (id),
    CONSTRAINT spapi_order_clave_unica UNIQUE (amazon_order_id, last_updated_time),
    -- Regla 4: no existe un total sin su moneda (igual que listing).
    CONSTRAINT spapi_order_total_con_moneda
        CHECK ((order_total_amount IS NULL) = (order_total_currency IS NULL)),
    CONSTRAINT spapi_order_items_no_negativo
        CHECK (number_of_items IS NULL OR number_of_items >= 0)
);

COMMENT ON TABLE spapi_order_observation IS
    'SP-API 01 A.2: resumenes searchOrders 2026-01-01 append-only por '
    '(amazon_order_id, last_updated_time). La ventana diaria usa '
    'lastUpdatedAfter = max(last_updated_time) - 1 dia de solape; la '
    'primera corrida usa createdAfter = ahora - 30 dias. Sin PII: sin '
    'columnas de comprador ni direccion.';

COMMENT ON COLUMN spapi_order_observation.purchase_date IS
    'createdTime de Amazon; NULL cuando el resumen no lo trae (regla 3).';

COMMENT ON COLUMN spapi_order_observation.last_updated_time IS
    'lastUpdatedTime de Amazon: mitad de la clave de idempotencia con '
    'amazon_order_id (E/0.1).';

CREATE INDEX spapi_order_por_plataforma
    ON spapi_order_observation (platform, last_updated_time);

-- Tiempo fuente coherente en trigger con comparacion absoluta (no en CHECK
-- dependiente de sesion, regla del repo): una orden no se actualiza antes
-- de crearse. Solo cuando Amazon trae ambas fechas.
CREATE FUNCTION spapi_order_tiempo_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.purchase_date IS NOT NULL
        AND NEW.purchase_date > NEW.last_updated_time THEN
        RAISE EXCEPTION
            'spapi_order: purchase_date (%) posterior a last_updated_time (%)',
            NEW.purchase_date, NEW.last_updated_time
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION spapi_order_tiempo_coherente() IS
    'SP-API 01 A.2: purchase_date <= last_updated_time cuando ambas vienen; '
    'comparacion absoluta de timestamptz en trigger, no en CHECK.';

CREATE TRIGGER spapi_order_tiempo_coherente
    BEFORE INSERT ON spapi_order_observation
    FOR EACH ROW EXECUTE FUNCTION spapi_order_tiempo_coherente();

-- Permisos minimos: ingesta escribe observaciones; lectura pura decide/read.
GRANT SELECT ON spapi_order_observation TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON spapi_order_observation TO app_ingest;
GRANT USAGE ON SEQUENCE spapi_order_observation_id_seq TO app_ingest;

COMMIT;
