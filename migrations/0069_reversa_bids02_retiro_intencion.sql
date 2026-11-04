-- Reversa de 0069 (BIDS 02 R09-r4): quita triggers, funcion e
-- indice y tabla de intenciones. Aborta si hay una intencion
-- pendiente (hay estado que la reversa perderia). No idempotente
-- (regla 7).

BEGIN;

DO $$
BEGIN
    IF (SELECT count(*) FROM anuncio_retiro_intencion WHERE cerrada_el IS NULL) > 0 THEN
        RAISE EXCEPTION '0069 reversa: hay intenciones de retiro pendientes, no se revierte';
    END IF;
END;
$$;

DROP TRIGGER anuncio_retiro_intencion_sin_delete ON anuncio_retiro_intencion;
DROP TRIGGER anuncio_retiro_intencion_update_solo_cierre ON anuncio_retiro_intencion;
DROP FUNCTION anuncio_retiro_intencion_0069_solo_cierre();
DROP INDEX anuncio_retiro_intencion_una_pendiente;
DROP TABLE anuncio_retiro_intencion;

COMMIT;
