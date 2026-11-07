-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'anuncio_retiro' AND column_name = 'autoria') AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'anuncio_retiro_intencion' AND column_name = 'decision_id') AND to_regclass('public.anuncio_recreacion_intento') IS NOT NULL
-- 0078 (AUTO-03 G.1): el ledger de retiro de product ads distingue la
-- autoria y admite la pausa por producto que decide el ciclo.
--
-- anuncio_retiro.autoria: 'confirmada' si el PUT de Orbit movio el anuncio
-- de ENABLED a PAUSED; 'adoptada' si Orbit lo encontro ya PAUSED al
-- confirmar (retoma de un PUT incierto, reconciliacion, o ya_estaba): el
-- estado esta probado, la autoria no, y `repone` no lo reactiva salvo que
-- un humano lo pida (S14). Las filas que ya existen nacen 'confirmada' por
-- el DEFAULT: es lo que el codigo anterior presumia de todas.
--
-- producto_sin_ventas entra en el vocabulario de motivo de las dos tablas.
-- Solo ese motivo trae decision_id en la intencion (UNIQUE: una intencion
-- por decision) y ningun otro lo trae.
--
-- El trigger solo-cierre de 0069 gana decision_id entre las columnas que
-- no cambian. app_admin ya tiene INSERT y SELECT de tabla (cubre las
-- columnas nuevas) y UPDATE solo de cerrada_el/detalle/retiro_id.
--
-- anuncio_recreacion_intento: la recreacion explicita (`recrea`) escribe su
-- intento ANTES del POST que crea otro anuncio, y guarda el adId que trajo
-- el ack. Si la respuesta es incierta, el intento queda abierto y ningun
-- reintento manda otro POST mientras siga abierto. Solo se cierra con
-- evidencia: junto con la reposicion (misma transaccion) cuando Amazon
-- muestra el anuncio, o con el rechazo explicito del create. Uno abierto por
-- retiro. Solo se escribe el adId (NULL -> valor, una vez) y el cierre (una
-- vez).
--
-- OJO: el motivo se escribe con ANY, nunca con IN (ver 0066).
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0078_reversa_auto03_retiro_decision.sql (aborta si ya hay filas con el
-- motivo nuevo o retiros adoptados).

ALTER TABLE anuncio_retiro
    ADD COLUMN autoria TEXT NOT NULL DEFAULT 'confirmada'
        CONSTRAINT retiro_autoria CHECK (autoria = ANY (ARRAY['confirmada', 'adoptada']));

ALTER TABLE anuncio_retiro DROP CONSTRAINT retiro_motivo;
ALTER TABLE anuncio_retiro ADD CONSTRAINT retiro_motivo
    CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia', 'producto_sin_ventas']));

ALTER TABLE anuncio_retiro_intencion DROP CONSTRAINT intencion_motivo;
ALTER TABLE anuncio_retiro_intencion ADD CONSTRAINT intencion_motivo
    CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia', 'producto_sin_ventas']));

ALTER TABLE anuncio_retiro_intencion
    ADD COLUMN decision_id BIGINT NULL
        CONSTRAINT intencion_decision_unica UNIQUE
        CONSTRAINT intencion_decision_fk REFERENCES decision (id),
    ADD CONSTRAINT intencion_decision_coherente
        CHECK ((motivo = 'producto_sin_ventas') = (decision_id IS NOT NULL));

CREATE OR REPLACE FUNCTION anuncio_retiro_intencion_0069_solo_cierre() RETURNS trigger
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
       OR OLD.decision_id IS DISTINCT FROM NEW.decision_id
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

CREATE TABLE anuncio_recreacion_intento (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    retiro_id   BIGINT      NOT NULL REFERENCES anuncio_retiro (id),
    actor       TEXT        NOT NULL,
    creada_el   TIMESTAMPTZ NOT NULL,
    ad_id_nuevo TEXT        NULL,
    cerrada_el  TIMESTAMPTZ NULL,
    detalle     TEXT        NULL,
    CONSTRAINT recreacion_cierre_con_detalle CHECK ((cerrada_el IS NULL) = (detalle IS NULL))
);

