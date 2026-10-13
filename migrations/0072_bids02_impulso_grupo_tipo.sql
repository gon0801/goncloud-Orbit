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
    -- FOR UPDATE sobre el grupo: un UPDATE de tipo simultaneo espera a
    -- esta lectura (o esta lectura espera a ese UPDATE) y el chequeo ve
    -- el tipo ya confirmado.
    IF (SELECT tipo FROM campana_grupo WHERE id = NEW.grupo_id FOR UPDATE) IS DISTINCT FROM 'impulso' THEN
        RAISE EXCEPTION 'impulso %: el grupo % no es de tipo impulso', NEW.id, NEW.grupo_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER impulso_grupo_tipo
    BEFORE INSERT ON impulso
    FOR EACH ROW
    EXECUTE FUNCTION impulso_grupo_es_impulso();

CREATE OR REPLACE FUNCTION grupo_con_impulso_no_cambia_tipo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tipo IS DISTINCT FROM 'impulso'
       AND EXISTS (SELECT 1 FROM impulso WHERE grupo_id = NEW.id) THEN
        RAISE EXCEPTION 'el grupo % tiene impulsos y no puede dejar de ser tipo impulso', NEW.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER grupo_impulso_tipo_guardia
    BEFORE UPDATE OF tipo ON campana_grupo
    FOR EACH ROW
    EXECUTE FUNCTION grupo_con_impulso_no_cambia_tipo();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
         WHERE t.tgname = 'impulso_grupo_tipo' AND NOT t.tgisinternal
           AND c.relname = 'impulso' AND t.tgenabled <> 'D'
           AND t.tgfoid = 'impulso_grupo_es_impulso()'::regprocedure
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
         WHERE t.tgname = 'grupo_impulso_tipo_guardia' AND NOT t.tgisinternal
           AND c.relname = 'campana_grupo' AND t.tgenabled <> 'D'
           AND t.tgfoid = 'grupo_con_impulso_no_cambia_tipo()'::regprocedure
    ) THEN
        RAISE EXCEPTION '0072: falta un trigger del candado de tipo del impulso';
    END IF;
    IF EXISTS (
        SELECT 1 FROM impulso i JOIN campana_grupo g ON g.id = i.grupo_id
         WHERE g.tipo IS DISTINCT FROM 'impulso'
    ) THEN
        RAISE EXCEPTION '0072: ya hay impulsos sobre grupos que no son tipo impulso';
    END IF;
END;
$$;

COMMIT;
