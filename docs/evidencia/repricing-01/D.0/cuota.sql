-- D.0 (REPRICING 01): `apply_quota_state` de `precio:amazon_mx` nace con
-- `cap = 5` (el trigger de 0002 exige que el cap sea el de la config
-- vigente), dentro de `BEGIN … ROLLBACK`: no queda ninguna fila. Corre con el
-- DSN de admin de la app (`ORBIT_DSN_ADMIN`, el rol que tiene INSERT en esa
-- tabla). Salida `clave|valor` (psql -tA -F'|').

BEGIN;
INSERT INTO apply_quota_state (motor, quota_date, cap, used)
VALUES ('precio:amazon_mx', (now() AT TIME ZONE 'UTC')::date, 5, 0)
RETURNING 'cuota:precio:amazon_mx', cap::text;
ROLLBACK;
SELECT 'cuota_filas_despues', count(*)::text
  FROM apply_quota_state
 WHERE motor = 'precio:amazon_mx';
