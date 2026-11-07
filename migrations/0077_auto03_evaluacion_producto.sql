-- sonda-aplicada: SELECT to_regclass('public.evaluacion_producto_ciclo') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'ads_report_result' AND column_name = 'cobertura_integra') AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'decision' AND column_name = 'evaluacion_producto_id')
-- 0077 (AUTO-03 D.1 y D.7): evaluacion por producto de cada ciclo y rango
-- pedido de cada reporte de Amazon Ads.
--
-- D.1: evaluacion_producto_ciclo, append-only, una fila por (ciclo,
-- plataforma, producto). Congela la entrada que leyo app/productos_io.py y
-- el resultado de app/optimizer/productos.resolver_producto; el replay
-- (tools/auto03_verifica.py) vuelve a correr el resolutor sobre `entrada`.
-- La ventana [ventana_desde, ventana_hasta) es la de la pausa, [D-60, D-30),
-- y gasto/ventas/clics/compras son su actividad (NULL si no esta completa).
-- decision.evaluacion_producto_id liga la decision con la evaluacion que la
-- sustenta (la llena el bloque F).
--
-- D.7: ads_report_result guarda el rango que la ingesta pidio a Amazon
-- (fecha_desde, fecha_hasta) y, en el written del reporte de productos,
-- cobertura_integra: true solo si la ingesta no descarto ninguna fila que
-- pudiera ser actividad del rango (S6). Las filas viejas quedan en NULL: un
-- rango desconocido no cubre ninguna fecha.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0077_reversa_auto03_evaluacion_producto.sql (aborta si ya hay datos).

CREATE TABLE evaluacion_producto_ciclo (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle_id          BIGINT      NOT NULL REFERENCES optimizer_cycle(id),
    platform          platform    NOT NULL,
    product_id        BIGINT      NOT NULL REFERENCES product(id),
    decided_at        TIMESTAMPTZ NOT NULL,
    config_version_id BIGINT      REFERENCES config_version(id),
    politica          TEXT        NOT NULL,
    moneda            currency    NOT NULL,
    gasto             NUMERIC(14, 4),
    ventas            NUMERIC(14, 4),
    clics             BIGINT,
    compras           NUMERIC(14, 4),
    meta_pct          NUMERIC(8, 4),
    entrada           JSONB       NOT NULL,
    resultado         JSONB       NOT NULL,
    huella_alcance    TEXT        NOT NULL,
    ventana_desde     DATE        NOT NULL,
    ventana_hasta     DATE        NOT NULL,
    UNIQUE (cycle_id, platform, product_id),
    CONSTRAINT evaluacion_producto_ventana CHECK (ventana_desde < ventana_hasta),
    CONSTRAINT evaluacion_producto_actividad_completa CHECK (
        (gasto IS NULL) = (ventas IS NULL)
        AND (gasto IS NULL) = (clics IS NULL)
        AND (gasto IS NULL) = (compras IS NULL)
    ),
    CONSTRAINT evaluacion_producto_meta_positiva CHECK (meta_pct IS NULL OR meta_pct > 0),
    CONSTRAINT evaluacion_producto_pausas_lista CHECK (
        jsonb_typeof(resultado -> 'pausas') = 'array'
    )
);

CREATE INDEX evaluacion_producto_ciclo_producto_idx
    ON evaluacion_producto_ciclo (platform, product_id, cycle_id DESC);

COMMENT ON TABLE evaluacion_producto_ciclo IS
    'AUTO-03 D.1: evaluacion por producto de cada ciclo (append-only). entrada '
    'congela los insumos de app/productos_io.py; resultado trae meta, motivos, '
    'pausas y la sombra por hoja. El replay corre el resolutor sobre entrada, '
    'jamas sobre el margen vigente. No gobierna el ciclo siguiente.';

