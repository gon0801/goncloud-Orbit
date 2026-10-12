-- 0070 (BIDS 02 R09-r5 B1b): permisos que lanza necesita con el rol
-- de la app (miembro de app_admin, no superusuario).
--
-- 1) DELETE en fabrica_lote. Razon: H6 (lote fallido/planeado sin nada
-- creado en Amazon) archiva el nombre canonico para que el reintento
-- lo reuse: INSERT-copia a -descartado (historia preservada) + UPDATE
-- de hijos + DELETE del canonico vacio. Sin DELETE, en produccion el
-- reintento muere con InsufficientPrivilege y cada intento deja una
-- copia -descartado: el producto queda bloqueado, justo lo que H6
-- debia quitar. El unico camino que borra es _archiva_lote_vacio
-- (lote sin externals y sin impulso); lo demas sigue append-only.
-- 2) INSERT en ad_entity. Razon: el registro de lanza anota las
-- campanas y ad groups que la propia fabrica creo en Amazon (el sync
-- embebido en _mutar corre con el rol de la ruta). La ingesta diaria
-- (app_ingest) sigue siendo la escritora general y la unica que
-- corrige por columnas; este permiso solo registra lo recien creado.
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0070_reversa_bids02_permisos_lanza.sql.

BEGIN;

GRANT DELETE ON fabrica_lote TO app_admin;
GRANT INSERT ON ad_entity TO app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_admin', 'fabrica_lote', 'DELETE') THEN
        RAISE EXCEPTION '0070: app_admin sin DELETE en fabrica_lote';
    END IF;
    IF NOT has_table_privilege('app_admin', 'ad_entity', 'INSERT') THEN
        RAISE EXCEPTION '0070: app_admin sin INSERT en ad_entity';
    END IF;
END;
$$;

COMMIT;
