-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0062 (patron 0011/0031/0049_reversa_*/0052_reversa_*/0061_reversa_*).
--
-- Deshace `0062_bids02_placement.sql`: borra la tabla
-- ads_placement_observation y las funciones de los triggers de kind, moneda
-- y fecha. El indice de dedupe y los triggers caen con la tabla. El
-- historial se vuelve a generar en el siguiente reporte de placements.
-- Nada la referencia, asi que no hace falta CASCADE.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DROP TABLE ads_placement_observation;
DROP FUNCTION ads_placement_0062_kind();
DROP FUNCTION ads_placement_0062_moneda();
DROP FUNCTION ads_placement_0062_fecha();

COMMIT;
