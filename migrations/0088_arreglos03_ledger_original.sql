-- sonda-aplicada: SELECT count(*) = 3 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'ledger_event' AND column_name IN ('amount_original', 'amount_original_currency', 'fx_fecha')
-- 0088 (ARREGLOS 03 D-I, ingesta.md F4, regla 4): el importe original ademas
-- del convertido.
--
-- Contabilidad entrega las ventas y los cargos de Amazon US ya en pesos y
-- Orbit guardaba solo los pesos. `amount` sigue en la moneda que entrega
-- contabilidad (la vista de margen exige venta, costo y cargos en una sola
-- moneda); el importe en la otra moneda va aparte:
--   - amount_original NULL con amount_original_currency puesto = "se
--     convirtio desde esa moneda y la fuente no entrego el original" (los
--     cargos de US: contabilidad solo manda orig_currency).
--   - Las tres NULL = no hubo conversion.
--   - fx_fecha NULL = no se sabe que dia de tasa uso contabilidad (solo se
--     llena con fx_source 'exact', la tasa del dia de la venta).
-- Los CHECK aceptan NULL a proposito (NULL es "no aplica" o "no viene").
--
-- Solo agrega columnas y CHECK: no escribe datos. Las filas ya guardadas
-- quedan con las tres NULL (el libro es de solo agregar y el payload no se
-- guardo). El trigger ledger_solo_reatribuye (0085) compara la fila entera
-- menos product_id en JSON: las columnas nuevas no lo afectan.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). Reversa: 0088_reversa_arreglos03_ledger_original.sql.

ALTER TABLE ledger_event
    ADD COLUMN amount_original money_amount,
    ADD COLUMN amount_original_currency currency,
    ADD COLUMN fx_fecha DATE;

ALTER TABLE ledger_event
    ADD CONSTRAINT ledger_original_con_moneda
        CHECK (amount_original IS NULL OR amount_original_currency IS NOT NULL),
    ADD CONSTRAINT ledger_original_otra_moneda
        CHECK (amount_original_currency IS NULL OR amount_original_currency <> amount_currency),
    ADD CONSTRAINT ledger_original_mismo_signo
        CHECK (amount_original IS NULL OR sign(amount_original) = sign(amount));
