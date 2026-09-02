-- =============================================================================
--  ORBIT · MIGRACIÓN 0011 · COSTO HISTÓRICO DE LOS DOS SKU «PESETA»
--
--  ORBIT 06 · palanca de mapeo (2026-09-02). Es una corrección de DATOS, no de
--  esquema: la primera de este repo. No toca tablas, vistas, triggers
--  permanentes, GRANTs ni roles.
--
--  QUÉ PASÓ. Al mapear los 5 ASIN de la palanca (decisión del dueño
--  2026-09-01), dos anuncios de los grupos Arras MX quedaron ligados a
--  productos que contabilidad publicó por primera vez el 2026-08-18:
--
--    NH-GAM-NEG-PESETA-PLA   325.00 MXN  desde 2026-08-18 (abierta)
--    NH-NOG-VEN-PESETA-DOR   459.29 MXN  desde 2026-08-18 (abierta)
--
--  `v_contribucion_entidad` exige costo as-of CADA metric_date de la ventana
--  de 90 días maduros (hoy: 2026-05-21..2026-08-18) para el catálogo vivo
--  COMPLETO del ad group. Con 89 de 90 días sin costo, esos dos productos
--  dejan a los SEIS grupos Arras vivos en `catalogo_parcial` — son los únicos
--  así en esos grupos (medido: los demás ASIN vivos tienen listing, producto
--  y costo). El gasto que no se puede medir por esto: 3,325.79 MXN / 90d.
--
--  DE DÓNDE SALEN LOS NÚMEROS (regla 3: no se inventan). Es el MISMO producto
--  físico que sus hermanos de familia, que sí tienen historia desde el
--  backfill del 2026-02-20:
--    - gamuza negro plateado  → NH-GAM-NEG-MAX-PLA / -COR-PLA: 325.00 MXN
--      sin cambios desde 2026-02-20;
--    - nogal con ventana dorado → NH-NOG-VEN-CEN-DOR: 458.00 MXN desde
--      2026-02-20, cerrada el 2026-08-18, y 459.29 MXN desde entonces.
--  Confirmado por el dueño el 2026-09-02 («correcto»).
--
--  DOS CAMINOS DISTINTOS, Y POR QUÉ:
--
--  (a) NH-NOG-VEN-PESETA-DOR — APPEND PURO, sin excepción. El costo histórico
--      (458.00) DIFIERE del publicado (459.29), así que la vigencia nueva es
--      [2026-02-20, 2026-08-18) y ABUTA con la publicada [2026-08-18, ∞):
--      `daterange '[)'` no las solapa, el EXCLUDE de sku_cost está contento y
--      el trigger `sku_cost_solo_cierra_vigencia` ni se entera (es BEFORE
--      UPDATE OR DELETE; un INSERT no lo dispara).
--
--  (b) NH-GAM-NEG-PESETA-PLA — EXCEPCIÓN DECLARADA, aprobada por el dueño.
--      Aquí el costo es el MISMO (325.00) antes y después, y ahí está la
--      trampa: `colapsar()` (app/costs.py) FUSIONA tramos contiguos de igual
--      costo y moneda. Si dejáramos el par abutido, el origen colapsaría a UNA
--      vigencia [2026-02-20, ∞) que jamás volvería a cuadrar contra las dos
--      filas publicadas: `_plan_sku` rechazaría el SKU COMPLETO en cada
--      corrida futura («origen reabre vigencia ya publicada»), y ese producto
--      dejaría de recibir actualizaciones de costo para siempre. Un skip
--      permanente no es un arreglo. El estado correcto es UNA fila
--      [2026-02-20, ∞) con 325.00 — y llegar ahí exige BORRAR la fila
--      publicada del 2026-08-18, que es justo lo que el trigger append-only
--      prohíbe. Por eso este es el único DELETE de sku_cost del repo: se hace
--      con el trigger deshabilitado dentro de esta transacción, se re-habilita
--      en la misma transacción (si algo falla, ROLLBACK lo deja como estaba) y
--      queda escrito aquí para siempre. NO sienta precedente: corregir un
--      costo sigue siendo insertar una fila nueva.
--
--  PROCEDENCIA. Las dos filas nuevas cuelgan de un `ingest_run` propio con
--  `source = 'manual_costo_historico_0011'`: quien audite el histórico ve que
--  no vinieron del pipeline de contabilidad.
--
--  CONTABILIDAD (fuera de esta migración, mismo cambio). Para que
--  `ingest costs` vuelva a ser no-op, la SQLite de contabilidad recibe la
--  misma historia: al SKU de plata se le corre `valid_from` a 2026-02-20
--  (queda UNA fila abierta, que es lo que el colapso produce), y al de oro se
--  le agrega la fila cerrada 458.00 [2026-02-20, 2026-08-18). El sync horario
--  de Odoo (`sync_cogs_odoo.py`) sólo mira `WHERE sku=? AND valid_to IS NULL`:
--  no ve la fila cerrada y no toca la abierta si el costo no cambió.
--
--  FAIL-CLOSED. Cada paso valida el estado que espera y REVIENTA si no lo
--  encuentra (SKU ausente, fila del 2026-08-18 con otro importe, vigencia ya
--  corregida). NO es re-runnable a propósito: la segunda corrida aborta en la
--  primera guarda en vez de duplicar historia.
-- =============================================================================

