-- =============================================================================
--  ORBIT · MIGRACION 0006 · contribucion por entidad · PostgreSQL 16
--
--  Implementa docs/MARGEN-ENTIDAD.md (SELLADO 2026-08-31, D1.mx + D1-D8).
--  CREATE OR REPLACE de v_tacos: agrega gasto_campaign_sin_contraparte al
--  FINAL (PG16 permite columnas nuevas al final). Fail-loud cost/FX intacto.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- v_tacos: grano 0005 + residual campaign sin contraparte (D6)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_tacos AS
WITH gasto AS (
    SELECT e.platform                              AS platform,
           date_trunc('month', m.metric_date)::date AS mes,
           SUM(CASE
                   WHEN m.metric_currency = 'MXN'::currency THEN m.cost
                   ELSE m.cost * fx.rate
               END)                                AS gasto_ads,
           COUNT(*) FILTER (
               WHERE m.metric_currency <> 'MXN'::currency AND fx.rate IS NULL
           )                                       AS gasto_sin_tasa,
           COUNT(*) FILTER (WHERE m.cost IS NULL)  AS gasto_sin_costo
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(m.metric_date, m.metric_currency, 'MXN'::currency) r
      ) fx ON m.metric_currency <> 'MXN'::currency
     WHERE e.kind IN ('keyword', 'product_target')
     GROUP BY 1, 2
), gasto_campaign AS (
    -- Residual medido ~0.015% MX: SUM(campaign) - SUM(keyword+target).
    -- Misma conversion y mismos contadores fail-loud; si el lado campaign
    -- tiene hueco de FX/cost, el residual queda NULL (no un parcial).
    SELECT e.platform                              AS platform,
           date_trunc('month', m.metric_date)::date AS mes,
           SUM(CASE
                   WHEN m.metric_currency = 'MXN'::currency THEN m.cost
                   ELSE m.cost * fx.rate
               END)                                AS gasto_campaign,
           COUNT(*) FILTER (
               WHERE m.metric_currency <> 'MXN'::currency AND fx.rate IS NULL
           )                                       AS campaign_sin_tasa,
           COUNT(*) FILTER (WHERE m.cost IS NULL)  AS campaign_sin_costo
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(m.metric_date, m.metric_currency, 'MXN'::currency) r
      ) fx ON m.metric_currency <> 'MXN'::currency
     WHERE e.kind = 'campaign'
     GROUP BY 1, 2
), venta AS (
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
SELECT COALESCE(g.platform, v.platform, c.platform) AS platform,
       COALESCE(g.mes,    v.mes, c.mes)             AS mes,
       g.gasto_ads,
       v.venta_total,
       COALESCE(g.gasto_sin_tasa, 0)    AS filas_gasto_sin_tasa,
       COALESCE(v.ventas_sin_tasa, 0)   AS filas_venta_sin_tasa,
       COALESCE(g.gasto_sin_costo, 0)   AS filas_gasto_sin_costo,
       CASE
           WHEN g.gasto_ads IS NULL OR NULLIF(v.venta_total, 0) IS NULL THEN NULL
           WHEN COALESCE(g.gasto_sin_tasa, 0) > 0
                OR COALESCE(v.ventas_sin_tasa, 0) > 0
                OR COALESCE(g.gasto_sin_costo, 0) > 0 THEN NULL
           ELSE ROUND(100 * g.gasto_ads / v.venta_total, 2)
       END AS tacos_pct,
       CASE
           WHEN c.gasto_campaign IS NULL OR g.gasto_ads IS NULL THEN NULL
           WHEN COALESCE(c.campaign_sin_tasa, 0) > 0
                OR COALESCE(c.campaign_sin_costo, 0) > 0
                OR COALESCE(g.gasto_sin_tasa, 0) > 0
                OR COALESCE(g.gasto_sin_costo, 0) > 0 THEN NULL
           ELSE c.gasto_campaign - g.gasto_ads
       END AS gasto_campaign_sin_contraparte
  FROM gasto g
  FULL OUTER JOIN venta v
    ON v.platform = g.platform AND v.mes = g.mes
  FULL OUTER JOIN gasto_campaign c
    ON c.platform = COALESCE(g.platform, v.platform)
   AND c.mes = COALESCE(g.mes, v.mes);

COMMENT ON VIEW v_tacos IS
  'TACoS POR PLATAFORMA (no por moneda). '
  'GRANO UNICO (0005/0006): gasto_ads suma SOLO kind IN (''keyword'', '
  '''product_target''). ads_metric_observation duplica el costo en '
  'kind=''campaign'' y en sus hijas — sumar ambos inflaba ~2x. '
  'RESIDUAL (0006, D6): gasto_campaign_sin_contraparte = SUM(campaign) - '
  'SUM(keyword+target) en la misma (platform, mes), convertido a MXN con '
  'fx_resolve. Es el residuo medido ~0.015% MX (campaign sin contraparte '
  'hija). NULL si hay hueco de FX o cost en cualquiera de los dos lados. '
  'Fail-loud de tacos_pct intacto: filas_gasto_sin_tasa / '
  'filas_venta_sin_tasa / filas_gasto_sin_costo anulan tacos_pct. '
  'Ventanas simetricas D-15 UTC. Meta declarada: 8-12%.';


-- ---------------------------------------------------------------------------
-- v_contribucion_entidad (D1.mx + D1-D8; no decisoria)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_contribucion_entidad AS
WITH
ventana AS (
    SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89) AS d_from,
           ((now() AT TIME ZONE 'UTC')::date - 15)      AS d_to
),
-- Metricas maduras de la ventana al grano de decision.
metricas AS (
    SELECT e.id              AS ad_entity_id,
           e.platform,
           e.kind,
           e.parent_id       AS ad_group_id,
           m.metric_date,
           m.metric_currency,
           m.cost,
           m.ad_revenue,
           m.revenue_same_sku
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      CROSS JOIN ventana v
     WHERE e.kind IN ('keyword', 'product_target')
       AND e.platform IN ('amazon_mx'::platform, 'amazon_us'::platform)
       AND m.metric_date BETWEEN v.d_from AND v.d_to
),
-- Serie por entidad: huecos de cost o del par halo → ausente.
serie AS (
    SELECT ad_entity_id,
           platform,
           kind,
           ad_group_id,
           metric_currency,
           COUNT(*) AS filas,
           COUNT(*) FILTER (WHERE cost IS NULL) AS filas_sin_costo,
           COUNT(*) FILTER (
               WHERE ad_revenue IS NULL OR revenue_same_sku IS NULL
           ) AS filas_sin_par_halo,
           SUM(cost) AS cost_sum,
           SUM(ad_revenue) AS ad_revenue_sum,
           SUM(revenue_same_sku) AS revenue_same_sku_sum
      FROM metricas
     GROUP BY 1, 2, 3, 4, 5
),
serie_ok AS (
    SELECT *
      FROM serie
     WHERE ad_group_id IS NOT NULL
       AND filas_sin_costo = 0
       AND filas_sin_par_halo = 0
       AND filas > 0
),
-- Catalogo vivo del ad_group: product_ads ENABLED|PAUSED.
vivos AS (
    SELECT pa.parent_id AS ad_group_id,
           pa.platform,
           pa.id AS product_ad_id,
           pa.listing_id,
           l.product_id,
           l.listing_price,
           l.price_currency
      FROM ad_entity pa
      JOIN ad_entity_state st ON st.ad_entity_id = pa.id
      LEFT JOIN listing l ON l.id = pa.listing_id
     WHERE pa.kind = 'product_ad'
       AND st.status IN ('ENABLED', 'PAUSED')
),
vivos_cnt AS (
    SELECT ad_group_id, platform, COUNT(*) AS n_vivos
      FROM vivos
     GROUP BY 1, 2
),
-- Precio MX (D1.mx): promedio ponderado item_price/quantity en la ventana.
precio_mx AS (
    SELECT l.platform,
           l.product_id,
           SUM(l.item_price) / SUM(l.quantity)::numeric AS price_i,
           SUM(l.amount) AS venta_ledger
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind = 'sale'
       AND l.product_id IS NOT NULL
       AND l.item_price IS NOT NULL
       AND l.quantity IS NOT NULL
       AND l.quantity > 0
       AND l.event_date BETWEEN v.d_from AND v.d_to
       AND l.platform = 'amazon_mx'::platform
     GROUP BY 1, 2
),
-- Precio US: listing_price (D1.bis vivo). Un precio por producto; moneda = USD.
precio_us AS (
    SELECT v.platform,
           v.product_id,
           MIN(v.listing_price) AS price_i,
           'USD'::currency AS price_currency
      FROM vivos v
     WHERE v.platform = 'amazon_us'::platform
       AND v.listing_price IS NOT NULL
       AND v.product_id IS NOT NULL
       AND v.price_currency = 'USD'::currency
     GROUP BY 1, 2
    HAVING COUNT(DISTINCT v.listing_price) = 1
),
-- Catalogo vivo a grano (ad_group, platform, product_id) — evita fan-out.
catalogo_vivo AS (
    SELECT DISTINCT ad_group_id, platform, product_id
      FROM vivos
     WHERE product_id IS NOT NULL
),
-- Ventas ledger del catalogo (pesos w_i), misma ventana. Mezcla de monedas
-- en el mismo producto → el producto no entra al peso (fail-loud por exclusion).
ventas_peso AS (
    SELECT vl.ad_group_id,
           vl.platform,
           vl.product_id,
           SUM(vl.amount) AS venta_ledger
      FROM (
           SELECT cv.ad_group_id,
                  cv.platform,
                  cv.product_id,
                  l.amount,
                  l.amount_currency
             FROM catalogo_vivo cv
             JOIN ledger_event l
               ON l.product_id = cv.product_id
              AND l.platform = cv.platform
             CROSS JOIN ventana v
            WHERE l.kind = 'sale'
              AND l.event_date BETWEEN v.d_from AND v.d_to
      ) vl
     GROUP BY 1, 2, 3
    HAVING COUNT(DISTINCT vl.amount_currency) = 1
),
sigma AS (
    SELECT ad_group_id, platform, SUM(venta_ledger) AS sigma_ventas
      FROM ventas_peso
     GROUP BY 1, 2
),
pesos AS (
    SELECT p.ad_group_id,
           p.platform,
           p.product_id,
           p.venta_ledger / s.sigma_ventas AS w_i
      FROM ventas_peso p
      JOIN sigma s
        ON s.ad_group_id = p.ad_group_id
       AND s.platform = p.platform
     WHERE s.sigma_ventas > 0
),
-- Dias distintos con metrica por (entidad) — para exigir costo as-of cada dia.
dias_entidad AS (
    SELECT DISTINCT ad_entity_id, ad_group_id, platform, metric_date, metric_currency
      FROM metricas
),
-- Costo as-of por (product, metric_date), en moneda del reporte.
-- US: cost MXN / fx_resolve(date,'USD','MXN') — NUNCA el par invertido.
costo_dia AS (
    SELECT d.ad_entity_id,
           d.ad_group_id,
           d.platform,
           d.metric_date,
           d.metric_currency,
           vv.product_id,
           vv.listing_id,
           CASE
               WHEN c.id IS NULL THEN NULL
               WHEN d.metric_currency = c.cost_currency THEN c.cost_amount
               WHEN d.metric_currency = 'USD'::currency
                    AND c.cost_currency = 'MXN'::currency
                    AND fx.rate IS NOT NULL
                   THEN c.cost_amount / fx.rate
               WHEN d.metric_currency = 'MXN'::currency
                    AND c.cost_currency = 'USD'::currency
                    AND fx_up.rate IS NOT NULL
                   THEN c.cost_amount * fx_up.rate
               ELSE NULL
           END AS cost_i,
           CASE
               WHEN d.metric_currency = c.cost_currency THEN NULL
               WHEN d.metric_currency = 'USD'::currency
                    AND c.cost_currency = 'MXN'::currency
                   THEN fx.source
               WHEN d.metric_currency = 'MXN'::currency
                    AND c.cost_currency = 'USD'::currency
                   THEN fx_up.source
               ELSE NULL
           END AS fx_source_dia,
           CASE
               WHEN c.id IS NULL THEN true
               WHEN d.metric_currency = c.cost_currency THEN false
               WHEN d.metric_currency = 'USD'::currency
                    AND c.cost_currency = 'MXN'::currency
                    AND fx.rate IS NULL THEN true
               WHEN d.metric_currency = 'MXN'::currency
                    AND c.cost_currency = 'USD'::currency
                    AND fx_up.rate IS NULL THEN true
               WHEN d.metric_currency <> c.cost_currency
                    AND NOT (
                        (d.metric_currency = 'USD'::currency
                         AND c.cost_currency = 'MXN'::currency)
                        OR (d.metric_currency = 'MXN'::currency
                            AND c.cost_currency = 'USD'::currency)
                    ) THEN true
               ELSE false
           END AS sin_fx
      FROM dias_entidad d
      JOIN vivos vv
        ON vv.ad_group_id = d.ad_group_id
       AND vv.platform = d.platform
      LEFT JOIN sku_cost c
        ON c.product_id = vv.product_id
       AND d.metric_date >= c.valid_from
       AND (c.valid_to IS NULL OR d.metric_date < c.valid_to)
      LEFT JOIN LATERAL (
           SELECT r.rate, r.source
             FROM fx_resolve(d.metric_date, 'USD'::currency, 'MXN'::currency) r
      ) fx ON d.metric_currency = 'USD'::currency
           AND c.cost_currency = 'MXN'::currency
      LEFT JOIN LATERAL (
           SELECT r.rate, r.source
             FROM fx_resolve(d.metric_date, 'USD'::currency, 'MXN'::currency) r
      ) fx_up ON d.metric_currency = 'MXN'::currency
              AND c.cost_currency = 'USD'::currency
),
-- Catalogo completo: todo vivo con listing+product+costo ese dia + precio.
catalogo_dia AS (
    SELECT cd.ad_entity_id,
           cd.ad_group_id,
           cd.platform,
           cd.metric_date,
           cd.product_id,
           cd.cost_i,
           cd.fx_source_dia,
           cd.sin_fx,
           cd.listing_id,
           CASE
               WHEN cd.platform = 'amazon_mx'::platform THEN pm.price_i
               WHEN cd.platform = 'amazon_us'::platform
                    AND pu.price_currency = cd.metric_currency
                   THEN pu.price_i
               ELSE NULL
           END AS price_i
      FROM costo_dia cd
      LEFT JOIN precio_mx pm
        ON pm.platform = cd.platform
       AND pm.product_id = cd.product_id
      LEFT JOIN precio_us pu
        ON pu.platform = cd.platform
       AND pu.product_id = cd.product_id
),
-- Cobertura 100% del catalogo vivo:
--   base: listing + product + cost_i (+ FX si aplica) en CADA dia de la serie
--   US: ademas listing_price
--   MX (D1.mx): price_i ledger obligatorio solo para productos con w_i > 0
--     (un ASIN sin ventas no inventa precio; si tiene peso y no precio → ausente)
cobertura AS (
    SELECT c.ad_entity_id,
           c.metric_date,
           COUNT(*) AS n_vivos_dia,
           COUNT(*) FILTER (
               WHERE c.listing_id IS NOT NULL
                 AND c.product_id IS NOT NULL
                 AND c.cost_i IS NOT NULL
                 AND NOT c.sin_fx
                 AND (
                     (c.platform = 'amazon_us'::platform
                      AND c.price_i IS NOT NULL AND c.price_i > 0)
                     OR c.platform = 'amazon_mx'::platform
                 )
           ) AS n_completos_base,
           COUNT(*) FILTER (
               WHERE p.w_i IS NOT NULL
                 AND (c.price_i IS NULL OR c.price_i <= 0)
           ) AS n_peso_sin_precio
      FROM catalogo_dia c
      LEFT JOIN pesos p
        ON p.ad_group_id = c.ad_group_id
       AND p.platform = c.platform
       AND p.product_id = c.product_id
     GROUP BY 1, 2
),
cobertura_ok AS (
    SELECT s.ad_entity_id
      FROM serie_ok s
      JOIN vivos_cnt vc
        ON vc.ad_group_id = s.ad_group_id
       AND vc.platform = s.platform
     WHERE vc.n_vivos > 0
       AND NOT EXISTS (
           SELECT 1
             FROM cobertura c
            WHERE c.ad_entity_id = s.ad_entity_id
              AND (
                  c.n_completos_base < c.n_vivos_dia
                  OR c.n_peso_sin_precio > 0
                  OR c.n_vivos_dia <> vc.n_vivos
              )
       )
),
-- Pesos disponibles (sigma > 0) para el grupo.
con_mezcla AS (
    SELECT s.ad_entity_id
      FROM serie_ok s
      JOIN sigma g
        ON g.ad_group_id = s.ad_group_id
       AND g.platform = s.platform
     WHERE g.sigma_ventas > 0
),
-- ratio_G(d) = SUM w_i * (cost_i(d) / price_i); FX source peor del dia.
ratio_dia AS (
    SELECT c.ad_entity_id,
           c.metric_date,
           SUM(p.w_i * (c.cost_i / c.price_i)) AS ratio_g,
           -- nearest_prior gana a exact si aparece en cualquier producto.
           CASE
               WHEN BOOL_OR(c.fx_source_dia = 'nearest_prior'::fx_source)
                   THEN 'nearest_prior'::fx_source
               WHEN BOOL_OR(c.fx_source_dia = 'exact'::fx_source)
                   THEN 'exact'::fx_source
               ELSE NULL
           END AS fx_source_dia,
           BOOL_OR(c.sin_fx) AS sin_fx_dia,
           BOOL_OR(c.cost_i IS NULL OR c.price_i IS NULL OR c.price_i <= 0)
               AS hueco_ratio
      -- DEDUP por (entidad, dia, producto): catalogo_dia hereda UNA fila por
      -- product_AD, y dos ads del MISMO producto en el grupo duplicaban su
      -- termino w_i*(cost/price) — COGS al doble (cazado por el test de
      -- integracion 'dos_product_ads_mismo_producto': esperado 7.5, salia
      -- -5). cost_i y price_i son identicos entre duplicados (mismo
      -- producto, mismo dia), asi que DISTINCT es exacto, no un promedio.
      FROM (
           SELECT DISTINCT ad_entity_id, metric_date, ad_group_id, platform,
                  product_id, cost_i, price_i, fx_source_dia, sin_fx
             FROM catalogo_dia
      ) c
      JOIN pesos p
        ON p.ad_group_id = c.ad_group_id
       AND p.platform = c.platform
       AND p.product_id = c.product_id
      JOIN cobertura_ok ok ON ok.ad_entity_id = c.ad_entity_id
      JOIN con_mezcla mx ON mx.ad_entity_id = c.ad_entity_id
     GROUP BY 1, 2
),
ratio_ok AS (
    SELECT ad_entity_id
      FROM (
           SELECT ad_entity_id,
                  COUNT(*) AS dias,
                  COUNT(*) FILTER (
                      WHERE sin_fx_dia OR hueco_ratio OR ratio_g IS NULL
                  ) AS dias_malos
             FROM ratio_dia
            GROUP BY 1
      ) x
     WHERE dias > 0 AND dias_malos = 0
),
-- COGS diario: revenue del dia * ratio del dia (puntas halo).
cogs_diario AS (
    SELECT m.ad_entity_id,
           SUM(m.revenue_same_sku * r.ratio_g) AS cogs_sin_halo,
           SUM(m.ad_revenue * r.ratio_g)       AS cogs_con_halo,
           BOOL_OR(r.ratio_g > 1)              AS rango_invertido,
           CASE
               WHEN BOOL_OR(r.fx_source_dia = 'nearest_prior'::fx_source)
                   THEN 'nearest_prior'::fx_source
               WHEN BOOL_OR(r.fx_source_dia = 'exact'::fx_source)
                   THEN 'exact'::fx_source
               ELSE NULL
           END AS fx_source
      FROM metricas m
      JOIN ratio_dia r
        ON r.ad_entity_id = m.ad_entity_id
       AND r.metric_date = m.metric_date
      JOIN ratio_ok ok ON ok.ad_entity_id = m.ad_entity_id
     GROUP BY 1
)
SELECT s.platform,
       s.ad_entity_id,
       s.kind,
       s.metric_currency,
       v.d_from AS metric_date_from,
       v.d_to   AS metric_date_to,
       s.ad_revenue_sum,
       s.revenue_same_sku_sum,
       s.cost_sum,
       c.cogs_sin_halo,
       c.cogs_con_halo,
       (s.revenue_same_sku_sum - s.cost_sum - c.cogs_sin_halo) AS contrib_sin_halo,
       (s.ad_revenue_sum - s.cost_sum - c.cogs_con_halo)       AS contrib_con_halo,
       c.rango_invertido,
       c.fx_source,
       true AS no_decisoria,
       'contribucion_pre_cargos · no decisoria'::text AS etiqueta,
       'gasto_ads_metricas,cogs_proxy_d1'::text AS cargos_incluidos,
       'fee,refund,withholding,fee_type_ads_ledger'::text AS cargos_excluidos,
       CASE
           WHEN s.platform = 'amazon_mx'::platform
               THEN (v.d_to::timestamp AT TIME ZONE 'UTC')
           ELSE now()
       END AS precio_as_of
  FROM serie_ok s
  JOIN cobertura_ok cob ON cob.ad_entity_id = s.ad_entity_id
  JOIN con_mezcla mx ON mx.ad_entity_id = s.ad_entity_id
  JOIN ratio_ok ro ON ro.ad_entity_id = s.ad_entity_id
  JOIN cogs_diario c ON c.ad_entity_id = s.ad_entity_id
  CROSS JOIN ventana v
 -- Par halo siempre presente (ambos contrib_* NOT NULL) o no hay fila.
 WHERE c.cogs_sin_halo IS NOT NULL
   AND c.cogs_con_halo IS NOT NULL;

COMMENT ON VIEW v_contribucion_entidad IS
  'Contribucion PRE-CARGOS por entidad (keyword|product_target). '
  'docs/MARGEN-ENTIDAD.md SELLADO 2026-08-31. '
  'Grano: kind IN (''keyword'', ''product_target'') — mismo allowlist que '
  'v_tacos 0005/0006 y app/cobertura.py. '
  'Ventana: metric_date IN [D_corte-89, D_corte], D_corte = hoy_UTC-15 via '
  'v_metric_mature (sin ese corte la vista no publica filas maduras). '
  'Ingreso = ad_revenue / revenue_same_sku (metricas; ledger NO atribuye). '
  'Gasto = cost de metricas (fee_type=ads del ledger es OTRA superficie). '
  'COGS = C+B: ratio_G(d) = SUM w_i*(cost_i(d)/price_i); w_i = mezcla ledger '
  'del catalogo del ad_group; cost_i = sku_cost as-of cada metric_date. '
  'D1.mx: price_i MX = promedio ponderado item_price/quantity del ledger '
  '(razon adimensional MXN/MXN). US: listing_price con price_currency = USD '
  'y un solo precio por producto; costo MXN→USD = '
  'cost/fx_resolve(date,''USD'',''MXN'') — NUNCA el par invertido. '
  'Cobertura 100% del catalogo vivo (ENABLED|PAUSED) o fila AUSENTE. '
  'sigma ventas = 0 → ausente. Serie con cost NULL o sin par halo → ausente. '
  'Sin FX utilizable → ausente. Par contrib_sin_halo/contrib_con_halo siempre '
  'ambos o no hay fila. rango_invertido si ratio_G > 1. '
  'no_decisoria=true siempre (D1.bis) hasta sello del lead para Fase 2. '
  'Cargos fee/refund/withholding NO entran (multi-home).';


-- ---------------------------------------------------------------------------
-- v_contribucion_cobertura: entidades ausentes con motivo (D6/D7)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_contribucion_cobertura AS
WITH
ventana AS (
    SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89) AS d_from,
           ((now() AT TIME ZONE 'UTC')::date - 15)      AS d_to
),
candidatos AS (
    SELECT DISTINCT e.id AS ad_entity_id,
           e.platform,
           e.kind,
           e.parent_id AS ad_group_id
      FROM ad_entity e
      JOIN v_metric_mature m ON m.ad_entity_id = e.id
      CROSS JOIN ventana v
     WHERE m.metric_date BETWEEN v.d_from AND v.d_to
),
metricas AS (
    SELECT e.id AS ad_entity_id,
           m.cost,
           m.ad_revenue,
           m.revenue_same_sku,
           m.metric_date
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      CROSS JOIN ventana v
     WHERE e.kind IN ('keyword', 'product_target')
       AND m.metric_date BETWEEN v.d_from AND v.d_to
),
serie_flags AS (
    SELECT ad_entity_id,
           COUNT(*) FILTER (WHERE cost IS NULL) > 0 AS sin_costo,
           COUNT(*) FILTER (
               WHERE ad_revenue IS NULL OR revenue_same_sku IS NULL
           ) > 0 AS sin_par_halo
      FROM metricas
     GROUP BY 1
),
vivos AS (
    SELECT pa.parent_id AS ad_group_id,
           pa.platform,
           pa.id AS product_ad_id,
           pa.listing_id,
           l.product_id,
           l.listing_price,
           l.price_currency
      FROM ad_entity pa
      JOIN ad_entity_state st ON st.ad_entity_id = pa.id
      LEFT JOIN listing l ON l.id = pa.listing_id
     WHERE pa.kind = 'product_ad'
       AND st.status IN ('ENABLED', 'PAUSED')
),
vivos_huecos AS (
    SELECT v.ad_group_id,
           v.platform,
           COUNT(*) AS n_vivos,
           COUNT(*) FILTER (
               WHERE v.listing_id IS NULL
                  OR v.product_id IS NULL
                  OR (v.platform = 'amazon_us'::platform
                      AND (v.listing_price IS NULL
                           OR v.price_currency IS DISTINCT FROM 'USD'::currency))
           ) AS n_sin_catalogo
      FROM vivos v
     GROUP BY 1, 2
),
precio_mx_prod AS (
    SELECT l.product_id
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind = 'sale'
       AND l.platform = 'amazon_mx'::platform
       AND l.product_id IS NOT NULL
       AND l.item_price IS NOT NULL
       AND l.event_date BETWEEN v.d_from AND v.d_to
     GROUP BY 1
),
mx_sin_precio AS (
    -- Solo productos del catalogo que entran al peso y no tienen item_price.
    SELECT vv.ad_group_id, vv.platform
      FROM vivos vv
      JOIN ledger_event l
        ON l.product_id = vv.product_id
       AND l.platform = vv.platform
       AND l.kind = 'sale'
       AND l.event_date BETWEEN ((now() AT TIME ZONE 'UTC')::date - 15 - 89)
                           AND ((now() AT TIME ZONE 'UTC')::date - 15)
     WHERE vv.platform = 'amazon_mx'::platform
       AND vv.product_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM precio_mx_prod p WHERE p.product_id = vv.product_id
       )
     GROUP BY 1, 2
),
sigma AS (
    SELECT vv.ad_group_id,
           vv.platform,
           COALESCE(SUM(l.amount), 0) AS sigma_ventas
      FROM vivos vv
      LEFT JOIN ledger_event l
        ON l.product_id = vv.product_id
       AND l.platform = vv.platform
       AND l.kind = 'sale'
       AND l.event_date BETWEEN ((now() AT TIME ZONE 'UTC')::date - 15 - 89)
                           AND ((now() AT TIME ZONE 'UTC')::date - 15)
     GROUP BY 1, 2
),
-- Costo/FX: algun vivo sin sku_cost as-of algun dia de la serie de la entidad,
-- o sin FX cuando hace falta (US cost MXN→USD).
costo_fx_hueco AS (
    SELECT DISTINCT m.ad_entity_id,
           BOOL_OR(c.id IS NULL) AS sin_costo_sku,
           BOOL_OR(
               c.id IS NOT NULL
               AND e.platform = 'amazon_us'::platform
               AND c.cost_currency = 'MXN'::currency
               AND fx.rate IS NULL
           ) AS sin_fx
      FROM metricas m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      JOIN vivos vv ON vv.ad_group_id = e.parent_id AND vv.platform = e.platform
      LEFT JOIN sku_cost c
        ON c.product_id = vv.product_id
       AND m.metric_date >= c.valid_from
       AND (c.valid_to IS NULL OR m.metric_date < c.valid_to)
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(m.metric_date, 'USD'::currency, 'MXN'::currency) r
      ) fx ON e.platform = 'amazon_us'::platform
           AND c.cost_currency = 'MXN'::currency
     GROUP BY 1
)
SELECT c.platform,
       c.ad_entity_id,
       c.kind,
       CASE
           WHEN c.kind NOT IN ('keyword', 'product_target') THEN 'kind_fuera'
           WHEN c.ad_group_id IS NULL THEN 'sin_padre'
           WHEN COALESCE(sf.sin_costo, false) OR COALESCE(sf.sin_par_halo, false)
               THEN 'serie_incompleta'
           WHEN vh.ad_group_id IS NULL OR vh.n_vivos = 0 OR vh.n_sin_catalogo > 0
               THEN 'catalogo_parcial'
           WHEN c.platform = 'amazon_mx'::platform
                AND mx.ad_group_id IS NOT NULL
               THEN 'sin_precio'
           WHEN COALESCE(sg.sigma_ventas, 0) = 0 THEN 'sin_mezcla_ledger'
           WHEN COALESCE(cf.sin_costo_sku, false) THEN 'catalogo_parcial'
           WHEN COALESCE(cf.sin_fx, false) THEN 'sin_fx'
           ELSE 'catalogo_parcial'
       END AS motivo
  FROM candidatos c
  LEFT JOIN v_contribucion_entidad ok ON ok.ad_entity_id = c.ad_entity_id
  LEFT JOIN serie_flags sf ON sf.ad_entity_id = c.ad_entity_id
  LEFT JOIN vivos_huecos vh
    ON vh.ad_group_id = c.ad_group_id AND vh.platform = c.platform
  LEFT JOIN mx_sin_precio mx
    ON mx.ad_group_id = c.ad_group_id AND mx.platform = c.platform
  LEFT JOIN sigma sg
    ON sg.ad_group_id = c.ad_group_id AND sg.platform = c.platform
  LEFT JOIN costo_fx_hueco cf ON cf.ad_entity_id = c.ad_entity_id
 WHERE ok.ad_entity_id IS NULL;

COMMENT ON VIEW v_contribucion_cobertura IS
  'Entidades con metricas maduras en la ventana 90d que NO salen en '
  'v_contribucion_entidad, con motivo: kind_fuera, sin_padre, '
  'serie_incompleta, catalogo_parcial, sin_precio, sin_mezcla_ledger, '
  'sin_fx. Distingue "sin actividad" de "excluida por datos" (D7). '
  'Allowlist de grano: keyword|product_target (misma que v_tacos y '
  'app/cobertura.py). Solo lectura; cero escritura.';


-- ---------------------------------------------------------------------------
-- v_desfase_gasto_ads: metricas vs ledger fee_type=ads (D6)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_desfase_gasto_ads AS
WITH
ventana AS (
    SELECT ((now() AT TIME ZONE 'UTC')::date - 15 - 89) AS d_from,
           ((now() AT TIME ZONE 'UTC')::date - 15)      AS d_to
),
gasto_metricas AS (
    SELECT e.platform,
           m.metric_currency,
           SUM(m.cost) AS gasto_metricas,
           COUNT(*) FILTER (WHERE m.cost IS NULL) AS filas_sin_costo
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      CROSS JOIN ventana v
     WHERE e.kind IN ('keyword', 'product_target')
       AND m.metric_date BETWEEN v.d_from AND v.d_to
     GROUP BY 1, 2
),
gasto_ledger AS (
    -- fee_type=ads: convención de signos NEGATIVA. Se compara magnitud.
    SELECT l.platform,
           l.amount_currency AS metric_currency,
           SUM(-l.amount) AS gasto_ledger_ads,
           COUNT(*) AS filas_ledger
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind = 'fee'
       AND l.fee_type = 'ads'
       AND l.event_date BETWEEN v.d_from AND v.d_to
     GROUP BY 1, 2
)
SELECT COALESCE(m.platform, l.platform) AS platform,
       COALESCE(m.metric_currency, l.metric_currency) AS currency,
       m.gasto_metricas,
       l.gasto_ledger_ads,
       CASE
           WHEN m.gasto_metricas IS NULL OR l.gasto_ledger_ads IS NULL THEN NULL
           WHEN COALESCE(m.filas_sin_costo, 0) > 0 THEN NULL
           ELSE m.gasto_metricas - l.gasto_ledger_ads
       END AS desfase,
       COALESCE(m.filas_sin_costo, 0) AS filas_metricas_sin_costo,
       COALESCE(l.filas_ledger, 0) AS filas_ledger_ads
  FROM gasto_metricas m
  FULL OUTER JOIN gasto_ledger l
    ON l.platform = m.platform
   AND l.metric_currency = m.metric_currency;

COMMENT ON VIEW v_desfase_gasto_ads IS
  'Reconciliacion fail-loud (D6): gasto de ads en metricas '
  '(kind IN keyword|product_target, ventana madura 90d via v_metric_mature) '
  'versus fee_type=''ads'' del ledger en la misma ventana. '
  'desfase = gasto_metricas - |gasto_ledger|; NULL si falta un lado o hay '
  'cost NULL en metricas — el desfase se CUENTA, no se silencia. '
  'Son dos superficies del mismo hecho; un consumidor elige una (regla 2). '
  'No se suman entre si.';

GRANT SELECT ON v_contribucion_entidad, v_contribucion_cobertura, v_desfase_gasto_ads
    TO app_read, app_ingest, app_decide, app_admin;
