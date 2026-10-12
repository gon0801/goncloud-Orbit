-- Reversa de 0065 (BIDS 02 I.3): quita vista, triggers, funciones,
-- tablas y devuelve el CHECK de fabrica_lote a sus cuatro estados.
-- Aborta si impulso tiene una fila. No idempotente (regla 7).

BEGIN;

DO $$
BEGIN
    IF (SELECT count(*) FROM impulso) > 0 THEN
        RAISE EXCEPTION '0065 reversa: impulso tiene filas, no se revierte';
    END IF;
END;
$$;

DROP VIEW v_impulso_estado;
DROP TRIGGER impulso_lectura_sin_update_delete ON impulso_lectura;
DROP TRIGGER impulso_sin_update_delete ON impulso;
DROP FUNCTION impulso_inmutable();
DROP TRIGGER lectura_moneda_coherente ON impulso_lectura;
DROP FUNCTION lectura_moneda_coherente();
DROP TRIGGER impulso_coherente ON impulso;
DROP FUNCTION impulso_coherente();
DROP TABLE impulso_lectura;
DROP TABLE impulso;
ALTER TABLE fabrica_lote DROP CONSTRAINT fabrica_lote_estado_check;
ALTER TABLE fabrica_lote ADD CONSTRAINT fabrica_lote_estado_check
    CHECK (estado IN ('planeado', 'applied', 'failed', 'desarmado'));

COMMIT;
