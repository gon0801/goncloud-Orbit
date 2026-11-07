-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'anuncio_retiro' AND column_name = 'autoria') AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'anuncio_retiro_intencion' AND column_name = 'decision_id')
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
       OR has_table_privilege('app_decide', 'anuncio_retiro_intencion', 'SELECT') THEN
        RAISE EXCEPTION '0078: privilegios de anuncio_retiro(_intencion) distintos de los de 0066/0069';
    END IF;
END;
$$;
