-- ---------------------------------------------------------------------------
-- SP-API 01 A.3 — observaciones diarias de Pricing v0 con Buy Box.
--
-- Un pase diario por los ASIN propios en `listing` (por plataforma):
-- ofertas (`IsBuyBoxWinner`, `ListingPrice`, `SellerId`,
-- `IsFulfilledByAmazon`) + precio competitivo como complemento. 0 ofertas
-- = fila con offers_count=0 y precios NULL (ausencia, no error, E/0.2).
--
-- Append-only real (patron A.2 F2): ni UPDATE ni DELETE ni TRUNCATE.
-- Es expansiva. No re-runnable.
-- NOTA: numero 0032 suponiendo que A.2b ocupa 0031 (PR #240 en revision);
-- si cambia, se renumera al merge.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE spapi_price_observation (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asin                 TEXT NOT NULL,
    platform             platform NOT NULL,
    metric_date          DATE NOT NULL,
    observed_at          TIMESTAMPTZ NOT NULL,
    own_listing_price    money_amount,
    own_listing_currency currency,
    buy_box_price        money_amount,
    buy_box_currency     currency,
    buy_box_seller_id    TEXT,
    buy_box_is_own       BOOLEAN,
    offers_count         INTEGER,
    fba_offers_count     INTEGER,
    lowest_price         money_amount,
    lowest_currency      currency,
    ingest_run_id        BIGINT REFERENCES ingest_run (id),
    CONSTRAINT spapi_price_clave_unica UNIQUE (asin, platform, observed_at),
    -- Regla 4: ningun precio sin su moneda (igual que listing).
    CONSTRAINT spapi_price_propio_con_moneda
        CHECK ((own_listing_price IS NULL) = (own_listing_currency IS NULL)),
    CONSTRAINT spapi_price_buybox_con_moneda
        CHECK ((buy_box_price IS NULL) = (buy_box_currency IS NULL)),
    CONSTRAINT spapi_price_minimo_con_moneda
        CHECK ((lowest_price IS NULL) = (lowest_currency IS NULL)),
    CONSTRAINT spapi_price_conteos_no_negativos
        CHECK (
            (offers_count IS NULL OR offers_count >= 0)
            AND (fba_offers_count IS NULL OR fba_offers_count >= 0)
        )
);

COMMENT ON TABLE spapi_price_observation IS
    'SP-API 01 A.3: pase diario de Pricing v0 por ASIN propio (ofertas + '
    'competitivo). 0 ofertas = fila con offers_count=0 y precios NULL. '
    'Un numero, una fuente: el bridge sigue mandando en precio/stock de '
    'listing (D2); esto son observaciones propias de Buy Box y competencia.';

COMMENT ON COLUMN spapi_price_observation.metric_date IS
    'Dia UTC de captura del pase (observed_at::date en la practica).';

COMMENT ON COLUMN spapi_price_observation.buy_box_is_own IS
    'NULL cuando no hay ganador de Buy Box o el seller propio es '
    'desconocido para el marketplace.';

-- F3: los conteos son totales, no de pagina. Fuente en orden:
-- Summary (TotalOfferCount; NumberOfOffers canal Amazon; LowestPrices
-- condicion New) y, solo si el Summary falta o no trae el dato, la pagina
-- de ofertas como respaldo declarado. Fase B puede leerlos como totales.
COMMENT ON COLUMN spapi_price_observation.offers_count IS
    'Total de ofertas: Summary.TotalOfferCount; respaldo: tamano de la pagina.';
COMMENT ON COLUMN spapi_price_observation.fba_offers_count IS
    'Ofertas con fulfillment Amazon: suma de OfferCount en '
    'Summary.NumberOfOffers con fulfillmentChannel=Amazon; respaldo: pagina.';
COMMENT ON COLUMN spapi_price_observation.lowest_price IS
    'Precio minimo New: Summary.LowestPrices (ListingPrice); respaldo: '
    'minimo de la pagina.';

CREATE INDEX spapi_price_por_plataforma
    ON spapi_price_observation (platform, metric_date);

-- Append-only real: ni UPDATE ni DELETE ni TRUNCATE (patron 0001/A.2 F2).
CREATE TRIGGER spapi_price_append_only
    BEFORE UPDATE OR DELETE ON spapi_price_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER spapi_price_append_only_truncate
    BEFORE TRUNCATE ON spapi_price_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Permisos minimos: ingesta escribe observaciones; lectura pura decide/read.
GRANT SELECT ON spapi_price_observation TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON spapi_price_observation TO app_ingest;
GRANT USAGE ON SEQUENCE spapi_price_observation_id_seq TO app_ingest;

COMMIT;
