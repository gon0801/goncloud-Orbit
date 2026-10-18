\set ON_ERROR_STOP on
BEGIN;
INSERT INTO config_version (label, settings)
SELECT 'jev-ads-03 D.2: enciende jev.bloqueo',
       jsonb_set(settings, '{jev.bloqueo}', 'true'::jsonb)
FROM config_version
WHERE id = 41
  AND id = (SELECT max(id) FROM config_version)
RETURNING id, label, settings -> 'jev.bloqueo' AS bloqueo, jsonb_typeof(settings -> 'jev.bloqueo') AS tipo;
SELECT (SELECT settings - 'jev.bloqueo' FROM config_version ORDER BY id DESC LIMIT 1)
     = (SELECT settings FROM config_version WHERE id = 41) AS solo_cambia_bloqueo;
COMMIT;
