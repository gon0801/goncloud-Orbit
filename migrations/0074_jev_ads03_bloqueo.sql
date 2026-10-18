-- ---------------------------------------------------------------------------
-- 0074 — JEV ADS 03 B.1: ledger del bloqueo que el dueno aprueba.
-- PostgreSQL 16.
--
-- Diseno que manda: docs/superpowers/specs/2026-10-17-jev-ads-03-design.md
-- ("Persistencia (migracion 0074)" y "El efecto es una maquina de estados").
-- DDL del diseno mas lo que 0052 da por entendido: triggers append-only
-- (UPDATE/DELETE y TRUNCATE sobre prohibir_mutacion), indice por cada FK
-- que la PK no encabeza, REVOKE explicito tras cada CREATE y bloque DO.
--
-- El paso cuelga del aprobado, no del quitado. El estado de un aprobado es
-- el ultimo paso de bloqueo_efecto con ese veredicto_id. Quitar escribe un
-- veredicto quitado (origen_id = ese aprobado) y el paso en bloqueo_efecto
-- del aprobado. negative_keyword_id es texto de Amazon, sin FK: los
-- negativos no entran a la estructura.
--
-- Perimetro: 0001 concede SELECT por omision a app_read, app_ingest,
-- app_decide y app_admin. Esta migracion revoca el ciclo y a app_jev.
-- app_jev no gana permisos. Solo app_admin inserta. app_read lee.
--
-- Expansiva, NO re-runnable. Reversa: 0074_reversa_jev_ads03_bloqueo.sql
-- (el nombre lleva _reversa_ para que Launch no la aplique). Se aplica
-- antes que la de 0052: senal_id apunta a jev_senal y 0052 la dropea sin
-- CASCADE. Con filas, la salida es Quitar, no este DROP.
-- ---------------------------------------------------------------------------

BEGIN;

-- ===========================================================================
-- 1. VEREDICTO (decision del dueno)
-- ===========================================================================

CREATE TABLE bloqueo_veredicto (
    id UUID PRIMARY KEY,
    plataforma platform NOT NULL,
    ad_group_id BIGINT REFERENCES ad_entity(id),
    termino TEXT,
    termino_sha256 TEXT,
    senal_id UUID REFERENCES jev_senal(id),
    huella_catalogo TEXT,
    origen_id UUID REFERENCES bloqueo_veredicto(id),
    tipo TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bloqueo_veredicto_tipo CHECK (
        tipo IN ('aprobado', 'rechazado', 'quitado', 'reanudacion')),
    -- "lleva solo plataforma": el resto de la identidad va en NULL.
    CONSTRAINT bloqueo_veredicto_reanudacion_solo_plataforma CHECK (
        tipo <> 'reanudacion'
        OR (ad_group_id IS NULL AND termino IS NULL AND termino_sha256 IS NULL
            AND senal_id IS NULL AND huella_catalogo IS NULL AND origen_id IS NULL)),
    -- "las demas" despues de las dos excepciones nombradas (reanudacion y
    -- quitado). El spec no mete termino_sha256 en esa frase.
    CONSTRAINT bloqueo_veredicto_decision_exige_clave CHECK (
        tipo IN ('reanudacion', 'quitado')
        OR (ad_group_id IS NOT NULL AND termino IS NOT NULL
            AND senal_id IS NOT NULL AND huella_catalogo IS NOT NULL)),
    CONSTRAINT bloqueo_veredicto_quitado_cita_origen CHECK (
        tipo <> 'quitado' OR origen_id IS NOT NULL));

REVOKE ALL ON TABLE bloqueo_veredicto FROM app_decide, app_ingest, app_jev;

COMMENT ON TABLE bloqueo_veredicto IS
  'JEV ADS 03: decision del dueno, append-only. Un quitado cita en '
  'origen_id el aprobado que quita. Una reanudacion lleva solo plataforma. '
  'aprobado y rechazado traen grupo, termino, senal y huella.';
COMMENT ON COLUMN bloqueo_veredicto.origen_id IS
  'Solo lo exige un quitado. Apunta a la fila aprobado que se quita. El '
  'CHECK obliga la cita; que esa fila sea tipo aprobado lo escribe B.3.';
COMMENT ON COLUMN bloqueo_veredicto.termino_sha256 IS
  'Copia de jev_senal, nunca se recalcula. El CHECK de clave no lo exige: '
  'el spec nombra grupo, termino, senal y huella, no este hash.';

-- ===========================================================================
-- 2. EFECTO (pasos del aprobado en Amazon)
-- ===========================================================================

CREATE TABLE bloqueo_efecto (
    id UUID PRIMARY KEY,
    veredicto_id UUID NOT NULL REFERENCES bloqueo_veredicto(id),
    paso TEXT NOT NULL,
    intento INTEGER,
    negative_keyword_id TEXT,
    http_status INTEGER,
    respuesta TEXT,
    motivo TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bloqueo_efecto_paso CHECK (paso IN (
        'intencion', 'aplicado', 'fallido', 'anulado',
        'reversa_intencion', 'revertido', 'reversa_fallida')),
    -- Presencia, no exclusion: el otro campo puede venir lleno.
    CONSTRAINT bloqueo_efecto_paso_exige_negative CHECK (
        paso <> ALL (ARRAY['aplicado', 'reversa_intencion', 'revertido'])
        OR negative_keyword_id IS NOT NULL),
    CONSTRAINT bloqueo_efecto_paso_exige_motivo CHECK (
        paso <> ALL (ARRAY['fallido', 'anulado', 'reversa_fallida'])
        OR motivo IS NOT NULL));

