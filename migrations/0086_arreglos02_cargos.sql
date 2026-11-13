-- sonda-aplicada: SELECT to_regclass('public.v_ledger_cargo') IS NOT NULL AND pg_get_viewdef('public.v_target_margen_plataforma'::regclass) LIKE '%v_ledger_cargo%' AND pg_get_viewdef('public.v_margen_producto'::regclass) LIKE '%v_ledger_cargo%' AND pg_get_viewdef('public.v_economia_producto'::regclass) LIKE '%v_ledger_cargo%' AND pg_get_viewdef('public.v_margen_familia'::regclass) LIKE '%v_ledger_cargo%'
-- 0086 (ARREGLOS 02 D.2, estimacion.md F1): la comision de Amazon ya no se
-- cuenta dos veces en el margen.
--
-- Contabilidad manda una comision estimada por pedido (source_event_id
-- `amazon|fee_estimate|referral_fee|<pedido>`) y despues la real
-- (`amazon|finance|fee|...|Commission`). El libro es append-only y guarda las
-- dos; las cuatro vistas de margen restaban las dos, y el margen de Mexico y
-- Estados Unidos salia mas bajo (con el, la meta de ACoS). v_ledger_cargo
-- ignora la estimada SOLO cuando ya llego la real del mismo pedido.
--
-- Las cuatro vistas se recrean con el texto vigente de 0016, 0018, 0021 y
-- 0048 y dos cambios, nada mas: la primera linea pasa a CREATE OR REPLACE
-- VIEW y los CTE de cargos leen v_ledger_cargo en vez de ledger_event. Los
-- CTE de ventas siguen leyendo ledger_event.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0086_reversa_arreglos02_cargos.sql.

CREATE VIEW v_ledger_cargo AS
SELECT l.*
  FROM ledger_event l
 WHERE l.kind IN ('fee', 'refund', 'withholding')
   AND NOT (
         split_part(l.source_event_id, '|', 2) = 'fee_estimate'
         AND EXISTS (
             SELECT 1 FROM ledger_event r
              WHERE r.platform = l.platform
                AND r.order_id = l.order_id
                AND r.kind = l.kind
                AND r.fee_type IS NOT DISTINCT FROM l.fee_type
                AND split_part(r.source_event_id, '|', 2) <> 'fee_estimate'));

COMMENT ON VIEW v_ledger_cargo IS
  'ARREGLOS 02 D: los cargos del libro SIN la comision estimada cuando ya '
  'llego la real del mismo pedido (misma plataforma, mismo order_id, mismo '
  'kind y fee_type). La leen las cuatro vistas de margen; la estimada de un '
  'pedido sin liquidar sigue contando: es el mejor costo conocido.';

GRANT SELECT ON v_ledger_cargo TO app_read, app_ingest, app_decide, app_admin;


