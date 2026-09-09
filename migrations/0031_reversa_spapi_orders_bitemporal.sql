-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0031 (patron 0011_reversa_*).
--
-- Deshace `0031_spapi_orders_bitemporal.sql`: borra la vista
-- `v_spapi_order_ultima`, quita la columna `fulfillment_status` y restaura
-- la clave de tres columnas (platform, amazon_order_id, last_updated_time).
--
-- Solo aplica ANTES de la primera re-observacion: despues habria filas
-- duplicadas en la tripleta (legitimas bajo la clave bitemporal) y
-- restaurar la clave de tres las dejaria huerfanas. La guarda de abajo
-- aborta en ese caso sin escribir nada.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

-- Guarda: ninguna tripleta repetida (sin re-observaciones todavia).
DO $$
DECLARE
    v_repetidas INTEGER;
BEGIN
    SELECT count(*) INTO v_repetidas FROM (
        SELECT platform, amazon_order_id, last_updated_time
          FROM spapi_order_observation
         GROUP BY platform, amazon_order_id, last_updated_time
        HAVING count(*) > 1
    ) dup;
    IF v_repetidas > 0 THEN
        RAISE EXCEPTION
            'reversa 0031: hay % tripletas re-observadas; ya no aplica',
            v_repetidas;
    END IF;
END $$;

DROP VIEW v_spapi_order_ultima;

ALTER TABLE spapi_order_observation
    DROP COLUMN fulfillment_status;

ALTER TABLE spapi_order_observation
    DROP CONSTRAINT spapi_order_clave_unica;
ALTER TABLE spapi_order_observation
    ADD CONSTRAINT spapi_order_clave_unica
    UNIQUE (platform, amazon_order_id, last_updated_time);

-- Verificacion: volvimos al estado previo a 0031.
DO $$
DECLARE
    v_def TEXT;
    v_vista INTEGER;
    v_col INTEGER;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def
      FROM pg_constraint
     WHERE conname = 'spapi_order_clave_unica'
       AND conrelid = 'spapi_order_observation'::regclass;
    IF v_def IS NULL OR v_def NOT LIKE '%(platform, amazon_order_id, last_updated_time)%' THEN
        RAISE EXCEPTION 'reversa 0031: la clave no quedo en tres columnas: %', v_def;
    END IF;

    SELECT count(*) INTO v_vista
      FROM pg_views
     WHERE schemaname = 'public' AND viewname = 'v_spapi_order_ultima';
    IF v_vista <> 0 THEN
        RAISE EXCEPTION 'reversa 0031: la vista v_spapi_order_ultima sigue viva';
    END IF;

    SELECT count(*) INTO v_col
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'spapi_order_observation'
       AND column_name = 'fulfillment_status';
    IF v_col <> 0 THEN
        RAISE EXCEPTION 'reversa 0031: la columna fulfillment_status sigue viva';
    END IF;
END $$;

COMMIT;
