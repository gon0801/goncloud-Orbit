-- 0048: margen neto % POR FAMILIA (A5) + peldaño `margen_familia` en el CHECK.
--
-- `v_margen_familia` mide con la maquinaria de `v_margen_producto` (0018) al
-- grano familia. ESTRUCTURA (forzada por el planner, medido): los CTEs
-- pesados son copia de 0018 a grano producto (el planner los planea como a
-- 0018: ~30 ms); los joins con etiquetas tocan SOLO agregados chicos y
-- TABLAS con estadisticas (producto_familia, familia) — jamas un UNION-CTE
-- joined con CTEs grandes (eso inlinea nested loops por miembro: medido
-- 261 s). Cada producto aporta a su etiqueta DIRECTA y, si es subfamilia,
-- al padre (la raiz junta el subarbol; UNION ALL de agregados + GROUP BY
-- final, no UNION de miembros).
-- Los denominadores del prorrateo (orden_cubierta, cargos_orden,
-- venta_plataforma) son GLOBALES, identicos a 0018: una familia de un
-- producto concilia EXACTO con su fila de producto (regla 10, pineado).
-- Guards al grano familia: moneda unica (ventas + moneda-miembro NULL si el
-- miembro mezcla, incluido en sus cargos: fail-closed sin residuo), fees =
-- 0, cubierta > 0, cobertura >= 0.95, 30 dias con venta EXACTOS (COUNT
-- DISTINCT sobre (producto, fecha) x etiquetas: sumar dias seria fail-open).
-- Ventana = la de 0018 ([2026-02-20, D-15) UTC, arranque fijo): familias
-- muertas (sin venta reciente) siguen midiendo con margen viejo — declarado,
-- no construido (futuro guard de frescura fuera de A5).
--
-- El CHECK de `target_acos_ciclo.procedencia` gana `margen_familia` (tercero,
-- mismo orden que `app.optimizer.goals.PELDANOS_CASCADA`; el espejo estático
-- y el vivo lo exigen). Deploy fuera de ciclo `running` (DROP/ADD valida).

BEGIN;

CREATE VIEW v_margen_familia AS
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
      FROM ledger_event l
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
           MAX(co.n_monedas) AS n_monedas_cargos,
           MAX(co.moneda) AS moneda_cargos,
           MAX(o.n_monedas) AS n_monedas_orden,
           CASE
               WHEN COUNT(DISTINCT v.amount_currency) <> 1 THEN NULL
               WHEN MAX(co.n_monedas) > 1 THEN NULL
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

COMMENT ON VIEW v_margen_familia IS
  'A5 (0048): margen neto % POR FAMILIA con la maquinaria de '
  'v_margen_producto (0018) a grano producto + agregacion familiar. Forma '
  'forzada por el planner: CTEs pesados copia de 0018, joins con etiquetas '
  'solo sobre agregados chicos y tablas con stats (UNION-CTE joined con '
  'CTEs grandes inlinea nested loops: medido 261 s). Cada producto aporta '
  'a su etiqueta y al padre; denominadores globales (concilia exacto con '
  'producto en familia-de-1). Guards: moneda unica (miembro NULL si mezcla), '
  'fees = 0, cubierta > 0, cobertura >= 0.95, 30 dias exactos, denominador '
  'una-moneda. Ventana fija [2026-02-20, D-15): familias muertas miden '
  'viejo (declarado).';

GRANT SELECT ON v_margen_familia TO app_decide, app_read, app_admin;

-- A5: ancla del paso familiar = ultimo target POR HOJA (DISTINCT ON over
-- historial; sin este indice el planner seq-scanea target_acos_ciclo).
CREATE INDEX target_acos_ciclo_hoja_ciclo_idx
    ON target_acos_ciclo (ad_entity_id, cycle_id DESC);

ALTER TABLE target_acos_ciclo
    DROP CONSTRAINT target_acos_ciclo_procedencia_check,
    ADD CONSTRAINT target_acos_ciclo_procedencia_check CHECK (procedencia IN (
        'goal_campana', 'goal_plataforma', 'margen_familia', 'margen_plataforma',
        'setting_plataforma', 'cache_estado', 'default'
    ));

DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'v_margen_familia', 'SELECT') THEN
        RAISE EXCEPTION '0048: app_decide debe leer v_margen_familia';
    END IF;
END
$$;

COMMIT;
