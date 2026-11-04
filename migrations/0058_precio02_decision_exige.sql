-- REPRICING 02 carril S, paso S.1 (se despliega en D.2).
--
-- Desde 0.b `persistir_decision` escribe `l_origen` y `verificacion` en toda
-- fila. Cuando ese codigo ya corre en produccion (D.1), esta migracion lo
-- vuelve obligatorio. Las ~120 decisiones anteriores no se validan.
BEGIN;
ALTER TABLE precio_decision
    ADD CONSTRAINT precio_decision_l_exige_origen
        CHECK (l_valor IS NULL OR l_origen IS NOT NULL) NOT VALID,
    ADD CONSTRAINT precio_decision_mover_exige_verificacion
        CHECK (resultado NOT IN ('subir', 'bajar') OR verificacion IS NOT NULL) NOT VALID;
COMMIT;
