-- 0066 (BIDS 02 I.4, seccion 5): retiro de product ads de bolsas
-- viejas y su reposicion.
--
-- Dos tablas append-only en vez de una fila que se actualiza:
-- "retirado y no repuesto" se DERIVA (v_anuncio_retirado). Un anuncio
-- tiene como maximo un retiro ABIERTO (sin fila en reposicion); ya
-- repuesto acepta un retiro nuevo. `modo` dice como salio: 'pausa'
-- (sonda 1 de V.0 sellada: PUT /sp/productAds con state) o 'archivo'
-- (camino viejo sin sello).
-- (Bloque "0062" de docs/evidencia/bids-02/diseno/datos.sql; el numero
-- real es 0066.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0066_reversa_bids02_retiro_anuncios.sql.
--
-- OJO: el motivo se escribe con ANY, nunca con IN: un test de
-- test_apply_schema.py pinza la lista de MOTIVOS_SIN_APLICAR de la
-- ULTIMA migracion que contiene cierto texto, y esta no es esa.

BEGIN;

CREATE TABLE anuncio_retiro (
    id                BIGSERIAL PRIMARY KEY,
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
    retirado_el       TIMESTAMPTZ NOT NULL,
    actor             TEXT        NOT NULL,
    CONSTRAINT retiro_motivo CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia'])),
    CONSTRAINT retiro_modo CHECK (modo = ANY (ARRAY['pausa', 'archivo'])),
    CONSTRAINT retiro_impulso_coherente CHECK ((motivo = 'impulso') = (impulso_id IS NOT NULL))
);

CREATE TABLE anuncio_reposicion (
    retiro_id        BIGINT      PRIMARY KEY REFERENCES anuncio_retiro (id),
    ad_id_nuevo      TEXT        NOT NULL,
    estado_nuevo     TEXT        NOT NULL,
    repuesto_el      TIMESTAMPTZ NOT NULL,
    actor            TEXT        NOT NULL,
    CONSTRAINT reposicion_estado CHECK (estado_nuevo = ANY (ARRAY['ENABLED', 'PAUSED']))
);

CREATE VIEW v_anuncio_retirado AS
SELECT r.*, (p.retiro_id IS NOT NULL) AS repuesto
  FROM anuncio_retiro r
  LEFT JOIN anuncio_reposicion p ON p.retiro_id = r.id;

-- Trigger (no UNIQUE): un retiro nuevo de un anuncio con un retiro
-- sin fila en anuncio_reposicion se rechaza.
CREATE FUNCTION anuncio_retiro_sin_abierto() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM anuncio_retiro r
     WHERE r.ad_entity_id = NEW.ad_entity_id
       AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id);
    IF FOUND THEN
        RAISE EXCEPTION 'anuncio % ya tiene un retiro abierto', NEW.ad_entity_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER anuncio_retiro_sin_abierto
    BEFORE INSERT ON anuncio_retiro
    FOR EACH ROW EXECUTE FUNCTION anuncio_retiro_sin_abierto();

COMMENT ON TABLE anuncio_retiro IS
    'BIDS 02 I.4: product ad sacado de una bolsa vieja, append-only. Un retiro abierto por anuncio.';
COMMENT ON TABLE anuncio_reposicion IS
    'BIDS 02 I.4: regreso del anuncio a su ad group, una fila por retiro (regla 7).';

-- Append-only: nadie hace UPDATE ni DELETE sobre las dos tablas
-- (trigger, no solo GRANT).
CREATE FUNCTION anuncio_retiro_inmutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'retiro es append-only: % no permitido en %', TG_OP, TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER anuncio_retiro_sin_update_delete
    BEFORE UPDATE OR DELETE ON anuncio_retiro
    FOR EACH ROW EXECUTE FUNCTION anuncio_retiro_inmutable();

CREATE TRIGGER anuncio_reposicion_sin_update_delete
    BEFORE UPDATE OR DELETE ON anuncio_reposicion
    FOR EACH ROW EXECUTE FUNCTION anuncio_retiro_inmutable();

GRANT SELECT, INSERT ON anuncio_retiro, anuncio_reposicion TO app_admin;
GRANT SELECT ON anuncio_retiro, anuncio_reposicion TO app_read;
REVOKE SELECT ON anuncio_retiro, anuncio_reposicion FROM app_ingest, app_decide;
GRANT USAGE, SELECT ON SEQUENCE anuncio_retiro_id_seq TO app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_admin', 'anuncio_retiro', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'anuncio_retiro', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'anuncio_reposicion', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'anuncio_reposicion', 'INSERT')
       OR NOT has_table_privilege('app_read', 'anuncio_retiro', 'SELECT')
       OR NOT has_table_privilege('app_read', 'anuncio_reposicion', 'SELECT')
       OR has_table_privilege('app_read', 'anuncio_retiro', 'INSERT')
       OR has_table_privilege('app_read', 'anuncio_reposicion', 'INSERT')
       OR has_table_privilege('app_decide', 'anuncio_retiro', 'SELECT')
       OR has_table_privilege('app_ingest', 'anuncio_reposicion', 'SELECT')
       OR has_table_privilege('app_admin', 'anuncio_retiro', 'UPDATE')
       OR has_table_privilege('app_admin', 'anuncio_reposicion', 'DELETE') THEN
        RAISE EXCEPTION '0066: privilegios de anuncio_retiro(_reposicion) distintos de los declarados';
    END IF;
END;
$$;

COMMIT;
