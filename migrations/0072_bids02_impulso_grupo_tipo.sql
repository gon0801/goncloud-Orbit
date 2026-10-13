-- 0072 (BIDS 02 R09-J): candado de tipo del grupo del impulso.
--
-- Hoy solo lo garantiza la tupla de roles en _registrar: nada en la
-- base impide que un impulso cuelgue de un grupo fabrica5 si entra
-- por otro camino. El invariante (un impulso solo vive sobre un
-- grupo tipo impulso) se hace cumplir en el esquema, como los demas
-- (impulso_coherente de la 0065), para que valga venga de donde
-- venga. Una FK no puede cruzar a campana_grupo.tipo; por eso es
-- trigger, no FK.
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0072_reversa_bids02_impulso_grupo_tipo.sql.

BEGIN;

CREATE OR REPLACE FUNCTION impulso_grupo_es_impulso() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT tipo FROM campana_grupo WHERE id = NEW.grupo_id) IS DISTINCT FROM 'impulso' THEN
        RAISE EXCEPTION 'impulso %: el grupo % no es de tipo impulso', NEW.id, NEW.grupo_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER impulso_grupo_tipo
    BEFORE INSERT ON impulso
    FOR EACH ROW
    EXECUTE FUNCTION impulso_grupo_es_impulso();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'impulso_grupo_tipo' AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION '0072: falta el trigger impulso_grupo_tipo';
    END IF;
END;
$$;

COMMIT;
