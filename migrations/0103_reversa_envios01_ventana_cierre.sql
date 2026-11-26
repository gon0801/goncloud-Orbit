-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0103 (ENVIOS 01 seguimiento).
--
-- Inserta una config_version nueva copiando la vigente sin
-- `envios_meli_dias_cierre`. La historia de config_version queda intacta.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). Re-runnable.
-- ---------------------------------------------------------------------------

-- El candado de app/config_write.lock_config_version, en sentencia aparte: el
-- INSERT lee la vigente despues de que confirme un guardado en curso.
SELECT pg_advisory_xact_lock(hashtext('config_version'));

INSERT INTO config_version (label, settings)
SELECT 'ENVIOS 01: reversa 0103, sin envios_meli_dias_cierre',
       v.settings - 'envios_meli_dias_cierre'
  FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) AS v
 WHERE v.settings ? 'envios_meli_dias_cierre';
