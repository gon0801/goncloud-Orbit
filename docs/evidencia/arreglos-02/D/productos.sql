SELECT h.platform, h.product_id, round(h.margen_neto_pct, 2) AS hoy, round(n.margen_neto_pct, 2) AS sin_dup,
       round(n.margen_neto_pct - h.margen_neto_pct, 2) AS sube
  FROM v_margen_producto h
  JOIN (
WITH v_ledger_cargo AS (
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
                  AND split_part(r.source_event_id, '|', 2) <> 'fee_estimate'))
),
ventana AS (
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
  LEFT JOIN plataforma p ON p.platform = a.platform
       ) n ON n.platform = h.platform AND n.product_id = h.product_id
 WHERE h.margen_neto_pct IS NOT NULL
 ORDER BY 1, 2;
