-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0051 (patron 0011/0031/0049_reversa_*).
--
-- Borra las dos tablas del acta de listado aunque tengan filas: el acta se
-- vuelve a generar en la siguiente corrida de `ingest structure`. Nada las
-- referencia, asi que no hace falta CASCADE. Los triggers append-only caen
-- con sus tablas.
--
-- NO se aplica en el despliegue normal.
-- ---------------------------------------------------------------------------

BEGIN;

DROP TABLE ads_listado_grupo;
DROP TABLE ads_listado_plataforma;

COMMIT;
