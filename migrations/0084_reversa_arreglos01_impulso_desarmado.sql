-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0084 (ARREGLOS 01 Q.2).
--
-- Devuelve el CHECK lectura_veredicto de impulso_lectura al vocabulario de la
-- 0065, sin 'desarmado'. FALLA con mensaje claro si ya hay filas con veredicto
-- 'desarmado': impulso_lectura es append-only (sin UPDATE ni DELETE), asi que
-- no se pueden corregir para volver al CHECK viejo. Piensa antes de revertir.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM impulso_lectura WHERE veredicto = 'desarmado') THEN
        RAISE EXCEPTION
            '0084 reversa: impulso_lectura ya tiene filas con veredicto desarmado'
            ' (append-only, no se pueden corregir); no se puede volver al CHECK de la 0065';
    END IF;
END
$$;

ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_veredicto;
ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_veredicto CHECK (veredicto IN
    ('en_curso', 'vende', 'clics_sin_ventas', 'impresiones_sin_clics', 'sin_impresiones'));
