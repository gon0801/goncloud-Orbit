-- =============================================================================
--  ORBIT 19 B.1 — Metricas Ads por PRODUCTO ANUNCIADO (spAdvertisedProduct).
--
--  Tabla append-only ads_product_metric_observation a grano
--  (platform, advertised_asin, advertised_sku, metric_date). NO es ad_entity:
--  el producto anunciado no tiene external_id de entidad en la API, y el
--  agregado de campana JAMAS se reparte entre productos (politica 0.4 §2:
--  filas del mismo ASIN en varias campanas se SUMAN hacia la clave del
--  esquema). Migracion expansiva y NO re-runnable (estilo 0001-0019).
--
--  Invariante "observed_at >= metric_date": igual que ads_metric_observation,
--  NO vive en un CHECK (un cast date::timestamptz en CHECK se evalua segun la
--  TimeZone de cada sesion): lo valida la ingesta con el reloj de la DB y UTC
--  fijado (ver _SQL_FECHA_HOY y el guard de fecha futura en app/ads/reports).
--
--  campaign_ids/ad_ids son TRAZABILIDAD JSONB, no insumo de reparto.
-- =============================================================================

CREATE TABLE ads_product_metric_observation (
    platform          platform    NOT NULL,
    advertised_asin   TEXT        NOT NULL,
    advertised_sku    TEXT        NOT NULL,
    metric_date       DATE        NOT NULL,   -- el dia del HECHO
    observed_at       TIMESTAMPTZ NOT NULL,   -- el dia de la OBSERVACION
    metric_currency   currency    NOT NULL,
    impressions       BIGINT,
    clicks            BIGINT,
    cost              money_amount,
    purchases30d      NUMERIC(14, 4),
    sales30d          NUMERIC(14, 4),
    purchases_same_sku30d NUMERIC(14, 4),
    attributed_sales_same_sku_30d NUMERIC(14, 4),
    campaign_ids      JSONB,                  -- trazabilidad, NO para repartir
    ad_ids            JSONB,
    source_report_id  TEXT,
    ingest_run_id     BIGINT NOT NULL REFERENCES ingest_run(id),

    PRIMARY KEY (platform, advertised_asin, advertised_sku, metric_date, observed_at),

    CONSTRAINT apm_no_negativos CHECK (
        (impressions IS NULL OR impressions >= 0) AND
        (clicks IS NULL OR clicks >= 0) AND
        (cost IS NULL OR cost >= 0) AND
        (purchases30d IS NULL OR purchases30d >= 0) AND
        (sales30d IS NULL OR sales30d >= 0) AND
        (purchases_same_sku30d IS NULL OR purchases_same_sku30d >= 0) AND
        (attributed_sales_same_sku_30d IS NULL OR attributed_sales_same_sku_30d >= 0)
    ),
    -- Regla 4 espejo de metric_same_sku_cabe: lo promovido (mismo SKU) nunca
    -- puede exceder el total (que incluye halo).
    CONSTRAINT apm_same_sku_cabe CHECK (
        attributed_sales_same_sku_30d IS NULL OR sales30d IS NULL
        OR attributed_sales_same_sku_30d <= sales30d
    ),
    CONSTRAINT apm_purchases_same_sku_cabe CHECK (
        purchases_same_sku30d IS NULL OR purchases30d IS NULL
        OR purchases_same_sku30d <= purchases30d
    )
);

