-- 0069 (BIDS 02 R09-r4): intenciones de retiro ANTES del HTTP.
--
-- Patron campana_ajuste (0067) con tabla propia: la intencion se
-- inserta ANTES del PUT y la fila de `anuncio_retiro` solo se escribe
-- tras el readback que confirma. Asi `anuncio_retiro` vuelve a ser
-- "fila = retirado" para TODOS los lectores (pantalla, vigia, alcance
-- y cualquier SELECT directo): una intencion sin sello no cuenta como
-- retirado en ninguna lectura porque vive en otra tabla. El reintento
-- retoma la intencion pendiente y manda el PUT; regresar una
-- intencion es imposible estructuralmente (no tiene retiro_id).
--
-- OJO: el motivo se escribe con ANY, nunca con IN (ver 0066: un test
-- de test_apply_schema.py pinza la lista de MOTIVOS_SIN_APLICAR de la
-- ULTIMA migracion que contiene cierto texto).
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0069_reversa_bids02_retiro_intencion.sql.

BEGIN;

CREATE TABLE anuncio_retiro_intencion (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform          platform    NOT NULL,
    motivo            TEXT        NOT NULL,
    modo              TEXT        NOT NULL,
    lote_retiro       TEXT        NOT NULL,
    impulso_id        BIGINT      REFERENCES impulso (id),
    ad_entity_id      BIGINT      NOT NULL REFERENCES ad_entity (id),
    ad_id_externo     TEXT        NOT NULL,
    ad_group_externo  TEXT        NOT NULL,
    campana_externa   TEXT        NOT NULL,
    sku               TEXT        NOT NULL,
    asin              TEXT        NOT NULL,
    reversa           TEXT        NOT NULL,
    actor             TEXT        NOT NULL,
    creada_el         TIMESTAMPTZ NOT NULL,
    cerrada_el        TIMESTAMPTZ NULL,
    detalle           TEXT        NULL,
    retiro_id         BIGINT      NULL REFERENCES anuncio_retiro (id),
    CONSTRAINT intencion_motivo CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia'])),
    CONSTRAINT intencion_modo CHECK (modo = ANY (ARRAY['pausa', 'archivo'])),
    CONSTRAINT intencion_impulso_coherente CHECK ((motivo = 'impulso') = (impulso_id IS NOT NULL))
);

-- Una intencion pendiente por anuncio: el reintento retoma LA
-- pendiente, no abre otra.
CREATE UNIQUE INDEX anuncio_retiro_intencion_una_pendiente
    ON anuncio_retiro_intencion (ad_entity_id) WHERE cerrada_el IS NULL;

-- Solo-cierre: el unico UPDATE legitimo cierra una intencion
-- pendiente (cerrada_el NULL -> valor, con su detalle y, si el
-- readback confirmo, el retiro que sello). Lo demas es historia.
CREATE FUNCTION anuncio_retiro_intencion_0069_solo_cierre() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.platform IS DISTINCT FROM NEW.platform
       OR OLD.motivo IS DISTINCT FROM NEW.motivo
       OR OLD.modo IS DISTINCT FROM NEW.modo
       OR OLD.lote_retiro IS DISTINCT FROM NEW.lote_retiro
       OR OLD.impulso_id IS DISTINCT FROM NEW.impulso_id
       OR OLD.ad_entity_id IS DISTINCT FROM NEW.ad_entity_id
       OR OLD.ad_id_externo IS DISTINCT FROM NEW.ad_id_externo
       OR OLD.ad_group_externo IS DISTINCT FROM NEW.ad_group_externo
       OR OLD.campana_externa IS DISTINCT FROM NEW.campana_externa
       OR OLD.sku IS DISTINCT FROM NEW.sku
       OR OLD.asin IS DISTINCT FROM NEW.asin
       OR OLD.reversa IS DISTINCT FROM NEW.reversa
       OR OLD.actor IS DISTINCT FROM NEW.actor
       OR OLD.creada_el IS DISTINCT FROM NEW.creada_el
       OR OLD.cerrada_el IS NOT DISTINCT FROM NEW.cerrada_el
       OR OLD.cerrada_el IS NOT NULL THEN
        RAISE EXCEPTION
            'anuncio_retiro_intencion: SOLO-CIERRE: el unico UPDATE valido cierra '
            'cerrada_el de una intencion pendiente, sin tocar lo demas'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER anuncio_retiro_intencion_update_solo_cierre
    BEFORE UPDATE ON anuncio_retiro_intencion
    FOR EACH ROW EXECUTE FUNCTION anuncio_retiro_intencion_0069_solo_cierre();
CREATE TRIGGER anuncio_retiro_intencion_sin_delete
    BEFORE DELETE ON anuncio_retiro_intencion
    FOR EACH ROW EXECUTE FUNCTION anuncio_retiro_inmutable();

COMMENT ON TABLE anuncio_retiro_intencion IS
    'BIDS 02 R09-r4: intencion de retiro escrita ANTES del PUT (patron campana_ajuste 0067, tabla propia). cerrada_el NULL = pendiente (el reintento la retoma); con valor = cerrada (retiro_id apunta al retiro si el readback confirmo). anuncio_retiro solo recibe filas selladas.';
COMMENT ON COLUMN anuncio_retiro_intencion.retiro_id IS
    'BIDS 02 R09-r4: el retiro que sello esta intencion, o NULL si se cerro sin retirar (rechazo limpio).';

GRANT SELECT, INSERT ON anuncio_retiro_intencion TO app_admin;
GRANT UPDATE (cerrada_el, detalle, retiro_id) ON anuncio_retiro_intencion TO app_admin;
GRANT SELECT ON anuncio_retiro_intencion TO app_read;
REVOKE SELECT ON anuncio_retiro_intencion FROM app_ingest, app_decide;
GRANT USAGE, SELECT ON SEQUENCE anuncio_retiro_intencion_id_seq TO app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_admin', 'anuncio_retiro_intencion', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'anuncio_retiro_intencion', 'INSERT')
       OR NOT has_column_privilege('app_admin', 'anuncio_retiro_intencion', 'cerrada_el', 'UPDATE')
       OR NOT has_column_privilege('app_admin', 'anuncio_retiro_intencion', 'detalle', 'UPDATE')
       OR NOT has_column_privilege('app_admin', 'anuncio_retiro_intencion', 'retiro_id', 'UPDATE')
       OR NOT has_table_privilege('app_read', 'anuncio_retiro_intencion', 'SELECT')
       OR has_table_privilege('app_read', 'anuncio_retiro_intencion', 'INSERT')
       OR has_table_privilege('app_decide', 'anuncio_retiro_intencion', 'SELECT')
       OR has_table_privilege('app_ingest', 'anuncio_retiro_intencion', 'SELECT') THEN
        RAISE EXCEPTION '0069: privilegios de anuncio_retiro_intencion distintos de los declarados';
    END IF;
    IF NOT has_sequence_privilege('app_admin', 'anuncio_retiro_intencion_id_seq', 'USAGE')
       OR NOT has_sequence_privilege('app_admin', 'anuncio_retiro_intencion_id_seq', 'SELECT') THEN
        RAISE EXCEPTION '0069: privilegios de secuencia invalidos';
    END IF;
END;
$$;

COMMIT;
