-- ADS PROTECCION 01 A.2: resultado auditable por reporte/perfil.
-- ingest_run conserva el resultado agregado; esta tabla es la fuente del
-- estado de cada unidad. Los eventos son append-only: downloaded significa
-- que la API entrego el reporte, written que la transaccion de metricas
-- termino. Solo written de una run ok=true acredita salud de ese reporte.
CREATE TABLE ads_report_result (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_run(id),
    profile_id BIGINT,
    platform platform,
    report_name TEXT,
    source_report_id TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('pending', 'downloaded', 'written', 'failed', 'rejected', 'global_failed')),
    reason TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ads_report_result_scope CHECK (
        (status = 'global_failed' AND profile_id IS NULL AND platform IS NULL
            AND report_name IS NULL)
        OR (status = 'rejected' AND report_name IS NULL)
        OR (status IN ('pending', 'downloaded', 'written', 'failed')
            AND profile_id IS NOT NULL AND platform IS NOT NULL
            AND report_name IS NOT NULL)
    ),
    CONSTRAINT ads_report_result_reason CHECK (
        (status IN ('failed', 'rejected', 'global_failed') AND reason IS NOT NULL)
        OR (status IN ('pending', 'downloaded', 'written') AND reason IS NULL)
    )
);

CREATE INDEX ads_report_result_salud_idx
    ON ads_report_result (platform, report_name, observed_at DESC)
    WHERE status = 'written';
CREATE INDEX ads_report_result_run_idx ON ads_report_result (ingest_run_id);

CREATE TRIGGER ads_report_result_append_only
    BEFORE UPDATE OR DELETE ON ads_report_result
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ads_report_result_append_only_truncate
    BEFORE TRUNCATE ON ads_report_result
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE ads_report_result IS
    'A.2: eventos append-only por perfil/plataforma/reporte Ads. downloaded no '
    'certifica escritura; written solo acredita salud si ingest_run.ok=true. '
    'pending cubre cada reporte esperado, incluso si un fallo impide intentarlo. '
    'global_failed conserva el fallo anterior a identificar perfil sin inventar scope.';

GRANT SELECT ON ads_report_result TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON ads_report_result TO app_ingest;
GRANT USAGE, SELECT ON SEQUENCE ads_report_result_id_seq TO app_ingest;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'ads_report_result', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_report_result', 'INSERT')
       OR has_table_privilege('app_ingest', 'ads_report_result', 'UPDATE')
       OR has_table_privilege('app_ingest', 'ads_report_result', 'DELETE') THEN
        RAISE EXCEPTION '0040: privilegios de ads_report_result invalidos';
    END IF;
END $$;
