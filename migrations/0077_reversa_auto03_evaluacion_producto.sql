-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0077 (AUTO-03 D.1 y D.7).
--
-- Quita evaluacion_producto_ciclo, decision.evaluacion_producto_id y el rango
-- pedido de ads_report_result. Aborta si ya hay datos (regla 7): una
-- evaluacion guardada o un rango registrado por la ingesta. El codigo
-- anterior corre con la 0077 aplicada (solo agrega), asi que el rollback del
-- despliegue sigue aunque esta reversa aborte.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM evaluacion_producto_ciclo) THEN
        RAISE EXCEPTION 'reversa 0077: evaluacion_producto_ciclo tiene filas';
    END IF;
    IF EXISTS (SELECT 1 FROM ads_report_result
                WHERE fecha_desde IS NOT NULL OR cobertura_integra IS NOT NULL) THEN
        RAISE EXCEPTION 'reversa 0077: ads_report_result ya tiene rangos pedidos registrados';
    END IF;
END
$$;

ALTER TABLE ads_report_result
    DROP CONSTRAINT ads_report_result_cobertura_written,
    DROP CONSTRAINT ads_report_result_rango,
    DROP COLUMN cobertura_integra,
    DROP COLUMN fecha_hasta,
    DROP COLUMN fecha_desde;

ALTER TABLE decision DROP COLUMN evaluacion_producto_id;

DROP TABLE evaluacion_producto_ciclo;
DROP FUNCTION evaluacion_producto_coherente();
