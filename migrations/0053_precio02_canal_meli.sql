-- REPRICING 02 corte 0.a: el canal `meli` entra al enum `estimacion_canal`.
--
-- Va en su PROPIA corrida de psql, separada de la 0054: Postgres no permite
-- usar un valor agregado con ADD VALUE dentro de la misma transaccion que lo
-- agrega. No re-ejecutable (un segundo ADD VALUE del mismo literal falla).
BEGIN;
ALTER TYPE estimacion_canal ADD VALUE 'meli';
COMMIT;
