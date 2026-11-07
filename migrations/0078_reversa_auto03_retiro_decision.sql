-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0078 (AUTO-03 G.1).
--
-- Quita anuncio_retiro.autoria y anuncio_retiro_intencion.decision_id,
-- restaura el vocabulario de motivo de 0066/0069 y el trigger solo-cierre de
-- 0069. Aborta si ya hay filas con motivo producto_sin_ventas o retiros
-- adoptados: las dos tablas son append-only y esa historia se perderia.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM anuncio_retiro WHERE motivo = 'producto_sin_ventas')
       OR EXISTS (SELECT 1 FROM anuncio_retiro_intencion WHERE motivo = 'producto_sin_ventas') THEN
        RAISE EXCEPTION 'reversa 0078: hay retiros o intenciones con motivo producto_sin_ventas';
    END IF;
    IF EXISTS (SELECT 1 FROM anuncio_retiro WHERE autoria = 'adoptada') THEN
        RAISE EXCEPTION 'reversa 0078: hay retiros con autoria adoptada';
    END IF;
END
$$;

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

ALTER TABLE anuncio_retiro_intencion DROP CONSTRAINT intencion_decision_coherente;
ALTER TABLE anuncio_retiro_intencion DROP COLUMN decision_id;

ALTER TABLE anuncio_retiro_intencion DROP CONSTRAINT intencion_motivo;
ALTER TABLE anuncio_retiro_intencion ADD CONSTRAINT intencion_motivo
    CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia']));

ALTER TABLE anuncio_retiro DROP CONSTRAINT retiro_motivo;
ALTER TABLE anuncio_retiro ADD CONSTRAINT retiro_motivo
    CHECK (motivo = ANY (ARRAY['impulso', 'sin_pedidos', 'subfamilia']));

ALTER TABLE anuncio_retiro DROP COLUMN autoria;
