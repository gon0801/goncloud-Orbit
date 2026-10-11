-- 0064 (BIDS 02 I.1, seccion 5): de que tipo es el grupo.
--
-- campana_grupo.tipo ('fabrica5' o 'impulso'): la fabrica sigue creando
-- grupos de cinco y el impulso crea grupos de dos (exact + auto). Los
-- grupos que ya existen quedan en 'fabrica5' por el DEFAULT. El trigger
-- campana_grupo_rol_roles_de_impulso rechaza en un grupo impulso
-- cualquier rol fuera de category_exact y auto_discovery. (Nota "LO QUE
-- NO CAMBIA" de docs/evidencia/bids-02/diseno/datos.sql.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0064_reversa_bids02_grupo_tipo.sql (quita trigger,
-- funcion, CHECK y columna). Sin GRANTs nuevos: la columna hereda los
-- de tabla de 0018 (SELECT los cuatro roles, INSERT/UPDATE app_admin);
-- el bloque DO falla si falta o sobra un privilegio.

BEGIN;

ALTER TABLE campana_grupo ADD COLUMN tipo TEXT NOT NULL DEFAULT 'fabrica5'
    CHECK (tipo IN ('fabrica5', 'impulso'));

CREATE FUNCTION campana_grupo_rol_roles_de_impulso() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT tipo FROM campana_grupo WHERE id = NEW.grupo_id) = 'impulso'
       AND NEW.rol NOT IN ('category_exact', 'auto_discovery') THEN
        RAISE EXCEPTION 'grupo % es impulso: solo admite category_exact y auto_discovery, no %',
            NEW.grupo_id, NEW.rol;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER campana_grupo_rol_roles_de_impulso
    BEFORE INSERT OR UPDATE ON campana_grupo_rol
    FOR EACH ROW EXECUTE FUNCTION campana_grupo_rol_roles_de_impulso();

COMMENT ON COLUMN campana_grupo.tipo IS
    'BIDS 02 I.1: fabrica5 (grupo de cinco de la fabrica) o impulso (grupo de dos del dueno).';

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo', 'SELECT')
       OR NOT has_table_privilege('app_read', 'campana_grupo_rol', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo_rol', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo', 'UPDATE')
       OR NOT has_table_privilege('app_admin', 'campana_grupo_rol', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'campana_grupo_rol', 'UPDATE')
       OR has_table_privilege('app_read', 'campana_grupo', 'INSERT')
       OR has_table_privilege('app_read', 'campana_grupo', 'UPDATE')
       OR has_table_privilege('app_decide', 'campana_grupo', 'INSERT')
       OR has_table_privilege('app_decide', 'campana_grupo_rol', 'INSERT') THEN
        RAISE EXCEPTION '0064: privilegios de campana_grupo(_rol) distintos de 0018';
    END IF;
END;
$$;

COMMIT;