-- Idempotencia de ingesta (patron metric_dedupe_reporte): re-ingestar el
-- MISMO reporte no duplica observaciones aunque cambie observed_at. La
-- ingesta hace INSERT ... ON CONFLICT DO NOTHING contra este indice parcial
-- y las filas absorbidas cuentan como rows_skipped con motivo en ingest_run.
CREATE UNIQUE INDEX apm_dedupe_reporte
    ON ads_product_metric_observation
        (platform, advertised_asin, advertised_sku, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL;

CREATE INDEX ON ads_product_metric_observation
    (platform, advertised_asin, advertised_sku, metric_date DESC, observed_at DESC);
CREATE INDEX ON ads_product_metric_observation (ingest_run_id);

COMMENT ON TABLE ads_product_metric_observation IS
  'ORBIT 19 B.1. Regla 5 (append-only): la PK incluye observed_at, cada '
  're-lectura de la misma fecha es una FILA NUEVA, no un UPDATE. Grano de '
  'comparacion (politica 0.4 §2): (platform, advertised_asin, advertised_sku) '
  'agregado por SUMA de filas compatibles del reporte spAdvertisedProduct; '
  'PROHIBIDO repartir cost/sales de campana entre ASIN. El gzip SP solo trae '
  'filas con actividad (sondeo 0.3): ASIN ausente = Sin datos, no cero.';

COMMENT ON COLUMN ads_product_metric_observation.metric_currency IS
  'Regla 4: la moneda es la del perfil (amazon_mx=MXN, amazon_us=USD); las '
  'filas del reporte NO la traen. La sella el trigger compartido '
  'metric_moneda_de_plataforma (redeclarado abajo con la rama de esta tabla).';

COMMENT ON COLUMN ads_product_metric_observation.purchases30d IS
  'Compras totales atribuidas (incluyen halo). NUMERIC, no INTEGER: asi lo '
  'declara el contrato B.1; un valor fraccionario de la API NO se truncaria '
  'en silencio.';

COMMENT ON COLUMN ads_product_metric_observation.campaign_ids IS
  'Trazabilidad de las campanas que aportaron filas a la clave (ids de la API '
  'como strings, sin repeticion). JAMAS insumo para repartir agregados de '
  'campana entre productos (politica 0.4).';

-- Sello plataforma<->moneda: se AMPLIA la funcion compartida de 0001 con la
-- rama de esta tabla (platform viene DESNORMALIZADA en la fila, igual que
-- search_term_observation, pero aqui no hay ad_entity que cruzar: el grano
-- ES la plataforma).
CREATE OR REPLACE FUNCTION metric_moneda_de_plataforma() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_platform platform;
    v_esperada currency;
BEGIN
    IF TG_TABLE_NAME = 'ads_metric_observation' THEN
        SELECT e.platform INTO v_platform
          FROM ad_entity e
         WHERE e.id = NEW.ad_entity_id;
    ELSIF TG_TABLE_NAME = 'search_term_observation' THEN
        SELECT e.platform INTO v_platform
          FROM ad_entity e
         WHERE e.id = NEW.ad_entity_id;
        IF v_platform IS DISTINCT FROM NEW.platform THEN
            RAISE EXCEPTION
                'search_term_observation: la fila declara plataforma % pero '
                'su entidad % es %.', NEW.platform, NEW.ad_entity_id, v_platform
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'ads_product_metric_observation' THEN
        -- grano por plataforma: la platform viene en la fila y NO hay
        -- ad_entity que cruzar (el producto anunciado no es entidad).
        v_platform := NEW.platform;
    ELSE
        RAISE EXCEPTION
            'metric_moneda_de_plataforma: tabla % no soportada por el sello',
            TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
    END IF;

    v_esperada := CASE v_platform
                      WHEN 'amazon_mx' THEN 'MXN'::currency
                      WHEN 'amazon_us' THEN 'USD'::currency
                      WHEN 'meli'      THEN 'MXN'::currency
                  END;

    IF NEW.metric_currency IS DISTINCT FROM v_esperada THEN
        RAISE EXCEPTION
            '%: la plataforma % reporta sus metricas de ads en %, no en %. '
            'Una moneda distinta aqui es el bug de 18.66x en estado larval.',
            TG_TABLE_NAME, v_platform, v_esperada, NEW.metric_currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER apm_metric_moneda_sellada
    BEFORE INSERT ON ads_product_metric_observation
    FOR EACH ROW EXECUTE FUNCTION metric_moneda_de_plataforma();

-- GRANTs (patron seccion 16 de 0001): los sincronizadores escriben, el resto
-- solo lee. Append-only por permisos: SIN UPDATE/DELETE para ningun rol.
GRANT SELECT ON ads_product_metric_observation
    TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON ads_product_metric_observation TO app_ingest;
