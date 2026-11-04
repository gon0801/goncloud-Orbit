-- Reversa de 0066 (BIDS 02 I.4): quita vista, triggers, funciones y
-- tablas. Aborta si anuncio_retiro tiene una fila. No idempotente
-- (regla 7).

BEGIN;

DO $$
BEGIN
    IF (SELECT count(*) FROM anuncio_retiro) > 0 THEN
        RAISE EXCEPTION '0066 reversa: anuncio_retiro tiene filas, no se revierte';
    END IF;
END;
$$;

DROP VIEW v_anuncio_retirado;
DROP TRIGGER anuncio_reposicion_sin_update_delete ON anuncio_reposicion;
DROP TRIGGER anuncio_retiro_sin_update_delete ON anuncio_retiro;
DROP TRIGGER anuncio_retiro_sin_abierto ON anuncio_retiro;
DROP FUNCTION anuncio_retiro_inmutable();
DROP FUNCTION anuncio_retiro_sin_abierto();
DROP TABLE anuncio_reposicion;
DROP TABLE anuncio_retiro;

COMMIT;