CREATE OR REPLACE VIEW v_target_margen_plataforma AS
WITH ventana AS (
    SELECT CURRENT_DATE - 105 AS desde, CURRENT_DATE - 15 AS hasta
),
ventas AS (
    -- Ventas de la ventana con su COGS de linea (costo vigente a la fecha,
    -- MISMA moneda o la venta es NO CUBIERTA: jamas se convierte ni rellena).
    SELECT l.platform,
           l.event_date,
           l.amount,
           l.amount_currency,
           l.order_id,
           CASE
               WHEN c.id IS NOT NULL
                    AND c.cost_currency = l.amount_currency
                   THEN c.cost_amount * l.quantity
               ELSE NULL
           END AS cogs_linea
      FROM ledger_event l
      CROSS JOIN ventana v
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
     WHERE l.kind = 'sale'
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
ordenes_cubiertas AS (
    SELECT DISTINCT platform, order_id
      FROM ventas
     WHERE cogs_linea IS NOT NULL AND order_id IS NOT NULL
),
cargos AS (
    -- TODOS los cargos que entran al margen, en UN solo conjunto (revision
    -- del PR #147, CodeRabbit): asi el guard de moneda y el de fee_type sin
    -- clasificar cubren EXACTAMENTE lo que se suma, ni mas ni menos.
    --   con_orden: atados a su venta cubierta, SIN filtro de fecha propio
    --              (A5: la ventana la fija su venta).
    --   sin_orden: de plataforma, SOLO dentro de la ventana.
    SELECT l.platform, l.amount, l.amount_currency, l.kind, l.fee_type, true AS con_orden
      FROM v_ledger_cargo l
      JOIN ordenes_cubiertas o
        ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
    UNION ALL
    SELECT l.platform, l.amount, l.amount_currency, l.kind, l.fee_type, false
      FROM v_ledger_cargo l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
cargos_ag AS (
    SELECT platform,
           SUM(amount) FILTER (WHERE con_orden) AS monto_con_orden,
           SUM(amount) FILTER (WHERE NOT con_orden) AS monto_sin_orden,
           COUNT(DISTINCT amount_currency) AS n_monedas_cargos,
           MAX(amount_currency::text) AS moneda_cargos,
           -- fee sin clasificar DENTRO del conjunto que se suma: un cargo de
           -- ads mal tipado se restaria del margen Y se cobraria en el ACoS
           -- del motor (A6, doble castigo) -> margen NULL, fail-loud.
           -- kimi H8: el guard cubre los TRES kinds, no solo 'fee' (un
           -- refund de publicidad sin clasificar tambien seria doble castigo).
           COUNT(*) FILTER (WHERE fee_type IS NULL) AS fees_sin_tipo
      FROM cargos
     GROUP BY platform
),
ag AS (
    SELECT v.platform,
           SUM(v.amount) AS venta_total,
           SUM(v.amount) FILTER (WHERE v.cogs_linea IS NOT NULL) AS venta_cubierta,
           SUM(v.cogs_linea) AS cogs_conocido,
           COUNT(DISTINCT v.event_date) AS dias_con_venta,
           COUNT(DISTINCT v.amount_currency) AS n_monedas,
           MAX(v.amount_currency) AS moneda_unica
      FROM ventas v
     GROUP BY v.platform
),
fresco AS (
    -- Cross-review grok H5: la guarda `ledger_rancio` existe para detectar
    -- que la INGESTA murio, y `MAX(observed_at)` sobre toda la tabla la
    -- blanqueaba — bastaba una venta nueva de hoy para que pareciera fresca
    -- aunque los cargos de la ventana madura no hubieran llegado nunca.
    -- Se mide sobre la corrida de ingesta del ledger, que es exactamente lo
    -- que la guarda dice medir. La completitud del dato la cubre la madurez
    -- D-15 mas el sync de contabilidad, no esta columna.
    SELECT p.platform, i.ledger_fresco_at
      FROM (SELECT DISTINCT platform FROM ledger_event) p
      CROSS JOIN (
            SELECT MAX(started_at) AS ledger_fresco_at
              FROM ingest_run
             WHERE source = 'accounting_ledger_events' AND ok
      ) i
)
SELECT a.platform,
       (SELECT desde FROM ventana) AS ventana_desde,
       (SELECT hasta FROM ventana) AS ventana_hasta,
       a.venta_total,
       a.venta_cubierta,
       COALESCE(cg.monto_con_orden, 0) AS cargos_con_orden,
       COALESCE(cg.monto_sin_orden, 0) AS cargos_sin_orden,
       COALESCE(a.cogs_conocido, 0) AS cogs,
       CASE
           WHEN a.venta_total > 0
           THEN a.venta_cubierta / a.venta_total
       END AS cobertura,
       a.dias_con_venta,
       COALESCE(cg.fees_sin_tipo, 0) AS fees_sin_tipo,
       CASE
           WHEN a.n_monedas <> 1 THEN NULL
           -- Los cargos se SUMAN a la venta: si traen otra moneda (o mas de
           -- una) el numero seria una mezcla (regla 4, revision PR #147).
           WHEN COALESCE(cg.n_monedas_cargos, 0) > 1 THEN NULL
           WHEN cg.moneda_cargos IS NOT NULL
                AND cg.moneda_cargos <> a.moneda_unica::text THEN NULL
           WHEN COALESCE(cg.fees_sin_tipo, 0) > 0 THEN NULL
           WHEN a.venta_cubierta IS NULL OR a.venta_cubierta <= 0 THEN NULL
           WHEN a.venta_cubierta / NULLIF(a.venta_total, 0) < 0.95 THEN NULL
           WHEN a.dias_con_venta < 60 THEN NULL
           ELSE 100.0 * (a.venta_cubierta + COALESCE(cg.monto_con_orden, 0)
                + COALESCE(cg.monto_sin_orden, 0)
                  * (a.venta_cubierta / NULLIF(a.venta_total, 0))
                - COALESCE(a.cogs_conocido, 0)) / a.venta_cubierta
       END AS margen_neto_pct,
       fr.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 THEN a.moneda_unica END AS moneda
  FROM ag a
  JOIN fresco fr USING (platform)
  LEFT JOIN cargos_ag cg USING (platform);


CREATE OR REPLACE VIEW v_margen_producto AS
WITH ventana AS (
    -- arranque FIJO 2026-02-20 (= al primer valid_from de sku_cost; decision
    -- escrita del dueno, tarea 1). Es un literal, NO se deriva de sku_cost.
    -- hoy = fecha UTC FIJADA en la expresion (D-6): CURRENT_DATE sigue la
    -- TimeZone de la sesion y moveria el guard de 30 dias segun quien consulte.
    SELECT DATE '2026-02-20' AS desde, (now() AT TIME ZONE 'UTC')::date - 15 AS hasta
),
ventas AS (
    SELECT l.platform, l.product_id, l.event_date, l.order_id,
           l.amount, l.amount_currency,
           CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency
                THEN c.cost_amount * l.quantity END AS cogs_linea
      FROM ledger_event l
      CROSS JOIN ventana v
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
     WHERE l.kind = 'sale' AND l.product_id IS NOT NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
orden_cubierta AS (
    -- venta cubierta por orden: base del prorrateo de sus cargos. n_monedas:
    -- guard del DENOMINADOR (r3 codex 2): una orden con lineas cubiertas en
    -- dos monedas no puede prorratear (regla 4 — NULL, no suma a ciegas).
    SELECT platform, order_id, SUM(amount) AS venta_orden,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas
     WHERE order_id IS NOT NULL AND cogs_linea IS NOT NULL
     GROUP BY platform, order_id
),
cargos_orden AS (
    SELECT l.platform, l.order_id,
           SUM(l.amount) AS monto,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo
      FROM v_ledger_cargo l
      JOIN orden_cubierta o ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
     GROUP BY l.platform, l.order_id
),
cargos_producto AS (
    SELECT v.platform, v.product_id,
           SUM(co.monto * v.amount / o.venta_orden) AS cargos_con_orden,
           SUM(co.fees_sin_tipo) AS fees_sin_tipo,
           -- Guard de moneda de los cargos, DOS casos (regla 4): MAX(co.n_monedas)
           -- = fees en dos monedas DENTRO de una orden (co.moneda es MAX por orden
           -- y colapsaria el caso); COUNT(DISTINCT co.moneda) = fees en dos monedas
           -- ENTRE ordenes (D-3: MAX(moneda) era lexicografico y fail-open). Solo
           -- uno de los dos deja un hueco (D-4, review del lead PR #172).
           GREATEST(MAX(co.n_monedas), COUNT(DISTINCT co.moneda)) AS n_monedas_cargos,
           MAX(o.n_monedas) AS n_monedas_orden,
           MAX(co.moneda) AS moneda_cargos
      FROM ventas v
      JOIN orden_cubierta o ON o.platform = v.platform AND o.order_id = v.order_id
      JOIN cargos_orden co ON co.platform = v.platform AND co.order_id = v.order_id
     WHERE v.cogs_linea IS NOT NULL
     GROUP BY v.platform, v.product_id
),
plataforma AS (
    SELECT l.platform,
           SUM(l.amount) AS monto_sin_orden,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda
      FROM v_ledger_cargo l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
),
venta_plataforma AS (
    -- n_monedas: guard del DENOMINADOR (r3 codex 2): el sistema viejo
    -- reportaba MXN hasta en amazon_us; con ventas de la plataforma en dos
    -- monedas la fraccion no puede calcularse (regla 4).
    SELECT platform, SUM(amount) AS venta_total,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas GROUP BY platform
),
ag AS (
    SELECT platform, product_id,
           SUM(amount) AS venta_total,
           SUM(amount) FILTER (WHERE cogs_linea IS NOT NULL) AS venta_cubierta,
           SUM(cogs_linea) AS cogs_conocido,
           COUNT(DISTINCT event_date) AS dias_con_venta,
           COUNT(DISTINCT amount_currency) AS n_monedas,
           MAX(amount_currency) AS moneda_unica
      FROM ventas
     GROUP BY platform, product_id
),
fresco AS (
    SELECT MAX(started_at) AS ledger_fresco_at
      FROM ingest_run
     WHERE source = 'accounting_ledger_events' AND ok
)
SELECT a.platform,
       a.product_id,
       (SELECT desde FROM ventana) AS ventana_desde,
       (SELECT hasta FROM ventana) AS ventana_hasta,
       a.venta_total,
       a.venta_cubierta,
       COALESCE(cp.cargos_con_orden, 0) AS cargos_con_orden,
       COALESCE(p.monto_sin_orden, 0) * COALESCE(a.venta_cubierta, 0)
           / NULLIF(vp.venta_total, 0) AS cargos_sin_orden,
       COALESCE(a.cogs_conocido, 0) AS cogs,
       CASE WHEN a.venta_total > 0 THEN COALESCE(a.venta_cubierta, 0) / a.venta_total END
           AS cobertura,
       a.dias_con_venta,
       COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) AS fees_sin_tipo,
       CASE
           WHEN a.n_monedas <> 1 THEN NULL
           WHEN COALESCE(cp.n_monedas_cargos, 0) > 1 OR COALESCE(p.n_monedas, 0) > 1 THEN NULL
           WHEN cp.moneda_cargos IS NOT NULL AND cp.moneda_cargos <> a.moneda_unica::text THEN NULL
           WHEN p.moneda IS NOT NULL AND p.moneda <> a.moneda_unica::text THEN NULL
           WHEN COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) > 0 THEN NULL
           WHEN a.venta_cubierta IS NULL OR a.venta_cubierta <= 0 THEN NULL
           WHEN a.venta_cubierta / NULLIF(a.venta_total, 0) < 0.95 THEN NULL
           WHEN a.dias_con_venta < 30 THEN NULL
           WHEN COALESCE(cp.n_monedas_orden, 0) > 1 THEN NULL
           WHEN vp.n_monedas > 1 THEN NULL
           ELSE 100.0 * (a.venta_cubierta
                + COALESCE(cp.cargos_con_orden, 0)
                + COALESCE(p.monto_sin_orden, 0) * a.venta_cubierta / NULLIF(vp.venta_total, 0)
                - COALESCE(a.cogs_conocido, 0)) / a.venta_cubierta
       END AS margen_neto_pct,
       fr.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 THEN a.moneda_unica END AS moneda
  FROM ag a
  JOIN venta_plataforma vp ON vp.platform = a.platform
  CROSS JOIN fresco fr
  LEFT JOIN cargos_producto cp ON cp.platform = a.platform AND cp.product_id = a.product_id
  LEFT JOIN plataforma p ON p.platform = a.platform;


CREATE OR REPLACE VIEW v_economia_producto AS
WITH ventana AS (
    -- IDENTICO a 0018 (ADR B.2-1): arranque FIJO + hoy UTC FIJADO en la
    -- expresion (D-15), para que el resultado no dependa del TimeZone de
    -- la sesion que consulta.
    SELECT DATE '2026-02-20' AS desde, (now() AT TIME ZONE 'UTC')::date - 15 AS hasta
),
ventas AS (
    SELECT l.platform, l.product_id, l.event_date, l.order_id,
           l.amount, l.amount_currency,
           CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency
                THEN c.cost_amount * l.quantity END AS cogs_linea
      FROM ledger_event l
      CROSS JOIN ventana v
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
     WHERE l.kind = 'sale' AND l.product_id IS NOT NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
orden_cubierta AS (
    SELECT platform, order_id, SUM(amount) AS venta_orden,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas
     WHERE order_id IS NOT NULL AND cogs_linea IS NOT NULL
     GROUP BY platform, order_id
),
cargos_orden AS (
    SELECT l.platform, l.order_id,
           SUM(l.amount) AS monto,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo
      FROM v_ledger_cargo l
      JOIN orden_cubierta o ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
     GROUP BY l.platform, l.order_id
),
cargos_producto AS (
    SELECT v.platform, v.product_id,
           SUM(co.monto * v.amount / o.venta_orden) AS cargos_con_orden,
           SUM(co.fees_sin_tipo) AS fees_sin_tipo,
           GREATEST(MAX(co.n_monedas), COUNT(DISTINCT co.moneda)) AS n_monedas_cargos,
           MAX(o.n_monedas) AS n_monedas_orden,
           MAX(co.moneda) AS moneda_cargos
      FROM ventas v
      JOIN orden_cubierta o ON o.platform = v.platform AND o.order_id = v.order_id
      JOIN cargos_orden co ON co.platform = v.platform AND co.order_id = v.order_id
     WHERE v.cogs_linea IS NOT NULL
     GROUP BY v.platform, v.product_id
),
plataforma AS (
    SELECT l.platform,
           SUM(l.amount) AS monto_sin_orden,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda
      FROM v_ledger_cargo l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
),
venta_plataforma AS (
    SELECT platform, SUM(amount) AS venta_total,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas GROUP BY platform
),
ag AS (
    SELECT platform, product_id,
           SUM(amount) AS venta_total,
           SUM(amount) FILTER (WHERE cogs_linea IS NOT NULL) AS venta_cubierta,
           SUM(cogs_linea) AS cogs_conocido,
           COUNT(DISTINCT event_date) AS dias_con_venta,
           COUNT(DISTINCT amount_currency) AS n_monedas,
           MAX(amount_currency) AS moneda_unica
      FROM ventas
     GROUP BY platform, product_id
),
muestra AS (
    -- Mismo CASE de guards que 0018 SALVO `dias_con_venta < 30` (ADR B.2-1):
    -- aqui la integridad se publica como flag y el margen de muestra como
    -- numero; el guard de dias lo aplica la proyeccion final.
    SELECT a.platform, a.product_id,
           (a.n_monedas = 1
            AND COALESCE(cp.n_monedas_cargos, 0) <= 1
            AND COALESCE(p.n_monedas, 0) <= 1
            AND (cp.moneda_cargos IS NULL OR cp.moneda_cargos = a.moneda_unica::text)
            AND (p.moneda IS NULL OR p.moneda = a.moneda_unica::text)
            AND COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) = 0
            AND COALESCE(a.venta_cubierta, 0) > 0
            AND a.venta_cubierta / NULLIF(a.venta_total, 0) >= 0.95
            AND COALESCE(cp.n_monedas_orden, 0) <= 1
            AND vp.n_monedas <= 1) AS integridad_ok,
           100.0 * (a.venta_cubierta
                + COALESCE(cp.cargos_con_orden, 0)
                + COALESCE(p.monto_sin_orden, 0) * a.venta_cubierta / NULLIF(vp.venta_total, 0)
                - COALESCE(a.cogs_conocido, 0)) / a.venta_cubierta AS margen_muestra_pct
      FROM ag a
      JOIN venta_plataforma vp ON vp.platform = a.platform
      LEFT JOIN cargos_producto cp ON cp.platform = a.platform AND cp.product_id = a.product_id
      LEFT JOIN plataforma p ON p.platform = a.platform
)
SELECT m.platform,
       m.product_id,
       m.ventana_desde,
       m.ventana_hasta,
       m.venta_total,
       m.venta_cubierta,
       m.cargos_con_orden,
       m.cargos_sin_orden,
       m.cogs,
       m.cobertura,
       m.dias_con_venta,
       m.fees_sin_tipo,
       m.margen_neto_pct,
       m.ledger_fresco_at,
       m.moneda,
       mu.integridad_ok,
       mu.integridad_ok AND m.dias_con_venta BETWEEN 1 AND 29 AS muestra_limitada,
       CASE WHEN mu.integridad_ok AND m.dias_con_venta BETWEEN 1 AND 29
            THEN m.venta_total END AS muestra_venta,
       CASE WHEN mu.integridad_ok AND m.dias_con_venta BETWEEN 1 AND 29
            THEN mu.margen_muestra_pct END AS muestra_margen_neto_pct
  FROM v_margen_producto m
  LEFT JOIN muestra mu ON mu.platform = m.platform AND mu.product_id = m.product_id;


CREATE OR REPLACE VIEW v_margen_familia AS
WITH ventana AS (
    SELECT DATE '2026-02-20' AS desde, (now() AT TIME ZONE 'UTC')::date - 15 AS hasta
),
ventas AS (
    SELECT l.platform, l.product_id, l.event_date, l.order_id,
           l.amount, l.amount_currency,
           CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency
                THEN c.cost_amount * l.quantity END AS cogs_linea
      FROM ledger_event l
      CROSS JOIN ventana v
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
     WHERE l.kind = 'sale' AND l.product_id IS NOT NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
orden_cubierta AS (
    SELECT platform, order_id, SUM(amount) AS venta_orden,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas
     WHERE order_id IS NOT NULL AND cogs_linea IS NOT NULL
     GROUP BY platform, order_id
),
cargos_orden AS (
    SELECT l.platform, l.order_id,
           SUM(l.amount) AS monto,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo
      FROM v_ledger_cargo l
      JOIN orden_cubierta o ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
     GROUP BY l.platform, l.order_id
),
-- Grano producto (espejo 0018): componentes + moneda-miembro (NULL si el
-- miembro mezcla monedas en ventas o en sus cargos: fail-closed familiar).
prod AS (
    SELECT v.platform, v.product_id,
           SUM(v.amount) AS venta_total,
           SUM(v.amount) FILTER (WHERE v.cogs_linea IS NOT NULL) AS venta_cubierta,
           SUM(v.cogs_linea) AS cogs,
           COALESCE(SUM(co.monto * v.amount / o.venta_orden)
               FILTER (WHERE v.cogs_linea IS NOT NULL), 0) AS cargos_con_orden,
           COALESCE(SUM(co.fees_sin_tipo), 0) AS fees_sin_tipo,
           COUNT(DISTINCT v.amount_currency) AS n_monedas_ventas,
           MAX(v.amount_currency) AS moneda_ventas,
           -- Guard de moneda de los cargos, DOS casos (espejo 0018 D-3/D-4:
           -- MAX(co.n_monedas) solo ve mezcla DENTRO de una orden;
           -- COUNT(DISTINCT co.moneda) cierra la mezcla ENTRE ordenes).
           GREATEST(MAX(co.n_monedas), COUNT(DISTINCT co.moneda)) AS n_monedas_cargos,
           MAX(co.moneda) AS moneda_cargos,
           MAX(o.n_monedas) AS n_monedas_orden,
           CASE
               WHEN COUNT(DISTINCT v.amount_currency) <> 1 THEN NULL
               WHEN GREATEST(MAX(co.n_monedas), COUNT(DISTINCT co.moneda)) > 1 THEN NULL
               WHEN MAX(co.moneda) IS NOT NULL
                    AND MAX(co.moneda) <> MAX(v.amount_currency)::text THEN NULL
               ELSE MAX(v.amount_currency)
           END AS moneda
      FROM ventas v
      LEFT JOIN orden_cubierta o
        ON o.platform = v.platform AND o.order_id = v.order_id
      LEFT JOIN cargos_orden co
        ON co.platform = v.platform AND co.order_id = v.order_id
     GROUP BY v.platform, v.product_id
),
dias_prod AS (
    SELECT platform, product_id, event_date
      FROM ventas
     GROUP BY platform, product_id, event_date
),
-- Ramas directas y padre: joins con TABLAS (stats reales) sobre agregados
-- chicos; UNION ALL + GROUP BY final (una familia sale 2x si es etiqueta
-- de unos y padre de otros: se reagrega, no se duplica).
ramas AS (
    SELECT pf.familia_id, p.*
      FROM prod p
      JOIN producto_familia pf
        ON pf.platform = p.platform AND pf.product_id = p.product_id
    UNION ALL
    SELECT f.padre_id AS familia_id, p.*
      FROM prod p
      JOIN producto_familia pf
        ON pf.platform = p.platform AND pf.product_id = p.product_id
      JOIN familia f ON f.id = pf.familia_id
     WHERE f.padre_id IS NOT NULL
),
ag AS (
    SELECT r.familia_id,
           MAX(r.platform) AS platform,
           SUM(r.venta_total) AS venta_total,
           SUM(r.venta_cubierta) AS venta_cubierta,
           SUM(r.cogs) AS cogs,
           SUM(r.cargos_con_orden) AS cargos_con_orden,
           SUM(r.fees_sin_tipo) AS fees_sin_tipo,
           COUNT(DISTINCT r.moneda) AS n_monedas,
           COUNT(*) FILTER (WHERE r.moneda IS NULL) AS sin_moneda,
           MAX(r.moneda) AS moneda_max,
           MAX(r.n_monedas_orden) AS n_monedas_orden
      FROM ramas r
     GROUP BY r.familia_id
),
dias_fam AS (
    SELECT x.familia_id, COUNT(DISTINCT (d.platform, d.event_date)) AS dias_con_venta
      FROM dias_prod d
      JOIN (SELECT platform, product_id, familia_id FROM producto_familia
            UNION ALL
            SELECT pf.platform, pf.product_id, f.padre_id
              FROM producto_familia pf
              JOIN familia f ON f.id = pf.familia_id
             WHERE f.padre_id IS NOT NULL) x
        ON x.platform = d.platform AND x.product_id = d.product_id
     GROUP BY x.familia_id
),
plataforma AS (
    SELECT l.platform,
           SUM(l.amount) AS monto_sin_orden,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda
      FROM v_ledger_cargo l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
),
venta_plataforma AS (
    SELECT platform, SUM(amount) AS venta_total,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas GROUP BY platform
),
fresco AS (
    SELECT MAX(started_at) AS ledger_fresco_at
      FROM ingest_run
     WHERE source = 'accounting_ledger_events' AND ok
)
SELECT a.platform,
       a.familia_id,
       f.padre_id,
       (SELECT desde FROM ventana) AS ventana_desde,
       (SELECT hasta FROM ventana) AS ventana_hasta,
       a.venta_total,
       a.venta_cubierta,
       a.cargos_con_orden,
       COALESCE(p.monto_sin_orden, 0) * COALESCE(a.venta_cubierta, 0)
           / NULLIF(vp.venta_total, 0) AS cargos_sin_orden,
       COALESCE(a.cogs, 0) AS cogs,
       CASE WHEN a.venta_total > 0 THEN COALESCE(a.venta_cubierta, 0) / a.venta_total END
           AS cobertura,
       d.dias_con_venta,
       a.fees_sin_tipo + COALESCE(p.fees_sin_tipo, 0) AS fees_sin_tipo,
       CASE
           WHEN a.n_monedas <> 1 OR a.sin_moneda > 0 THEN NULL
           WHEN a.fees_sin_tipo + COALESCE(p.fees_sin_tipo, 0) > 0 THEN NULL
           WHEN a.venta_cubierta IS NULL OR a.venta_cubierta <= 0 THEN NULL
           WHEN a.venta_cubierta / NULLIF(a.venta_total, 0) < 0.95 THEN NULL
           WHEN d.dias_con_venta < 30 THEN NULL
           WHEN a.n_monedas_orden > 1 THEN NULL
           -- Espejo 0018: cargos de plataforma en > 1 moneda anulan aunque
           -- MAX(moneda) coincida con la familiar (comparar solo MAX es
           -- fail-open lexicografico).
           WHEN COALESCE(p.n_monedas, 0) > 1 THEN NULL
           WHEN p.moneda IS NOT NULL AND p.moneda <> a.moneda_max::text THEN NULL
           WHEN vp.n_monedas > 1 THEN NULL
           ELSE 100.0 * (a.venta_cubierta + a.cargos_con_orden
                + COALESCE(p.monto_sin_orden, 0) * a.venta_cubierta / NULLIF(vp.venta_total, 0)
                - COALESCE(a.cogs, 0)) / a.venta_cubierta
       END AS margen_neto_pct,
       fr.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 AND a.sin_moneda = 0 THEN a.moneda_max END AS moneda
  FROM ag a
  JOIN dias_fam d ON d.familia_id = a.familia_id
  JOIN familia f ON f.id = a.familia_id
  JOIN venta_plataforma vp ON vp.platform = a.platform
  CROSS JOIN fresco fr
  LEFT JOIN plataforma p ON p.platform = a.platform;