CREATE UNIQUE INDEX anuncio_recreacion_intento_uno_abierto
    ON anuncio_recreacion_intento (retiro_id) WHERE cerrada_el IS NULL;

CREATE FUNCTION anuncio_recreacion_intento_0078_solo_avance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.retiro_id IS DISTINCT FROM NEW.retiro_id
       OR OLD.actor IS DISTINCT FROM NEW.actor
       OR OLD.creada_el IS DISTINCT FROM NEW.creada_el
       OR OLD.cerrada_el IS NOT NULL
       OR (OLD.ad_id_nuevo IS NOT NULL AND OLD.ad_id_nuevo IS DISTINCT FROM NEW.ad_id_nuevo)
       OR (OLD.ad_id_nuevo IS NOT DISTINCT FROM NEW.ad_id_nuevo
           AND OLD.cerrada_el IS NOT DISTINCT FROM NEW.cerrada_el) THEN
        RAISE EXCEPTION
            'anuncio_recreacion_intento: SOLO-AVANCE: un intento abierto solo gana su adId '
            '(una vez) o se cierra (una vez), sin tocar lo demas'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER anuncio_recreacion_intento_update_solo_avance
    BEFORE UPDATE ON anuncio_recreacion_intento
    FOR EACH ROW EXECUTE FUNCTION anuncio_recreacion_intento_0078_solo_avance();
CREATE TRIGGER anuncio_recreacion_intento_sin_delete
    BEFORE DELETE ON anuncio_recreacion_intento
    FOR EACH ROW EXECUTE FUNCTION anuncio_retiro_inmutable();

GRANT SELECT, INSERT ON anuncio_recreacion_intento TO app_admin;
GRANT UPDATE (ad_id_nuevo, cerrada_el, detalle) ON anuncio_recreacion_intento TO app_admin;
GRANT SELECT ON anuncio_recreacion_intento TO app_read;
GRANT USAGE, SELECT ON SEQUENCE anuncio_recreacion_intento_id_seq TO app_admin;

COMMENT ON TABLE anuncio_recreacion_intento IS
    'AUTO-03 G.3: intento de recrear el anuncio de un retiro, escrito ANTES del POST. Abierto = Amazon pudo haberlo creado (ad_id_nuevo si el ack lo trajo): ningun otro POST para ese retiro hasta resolverlo.';

COMMENT ON COLUMN anuncio_retiro.autoria IS
    'AUTO-03 G.1: confirmada = el PUT de Orbit lo movio de ENABLED a PAUSED; adoptada = Orbit lo encontro ya PAUSED al confirmar (estado probado, autoria no). repone no reactiva adoptados sin incluye_adoptados (S14).';
COMMENT ON COLUMN anuncio_retiro_intencion.decision_id IS
    'AUTO-03 G.1: la decision pause sobre product_ad que autoriza este retiro (motivo producto_sin_ventas); NULL en los motivos humanos.';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM anuncio_retiro WHERE autoria <> 'confirmada') THEN
        RAISE EXCEPTION '0078: un retiro previo quedo con autoria distinta de confirmada';
    END IF;
    IF NOT has_table_privilege('app_admin', 'anuncio_retiro', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'anuncio_retiro_intencion', 'INSERT')
       OR has_column_privilege('app_admin', 'anuncio_retiro_intencion', 'decision_id', 'UPDATE')
       OR has_table_privilege('app_decide', 'anuncio_retiro_intencion', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'anuncio_recreacion_intento', 'INSERT')
       OR NOT has_column_privilege('app_admin', 'anuncio_recreacion_intento', 'ad_id_nuevo', 'UPDATE')
       OR has_column_privilege('app_admin', 'anuncio_recreacion_intento', 'retiro_id', 'UPDATE')
       OR has_table_privilege('app_read', 'anuncio_recreacion_intento', 'INSERT') THEN
        RAISE EXCEPTION '0078: privilegios de anuncio_retiro(_intencion) distintos de los de 0066/0069';
    END IF;
END;
$$;
