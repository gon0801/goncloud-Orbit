-- ORBIT 19 B.3 -- disponibilidad comercial (Recommended) con TRES estados
-- distinguibles: cero observado / ausente / desconocido (AC8).
--
-- Fuentes verificadas en 0.3 (docs/evidencia/orbit-19/0.3/reporte.md, tabla
-- (c)/(d) y bridge-observado.txt):
--   FBA = amazon_fba_inventory.quantity_available (fresco el dia de la sonda).
--   FBM = amazon_listing_prices.quantity SOLO con fulfillment_channel=DEFAULT
--         (en AMAZON_NA quantity es SIEMPRE NULL: leerlo como 0 mentiria 359
--         filas).
--   amazon_inventory_cache PROHIBIDO (stale ~27d, 806/806 filas en cero).
--   FBA y FBM NO se suman: son canales distintos, cada uno con su fila.
-- Featured Offer: sin columna en el bridge (HITS=[]). Sin fuente = Sin
-- verificar; queda como ampliacion abierta, no bloquea seleccion.
--
-- Es expansiva: solo CREATE TYPE/CREATE TABLE/GRANTs. No re-runnable.

BEGIN;

CREATE TYPE disponibilidad_fuente AS ENUM ('fba', 'fbm');

-- Append-only (Regla 5): corregir es insertar una fila nueva con otro
-- observed_at. Sin UPDATE, sin DELETE.
CREATE TABLE disponibilidad_observation (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform    platform NOT NULL,
    seller_sku  TEXT NOT NULL,
    metric_date DATE NOT NULL,
    fuente      disponibilidad_fuente NOT NULL,
    -- Regla 3: NULL = desconocido, JAMAS un cero inventado. El cero observado
    -- (0 real del bridge) es un 0 escrito, no un NULL rellenado.
    quantity    BIGINT,
    -- Frescura del dato EN EL ORIGEN (fetched_at del bridge, UTC). La
    -- observacion de Orbit vive en observed_at.
    fetched_at  TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT disponibilidad_observation_anti_duplicado
        UNIQUE (platform, seller_sku, metric_date, fuente, observed_at),
    CONSTRAINT disponibilidad_quantity_no_negativo
        CHECK (quantity IS NULL OR quantity >= 0)
);

CREATE INDEX disponibilidad_observation_por_sku
    ON disponibilidad_observation (platform, seller_sku);

COMMENT ON TYPE disponibilidad_fuente IS
    'ORBIT 19 B.3: fba = amazon_fba_inventory.quantity_available; fbm = '
    'amazon_listing_prices.quantity con fulfillment_channel=DEFAULT. No se '
    'suman. amazon_inventory_cache prohibido (stale, todo cero).';
COMMENT ON TABLE disponibilidad_observation IS
    'ORBIT 19 B.3: snapshot append-only de disponibilidad por (platform, '
    'seller_sku, fuente). NULL quantity = desconocido; fila ausente tambien '
    'es desconocido; 0 es cero OBSERVADO. Tres estados distintos (AC8).';
COMMENT ON COLUMN disponibilidad_observation.quantity IS
    'NULL = desconocido. JAMAS se escribe 0 para representar dato faltante '
    '(regla 3): en AMAZON_NA el quantity del listing siempre viene NULL y '
    'leerlo como 0 inventaria un cero.';
COMMENT ON COLUMN disponibilidad_observation.fetched_at IS
    'UTC del bridge (fetched_at de la fila origen). Frescura de la fuente, '
    'no de la ingesta.';
COMMENT ON CONSTRAINT disponibilidad_observation_anti_duplicado
    ON disponibilidad_observation IS
    'ORBIT 19 B.3: idempotencia de la ingesta. La misma corrida (mismo '
    'observed_at) no duplica filas; una re-observacion posterior es una fila '
    'nueva (append-only, Regla 5).';

GRANT SELECT ON disponibilidad_observation TO app_read;
GRANT SELECT ON disponibilidad_observation TO app_ingest, app_decide, app_admin;
GRANT INSERT ON disponibilidad_observation TO app_ingest;
GRANT USAGE ON SEQUENCE disponibilidad_observation_id_seq TO app_ingest, app_decide, app_admin;

COMMIT;
