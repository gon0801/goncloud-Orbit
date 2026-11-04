-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0060 (patron 0011/0031/0049_reversa_*/0052_reversa_*).
--
-- Deshace `0060_bids02_base_lectura.sql`: borra las dos vistas. No hay datos
-- que perder (una vista no guarda filas) ni permisos que devolver: los GRANT
-- mueren con la vista.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DROP VIEW v_hoja_activa;
DROP VIEW v_cambio_bid;

COMMIT;
