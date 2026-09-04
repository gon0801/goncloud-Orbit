-- =============================================================================
-- ORBIT 06 2.3 — vista v_target_margen_plataforma (la vista SOLO MIDE).
-- Spec: docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md
-- §3 (formula literal, aprobada por el dueno 2026-09-03). La vista NO conoce
-- fraccion ni target: eso lo resuelve goals.resuelve_target_margen en Python.
--
-- Ventana [D-105, D-15) con hoy = CURRENT_DATE UTC (estable dentro de
-- REPEATABLE READ): 15 dias de maduracion (regla 6) y 90 de historia.
-- COGS por LINEA de venta: costo vigente A LA FECHA (EXCLUDE de sku_cost
-- garantiza a lo mas uno), en la MISMA moneda o la linea cuenta SIN costo
-- (sin conversion FX: aqui no, por el spec; v_margen_plataforma si
-- convierte). Cobertura por LINEA (COUNT(*)/COUNT(cogs), precedente
-- v_margen_plataforma). El margen SOLO existe con cobertura >= 95 %
-- (fail-loud, precedente v_margen_plataforma que exige 100 %): con menos
-- cobertura es NULL y el resolver se abstiene con cobertura_baja.
-- moneda = unica amount_currency en la ventana, NULL si mezcla (canario:
-- el vocabulario de abstenciones es cerrado y no la incluye, D-2.3.9).
-- cargos excluye fee_type = 'ads' (la plataforma cobra su publicidad aparte;
-- el target es pre-ads). venta/cargos/cogs con COALESCE parcial: cargos y
-- cogs ausentes son 0 (una ventana sin fees es fees = 0); venta NULL
-- distingue ventana vacia (margen NULL -> sin_margen).
-- =============================================================================

CREATE VIEW v_target_margen_plataforma AS
WITH ventana AS (
    SELECT CURRENT_DATE - 105 AS desde, CURRENT_DATE - 15 AS hasta
),
lineas AS (
    SELECT l.platform,
           l.kind,
           l.event_date,
           l.amount,
           l.amount_currency,
           l.fee_type,
           CASE
               WHEN l.kind = 'sale' AND c.id IS NOT NULL
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
     WHERE l.event_date >= v.desde AND l.event_date < v.hasta
),
ag AS (
    SELECT platform,
           SUM(amount) FILTER (WHERE kind = 'sale') AS venta,
           SUM(amount) FILTER (WHERE kind IN ('fee', 'withholding', 'refund')
                                 AND fee_type IS DISTINCT FROM 'ads') AS cargos,
           SUM(cogs_linea) AS cogs_conocido,
           COUNT(*) FILTER (WHERE kind = 'sale') AS ventas_totales,
           COUNT(cogs_linea) FILTER (WHERE kind = 'sale') AS ventas_con_costo,
           COUNT(DISTINCT event_date) FILTER (WHERE kind = 'sale') AS dias_con_venta,
           COUNT(DISTINCT amount_currency) AS n_monedas,
           MAX(amount_currency) AS moneda_unica
      FROM lineas
     GROUP BY platform
),
fresco AS (
    SELECT platform, MAX(observed_at) AS ledger_fresco_at
      FROM ledger_event
     GROUP BY platform
)
SELECT a.platform,
       daterange((SELECT desde FROM ventana), (SELECT hasta FROM ventana), '[)') AS ventana,
       a.venta,
       COALESCE(a.cargos, 0) AS cargos,
       COALESCE(a.cogs_conocido, 0) AS cogs,
       CASE
           WHEN a.venta IS NULL OR a.venta <= 0 THEN NULL
           WHEN a.ventas_con_costo::numeric / NULLIF(a.ventas_totales, 0) < 0.95 THEN NULL
           ELSE 100.0 * (a.venta + COALESCE(a.cargos, 0) - COALESCE(a.cogs_conocido, 0))
                / a.venta
       END AS margen_neto_pct,
       CASE
           WHEN a.ventas_totales > 0
           THEN a.ventas_con_costo::numeric / a.ventas_totales
       END AS cobertura,
       a.dias_con_venta,
       f.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 THEN a.moneda_unica END AS moneda
  FROM ag a
  JOIN fresco f USING (platform);

COMMENT ON VIEW v_target_margen_plataforma IS
  'ORBIT 06 2.3 (spec 2026-09-03-target-margen-plataforma-design.md §3, '
  'formula literal): margen neto % por plataforma sobre la ventana '
  '[D-105, D-15) con hoy = CURRENT_DATE UTC — 15 dias de maduracion '
  '(regla 6) y 90 de historia. venta = SUM(amount) de sales; cargos = SUM '
  'de fee/withholding/refund EXCLUYENDO fee_type = ads (negativos por '
  'convencion); cogs = costo vigente a event_date x quantity SOLO en la '
  'misma moneda (linea sin costo = sin costo, sin FX). margen = 100 x '
  '(venta + cargos - cogs) / venta, SIN redondear; NULL si ventana vacia o '
  'cobertura < 95 % (fail-loud). cobertura = lineas con costo / lineas de '
  'venta (sin redondear: el borde 0.95 es guarda). dias_con_venta = fechas '
  'distintas con sale. ledger_fresco_at = max(observed_at) de TODA la '
  'plataforma (sin ventana). moneda = unica amount_currency, NULL si mezcla '
  '(canario). La vista SOLO MIDE: fraccion, banda y paso viven en '
  'goals.resuelve_target_margen; el ciclo la lee UNA vez por ciclo en TX2.';

GRANT SELECT ON v_target_margen_plataforma TO app_read, app_ingest, app_decide, app_admin;