-- Regla 4: la moneda de la evaluacion es la de la plataforma.
CREATE FUNCTION evaluacion_producto_coherente() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.moneda IS DISTINCT FROM (CASE NEW.platform
            WHEN 'amazon_mx' THEN 'MXN'::currency
            WHEN 'amazon_us' THEN 'USD'::currency
        END) THEN
        RAISE EXCEPTION 'evaluacion_producto_ciclo: moneda % no es la de %',
            NEW.moneda, NEW.platform;
    END IF;
    -- Una pausa solo se propone sobre una ventana madura: su fin exclusivo
    -- queda a 30 dias o mas de la decision, en UTC fijo.
    IF jsonb_array_length(NEW.resultado -> 'pausas') > 0
       AND NEW.ventana_hasta > (NEW.decided_at AT TIME ZONE 'UTC')::date - 30 THEN
        RAISE EXCEPTION 'evaluacion_producto_ciclo: ventana_hasta % sin madurez de 30 dias '
            'al decidir %', NEW.ventana_hasta, NEW.decided_at;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER evaluacion_producto_coherente
    BEFORE INSERT ON evaluacion_producto_ciclo
    FOR EACH ROW EXECUTE FUNCTION evaluacion_producto_coherente();
CREATE TRIGGER evaluacion_producto_append_only
    BEFORE UPDATE OR DELETE ON evaluacion_producto_ciclo
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER evaluacion_producto_append_only_truncate
    BEFORE TRUNCATE ON evaluacion_producto_ciclo
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

ALTER TABLE decision
    ADD COLUMN evaluacion_producto_id BIGINT REFERENCES evaluacion_producto_ciclo(id);

COMMENT ON COLUMN decision.evaluacion_producto_id IS
    'AUTO-03: evaluacion de producto que sustenta la decision (meta por '
    'producto o pausa de product ad); NULL en las demas.';

GRANT SELECT ON evaluacion_producto_ciclo TO app_read, app_admin, app_decide;
GRANT INSERT ON evaluacion_producto_ciclo TO app_decide;
GRANT USAGE ON SEQUENCE evaluacion_producto_ciclo_id_seq TO app_decide;
GRANT INSERT (evaluacion_producto_id) ON decision TO app_decide;

ALTER TABLE ads_report_result
    ADD COLUMN fecha_desde DATE,
    ADD COLUMN fecha_hasta DATE,
    ADD COLUMN cobertura_integra BOOLEAN,
    ADD CONSTRAINT ads_report_result_rango CHECK (
        (fecha_desde IS NULL) = (fecha_hasta IS NULL)
        AND (fecha_desde IS NULL OR fecha_desde <= fecha_hasta)
    ),
    ADD CONSTRAINT ads_report_result_cobertura_written CHECK (
        cobertura_integra IS NULL OR status = 'written'
    );

COMMENT ON COLUMN ads_report_result.fecha_desde IS
    'AUTO-03 D.7: primera fecha del rango pedido a Amazon (inclusive). NULL '
    '= rango desconocido: no cubre ninguna fecha (S6).';
COMMENT ON COLUMN ads_report_result.cobertura_integra IS
    'AUTO-03 D.7: en el written del reporte de productos, true solo si la '
    'ingesta no descarto ninguna fila que pudiera ser actividad del rango '
    '(asin/sku ausente, fecha o metrica invalida, fila sin metricas, same_sku '
    'incoherente, clave envenenada). Los duplicados por reingesta no la '
    'invalidan. NULL en los demas reportes y estados.';

DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'evaluacion_producto_ciclo', 'INSERT')
       OR NOT has_table_privilege('app_decide', 'evaluacion_producto_ciclo', 'SELECT')
       OR NOT has_table_privilege('app_read', 'evaluacion_producto_ciclo', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'evaluacion_producto_ciclo', 'SELECT')
       OR NOT has_sequence_privilege('app_decide', 'evaluacion_producto_ciclo_id_seq', 'USAGE')
       OR NOT has_column_privilege('app_decide', 'decision', 'evaluacion_producto_id', 'INSERT')
       OR has_table_privilege('app_read', 'evaluacion_producto_ciclo', 'INSERT')
       OR has_table_privilege('app_ingest', 'evaluacion_producto_ciclo', 'INSERT')
       OR has_table_privilege('app_decide', 'evaluacion_producto_ciclo', 'UPDATE')
       OR has_table_privilege('app_decide', 'evaluacion_producto_ciclo', 'DELETE')
       OR NOT has_column_privilege('app_ingest', 'ads_report_result', 'cobertura_integra', 'INSERT')
       OR NOT has_column_privilege('app_decide', 'ads_report_result', 'fecha_desde', 'SELECT')
       OR NOT has_column_privilege('app_read', 'ads_report_result', 'fecha_hasta', 'SELECT') THEN
        RAISE EXCEPTION '0077: privilegios de evaluacion_producto_ciclo o ads_report_result invalidos';
    END IF;
END $$;