REVOKE ALL ON TABLE bloqueo_efecto FROM app_decide, app_ingest, app_jev;

COMMENT ON TABLE bloqueo_efecto IS
  'JEV ADS 03: pasos de un aprobado en Amazon, append-only. El estado del '
  'aprobado es el ultimo paso de estas filas, no un paso colgado del quitado.';
COMMENT ON COLUMN bloqueo_efecto.id IS
  'Identidad de la fila. El spec no la nombra; sin ella dos '
  'reversa_intencion del mismo aprobado no tienen PK.';
COMMENT ON COLUMN bloqueo_efecto.veredicto_id IS
  'El aprobado cuyos pasos son estas filas. Quitar no cuelga el paso del '
  'quitado: el quitado es otro veredicto y el paso sigue aqui.';
COMMENT ON COLUMN bloqueo_efecto.negative_keyword_id IS
  'Id de Amazon del negativo. Texto, sin FK: los negativos no entran a '
  'ad_entity.';
COMMENT ON COLUMN bloqueo_efecto.respuesta IS
  'Cuerpo ya redactado por quien escribe. La migracion no redacta.';

-- ===========================================================================
-- 3. APPEND-ONLY (modelo 0052; la funcion vive en 0001)
-- ===========================================================================

CREATE TRIGGER bloqueo_veredicto_append_only
    BEFORE UPDATE OR DELETE ON bloqueo_veredicto
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER bloqueo_veredicto_append_only_truncate
    BEFORE TRUNCATE ON bloqueo_veredicto
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER bloqueo_efecto_append_only
    BEFORE UPDATE OR DELETE ON bloqueo_efecto
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER bloqueo_efecto_append_only_truncate
    BEFORE TRUNCATE ON bloqueo_efecto
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Un paso solo cuelga de un aprobado y un quitado solo cita un aprobado.
CREATE FUNCTION bloqueo_exige_aprobado() RETURNS trigger
    LANGUAGE plpgsql AS $f$
DECLARE
    ref UUID;
BEGIN
    IF TG_TABLE_NAME = 'bloqueo_efecto' THEN
        ref := NEW.veredicto_id;
    ELSIF NEW.tipo = 'quitado' THEN
        ref := NEW.origen_id;
    ELSE
        RETURN NEW;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM bloqueo_veredicto WHERE id = ref AND tipo = 'aprobado') THEN
        RAISE EXCEPTION 'bloqueo: % exige un veredicto aprobado (%)', TG_TABLE_NAME, ref
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$f$;

CREATE TRIGGER bloqueo_efecto_exige_aprobado
    BEFORE INSERT ON bloqueo_efecto
    FOR EACH ROW EXECUTE FUNCTION bloqueo_exige_aprobado();
CREATE TRIGGER bloqueo_veredicto_quitado_exige_aprobado
    BEFORE INSERT ON bloqueo_veredicto
    FOR EACH ROW EXECUTE FUNCTION bloqueo_exige_aprobado();

-- ===========================================================================
-- 4. INDICES (FK no encabezada por la PK)
-- ===========================================================================

CREATE INDEX bloqueo_veredicto_ad_group_idx ON bloqueo_veredicto (ad_group_id);
CREATE INDEX bloqueo_veredicto_senal_idx ON bloqueo_veredicto (senal_id);
CREATE INDEX bloqueo_veredicto_origen_idx ON bloqueo_veredicto (origen_id);
CREATE INDEX bloqueo_efecto_veredicto_idx ON bloqueo_efecto (veredicto_id);

-- ===========================================================================
-- 5. GRANTS
-- ===========================================================================

GRANT SELECT, INSERT ON bloqueo_veredicto, bloqueo_efecto TO app_admin;
GRANT SELECT ON bloqueo_veredicto, bloqueo_efecto TO app_read;

-- ===========================================================================
-- 6. VERIFICACION (aborta si el ciclo o app_jev leen, o si falta un grant)
-- ===========================================================================

DO $$
DECLARE
    v_t TEXT;
    v_rol TEXT;
BEGIN
    FOREACH v_t IN ARRAY ARRAY['bloqueo_veredicto', 'bloqueo_efecto'] LOOP
        IF NOT has_table_privilege('app_admin', v_t, 'SELECT')
           OR NOT has_table_privilege('app_admin', v_t, 'INSERT')
           OR has_table_privilege('app_admin', v_t, 'UPDATE')
           OR has_table_privilege('app_admin', v_t, 'DELETE') THEN
            RAISE EXCEPTION '0074: privilegios de app_admin sobre % invalidos', v_t;
        END IF;
        IF NOT has_table_privilege('app_read', v_t, 'SELECT')
           OR has_table_privilege('app_read', v_t, 'INSERT')
           OR has_table_privilege('app_read', v_t, 'UPDATE')
           OR has_table_privilege('app_read', v_t, 'DELETE') THEN
            RAISE EXCEPTION '0074: privilegios de app_read sobre % invalidos', v_t;
        END IF;
        FOREACH v_rol IN ARRAY ARRAY['app_decide', 'app_ingest', 'app_jev'] LOOP
            IF has_table_privilege(v_rol, v_t, 'SELECT')
               OR has_table_privilege(v_rol, v_t, 'INSERT')
               OR has_table_privilege(v_rol, v_t, 'UPDATE')
               OR has_table_privilege(v_rol, v_t, 'DELETE') THEN
                RAISE EXCEPTION '0074: % no debe tocar %', v_rol, v_t;
            END IF;
        END LOOP;
    END LOOP;
END
$$;

COMMIT;
