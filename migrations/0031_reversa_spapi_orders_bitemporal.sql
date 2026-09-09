-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0031 (patron 0011_reversa_*).
--
-- Deshace `0031_spapi_orders_bitemporal.sql`: borra la vista
-- `v_spapi_order_ultima`, quita la columna `fulfillment_status`, restaura
-- la clave de tres columnas (platform, amazon_order_id, last_updated_time)
-- y devuelve el COMMENT ON TABLE al texto de 0030.
--
-- Solo aplica ANTES de la primera re-observacion y de cualquier estado de
-- envio capturado: despues habria filas duplicadas en la tripleta
-- (legitimas bajo la clave bitemporal) y restaurar la clave de tres las
-- dejaria huerfanas; y una sola corrida ya puede poblar
-- fulfillment_status, que el DROP COLUMN borraria irrecuperable
-- (CodeRabbit PR #240). La guarda de abajo aborta en ambos casos sin
-- escribir nada.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

-- Guarda: ninguna tripleta repetida (sin re-observaciones todavia) y
-- ningun fulfillment_status poblado (una sola corrida basta para que el
-- DROP COLUMN pierda dato irrecuperable).
DO $$
DECLARE
    v_repetidas INTEGER;
    v_con_estado INTEGER;
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

    SELECT count(*) INTO v_con_estado
      FROM spapi_order_observation
     WHERE fulfillment_status IS NOT NULL;
    IF v_con_estado > 0 THEN
        RAISE EXCEPTION
            'reversa 0031: hay % filas con fulfillment_status; ya no aplica',
            v_con_estado;
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

-- 0031 refresco el COMMENT ON TABLE a la clave bitemporal; aqui vuelve
-- al texto original de 0030.
COMMENT ON TABLE spapi_order_observation IS
    'SP-API 01 A.2: resumenes searchOrders 2026-01-01 append-only por '
    '(platform, amazon_order_id, last_updated_time). La ventana diaria usa '
    'lastUpdatedAfter = max(last_updated_time) - 1 dia de solape; la '
    'primera corrida usa createdAfter = ahora - 30 dias. Sin PII: sin '
    'columnas de comprador ni direccion.';

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
