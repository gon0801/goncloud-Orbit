-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0074.
--
-- Deshace `0074_jev_ads03_bloqueo.sql`: dropea las dos tablas. Los triggers
-- mueren con ellas. No toca prohibir_mutacion (vive en 0001).
--
-- Solo aplica con las tablas vacias. Con un veredicto real el DROP
-- destruiria el ledger de negativos que siguen en Amazon; la salida es
-- Quitar, no esta reversa. Se aplica antes que la de 0052.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM bloqueo_veredicto)
       OR EXISTS (SELECT 1 FROM bloqueo_efecto) THEN
        RAISE EXCEPTION
            'reversa 0074: hay veredictos o efectos; la salida es Quitar, no DROP';
    END IF;
END
$$;

DROP TABLE bloqueo_efecto;
DROP TABLE bloqueo_veredicto;
DROP FUNCTION bloqueo_exige_aprobado();

COMMIT;
