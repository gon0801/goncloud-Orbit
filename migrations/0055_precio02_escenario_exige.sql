-- REPRICING 02 carril F, paso F.3 (se despliega en D.3).
--
-- Desde F.3 `sembrar_escenario_desde_refs` escribe `logistica_origen` y fees
-- partidos en todo escenario `disponible`. Cuando ese codigo ya corre en
-- produccion, esta migracion lo vuelve obligatorio. Los escenarios
-- anteriores (filas con NULL) no se validan.
BEGIN;
ALTER TABLE estimacion_escenario
    ADD CONSTRAINT estimacion_escenario_disponible_exige_origen
        CHECK (estado <> 'disponible' OR logistica_origen IS NOT NULL) NOT VALID,
    ADD CONSTRAINT estimacion_escenario_disponible_exige_fees_partidos
        CHECK (estado <> 'disponible'
               OR (fee_variable IS NOT NULL AND fee_fijo IS NOT NULL
                   AND fee_cotizable IS NOT NULL)) NOT VALID;
COMMIT;
