-- =============================================================================
--  ORBIT · MIGRACION 0005 · v_tacos grano único · PostgreSQL 16
--
--  BUG CONFIRMADO EN VIVO (2026-08-31): `ads_metric_observation` guarda el
--  gasto DOS VECES por cada campaña con hijos: una fila kind='campaign' Y
--  otra(s) kind='keyword'/'product_target' con el mismo costo (medido 90d
--  maduros: MX campaign 30,898.67 vs keyword+target 30,893.92; US campaign
--  4,828.76 vs keyword+target 4,828.76 EXACTO). `v_tacos` (0001, CTE
--  `gasto`) sumaba `v_metric_mature` SIN filtrar `kind` -> gasto_ads
--  inflado ~2x -> tacos_pct inflado ~2x.
--
--  DECISION DEL DUEÑO (tomada, no re-abrir): el grano correcto es
--  kind IN ('keyword', 'product_target') -- el MISMO grano del motor de
--  decisión y del candado 0.7 (app/cobertura.py). El grano 'campaign' queda
--  FUERA de la vista: es un duplicado agregado, no gasto adicional.
--
--  CREATE OR REPLACE: re-runnable. La lista de columnas de v_tacos NO
--  cambia (mismo SELECT final, solo el filtro de kind en el CTE `gasto`).
-- =============================================================================

CREATE OR REPLACE VIEW v_tacos AS
WITH gasto AS (
    -- Gasto maduro (corte D-15 de v_metric_mature, UTC fijado), convertido
    -- POR FILA a la moneda canónica (MXN). El gasto ya está sellado por
    -- trigger (amazon_us -> USD, amazon_mx/meli -> MXN), pero convertir por
    -- fila elimina cualquier supuesto de moneda única por mes.
    --
    -- FILTRO DE GRANO (0005): SOLO kind IN ('keyword', 'product_target').
    -- ads_metric_observation guarda el mismo costo en la fila 'campaign' Y
    -- en sus hijas keyword/product_target -- sumar todo duplicaba el gasto.
    SELECT e.platform                              AS platform,
           date_trunc('month', m.metric_date)::date AS mes,
           SUM(CASE
                   WHEN m.metric_currency = 'MXN'::currency THEN m.cost
                   ELSE m.cost * fx.rate
               END)                                AS gasto_ads,
           -- Filas que NO se pudieron convertir (sin tasa utilizable): SUM
           -- las ignora en silencio y el agregado quedaría corto sin señal.
           -- Se CUENTAN y se exponen; con una sola, tacos_pct es NULL.
           COUNT(*) FILTER (
               WHERE m.metric_currency <> 'MXN'::currency AND fx.rate IS NULL
           )                                       AS gasto_sin_tasa,
           -- Y el mismo agujero por el otro lado: SUM tampoco suma los NULL.
           -- Una fila con cost NULL bajaba gasto_ads sin dejar señal y
           -- tacos_pct salía CORTO — el sesgo optimista de siempre. Se
           -- cuentan igual que las filas sin tasa. (La venta no lo necesita:
           -- ledger_event.amount es NOT NULL.)
           COUNT(*) FILTER (WHERE m.cost IS NULL)  AS gasto_sin_costo
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(m.metric_date, m.metric_currency, 'MXN'::currency) r
      ) fx ON m.metric_currency <> 'MXN'::currency
     WHERE e.kind IN ('keyword', 'product_target')
     GROUP BY 1, 2
), venta AS (
    -- VENTANA SIMÉTRICA con el gasto: corte D-15 UTC con la MISMA expresión
    -- que v_metric_mature (el día en curso no cuenta: sin esto, el mes en
    -- curso salía con TACoS sistemáticamente bajo). TODAS las ventas, con o
    -- sin order_id, convertidas por fila a MXN.
    SELECT l.platform,
           date_trunc('month', l.event_date)::date AS mes,
           SUM(CASE
                   WHEN l.amount_currency = 'MXN'::currency THEN l.amount
                   ELSE l.amount * fx.rate
               END)                                AS venta_total,
           COUNT(*) FILTER (
               WHERE l.amount_currency <> 'MXN'::currency AND fx.rate IS NULL
           )                                       AS ventas_sin_tasa
      FROM ledger_event l
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(l.event_date, l.amount_currency, 'MXN'::currency) r
      ) fx ON l.amount_currency <> 'MXN'::currency
     WHERE l.kind = 'sale'
       AND l.event_date <= (now() AT TIME ZONE 'UTC')::date - 15
     GROUP BY 1, 2
)
SELECT COALESCE(g.platform, v.platform) AS platform,
       COALESCE(g.mes,    v.mes)        AS mes,
       g.gasto_ads,
       v.venta_total,
       COALESCE(g.gasto_sin_tasa, 0)    AS filas_gasto_sin_tasa,
       COALESCE(v.ventas_sin_tasa, 0)   AS filas_venta_sin_tasa,
       COALESCE(g.gasto_sin_costo, 0)   AS filas_gasto_sin_costo,
       -- Ambos lados ya están en MXN: si un mes tuviera dos monedas de venta,
       -- ambas se convirtieron y se SUMARON — el gasto no se repite por fila.
       -- Si un lado entero queda sin datos, tacos_pct es NULL. Y si CUALQUIER
       -- fila quedó sin tasa utilizable (aunque el resto sí convirtió), el
       -- agregado estaría corto: tacos_pct también es NULL y las columnas
       -- filas_*_sin_tasa dicen cuántas — fail-loud, NUNCA un número
       -- inventado (regla 3; endurecido en la ronda CodeRabbit del PR #1:
       -- antes un hueco PARCIAL de FX publicaba un tacos_pct corto).
       CASE
           WHEN g.gasto_ads IS NULL OR NULLIF(v.venta_total, 0) IS NULL THEN NULL
           WHEN COALESCE(g.gasto_sin_tasa, 0) > 0
                OR COALESCE(v.ventas_sin_tasa, 0) > 0
                OR COALESCE(g.gasto_sin_costo, 0) > 0 THEN NULL
           ELSE ROUND(100 * g.gasto_ads / v.venta_total, 2)
       END AS tacos_pct
  FROM gasto g
  FULL OUTER JOIN venta v
    ON v.platform = g.platform AND v.mes = g.mes;