BEGIN;

-- ── 0. Procedencia ──────────────────────────────────────────────────────────
CREATE TEMP TABLE _run_0011 ON COMMIT DROP AS
WITH nuevo AS (
    INSERT INTO ingest_run (source, rows_written, rows_skipped, ok, finished_at)
    VALUES ('manual_costo_historico_0011', 2, 0, TRUE, now())
    RETURNING id
)
SELECT id FROM nuevo;

-- ── 1. Guardas: el estado de partida es EXACTAMENTE el medido ───────────────
DO $$
DECLARE
    v_pla_id     BIGINT;
    v_dor_id     BIGINT;
    v_filas      INTEGER;
BEGIN
    SELECT id INTO v_pla_id FROM product WHERE odoo_sku = 'NH-GAM-NEG-PESETA-PLA';
    IF v_pla_id IS NULL THEN
        RAISE EXCEPTION '0011: no existe el producto NH-GAM-NEG-PESETA-PLA';
    END IF;
    SELECT id INTO v_dor_id FROM product WHERE odoo_sku = 'NH-NOG-VEN-PESETA-DOR';
    IF v_dor_id IS NULL THEN
        RAISE EXCEPTION '0011: no existe el producto NH-NOG-VEN-PESETA-DOR';
    END IF;

    -- Plata: UNA vigencia, abierta, 325.00 MXN, desde 2026-08-18.
    SELECT count(*) INTO v_filas FROM sku_cost WHERE product_id = v_pla_id;
    IF v_filas <> 1 THEN
        RAISE EXCEPTION
            '0011: NH-GAM-NEG-PESETA-PLA tiene % vigencias, se esperaba 1', v_filas;
    END IF;
    PERFORM 1 FROM sku_cost
     WHERE product_id = v_pla_id
       AND valid_from = DATE '2026-08-18'
       AND valid_to IS NULL
       AND cost_amount = 325.0000::money_amount
       AND cost_currency = 'MXN'
       AND includes_tax = FALSE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            '0011: la vigencia de NH-GAM-NEG-PESETA-PLA no es la esperada '
            '(325.0000 MXN, includes_tax=false, desde 2026-08-18, abierta)';
    END IF;

    -- Oro: UNA vigencia, abierta, 459.29 MXN, desde 2026-08-18.
    SELECT count(*) INTO v_filas FROM sku_cost WHERE product_id = v_dor_id;
    IF v_filas <> 1 THEN
        RAISE EXCEPTION
            '0011: NH-NOG-VEN-PESETA-DOR tiene % vigencias, se esperaba 1', v_filas;
    END IF;
    PERFORM 1 FROM sku_cost
     WHERE product_id = v_dor_id
       AND valid_from = DATE '2026-08-18'
       AND valid_to IS NULL
       AND cost_amount = 459.2900::money_amount
       AND cost_currency = 'MXN'
       AND includes_tax = FALSE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            '0011: la vigencia de NH-NOG-VEN-PESETA-DOR no es la esperada '
            '(459.2900 MXN, includes_tax=false, desde 2026-08-18, abierta)';
    END IF;
END $$;

