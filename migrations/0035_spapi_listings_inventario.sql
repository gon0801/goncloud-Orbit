-- ---------------------------------------------------------------------------
-- SP-API 01 A.4 — estado de listings e inventario FBA (observaciones propias).
--
-- D2 (cerrada por el dueno): el bridge sigue mandando en precio/stock de
-- `listing`; estas tablas guardan ESTADO del listing e INVENTARIO como
-- observaciones append-only. Ninguna escritura a `listing` en esta tarea.
--
-- spapi_listing_estado_observation: un GET por seller_sku
-- (/listings/2021-08-01/items/{sellerId}/{sku}, E/0.3); el estado sale de
-- summaries[0] del marketplace pedido. spapi_inventario_observation:
-- recorrido completo de /fba/inventory/v1/summaries (E/0.4).
--
-- Es expansiva (solo CREATE). No re-runnable. Sin reversa: tablas nuevas;
-- la reversa operativa es apagar la ingesta y las tablas se conservan.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE spapi_listing_estado_observation (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_sku           TEXT NOT NULL,
    asin                 TEXT,
    platform             platform NOT NULL,
    status               TEXT,
    product_type         TEXT,
    last_updated_date    TIMESTAMPTZ,
    api_version          TEXT NOT NULL,
    observed_at          TIMESTAMPTZ NOT NULL,
    ingest_run_id        BIGINT REFERENCES ingest_run (id),
    CONSTRAINT spapi_listing_estado_clave_unica
        UNIQUE (seller_sku, platform, observed_at)
);

COMMENT ON TABLE spapi_listing_estado_observation IS
    'SP-API 01 A.4: estado del listing propio por SKU (Listings Items '
    '2021-08-01). Observacion, no fuente de precio/stock (D2: el bridge '
    'manda en listing).';

COMMENT ON COLUMN spapi_listing_estado_observation.seller_sku IS
    'SKU del vendedor: identidad del listing con platform + observed_at.';

COMMENT ON COLUMN spapi_listing_estado_observation.asin IS
    'ASIN del summary; NULL si Amazon no lo trae (regla 3).';

COMMENT ON COLUMN spapi_listing_estado_observation.status IS
    'Estado del summary del marketplace pedido, TEXT sin enum inventado. '
    'Forma escalar (OPEN/CLOSED del brief) tal cual; si Amazon manda lista '
    '(modelo oficial: BUYABLE/DISCOVERABLE), join ordenado por coma. El '
    'acta 0.3 no pino el tipo: pendiente confirmarlo en sonda.';

COMMENT ON COLUMN spapi_listing_estado_observation.product_type IS
    'productType del summary; NULL si no viene.';

COMMENT ON COLUMN spapi_listing_estado_observation.last_updated_date IS
    'lastUpdatedDate del summary; NULL si ausente o ilegible.';

CREATE INDEX spapi_listing_estado_por_plataforma
    ON spapi_listing_estado_observation (platform, observed_at);

-- Append-only real: ni UPDATE ni DELETE ni TRUNCATE (patron 0001/A.2 F2).
CREATE TRIGGER spapi_listing_estado_append_only
    BEFORE UPDATE OR DELETE ON spapi_listing_estado_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER spapi_listing_estado_append_only_truncate
    BEFORE TRUNCATE ON spapi_listing_estado_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Permisos minimos: ingesta escribe observaciones; lectura pura decide/read.
GRANT SELECT ON spapi_listing_estado_observation TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON spapi_listing_estado_observation TO app_ingest;
GRANT USAGE ON SEQUENCE spapi_listing_estado_observation_id_seq TO app_ingest;

CREATE TABLE spapi_inventario_observation (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_sku           TEXT NOT NULL,
    asin                 TEXT,
    fn_sku               TEXT,
    platform             platform NOT NULL,
    metric_date          DATE NOT NULL,
    observed_at          TIMESTAMPTZ NOT NULL,
    total_quantity       INTEGER NOT NULL,
    fulfillable_quantity INTEGER,
    api_version          TEXT NOT NULL,
    ingest_run_id        BIGINT REFERENCES ingest_run (id),
    CONSTRAINT spapi_inventario_clave_unica
        UNIQUE (seller_sku, platform, observed_at),
    -- total_quantity es el dato de la tabla (ausente = fila no escrita en
    -- codigo); el CHECK lo blinda en la base. fulfillable puede faltar
    -- (solo viene con details=true, que no pedimos) y queda NULL.
    CONSTRAINT spapi_inventario_cantidades_no_negativas
        CHECK (
            total_quantity >= 0
            AND (fulfillable_quantity IS NULL OR fulfillable_quantity >= 0)
        )
);

COMMENT ON TABLE spapi_inventario_observation IS
    'SP-API 01 A.4: inventario FBA por SKU (summaries v1, granularity '
    'Marketplace). Observacion, no fuente de stock (D2).';

COMMENT ON COLUMN spapi_inventario_observation.seller_sku IS
    'SKU del vendedor: identidad del inventario con platform + observed_at.';

COMMENT ON COLUMN spapi_inventario_observation.fn_sku IS
    'fnSku de Amazon; NULL si no viene (regla 3).';

COMMENT ON COLUMN spapi_inventario_observation.metric_date IS
    'Dia UTC de captura del pase: el trigger '
    'spapi_inventario_tiempo_coherente exige metric_date = '
    '(observed_at AT TIME ZONE ''UTC'')::date.';

COMMENT ON COLUMN spapi_inventario_observation.total_quantity IS
    'totalQuantity del summary: el dato de la tabla; sin el la fila no se '
    'escribe (skip contado).';

COMMENT ON COLUMN spapi_inventario_observation.fulfillable_quantity IS
    'Cantidad disponible cuando Amazon la trae (inventoryDetails solo viene '
    'con details=true, que el pase no pide): NULL en la practica.';

-- Invariante de tiempo en trigger con UTC fijado EN LA EXPRESION (patron
-- 0032 spapi_price_tiempo_coherente; NUNCA en CHECK: observed_at::date se
-- evaluaria con la TimeZone de cada sesion).
CREATE FUNCTION spapi_inventario_tiempo_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.metric_date <> (NEW.observed_at AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION
            'spapi_inventario: metric_date (%) no es el dia UTC de observed_at (%)',
            NEW.metric_date, NEW.observed_at
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION spapi_inventario_tiempo_coherente() IS
    'SP-API 01 A.4: metric_date = dia UTC de observed_at. UTC fijado en la '
    'expresion (AT TIME ZONE ''UTC''): inmune a la TimeZone de la sesion.';

CREATE TRIGGER spapi_inventario_tiempo_coherente
    BEFORE INSERT ON spapi_inventario_observation
    FOR EACH ROW EXECUTE FUNCTION spapi_inventario_tiempo_coherente();

-- Append-only real: ni UPDATE ni DELETE ni TRUNCATE (patron 0001/A.2 F2).
CREATE TRIGGER spapi_inventario_append_only
    BEFORE UPDATE OR DELETE ON spapi_inventario_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER spapi_inventario_append_only_truncate
    BEFORE TRUNCATE ON spapi_inventario_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Permisos minimos: ingesta escribe observaciones; lectura pura decide/read.
GRANT SELECT ON spapi_inventario_observation TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON spapi_inventario_observation TO app_ingest;
GRANT USAGE ON SEQUENCE spapi_inventario_observation_id_seq TO app_ingest;

COMMIT;
