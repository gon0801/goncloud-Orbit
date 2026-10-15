-- Reversa de la 0055: quita los dos CHECK (las columnas `logistica_origen`,
-- `envio_muestra_id`, `fee_variable`, `fee_fijo` y `fee_cotizable` y sus
-- CHECK de vocabulario, de la 0054, quedan).
BEGIN;
ALTER TABLE estimacion_escenario
    DROP CONSTRAINT estimacion_escenario_disponible_exige_origen,
    DROP CONSTRAINT estimacion_escenario_disponible_exige_fees_partidos;
COMMIT;
