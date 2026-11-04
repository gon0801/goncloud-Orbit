-- =============================================================================
--  ORBIT · REVERSA DE LA MIGRACIÓN 0011 ·  PostgreSQL 16
--
--  Deshace `0011_costo_historico_peseta.sql` y deja `sku_cost` exactamente
--  como estaba antes de aplicarla. Existe porque la regla del repo es que
--  ninguna acción irreversible se ejecuta sin su reversa YA implementada
--  (invariante 7, la misma que obligó a `reponer-anuncios` antes de
--  `archivar-anuncios`; hallazgo de CodeRabbit en el PR #116).
--
--  NO se aplica en el despliegue normal. Es la salida de emergencia si tras
--  aplicar 0011 se descubre que alguno de los dos importes históricos estaba
--  mal: se revierte, se corrige el número con el dueño, y 0011 se vuelve a
--  aplicar (tras esta reversa el estado de partida vuelve a ser el que sus
--  guardas exigen, así que 0011 es aplicable de nuevo).
--
--  QUÉ HACE, en orden:
--    1. Exige que el estado sea EXACTAMENTE el que dejó 0011. Si alguien ya
--       tocó esas vigencias, aborta sin escribir nada.
--    2. Borra las dos filas que creó 0011 (identificadas por su `ingest_run`
--       de `source = 'manual_costo_historico_0011'`, no por su importe).
--    3. Restaura la vigencia de plata que 0011 borró: 325.00 MXN desde
--       2026-08-18, abierta.
--    4. Verifica que quedó UNA vigencia por SKU, ambas del 2026-08-18, y que
--       el trigger append-only quedó ARMADO.
--
--  LO QUE NO RESTAURA, declarado (no es dinero, es procedencia): la fila de
--  plata vuelve con el `ingest_run` de ESTA reversa, no con el de la ingesta
--  de contabilidad que la creó en su día. Importe, moneda, `includes_tax` y
--  fechas vuelven idénticos — que es lo que leen la vista de contribución y
--  el motor. El runbook (docs/DEPLOY.md) pide capturar el estado previo
--  COMPLETO —`ingest_run_id` incluido— como evidencia antes de aplicar 0011,
--  justo para que ese dato no se pierda aunque esta reversa no lo reponga.
--
--  Los dos `ingest_run` (el de 0011 y el de esta reversa) se CONSERVAN: que
--  se aplicó y se revirtió es historia real, y borrarla sería justo el tipo
--  de reescritura que `sku_cost` existe para impedir.
--
--  Igual que 0011, esta reversa apaga el trigger append-only para sus dos
--  DELETE y lo re-enciende DENTRO de la misma transacción (un fallo entre
--  ambos hace ROLLBACK con la tabla protegida). NO es re-runnable.
-- =============================================================================

BEGIN;

-- ── 0. Procedencia de la reversa ────────────────────────────────────────────
CREATE TEMP TABLE _reversa_0011 ON COMMIT DROP AS
WITH nuevo AS (
    INSERT INTO ingest_run (source, rows_written, rows_skipped, ok, finished_at)
    VALUES ('manual_reversa_0011', 1, 0, TRUE, now())
    RETURNING id
)
SELECT id FROM nuevo;

-- ── 1. Guardas: el estado es EXACTAMENTE el que dejó 0011 ──────────────────
DO $$
DECLARE
    v_run    BIGINT;
    v_filas  INTEGER;
BEGIN
    SELECT id INTO v_run FROM ingest_run
     WHERE source = 'manual_costo_historico_0011'
     ORDER BY id DESC LIMIT 1;
    IF v_run IS NULL THEN
        RAISE EXCEPTION
            'reversa 0011: no hay ingest_run manual_costo_historico_0011 — '
            '¿se aplicó 0011 en esta base?';
    END IF;

    SELECT count(*) INTO v_filas FROM sku_cost WHERE ingest_run_id = v_run;
    IF v_filas <> 2 THEN
        RAISE EXCEPTION
            'reversa 0011: hay % vigencias de ese run, se esperaban 2', v_filas;
    END IF;

    -- Plata: UNA vigencia, la de 0011, abierta desde 2026-02-20.
    PERFORM 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA'
       AND c.ingest_run_id = v_run
       AND c.valid_from = DATE '2026-02-20' AND c.valid_to IS NULL
       AND c.cost_amount = 325.0000::money_amount;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reversa 0011: la vigencia de plata no es la que dejó 0011';
    END IF;
    SELECT count(*) INTO v_filas
      FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA';
    IF v_filas <> 1 THEN
        RAISE EXCEPTION
            'reversa 0011: plata tiene % vigencias, se esperaba 1', v_filas;
    END IF;

    -- Oro: DOS vigencias; la histórica es la de 0011, la vigente NO se toca.
    PERFORM 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-NOG-VEN-PESETA-DOR'
       AND c.ingest_run_id = v_run
       AND c.valid_from = DATE '2026-02-20' AND c.valid_to = DATE '2026-08-18'
       AND c.cost_amount = 458.0000::money_amount;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reversa 0011: la vigencia histórica de oro no es la que dejó 0011';
    END IF;
    SELECT count(*) INTO v_filas
      FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-NOG-VEN-PESETA-DOR';
    IF v_filas <> 2 THEN
        RAISE EXCEPTION
            'reversa 0011: oro tiene % vigencias, se esperaban 2', v_filas;
    END IF;
END $$;

-- ── 2 y 3. Quitar lo que 0011 escribió y reponer lo que borró ──────────────
ALTER TABLE sku_cost DISABLE TRIGGER sku_cost_solo_cierra_vigencia;

DELETE FROM sku_cost
 WHERE ingest_run_id = (
     SELECT id FROM ingest_run
      WHERE source = 'manual_costo_historico_0011'
      ORDER BY id DESC LIMIT 1);

ALTER TABLE sku_cost ENABLE TRIGGER sku_cost_solo_cierra_vigencia;

INSERT INTO sku_cost
    (product_id, cost_amount, cost_currency, includes_tax,
     valid_from, valid_to, ingest_run_id)
SELECT p.id, 325.0000, 'MXN', FALSE,
       DATE '2026-08-18', NULL, (SELECT id FROM _reversa_0011)
  FROM product p
 WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA';

-- ── 4. Verificación: volvimos al estado previo a 0011 ──────────────────────
DO $$
DECLARE
    v_ok    BOOLEAN;
    v_filas INTEGER;
BEGIN
    SELECT count(*) INTO v_filas
      FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku IN ('NH-GAM-NEG-PESETA-PLA', 'NH-NOG-VEN-PESETA-DOR');
    IF v_filas <> 2 THEN
        RAISE EXCEPTION
            'reversa 0011: quedaron % vigencias entre ambos SKU, se esperaban 2', v_filas;
    END IF;

    PERFORM 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA'
       AND c.valid_from = DATE '2026-08-18' AND c.valid_to IS NULL
       AND c.cost_amount = 325.0000::money_amount AND c.cost_currency = 'MXN'
       AND c.includes_tax = FALSE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reversa 0011: la vigencia de plata no volvió a su estado previo';
    END IF;

    PERFORM 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-NOG-VEN-PESETA-DOR'
       AND c.valid_from = DATE '2026-08-18' AND c.valid_to IS NULL
       AND c.cost_amount = 459.2900::money_amount AND c.cost_currency = 'MXN'
       AND c.includes_tax = FALSE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reversa 0011: la vigencia vigente de oro se dañó';
    END IF;

    SELECT tgenabled <> 'D' INTO v_ok
      FROM pg_trigger
     WHERE tgrelid = 'sku_cost'::regclass
       AND tgname = 'sku_cost_solo_cierra_vigencia';
    IF NOT v_ok THEN
        RAISE EXCEPTION 'reversa 0011: el trigger sku_cost_solo_cierra_vigencia quedó APAGADO';
    END IF;
END $$;

COMMIT;
