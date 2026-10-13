-- Reversa de 0071 (BIDS 02 R09-E): quita la marca y el GRANT SELECT
-- explicito sobre v_impulso_estado. REVOKE siempre seguro (no borra
-- filas). No idempotente (regla 7).
-- OJO: donde la vista ya se leia por default privileges, el REVOKE
-- tambien quita ese privilegio previo; el rollback de D.6 lo detecta
-- tras reversar y vuelve a otorgar el SELECT suelto.

BEGIN;

COMMENT ON VIEW v_impulso_estado IS NULL;

REVOKE SELECT ON v_impulso_estado FROM app_read, app_admin;

COMMIT;
