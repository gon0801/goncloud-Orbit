-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0093 (S4 caja real Mexico -> S3).
--
-- Regla 7 (sin borrar hechos referenciados) y corte inmediato el mismo dia:
--   - Si ningun estimacion_escenario apunta a las filas S4 de 0093 (rollback
--     inmediato tras despliegue), las retira, reabre valid_to = NULL en las S3
--     cerradas y restaura el CHECK y trigger estimacion_politica_append_only
--     de 0028.
--   - Si ya existen escenarios S4 en produccion, conserva intactas esas filas,
--     cierra valid_to = hoy UTC (intervalo vacio [hoy, hoy) si se desplego hoy:
--     deja de seleccionarse AL INSTANTE en resolver_politica_aplicable) y abre
--     filas S3 de continuacion vigentes desde hoy UTC.
--   - Nota operativa: si ya se corrio tools/reancle_goals_s4.py, revertir tambien
--     el lote de metas con `tools/reancle_goals_s4.py --revertir` antes de correr
--     precios en live.
--
-- NO se aplica en el despliegue normal; corre en una transaccion (psql -1).
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    v_usadas BIGINT;
    v_cierre DATE := (now() AT TIME ZONE 'UTC')::date;
BEGIN
    SELECT count(*) INTO v_usadas
      FROM estimacion_escenario e
      JOIN estimacion_politica_version p ON p.id = e.politica_version_id
     WHERE p.universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
       AND p.formula_version = 'S4';

    IF v_usadas = 0 THEN
        DROP TRIGGER IF EXISTS estimacion_politica_solo_cierra_vigencia
            ON estimacion_politica_version;
        DROP TRIGGER IF EXISTS estimacion_politica_sin_borrado
            ON estimacion_politica_version;

        DELETE FROM estimacion_politica_version
         WHERE universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
           AND formula_version = 'S4';

        UPDATE estimacion_politica_version
           SET valid_to = NULL
         WHERE universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
           AND formula_version = 'S3'
           AND valid_to IS NOT NULL
           AND id = (
               SELECT max(p2.id)
                 FROM estimacion_politica_version p2
                WHERE p2.universo = estimacion_politica_version.universo
                  AND p2.formula_version = 'S3'
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM estimacion_politica_version p3
                WHERE p3.universo = estimacion_politica_version.universo
                  AND p3.valid_to IS NULL
           );

        ALTER TABLE estimacion_politica_version
            DROP CONSTRAINT IF EXISTS estimacion_politica_vigencia_coherente;
        ALTER TABLE estimacion_politica_version
            ADD CONSTRAINT estimacion_politica_vigencia_coherente
            CHECK (valid_to IS NULL OR valid_to > valid_from);

        CREATE TRIGGER estimacion_politica_append_only
            BEFORE UPDATE OR DELETE ON estimacion_politica_version
            FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

        DROP FUNCTION IF EXISTS estimacion_politica_solo_cierra_vigencia();
    ELSE
        -- Cierre efectivo hoy mismo: valid_to = GREATEST(valid_from, v_cierre)
        -- (con valid_to = valid_from, valid_to > fecha es falso de inmediato).
        UPDATE estimacion_politica_version
           SET valid_to = GREATEST(valid_from, v_cierre)
         WHERE universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
           AND formula_version = 'S4'
           AND valid_to IS NULL;

        INSERT INTO estimacion_politica_version (
            label, universo, formula_version, settings, valid_from, valid_to
        )
        SELECT DISTINCT ON (p.universo)
               p.label || '_reversa_0093',
               p.universo,
               'S3',
               p.settings,
               v_cierre,
               NULL
          FROM estimacion_politica_version p
         WHERE p.universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
           AND p.formula_version = 'S3'
           AND NOT EXISTS (
               SELECT 1
                 FROM estimacion_politica_version abierta
                WHERE abierta.universo = p.universo
                  AND abierta.formula_version = 'S3'
                  AND abierta.valid_to IS NULL
           )
         ORDER BY p.universo, p.id DESC;
    END IF;
END;
$$;
