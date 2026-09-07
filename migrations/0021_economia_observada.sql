-- ---------------------------------------------------------------------------
-- 0021 — ORBIT 19 B.2: economia observada por producto.
-- Expansiva, NO re-runnable. No toca v_margen_producto (FORBIDDEN del plan);
-- solo la LEE (SELECT) para la proyeccion madura.
--
-- ADR B.2-1 (compartir el motor sin alterar 0018): la vista madura
-- v_margen_producto (0018) queda INTACTA y es la UNICA fuente de las
-- columnas maduras (margen_neto_pct, venta_total, ...). La muestra limitada
-- necesita el mismo motor SIN el guard `dias_con_venta >= 30`; como 0018 no
-- puede alterarse ni parameterizarse, el bloque `muestra` de esta migracion
-- replica VERBATIM los CTE de 0018 (misma ventana de arranque fijo
-- [2026-02-20, D-15) UTC, mismo prorrateo, mismos guards) salvo ese guard.
-- El riesgo de deriva se pinea con test (tests/test_economia_observada.py):
-- con dias >= 30 e integridad OK, muestra_margen == margen_neto_pct de
-- v_margen_producto. Cualquier cambio futuro a 0018 debe espejarse aqui.
--
-- ADR B.2-2 (grano y duplicacion): el grano es (platform, product_id); dos
-- listings del mismo producto COMPARTEN la fila (politica 0.4 §5: "Dos
-- listings del mismo producto comparten venta/margen del producto. No
-- duplicar el total financiero"). El join a listing lo hace el consumidor
-- (app/economia_observada.py), nunca la vista.
--
-- ADR B.2-3 (muestra limitada, D4): `muestra_limitada` exige integridad OK
-- (moneda unica en ventas/cargos/denominadores, fees_sin_tipo = 0,
-- cobertura >= 0.95, venta cubierta > 0) Y 1 <= dias_con_venta <= 29. No
-- entra al sort D1 ni a ningun target: es solo lectura para la UI (D4).
-- Fuera de ese rango las columnas muestra_* van NULL (regla 3), jamas 0.
-- ---------------------------------------------------------------------------

CREATE VIEW v_economia_producto AS
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
      FROM ledger_event l
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
      FROM ledger_event l
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

COMMENT ON VIEW v_economia_producto IS
  'ORBIT 19 B.2 (politica 0.4 §5/§7, D1/D4): economia observada por '
  'PRODUCTO sobre la ventana madura [2026-02-20, D-15) UTC. Las columnas '
  'maduras son SELECT directo de v_margen_producto (0018 INTACTA, un numero '
  'una fuente). La muestra limitada replica el motor de 0018 sin el guard '
  'de 30 dias (ADR B.2-1): integridad_ok exige moneda unica en '
  'ventas/cargos/denominadores, fees_sin_tipo = 0, cobertura >= 0.95 y '
  'venta cubierta > 0; muestra_limitada = integridad_ok AND dias_con_venta '
  'BETWEEN 1 AND 29, y solo entonces muestra_venta/muestra_margen_neto_pct '
  'se publican (regla 3: NULL, jamas 0). D4: la muestra NO entra al sort '
  'D1 ni a ningun target automatico; es solo lectura para la UI. Grano '
  '(platform, product_id): dos listings del mismo producto comparten la '
  'fila y el total financiero NO se duplica (ADR B.2-2). Dinero NUMERIC '
  'por moneda de la fila; MX/US jamas se mezclan.';

GRANT SELECT ON v_economia_producto TO app_read, app_ingest, app_decide, app_admin;