-- ── 2. Oro: append puro. La nueva ABUTA con la publicada, no la solapa ──────
INSERT INTO sku_cost
    (product_id, cost_amount, cost_currency, includes_tax,
     valid_from, valid_to, ingest_run_id)
SELECT p.id, 458.0000, 'MXN', FALSE,
       DATE '2026-02-20', DATE '2026-08-18', (SELECT id FROM _run_0011)
  FROM product p
 WHERE p.odoo_sku = 'NH-NOG-VEN-PESETA-DOR';

-- ── 3. Plata: excepción declarada. Mismo costo ⇒ UNA sola vigencia ──────────
--  El trigger append-only se apaga y se vuelve a prender DENTRO de esta
--  transacción: un fallo entre ambos deja la tabla protegida (ROLLBACK).
ALTER TABLE sku_cost DISABLE TRIGGER sku_cost_solo_cierra_vigencia;

DELETE FROM sku_cost
 WHERE product_id = (SELECT id FROM product WHERE odoo_sku = 'NH-GAM-NEG-PESETA-PLA')
   AND valid_from = DATE '2026-08-18';

ALTER TABLE sku_cost ENABLE TRIGGER sku_cost_solo_cierra_vigencia;

INSERT INTO sku_cost
    (product_id, cost_amount, cost_currency, includes_tax,
     valid_from, valid_to, ingest_run_id)
SELECT p.id, 325.0000, 'MXN', FALSE,
       DATE '2026-02-20', NULL, (SELECT id FROM _run_0011)
  FROM product p
 WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA';

-- ── 4. Verificación: el estado final es el que esta migración promete ──────
DO $$
DECLARE
    v_ok      BOOLEAN;
    v_activos INTEGER;
BEGIN
    -- Plata: UNA vigencia abierta desde 2026-02-20, 325.00 MXN.
    SELECT count(*) = 1 INTO v_ok
      FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA';
    IF NOT v_ok THEN
        RAISE EXCEPTION '0011: NH-GAM-NEG-PESETA-PLA no quedó con UNA vigencia';
    END IF;
    PERFORM 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-GAM-NEG-PESETA-PLA'
       AND c.valid_from = DATE '2026-02-20' AND c.valid_to IS NULL
       AND c.cost_amount = 325.0000::money_amount AND c.cost_currency = 'MXN'
       AND c.includes_tax = FALSE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0011: la vigencia final de plata no es la esperada';
    END IF;

    -- Oro: DOS vigencias contiguas, 458.00 cerrada + 459.29 abierta.
    SELECT count(*) = 2 INTO v_ok
      FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-NOG-VEN-PESETA-DOR';
    IF NOT v_ok THEN
        RAISE EXCEPTION '0011: NH-NOG-VEN-PESETA-DOR no quedó con DOS vigencias';
    END IF;
    PERFORM 1 FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku = 'NH-NOG-VEN-PESETA-DOR'
       AND c.valid_from = DATE '2026-02-20' AND c.valid_to = DATE '2026-08-18'
       AND c.cost_amount = 458.0000::money_amount AND c.cost_currency = 'MXN'
       AND c.includes_tax = FALSE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0011: la vigencia histórica de oro no es la esperada';
    END IF;

    -- Cobertura del 2026-05-21 (inicio de la ventana viva al aplicar): ambos.
    SELECT count(*) INTO v_activos
      FROM sku_cost c JOIN product p ON p.id = c.product_id
     WHERE p.odoo_sku IN ('NH-GAM-NEG-PESETA-PLA', 'NH-NOG-VEN-PESETA-DOR')
       AND DATE '2026-05-21' >= c.valid_from
       AND (c.valid_to IS NULL OR DATE '2026-05-21' < c.valid_to);
    IF v_activos <> 2 THEN
        RAISE EXCEPTION
            '0011: al 2026-05-21 hay % de 2 SKU con costo vigente', v_activos;
    END IF;

    -- El trigger append-only quedó ARMADO (la excepción fue momentánea).
    SELECT tgenabled <> 'D' INTO v_ok
      FROM pg_trigger
     WHERE tgrelid = 'sku_cost'::regclass
       AND tgname = 'sku_cost_solo_cierra_vigencia';
    IF NOT v_ok THEN
        RAISE EXCEPTION '0011: el trigger sku_cost_solo_cierra_vigencia quedó APAGADO';
    END IF;
END $$;

COMMIT;
