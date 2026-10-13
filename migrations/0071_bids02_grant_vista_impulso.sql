-- 0071 (BIDS 02 R09-E): GRANT SELECT explicito sobre v_impulso_estado.
--
-- La 0065 creo la vista sin GRANT propio: hoy la leen app_read y
-- app_admin solo por los default privileges del esquema. El GRANT
-- queda declarado en una migracion, verificado en su bloque DO, en
-- vez de depender de ese default. La 0065 ya esta aplicada en
-- produccion y no se edita.
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0071_reversa_bids02_grant_vista_impulso.sql.

BEGIN;

GRANT SELECT ON v_impulso_estado TO app_read, app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'public.v_impulso_estado', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'public.v_impulso_estado', 'SELECT')
       OR has_table_privilege('app_read', 'public.v_impulso_estado', 'INSERT')
       OR has_table_privilege('app_read', 'public.v_impulso_estado', 'UPDATE')
       OR has_table_privilege('app_read', 'public.v_impulso_estado', 'DELETE')
       OR has_table_privilege('app_admin', 'public.v_impulso_estado', 'INSERT')
       OR has_table_privilege('app_admin', 'public.v_impulso_estado', 'UPDATE')
       OR has_table_privilege('app_admin', 'public.v_impulso_estado', 'DELETE') THEN
        RAISE EXCEPTION '0071: privilegios de v_impulso_estado distintos de los declarados';
    END IF;
END;
$$;

COMMIT;
