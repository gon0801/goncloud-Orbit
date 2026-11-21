-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0093 (S4 caja real Mexico -> S3).
--
-- Regla 7 (sin borrar hechos referenciados):
--   - Si ningun estimacion_escenario apunta a las filas S4 de 0093 (rollback
--     inmediato tras despliegue), las retira, reabre valid_to = NULL en las S3
--     cerradas y restaura el trigger estimacion_politica_append_only de 0028.
--   - Si ya existen escenarios S4 en produccion, conserva intactas esas filas y
--     cierra valid_to en S4 abriendo filas S3 de continuacion, dejando la sonda
--     de 0093 en falso sin perder trazabilidad historica.
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
     WHERE p.label IN (
         'amazon_mx_fba_s4_caja_real_2026_11',
         'amazon_mx_fbm_s4_caja_real_2026_11',
         'meli_s4_caja_real_2026_11'
     );

    IF v_usadas = 0 THEN
        DROP TRIGGER IF EXISTS estimacion_politica_solo_cierra_vigencia
            ON estimacion_politica_version;
        DROP TRIGGER IF EXISTS estimacion_politica_sin_borrado
            ON estimacion_politica_version;

        DELETE FROM estimacion_politica_version
         WHERE label IN (
             'amazon_mx_fba_s4_caja_real_2026_11',
             'amazon_mx_fbm_s4_caja_real_2026_11',
             'meli_s4_caja_real_2026_11'
         );

        UPDATE estimacion_politica_version
           SET valid_to = NULL
         WHERE universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
           AND formula_version = 'S3'
           AND valid_to IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
                 FROM estimacion_politica_version p2
                WHERE p2.universo = estimacion_politica_version.universo
                  AND p2.valid_to IS NULL
           );

        CREATE TRIGGER estimacion_politica_append_only
            BEFORE UPDATE OR DELETE ON estimacion_politica_version
            FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

        DROP FUNCTION IF EXISTS estimacion_politica_solo_cierra_vigencia();
    ELSE
        UPDATE estimacion_politica_version
           SET valid_to = GREATEST(valid_from + 1, v_cierre)
         WHERE label IN (
             'amazon_mx_fba_s4_caja_real_2026_11',
             'amazon_mx_fbm_s4_caja_real_2026_11',
             'meli_s4_caja_real_2026_11'
         )
           AND valid_to IS NULL;

        INSERT INTO estimacion_politica_version (
            label, universo, formula_version, settings, valid_from, valid_to
        )
        SELECT DISTINCT ON (p.universo)
               p.label || '_reversa_0093',
               p.universo,
               'S3',
               p.settings,
               GREATEST(
                   (SELECT max(s4.valid_to)
                      FROM estimacion_politica_version s4
                     WHERE s4.universo = p.universo AND s4.formula_version = 'S4'),
                   v_cierre
               ),
               NULL
          FROM estimacion_politica_version p
         WHERE p.universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
           AND p.formula_version = 'S3'
         ORDER BY p.universo, p.id DESC;
    END IF;
END;
$$;
