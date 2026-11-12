-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0084 (ARREGLOS 01 Q.2).
--
-- Devuelve el CHECK lectura_veredicto de impulso_lectura al vocabulario de la
-- 0065. Antes apaga el trigger append-only de impulso_lectura y BORRA las
-- lecturas con veredicto 'desarmado': solo las escribe el codigo de la 0084,
-- y el codigo anterior no sabe mapear ese veredicto (frase_final lanza
-- KeyError y la pantalla de productos deja de pintar los impulsos). Borrarlas
-- deja la base en el estado anterior a Q; el impulso vuelve a quedar "abierto"
-- para el codigo viejo, que es justo lo que significa revertir a esa version.
-- El resto de las lecturas no se toca y el trigger se vuelve a encender.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

ALTER TABLE impulso_lectura DISABLE TRIGGER impulso_lectura_sin_update_delete;

DELETE FROM impulso_lectura WHERE veredicto = 'desarmado';

ALTER TABLE impulso_lectura ENABLE TRIGGER impulso_lectura_sin_update_delete;

ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_veredicto;
ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_veredicto CHECK (veredicto IN
    ('en_curso', 'vende', 'clics_sin_ventas', 'impresiones_sin_clics', 'sin_impresiones'));
