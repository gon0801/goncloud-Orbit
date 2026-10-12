-- 0068 (BIDS 02 R09-r4): actor de la pausa y hecho de graduacion.
--
-- 1) fabrica_lote.pausado_por: actor que dejo el lote en 'pausado'
-- ('impulso-vigia' o el dueno). Graduar solo reactiva lo que pauso el
-- vigia; si pauso el dueno, gradua sin reencender.
-- 2) impulso_lectura.hecho 'graduado_sin_reencender': graduar solo
-- reactiva lo que pauso el vigia; si pauso el dueno, gradua sin
-- reencender y lo dice con este hecho (G1/G2).
--
-- (El sello de retiro vive en 0069, tabla propia
-- anuncio_retiro_intencion: anuncio_retiro solo recibe filas
-- selladas, "fila = retirado" para todos los lectores.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0068_reversa_bids02_sellos.sql.

BEGIN;

ALTER TABLE fabrica_lote ADD COLUMN pausado_por TEXT NULL;

COMMENT ON COLUMN fabrica_lote.pausado_por IS
    'BIDS 02 R09-r4: actor que dejo el lote en pausado (impulso-vigia o dueno). Graduar solo reactiva lo del vigia.';

-- 2) impulso_lectura.hecho 'graduado_sin_reencender': graduar solo
-- reactiva lo que pauso el vigia; si pauso el dueno, gradua sin
-- reencender y lo dice con este hecho (G1/G2).
ALTER TABLE impulso_lectura DROP CONSTRAINT lectura_hecho;
ALTER TABLE impulso_lectura ADD CONSTRAINT lectura_hecho CHECK (hecho IN
    ('nada', 'pausado', 'graduado', 'graduado_sin_reencender', 'retiro_de_bolsas'));

COMMIT;
