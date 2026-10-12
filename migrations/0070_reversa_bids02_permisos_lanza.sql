-- Reversa de 0070 (BIDS 02 R09-r5 B1b): quita el DELETE en
-- fabrica_lote y el INSERT en ad_entity de app_admin. REVOKE siempre
-- seguro (no borra filas). No idempotente (regla 7).

BEGIN;

REVOKE DELETE ON fabrica_lote FROM app_admin;
REVOKE INSERT ON ad_entity FROM app_admin;

COMMIT;
