-- ---------------------------------------------------------------------------
-- 0062 — BIDS 02 V.2 (seccion 3): metricas por placement.
-- PostgreSQL 16.
--
-- Metricas por placement de cada campana. Tabla y corrida propias: meter
-- placements en ads_metric_observation crearia un tercer grano del mismo
-- dinero (el segundo ya duplico el gasto una vez). Bitemporal como las
-- demas. (Bloque "0061 VER LO INVISIBLE" de
-- docs/evidencia/bids-02/diseno/datos.sql: la tabla va intacta salvo dos
-- cambios que manda la guia V.2, cambios 1-2: el CHECK usa los cuatro
-- valores del tipo Ubicacion del bosquejo, y NO se crea top_of_search_is
-- porque Amazon rechazo esa columna con 400 en la sonda.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, triggers
-- prohibir_mutacion() (UPDATE/DELETE + TRUNCATE), GRANT declarados y
-- bloque DO que falla si falta o sobra un privilegio. Los sellos de kind,
-- moneda y fecha son funciones propias (ronda 3, B1): la ingesta ya
-- resuelve la campana por kind antes de insertar, pero la FK sola admite
-- cualquier entidad y el esquema es quien sella (regla 4).
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE ads_placement_observation (
    platform         platform    NOT NULL,
    ad_entity_id     BIGINT      NOT NULL REFERENCES ad_entity (id),      -- campana
    placement        TEXT        NOT NULL,
    metric_date      DATE        NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    metric_currency  currency    NOT NULL,
    impressions      BIGINT,
    clicks           BIGINT,
    cost             money_amount,
    orders           BIGINT,
    ad_revenue       money_amount,
    source_report_id TEXT        NOT NULL,
    CONSTRAINT placement_vocabulario
        CHECK (placement IN ('arriba_de_busqueda', 'resto_de_busqueda',
                              'paginas_de_producto', 'fuera_de_amazon')),
    CONSTRAINT placement_no_negativos CHECK (
        (cost IS NULL OR cost >= 0) AND
        (ad_revenue IS NULL OR ad_revenue >= 0) AND
        (impressions IS NULL OR impressions >= 0) AND
        (clicks IS NULL OR clicks >= 0) AND
        (orders IS NULL OR orders >= 0)
    ),
    CONSTRAINT placement_reporte_no_vacio
        CHECK (source_report_id <> ''),
    PRIMARY KEY (platform, ad_entity_id, placement, metric_date, observed_at)
);
-- El mapeo del texto de Amazon (placementClassification) a este vocabulario
-- vive en la frontera de ingesta; un valor desconocido salta la fila y se
-- cuenta (regla 3), no se inventa categoria.

-- Idempotencia de ingesta (patron metric_dedupe_reporte, de
-- 0020_ads_producto_metrica.sql:65-68): re-ingestar el MISMO reporte no
-- duplica observaciones aunque cambie observed_at. La ingesta hace
-- INSERT ... ON CONFLICT DO NOTHING contra este indice parcial y las filas
-- absorbidas cuentan como saltadas.
CREATE UNIQUE INDEX apo_dedupe_reporte
    ON ads_placement_observation
        (platform, ad_entity_id, placement, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL;

-- Patron harvest_excepcion_kind (0018): la FK sola admite cualquier entidad.
CREATE FUNCTION ads_placement_0062_kind() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'ads_placement_observation: ad_entity_id % no existe o no es kind=campaign',
            NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ads_placement_observation_kind
    BEFORE INSERT OR UPDATE ON ads_placement_observation
    FOR EACH ROW EXECUTE FUNCTION ads_placement_0062_kind();

-- Sello plataforma<->moneda (regla 4, patron search_term_observation: la
-- platform viene desnormalizada en la fila y se cruza contra la entidad).
CREATE FUNCTION ads_placement_0062_moneda() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_platform platform;
    v_esperada currency;
BEGIN
    SELECT e.platform INTO v_platform
      FROM ad_entity e
     WHERE e.id = NEW.ad_entity_id;
    IF v_platform IS DISTINCT FROM NEW.platform THEN
        RAISE EXCEPTION
            'ads_placement_observation: la fila declara plataforma % pero '
            'su entidad % es %.',
            NEW.platform, NEW.ad_entity_id, v_platform
            USING ERRCODE = 'check_violation';
    END IF;
    v_esperada := CASE v_platform
                      WHEN 'amazon_mx' THEN 'MXN'::currency
                      WHEN 'amazon_us' THEN 'USD'::currency
                      WHEN 'meli'      THEN 'MXN'::currency
                  END;
    IF NEW.metric_currency IS DISTINCT FROM v_esperada THEN
        RAISE EXCEPTION
            'ads_placement_observation: la plataforma % reporta sus '
            'metricas de ads en %, no en %.',
            v_platform, v_esperada, NEW.metric_currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ads_placement_observation_moneda_sellada
    BEFORE INSERT OR UPDATE ON ads_placement_observation
    FOR EACH ROW EXECUTE FUNCTION ads_placement_0062_moneda();

-- Invariante de tiempo en trigger con UTC fijado (nunca en CHECK): el
-- reporte trae hechos pasados; una fecha futura es descarga equivocada.
CREATE FUNCTION ads_placement_0062_fecha() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.metric_date > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION
            'ads_placement_observation: metric_date % es futura (hoy % UTC).',
            NEW.metric_date, (now() AT TIME ZONE 'UTC')::date
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ads_placement_observation_fecha_no_futura
    BEFORE INSERT OR UPDATE ON ads_placement_observation
    FOR EACH ROW EXECUTE FUNCTION ads_placement_0062_fecha();

CREATE TRIGGER ads_placement_observation_append_only
    BEFORE UPDATE OR DELETE ON ads_placement_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ads_placement_observation_append_only_truncate
    BEFORE TRUNCATE ON ads_placement_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE ads_placement_observation IS
  'BIDS 02 V.2 (0062): metricas diarias por placement de cada campana '
  '(reporte spCampaigns por campaignPlacement). Append-only y bitemporal: '
  'la PK incluye observed_at. Grano propio, separado de '
  'ads_metric_observation para no duplicar el gasto.';
COMMENT ON INDEX apo_dedupe_reporte IS
  'BIDS 02 V.2 (0062): re-ingestar el mismo reporte no duplica filas '
  '(ON CONFLICT DO NOTHING contra este indice parcial).';
COMMENT ON FUNCTION ads_placement_0062_kind IS
  'BIDS 02 V.2 (0062, ronda 3): la observacion de placements es de '
  'kind=campaign (la FK sola admite cualquier entidad).';
COMMENT ON FUNCTION ads_placement_0062_moneda IS
  'BIDS 02 V.2 (0062, ronda 3): la fila declara la plataforma de su '
  'entidad y la moneda de esa plataforma (regla 4).';
COMMENT ON FUNCTION ads_placement_0062_fecha IS
  'BIDS 02 V.2 (0062, ronda 3): metric_date no futura en UTC (el reporte '
  'trae hechos pasados).';

-- Solo app_ingest escribe: es el rol con que corre el reporte de
-- placements. SELECT explicito a lectura, admin e ingest (leccion V.1:
-- explicito como 0051, sin depender solo del privilegio por omision
-- de 0001).
GRANT SELECT ON ads_placement_observation TO app_read, app_admin;
GRANT SELECT ON ads_placement_observation TO app_ingest;
GRANT INSERT ON ads_placement_observation TO app_ingest;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'ads_placement_observation', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'ads_placement_observation', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_placement_observation', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_placement_observation', 'INSERT')
       OR has_table_privilege('app_ingest', 'ads_placement_observation', 'UPDATE')
       OR has_table_privilege('app_ingest', 'ads_placement_observation', 'DELETE')
       OR has_table_privilege('app_read', 'ads_placement_observation', 'INSERT')
       OR has_table_privilege('app_read', 'ads_placement_observation', 'UPDATE')
       OR has_table_privilege('app_read', 'ads_placement_observation', 'DELETE')
       OR has_table_privilege('app_admin', 'ads_placement_observation', 'INSERT')
       OR has_table_privilege('app_admin', 'ads_placement_observation', 'UPDATE')
       OR has_table_privilege('app_admin', 'ads_placement_observation', 'DELETE')
       OR has_table_privilege('app_decide', 'ads_placement_observation', 'INSERT')
       OR has_table_privilege('app_decide', 'ads_placement_observation', 'UPDATE')
       OR has_table_privilege('app_decide', 'ads_placement_observation', 'DELETE') THEN
        RAISE EXCEPTION '0062: privilegios de ads_placement_observation invalidos';
    END IF;
END
$$;

COMMIT;
