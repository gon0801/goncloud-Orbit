-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0062 (patron 0011/0031/0049_reversa_*/0052_reversa_*/0061_reversa_*).
--
-- Deshace `0062_bids02_placement.sql`: borra la tabla
-- ads_placement_observation. El indice de dedupe y los dos triggers caen
-- con ella. El historial se vuelve a generar en el siguiente reporte de
-- placements. Nada la referencia, asi que no hace falta CASCADE.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DROP TABLE ads_placement_observation;

COMMIT;
