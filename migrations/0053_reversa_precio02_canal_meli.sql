-- Reversa de la 0053: Postgres no permite quitar un valor de un enum. El
-- valor 'meli' queda agregado y sin uso; ningun dato de produccion lo trae
-- mientras la reversa corra antes que el codigo del carril M.
BEGIN;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
         WHERE t.typname = 'estimacion_canal' AND e.enumlabel = 'meli'
    ) THEN
        RAISE NOTICE 'reversa 0053: estimacion_canal conserva el valor meli (Postgres no lo quita)';
    ELSE
        RAISE NOTICE 'reversa 0053: estimacion_canal nunca trajo el valor meli';
    END IF;
END
$$;
COMMIT;
