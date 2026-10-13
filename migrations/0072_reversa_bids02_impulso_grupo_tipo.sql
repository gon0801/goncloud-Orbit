-- Reversa de 0072 (BIDS 02 R09-J): quita el candado de tipo del
-- grupo del impulso. Solo quita el trigger y la funcion; no borra
-- filas. No idempotente (regla 7).

BEGIN;

DROP TRIGGER impulso_grupo_tipo ON impulso;

DROP FUNCTION impulso_grupo_es_impulso();

COMMIT;
