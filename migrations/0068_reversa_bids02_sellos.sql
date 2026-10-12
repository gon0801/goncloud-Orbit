-- Reversa de 0068 (BIDS 02 R09-r4): quita el actor de la pausa y
-- el hecho de graduacion. Aborta si hay lotes pausados con actor o
-- lecturas graduadas sin reencender (hay estado que la reversa
-- perderia). No idempotente (regla 7).

BEGIN;

DO $$
BEGIN
    IF (SELECT count(*) FROM fabrica_lote WHERE pausado_por IS NOT NULL) > 0 THEN
        RAISE EXCEPTION '0068 reversa: hay lotes pausados con actor, no se revierte';
    END IF;
    IF (SELECT count(*) FROM impulso_lectura WHERE hecho = 'graduado_sin_reencender') > 0 THEN
        RAISE EXCEPTION '0068 reversa: hay lecturas graduadas sin reencender, no se revierte';
    END IF;
END;
$$;

ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_hecho;
ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_hecho CHECK (hecho IN
    ('nada', 'pausado', 'graduado', 'retiro_de_bolsas'));
ALTER TABLE fabrica_lote DROP COLUMN pausado_por;

COMMIT;
