-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0088 (ARREGLOS 03 D-I).
--
-- Borra los tres CHECK y las tres columnas del importe original de
-- ledger_event. Se pierden los dolares guardados desde el despliegue: es lo
-- esperado (el codigo anterior no los escribe ni los lee).
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1).
-- ---------------------------------------------------------------------------

ALTER TABLE ledger_event
    DROP CONSTRAINT ledger_original_con_moneda,
    DROP CONSTRAINT ledger_original_otra_moneda,
    DROP CONSTRAINT ledger_original_mismo_signo;

ALTER TABLE ledger_event
    DROP COLUMN amount_original,
    DROP COLUMN amount_original_currency,
    DROP COLUMN fx_fecha;
