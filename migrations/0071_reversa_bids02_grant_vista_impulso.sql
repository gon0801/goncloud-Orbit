-- Reversa de 0071 (BIDS 02 R09-E): quita el GRANT SELECT explicito
-- sobre v_impulso_estado. REVOKE siempre seguro (no borra filas).
-- No idempotente (regla 7).

BEGIN;

REVOKE SELECT ON v_impulso_estado FROM app_read, app_admin;

COMMIT;
