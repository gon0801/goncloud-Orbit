-- Reversa de la 0058: quita los dos CHECK (las columnas `l_origen` y
-- `verificacion` y sus CHECK de vocabulario, de la 0054, quedan).
BEGIN;
ALTER TABLE precio_decision
    DROP CONSTRAINT precio_decision_l_exige_origen,
    DROP CONSTRAINT precio_decision_mover_exige_verificacion;
COMMIT;
