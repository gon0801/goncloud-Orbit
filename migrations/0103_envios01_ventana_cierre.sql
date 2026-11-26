-- sonda-aplicada: SELECT COALESCE((SELECT settings ? 'envios_meli_dias_cierre' FROM config_version ORDER BY id DESC LIMIT 1), false)
-- 0103 (ENVIOS 01 seguimiento): siembra `envios_meli_dias_cierre` = "90", los
-- dias desde la compra tras los que un envio de Mercado Libre sin estado final
-- se cierra sin confirmar entrega (app/envios/estado.py).
--
-- Config = fila nueva: copia la config vigente y agrega la clave. No pisa un
-- valor ya puesto y con config_version vacia no inserta. El valor se cambia
-- despues con `python -m app.cli envios-ventana --dias N`.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). Reversa: 0103_reversa_envios01_ventana_cierre.sql.

-- El candado de app/config_write.lock_config_version, en sentencia aparte: el
-- INSERT lee la vigente despues de que confirme un guardado en curso.
SELECT pg_advisory_xact_lock(hashtext('config_version'));

INSERT INTO config_version (label, settings)
SELECT 'ENVIOS 01: envios_meli_dias_cierre en 90',
       v.settings || '{"envios_meli_dias_cierre": "90"}'::jsonb
  FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) AS v
 WHERE NOT v.settings ? 'envios_meli_dias_cierre';