COMMENT ON VIEW v_tacos IS
  'TACoS POR PLATAFORMA (no por moneda): la medición que estuvo disponible '
  'todo el año y nunca se hizo. No necesita atribución, ni halo, ni margen '
  'per-SKU: gasto de ads sobre venta total de la plataforma. '
  'GRANO ÚNICO (0005): gasto_ads suma SOLO kind IN (''keyword'', '
  '''product_target''). `ads_metric_observation` duplica el costo por fila '
  'kind=''campaign'' y por sus hijas keyword/product_target -- sumar ambos '
  'grados inflaba gasto_ads ~2x (medido en vivo: MX 30,898.67 vs 30,893.92; '
  'US 4,828.76 == 4,828.76). Decisión del dueño: el mismo grano que el motor '
  'de decisión y el candado de cobertura (app/cobertura.py); ''campaign'' '
  'queda fuera de esta vista. '
  'VENTANAS SIMÉTRICAS: ambos lados cortan en D-15 UTC (el gasto vía '
  'v_metric_mature; la venta con la misma expresión) — antes la venta tomaba '
  'TODAS las ventas del mes y el mes en curso salía con TACoS '
  'sistemáticamente bajo (optimista: todo se ve rentable). '
  'SIN SUPUESTO DE MONEDA ÚNICA: cada fila se convierte a la canónica MXN con '
  'fx_resolve (exacta o nearest_prior <= 7d) y el JOIN es por (platform, mes) '
  'sobre montos ya en MXN — si un mes tuviera dos monedas de venta, ambas se '
  'convierten y se SUMAN en vez de duplicar el gasto por fila. Sin tasa '
  'utilizable, la fila NO entra al agregado y se cuenta en '
  'filas_gasto_sin_tasa / filas_venta_sin_tasa: con una sola fila sin '
  'convertir, tacos_pct es NULL — un agregado parcial disfrazado de completo '
  'es exactamente el hueco silencioso que este esquema existe para matar. '
  'Lo MISMO con el costo ausente: una fila de métrica con cost NULL también '
  'se cae del SUM en silencio y dejaba el gasto corto (tacos_pct optimista); '
  'se cuenta en filas_gasto_sin_costo y anula tacos_pct igual. '
  'Fail-loud, nunca un número inventado (regla 3). Meta declarada del '
  'dueño: 8-12%.';
