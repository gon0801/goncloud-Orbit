-- Reversa de 0064 (BIDS 02 I.1): quita el trigger, la funcion, el
-- CHECK y la columna tipo. No idempotente (regla 7).

BEGIN;

DROP TRIGGER campana_grupo_rol_roles_de_impulso ON campana_grupo_rol;
DROP FUNCTION campana_grupo_rol_roles_de_impulso();
ALTER TABLE campana_grupo DROP COLUMN tipo;

COMMIT;
