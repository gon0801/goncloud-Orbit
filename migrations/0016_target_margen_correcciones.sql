-- =============================================================================
-- ORBIT 06 2.4 — correcciones de la cross-review de la implementacion
-- (kimi + grok, 1 ronda, 2026-09-04) sobre la vista de 0015. La 0015 YA esta
-- aplicada en produccion, asi que no se edita: se reemplaza la vista aqui.
--
-- (1) grok H5 (ALTA para la guarda): `ledger_fresco_at` salia de
--     MAX(observed_at) sobre TODO el ledger, asi que una venta nueva de hoy
--     "blanqueaba" la guarda `ledger_rancio` aunque los cargos de la ventana
--     madura no hubieran llegado nunca — la guarda que existe para detectar
--     una ingesta muerta no habria disparado JAMAS mientras entrara una venta
--     cada 3 dias. Medido el 2026-09-04: sobre toda la tabla, fresco = hoy en
--     ambas plataformas; sobre la ventana madura, MX estaba en 2026-08-31.
--     Ahora se mide sobre la CORRIDA DE INGESTA del ledger, que es
--     exactamente lo que la guarda dice medir. La completitud del dato la
--     cubren la madurez D-15 y el sync de contabilidad, no esta columna.
-- (2) kimi H8: el guard de cargo sin clasificar solo miraba kind='fee'; un
--     refund o withholding de publicidad sin tipo tambien seria doble castigo
--     (se resta del margen Y se cobra en el ACoS del motor). Ahora cubre los
--     tres kinds.
--
-- Verificado contra la base viva: la vista devuelve los MISMOS numeros que
-- la 0015 (40.0308 % MX / 36.0802 % US).
-- =============================================================================

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
      FROM ledger_event l
      JOIN ordenes_cubiertas o
        ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
    UNION ALL
    SELECT l.platform, l.amount, l.amount_currency, l.kind, l.fee_type, false
      FROM ledger_event l
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
  'fees_sin_tipo (contado sobre EXACTAMENTE los cargos que se suman, no '
  'solo los de la ventana). Los cargos deben venir en UNA moneda y ser '
  'la misma de las ventas, o el margen es NULL (regla 4). margen = 100 x '
  '(cubierta + con_orden + sin_orden x '
  'cobertura - cogs) / cubierta, SIN redondear; NULL ante cualquier '
  'condicion §5 (mezcla, fees_sin_tipo > 0, cobertura < 0.95, dias < 60, '
  'cubierta <= 0). ISR retenido como costo: decision consciente. '
  'observed_at solo avanza con filas nuevas. La evidencia auditada es el '
  'freeze de ventana_desde/hasta, no esta vista. La vista SOLO MIDE: '
  'fraccion, banda y paso viven en goals.resuelve_target_margen; el ciclo '
  'la lee UNA vez por ciclo en TX2.';

GRANT SELECT ON v_target_margen_plataforma TO app_read, app_ingest, app_decide, app_admin;
