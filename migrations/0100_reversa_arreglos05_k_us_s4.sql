-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0100 (S4 caja real Amazon US FBM -> S3).
--
-- Regla 7 (sin borrar hechos referenciados) y corte inmediato el mismo dia:
--   - Si ningun estimacion_escenario apunta a filas S4 de amazon_us/fbm
--     (rollback inmediato tras despliegue), retira la fila S4 no usada y
--     reabre valid_to = NULL en la S3 previa de amazon_us/fbm, conservando
--     intactos los triggers de 0093 y todas las politicas de Mexico.
--   - Si ya existen escenarios S4 de amazon_us/fbm, conserva intactas esas
--     filas, cierra valid_to = GREATEST(valid_from, hoy UTC) (corte inmediato
--     en resolver_politica_aplicable) y abre una continuacion S3 desde hoy UTC.
--   - Nota operativa: si ya se corrio tools/reancle_goals_s4.py --platform amazon_us,
--     revertir tambien el lote con `--revertir --platform amazon_us --lote ...`
--     antes de correr precios en live.
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
     WHERE p.universo = 'amazon_us/fbm'
       AND p.formula_version = 'S4';

    IF v_usadas = 0 THEN
        ALTER TABLE estimacion_politica_version
            DISABLE TRIGGER estimacion_politica_solo_cierra_vigencia;
        ALTER TABLE estimacion_politica_version
            DISABLE TRIGGER estimacion_politica_sin_borrado;

        DELETE FROM estimacion_politica_version
         WHERE universo = 'amazon_us/fbm'
           AND formula_version = 'S4';

        UPDATE estimacion_politica_version
           SET valid_to = NULL
         WHERE universo = 'amazon_us/fbm'
           AND formula_version = 'S3'
           AND valid_to IS NOT NULL
           AND id = (
               SELECT max(p2.id)
                 FROM estimacion_politica_version p2
                WHERE p2.universo = 'amazon_us/fbm'
                  AND p2.formula_version = 'S3'
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM estimacion_politica_version p3
                WHERE p3.universo = 'amazon_us/fbm'
                  AND p3.valid_to IS NULL
           );

        ALTER TABLE estimacion_politica_version
            ENABLE TRIGGER estimacion_politica_solo_cierra_vigencia;
        ALTER TABLE estimacion_politica_version
            ENABLE TRIGGER estimacion_politica_sin_borrado;
    ELSE
        UPDATE estimacion_politica_version
           SET valid_to = GREATEST(valid_from, v_cierre)
         WHERE universo = 'amazon_us/fbm'
           AND formula_version = 'S4'
           AND valid_to IS NULL;

        INSERT INTO estimacion_politica_version (
            label, universo, formula_version, settings, valid_from, valid_to
        )
        SELECT p.label || '_reversa_0100',
               p.universo,
               'S3',
               p.settings,
               v_cierre,
               NULL
          FROM estimacion_politica_version p
         WHERE p.universo = 'amazon_us/fbm'
           AND p.formula_version = 'S3'
           AND NOT EXISTS (
               SELECT 1
                 FROM estimacion_politica_version abierta
                WHERE abierta.universo = 'amazon_us/fbm'
                  AND abierta.formula_version = 'S3'
                  AND abierta.valid_to IS NULL
           )
         ORDER BY p.id DESC
         LIMIT 1;

        INSERT INTO estimacion_politica_version (
            label, universo, formula_version, settings, valid_from, valid_to
        )
        SELECT 'repricing02_fbm_us_reversa_0100',
               'amazon_us/fbm',
               'S3',
               (
                   '{"iva_divisor": "1", "isr_tasa": "0.0207", "logistica": "0",'
                   ' "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",'
                   ' "precio_incluye_iva": false, "retencion_iva_reconciliacion": "0.08",'
                   ' "fee_tax_amount_requiere_politica": true}'
               )::jsonb,
               v_cierre,
               NULL
         WHERE NOT EXISTS (
             SELECT 1
               FROM estimacion_politica_version abierta
              WHERE abierta.universo = 'amazon_us/fbm'
                AND abierta.formula_version = 'S3'
                AND abierta.valid_to IS NULL
         );
    END IF;
END;
$$;
