-- sonda-aplicada: SELECT to_regclass('public.apply_queue') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'apply_queue' AND indexname = 'apply_queue_live_efecto_unico') AND NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'apply_queue' AND indexname = 'apply_queue_clave_efecto_en_vuelo') AND EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = to_regclass('public.apply_queue') AND t.tgname = 'apply_queue_clave_vigente' AND NOT t.tgisinternal)
-- =============================================================================
--  ARREGLOS 05 E - la practica vencida no bloquea el trabajo real - PG 16
--
--  0002 creo UN indice mixto (`apply_queue_clave_efecto_en_vuelo`) sobre todas
--  las filas NO terminales, sin mirar el modo: una propuesta de practica
--  (`modo='shadow'`, `pending_veto`) que ya venció su ventana de 48 h seguia
--  bloqueando el SELECT de claves y el INSERT de la decision REAL (live) para
--  siempre, porque la fila shadow no se descarta ni se borra (su perimetro es
--  vetoed|discarded y eso es del administrador).
--
--  Esta migracion SEPARA los dos invariantes que 0002 mezclaba:
--
--  1. Unicidad de EFECTOS LIVE: `apply_queue_live_efecto_unico`, indice unico
--     parcial sobre la clave de efecto, SOLO para `modo='live'` no terminal.
--     Es la garantia de la base de como maximo un efecto live no terminal por
--     clave, incluso con dos workers.
--  2. Deduplicacion de PRACTICAS SHADOW VIGENTES: trigger
--     `apply_queue_clave_vigente` en el INSERT. Una shadow no terminal solo
--     deduplica mientras `vence_el > now()` (reloj de la base). Al vencer deja
--     de bloquear sin borrarse ni promocionarse. El trigger tambien bloquea un
--     INSERT shadow cuando ya hay un efecto live no terminal de la clave, que
--     el indice unico no ve porque el indice cubre solo filas live.
--
--  Una restriccion parcial no puede usar `now()` (no es inmutable): por eso la
--  vigencia temporal vive en el trigger y en el SELECT, no en el indice. La
--  espera de 48 h (`vence_el`), los triggers de transicion y el veto durable
--  de 0002 no se tocan. Las practicas no se promocionan a live ni se borran.
--
--  La lectura previa de produccion (docs/evidencia/arreglos-05/E/previa.txt,
--  rol orbit_read, transaccion read_only) midio 31 filas shadow pending_veto,
--  las 31 ya vencidas, cero claves con mas de un efecto live no terminal y cero
--  claves con mas de una fila no terminal: los dos indices se pueden crear sin
--  backfill. No hay datos que corregir.
--
--  Sin BEGIN/COMMIT: desplegar.sh la aplica en una sola transaccion (psql -1).
--  No re-ejecutable. Reversa: 0098_reversa_arreglos05_indice_shadow.sql (aborta
--  si restaurar el indice mixto chocaria; jamas borra filas).
-- =============================================================================

-- El indice mixto de 0002 deja de gobernar la clave de efecto.
DROP INDEX IF EXISTS apply_queue_clave_efecto_en_vuelo;

-- Unicidad de efectos LIVE: como maximo una fila live no terminal por clave.
-- NULLS NOT DISTINCT sigue siendo obligatorio: search_term es NULL en los pause.
CREATE UNIQUE INDEX apply_queue_live_efecto_unico
    ON apply_queue (platform, ad_entity_id, familia, search_term)
    NULLS NOT DISTINCT
    WHERE modo = 'live'
      AND estado NOT IN ('applied', 'failed', 'vetoed', 'discarded');

COMMENT ON INDEX apply_queue_live_efecto_unico IS
  'ARREGLOS 05 E: a lo sumo un EFECTO LIVE no terminal por clave '
  '(platform, ad_entity_id, familia, search_term). Sustituye al indice mixto '
  'de 0002: una practica shadow vencida ya no hace sombra sobre la clave. '
  'NULLS NOT DISTINCT porque los pause llevan search_term NULL.';

