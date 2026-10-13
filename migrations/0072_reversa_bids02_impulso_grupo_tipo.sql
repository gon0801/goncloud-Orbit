-- Reversa de 0072 (BIDS 02 R09-J): quita el candado de tipo del
-- grupo del impulso. Solo quita los triggers y las funciones; no
-- borra filas. No idempotente (regla 7).

BEGIN;

DROP TRIGGER grupo_impulso_tipo_guardia ON campana_grupo;

DROP TRIGGER impulso_grupo_tipo ON impulso;

DROP FUNCTION grupo_con_impulso_no_cambia_tipo();

DROP FUNCTION impulso_grupo_es_impulso();

COMMIT;
