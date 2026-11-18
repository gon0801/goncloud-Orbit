-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0088 (ARREGLOS 03 D-K).
--
-- Borra el CHECK reputation_alert_avisada_despues y la columna avisada_at de
-- reputation_alert (el GRANT de columna a app_decide cae con ella). Se pierde
-- el registro de que alertas ya se avisaron: con el codigo anterior las
-- alertas vuelven a viajar solo en el resumen del ciclo.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

ALTER TABLE reputation_alert DROP CONSTRAINT reputation_alert_avisada_despues;
ALTER TABLE reputation_alert DROP COLUMN avisada_at;
