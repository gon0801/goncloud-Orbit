-- Revertir antes el lote operativo y respaldar sus aliases verificados.
-- Sin CASCADE: no elimina dependencias ajenas.
BEGIN;
DROP FUNCTION public.meli_sku_alias_bloquear();
REVOKE UPDATE (product_id) ON listing_miembro FROM app_admin;
DROP TABLE meli_sku_alias;
COMMIT;
