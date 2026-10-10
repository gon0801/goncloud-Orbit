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
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, dos triggers
-- prohibir_mutacion(), GRANT declarados y bloque DO que falla si falta o
-- sobra un privilegio. Sin trigger de kind: la ingesta resuelve la campana
-- por kind antes de insertar (el planeador casa external_id contra las
-- campanas; una campana desconocida salta la fila y se cuenta).
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE ads_placement_observation (
    platform         platform    NOT NULL,
    ad_entity_id     BIGINT      NOT NULL REFERENCES ad_entity (id),      -- campana
    placement        TEXT        NOT NULL,
    metric_date      DATE        NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    metric_currency  TEXT        NOT NULL,
    impressions      BIGINT,
    clicks           BIGINT,
    cost             NUMERIC,
    orders           BIGINT,
    ad_revenue       NUMERIC,
    source_report_id TEXT        NOT NULL,
    CONSTRAINT placement_vocabulario
        CHECK (placement IN ('arriba_de_busqueda', 'resto_de_busqueda',
                              'paginas_de_producto', 'fuera_de_amazon')),
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