-- Deduplicacion de PRACTICAS SHADOW VIGENTES, bloqueo de un shadow contra un
-- efecto live no terminal y VETO VIGENTE al dar de alta. BEFORE INSERT y
-- ERRCODE unique_violation: el motor ya captura psycopg.errors.UniqueViolation
-- como choque_clave.
--
-- La clave se compara por (platform, ad_entity_id, search_term) y NO por
-- `familia`: `familia` es una columna GENERATED y un trigger BEFORE no ve su
-- valor en NEW. El CHECK apply_queue_clave_coherente de 0002 garantiza que
-- search_term determina la familia (entity_cut = NULL, term_cut = NOT NULL), y
-- el indice unico parcial (que si ve la generada) es el candado del live.
--
-- El candado asesor por clave serializa el ALTA con el VETO
-- (app/apply_veto.veta_efecto toma el mismo hashtextextended). Sin el, una
-- decision calculada antes del veto podia insertarse despues de confirmado y
-- escapar de los 30d de veto: la fila no terminal convivia con la vetoed y el
-- barrido la enviaba 48h despues.
CREATE FUNCTION apply_queue_clave_vigente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_live INTEGER;
    v_practica INTEGER;
    v_clave TEXT;
BEGIN
    v_clave := 'apply_queue:' || NEW.platform::text || ':' || NEW.ad_entity_id::text
               || ':' || coalesce(NEW.search_term, '<null>');
    PERFORM pg_advisory_xact_lock(hashtextextended(v_clave, 0));

    SELECT count(*) FILTER (WHERE q.modo = 'live'),
           count(*) FILTER (
               WHERE q.modo = 'shadow' AND q.vence_el > now()
           )
      INTO v_live, v_practica
      FROM apply_queue q
     WHERE q.platform = NEW.platform
       AND q.ad_entity_id = NEW.ad_entity_id
       AND q.search_term IS NOT DISTINCT FROM NEW.search_term
       AND q.estado NOT IN ('applied', 'failed', 'vetoed', 'discarded');

    IF v_live > 0 THEN
        RAISE EXCEPTION
            'apply_queue: la clave de efecto (platform=%, entidad=%, kind=%, '
            'termino=%) ya tiene un efecto LIVE no terminal; no se duplica.',
            NEW.platform, NEW.ad_entity_id, NEW.kind, NEW.search_term
            USING ERRCODE = 'unique_violation';
    END IF;

    IF v_practica > 0 THEN
        RAISE EXCEPTION
            'apply_queue: la clave de efecto (platform=%, entidad=%, kind=%, '
            'termino=%) tiene una PRACTICA shadow VIGENTE (vence_el > now()); '
            'se deduplica hasta que venza.',
            NEW.platform, NEW.ad_entity_id, NEW.kind, NEW.search_term
            USING ERRCODE = 'unique_violation';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM apply_queue q
         WHERE q.platform = NEW.platform
           AND q.ad_entity_id = NEW.ad_entity_id
           AND q.search_term IS NOT DISTINCT FROM NEW.search_term
           AND q.estado = 'vetoed'
           AND q.vence_el > now()
    ) THEN
        RAISE EXCEPTION
            'apply_queue: la clave de efecto (platform=%, entidad=%, kind=%, '
            'termino=%) tiene un VETO vigente; el alta queda bloqueada hasta '
            'que venza.',
            NEW.platform, NEW.ad_entity_id, NEW.kind, NEW.search_term
            USING ERRCODE = 'unique_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER apply_queue_clave_vigente
    BEFORE INSERT ON apply_queue
    FOR EACH ROW EXECUTE FUNCTION apply_queue_clave_vigente();

COMMENT ON FUNCTION apply_queue_clave_vigente IS
  'ARREGLOS 05 E: la deduplicacion de PROPUESTAS shadow vigentes, el bloqueo de '
  'un shadow contra un efecto live no terminal y el VETO VIGENTE al dar de '
  'alta. La vigencia es del reloj de la base (now() sobre TIMESTAMPTZ), nunca '
  'del proceso. Una shadow vencida NO bloquea: conserva su fila e historia, no '
  'se promociona ni se borra. El candado asesor por clave (hashtextextended) '
  'serializa el alta con el veto de app/apply_veto.veta_efecto: una decision '
  'calculada antes del veto no puede insertarse despues. Levanta '
  'unique_violation para que el motor lo trate como el choque_clave de siempre.';
