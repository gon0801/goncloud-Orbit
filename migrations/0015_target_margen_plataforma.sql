-- =============================================================================
-- ORBIT 06 2.3 — vista v_target_margen_plataforma (la vista SOLO MIDE).
-- Spec: docs/superpowers/specs/2026-09-03-target-margen-plataforma-design.md
-- §3 (formula literal adjudicada §10.bis, 2026-09-04). La vista NO conoce
-- fraccion ni target: eso lo resuelve goals.resuelve_target_margen en Python.
--
-- Ventana [D-105, D-15) con hoy = CURRENT_DATE UTC: 15 dias de maduracion
-- (regla 6) y 90 de historia. NOTA (A10, declaracion): CURRENT_DATE se
-- evalua en la TimeZone de la sesion — la evidencia auditada es el freeze
-- de ventana_desde/hasta en decision.inputs, no esta vista.
-- VENTAS partidas por cobertura (A3, regla 3: una venta sin costo NO se
-- cuenta con costo cero; solo la venta CUBIERTA entra al margen).
-- cobertura = POR MONTO (venta_cubierta/venta_total), no por conteo.
-- CARGOS con order_id pertenecen a SU venta cubierta aunque caigan fuera
-- de la ventana (A5: la ventana la fija su venta); sin order_id son de
-- plataforma y se prorratean por cobertura (el prorrateo vive AQUI, en el
-- margen; las columnas crudas quedan para auditar). fee_type NULL se trata
-- como no-ads en los cargos Y se cuenta en fees_sin_tipo (A6: doble
-- castigo potencial -> margen NULL, fail-loud como la mezcla de moneda).
-- El margen se NULIFICA ante CUALQUIER condicion §5 de datos (mezcla,
-- fees_sin_tipo > 0, cobertura < 0.95, dias < 60, venta_cubierta <= 0):
-- el resolver conserva el motivo fino con las columnas crudas.
-- ISR retenido como costo: decision consciente y conservadora (A10).
-- observed_at solo avanza con filas nuevas: ledger_fresco_at = max sobre
-- TODA la plataforma, sin ventana (A10).
-- =============================================================================

CREATE VIEW v_target_margen_plataforma AS
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
cargos_orden AS (
    -- Cargos atados a su venta cubierta: SIN filtro de fecha propio.
    SELECT l.platform, SUM(l.amount) AS monto
      FROM ledger_event l
      JOIN ordenes_cubiertas o
        ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
     GROUP BY l.platform
),
cargos_plataforma AS (
    -- Cargos sin order_id: de plataforma, SOLO dentro de la ventana.
    SELECT l.platform, SUM(l.amount) AS monto
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
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
fees AS (
    SELECT l.platform, COUNT(*) AS n
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind = 'fee' AND l.fee_type IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
),
fresco AS (
    SELECT platform, MAX(observed_at) AS ledger_fresco_at
      FROM ledger_event
     GROUP BY platform
)
SELECT a.platform,
       (SELECT desde FROM ventana) AS ventana_desde,
       (SELECT hasta FROM ventana) AS ventana_hasta,
       a.venta_total,
       a.venta_cubierta,
       COALESCE(o.monto, 0) AS cargos_con_orden,
       COALESCE(p.monto, 0) AS cargos_sin_orden,
       COALESCE(a.cogs_conocido, 0) AS cogs,
       CASE
           WHEN a.venta_total > 0
           THEN a.venta_cubierta / a.venta_total
       END AS cobertura,
       a.dias_con_venta,
       COALESCE(f.n, 0) AS fees_sin_tipo,
       CASE
           WHEN a.n_monedas <> 1 THEN NULL
           WHEN COALESCE(f.n, 0) > 0 THEN NULL
           WHEN a.venta_cubierta IS NULL OR a.venta_cubierta <= 0 THEN NULL
           WHEN a.venta_cubierta / NULLIF(a.venta_total, 0) < 0.95 THEN NULL
           WHEN a.dias_con_venta < 60 THEN NULL
           ELSE 100.0 * (a.venta_cubierta + COALESCE(o.monto, 0)
                + COALESCE(p.monto, 0)
                  * (a.venta_cubierta / NULLIF(a.venta_total, 0))
                - COALESCE(a.cogs_conocido, 0)) / a.venta_cubierta
       END AS margen_neto_pct,
       fr.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 THEN a.moneda_unica END AS moneda
  FROM ag a
  JOIN fresco fr USING (platform)
  LEFT JOIN cargos_orden o USING (platform)
  LEFT JOIN cargos_plataforma p USING (platform)
  LEFT JOIN fees f USING (platform);

COMMENT ON VIEW v_target_margen_plataforma IS
  'ORBIT 06 2.3 (spec 2026-09-03-target-margen-plataforma-design.md §3, '
  'formula literal adjudicada §10.bis 2026-09-04): margen neto % por '
  'plataforma sobre la ventana [D-105, D-15) con hoy = CURRENT_DATE UTC — '
  '15 dias de maduracion (regla 6) y 90 de historia. venta_cubierta = SUM de '
  'ventas CON costo (misma moneda, sin FX ni relleno); cobertura = POR MONTO '
  '(cubierta/total); cargos_con_orden = fee/refund/withholding no-ads con '
  'order_id de venta CUBIERTA (sin filtro de fecha propio); '
  'cargos_sin_orden = los de plataforma sin order_id en ventana, '
  'PRORRATEADOS por cobertura dentro del margen; fee_type NULL cuenta en '
  'fees_sin_tipo. margen = 100 x (cubierta + con_orden + sin_orden x '
  'cobertura - cogs) / cubierta, SIN redondear; NULL ante cualquier '
  'condicion §5 (mezcla, fees_sin_tipo > 0, cobertura < 0.95, dias < 60, '
  'cubierta <= 0). ISR retenido como costo: decision consciente. '
  'observed_at solo avanza con filas nuevas. La evidencia auditada es el '
  'freeze de ventana_desde/hasta, no esta vista. La vista SOLO MIDE: '
  'fraccion, banda y paso viven en goals.resuelve_target_margen; el ciclo '
  'la lee UNA vez por ciclo en TX2.';

GRANT SELECT ON v_target_margen_plataforma TO app_read, app_ingest, app_decide, app_admin;
