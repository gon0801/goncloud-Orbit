-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0087 (ARREGLOS 03 D-H.4).
--
-- Restaura fee_type NULL solo en las filas que la ida corrigio (las del
-- UPDATE ... RETURNING, guardadas en arreglos03_devoluciones_meli_ids) y
-- borra esa tabla. Una devolucion MeLi etiquetada despues de la ida, o una
-- con categoria explicita, no se toca: solo la ida sabe que corrijo.
--
-- El trigger ledger_solo_reatribuye (0085) se apaga solo durante el UPDATE y
-- se reenciende en la misma transaccion.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

ALTER TABLE ledger_event DISABLE TRIGGER ledger_solo_reatribuye;
UPDATE ledger_event AS l SET fee_type = NULL
FROM public.arreglos03_devoluciones_meli_ids AS c WHERE l.id = c.id;
ALTER TABLE ledger_event ENABLE TRIGGER ledger_solo_reatribuye;
DROP TABLE public.arreglos03_devoluciones_meli_ids;
