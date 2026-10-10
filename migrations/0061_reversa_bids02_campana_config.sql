-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0061 (patron 0011/0031/0049_reversa_*/0052_reversa_*).
--
-- Deshace `0061_bids02_campana_config.sql`: borra la vista, la tabla y la
-- funcion del trigger de kind. El historial se vuelve a generar en el
-- siguiente sync de estructura. Nada las referencia, asi que no hace falta
-- CASCADE. Los tres triggers caen con su tabla.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DROP VIEW v_campana_config_vigente;
DROP TABLE ads_campana_config_observation;
DROP FUNCTION ads_campana_config_0061_kind();

COMMIT;
