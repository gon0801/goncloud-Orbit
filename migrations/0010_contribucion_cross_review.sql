-- =============================================================================
--  ORBIT · MIGRACION 0010 · cross-review 1.5: marca precisa + reconciliacion
--
--  Dos hallazgos MEDIA del cross-review de la 1.5 (claude/codex/grok
--  2026-09-01, out/_cr_1_5_*.txt), cada uno con su test rojo primero
--  (tests/test_contribucion_multilisting.py):
--
--  1. La marca precio_min_multilisting SOBRE-DECLARABA: grupo_multilisting
--     marcaba al ad_group por la simple PRESENCIA de un producto
--     multilisting vivo, aunque ese producto no tuviera ventas en la
--     ventana (w_i NULL) y su MIN jamas hubiera entrado al ratio. El chip
--     del dashboard y el digest afirman "esta contribucion uso el precio
--     MENOR" — mentira en ese caso (jamas silencioso, jamas inflado: la
--     marca tambien). Fix: la marca exige PESO — grupo_multilisting se
--     define sobre `pesos` (el producto multilisting participo del ratio
--     con su MIN). En prod no cambia ninguna marca publicada hoy: los dos
--     productos multilisting (120/356) venden; es un fix de veracidad
--     latente, no de datos.
--
--  2. Las columnas publicadas NO reconciliaban entre si: 0009 redondeaba
--     cogs pero computaba contrib del cogs CRUDO — ROUND(a-b) != a -
--     ROUND(b) (empate en el 5o decimal: quien cuadre revenue - cost -
--     cogs a mano ve un descuadre de 0.0001). Fix: contrib se computa del
--     cogs YA redondeado; las columnas publicadas cuadran exactas al 4o
--     decimal (ROUND final es identidad: revenue/cost ya son escala 4).
--
--  Definicion = 0009 + los dos fixes. Misma interfaz, misma ventana,
--  mismo sellado FX. Reversa: re-aplicar la 0009 (misma interfaz; OR
--  REPLACE directo). OJO si se revierte MAS ALLA de 0008 (DROP CASCADE):
--  los GRANT SELECT de los roles de servicio viven SOLO en la 0006 y hay
--  que re-correrlos (ver 0006, seccion final) — la reversa original de la
--  0008 no lo decia (hallazgo codex del mismo cross-review).
-- =============================================================================

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
-- Precio US (D1.bis, ENMIENDA 0008): MIN(listing_price) entre los vivos del
-- producto. Antes de 0008 se exigia UN SOLO precio (HAVING COUNT(DISTINCT)
-- = 1) y el producto multilisting quedaba sin precio -> entidad ausente.
-- Sello del dueno 2026-09-01: el MENOR, MARCADO (margen pesimista).
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
),
-- 0008: productos US con 2+ precios distintos entre sus vivos — la marca.
producto_multilisting AS (
    SELECT v.platform,
           v.product_id
      FROM vivos v
     WHERE v.platform = 'amazon_us'::platform
       AND v.listing_price IS NOT NULL
       AND v.product_id IS NOT NULL
       AND v.price_currency = 'USD'::currency
     GROUP BY 1, 2
    HAVING COUNT(DISTINCT v.listing_price) > 1
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
-- 0010: la marca exige PESO — el grupo se marca solo si algun producto
-- multilisting PARTICIPO del ratio (w_i en la ventana), es decir, si su
-- MIN entro de verdad al calculo. En 0008/0009 bastaba la presencia del
-- producto en vivos y la marca podia mentir (cross-review 1.5).
grupo_multilisting AS (
    SELECT DISTINCT p.ad_group_id, p.platform
      FROM pesos p
      JOIN producto_multilisting pm
        ON pm.platform = p.platform AND pm.product_id = p.product_id
),
-- 0007: pesos pre-unidos a vivos (join chico 11k x 2.7k). w_i es por
-- (ad_group, platform, product_id) — sin dia — asi que viaja con el vivo y
-- NINGUN join grande vuelve a tocar el CTE pesos (el Nested Loop de 144s
-- en prod: pesos escaneado 528k veces por misestimacion del planner).
vivos_pesos AS (
    SELECT v.ad_group_id,
           v.platform,
           v.product_ad_id,
           v.listing_id,
           v.product_id,
           p.w_i
      FROM vivos v
      LEFT JOIN pesos p
        ON p.ad_group_id = v.ad_group_id
       AND p.platform = v.platform
       AND p.product_id = v.product_id
),
-- Dias distintos con metrica por (entidad) — para exigir costo as-of cada dia.
dias_entidad AS (
    SELECT DISTINCT ad_entity_id, ad_group_id, platform, metric_date, metric_currency
      FROM metricas
),
-- 0007: fx_resolve es STABLE y solo depende de (metric_date, 'USD', 'MXN').
-- UNA VEZ por dia de la ventana; en 0006 corria en LATERAL por cada fila
-- (entidad-dia x producto vivo): 1.1M llamadas medidas en prod.
fx_dia AS (
    SELECT d.metric_date, r.rate, r.source
      FROM (SELECT DISTINCT metric_date FROM dias_entidad) d
      CROSS JOIN LATERAL fx_resolve(d.metric_date, 'USD'::currency, 'MXN'::currency) r
),
-- 0007: sku_cost as-of por (producto, dia) distinto, no por fila del millon.
-- A lo mas UN costo vigente por producto y fecha (EXCLUDE sin solapamientos,
-- 0001), asi que el resultado por par es identico al join de 0006.
costo_producto_dia AS (
    SELECT pd.product_id,
           pd.metric_date,
           c.id            AS sku_cost_id,
           c.cost_amount,
           c.cost_currency
      FROM (
           SELECT DISTINCT vv.product_id, d.metric_date
             FROM dias_entidad d
             JOIN vivos vv
               ON vv.ad_group_id = d.ad_group_id
              AND vv.platform = d.platform
            WHERE vv.product_id IS NOT NULL
      ) pd
      LEFT JOIN sku_cost c
        ON c.product_id = pd.product_id
       AND pd.metric_date >= c.valid_from
       AND (c.valid_to IS NULL OR pd.metric_date < c.valid_to)
),
-- Catalogo del grupo por dia y moneda de reporte: las MISMAS expresiones de
-- costo_dia/catalogo_dia de 0006, a grano (ad_group, dia, moneda, product_ad)
-- en vez de (entidad, dia, product_ad) — la entidad no cambia ningun valor.
-- US: cost MXN / fx_resolve(date,'USD','MXN') — NUNCA el par invertido.
vivo_dia AS (
    SELECT d.ad_group_id,
           d.platform,
           d.metric_date,
           d.metric_currency,
           vp.product_ad_id,
           vp.product_id,
           vp.listing_id,
           vp.w_i,
           CASE
               WHEN cpd.sku_cost_id IS NULL THEN NULL
               WHEN d.metric_currency = cpd.cost_currency THEN cpd.cost_amount
               WHEN d.metric_currency = 'USD'::currency
                    AND cpd.cost_currency = 'MXN'::currency
                    AND fxd.rate IS NOT NULL
                   THEN cpd.cost_amount / fxd.rate
               WHEN d.metric_currency = 'MXN'::currency
                    AND cpd.cost_currency = 'USD'::currency
                    AND fxd.rate IS NOT NULL
                   THEN cpd.cost_amount * fxd.rate
               ELSE NULL
           END AS cost_i,
           CASE
               WHEN d.metric_currency = cpd.cost_currency THEN NULL
               WHEN d.metric_currency = 'USD'::currency
                    AND cpd.cost_currency = 'MXN'::currency
                   THEN fxd.source
               WHEN d.metric_currency = 'MXN'::currency
                    AND cpd.cost_currency = 'USD'::currency
                   THEN fxd.source
               ELSE NULL
           END AS fx_source_dia,
           CASE
               WHEN cpd.sku_cost_id IS NULL THEN true
               WHEN d.metric_currency = cpd.cost_currency THEN false
               WHEN d.metric_currency = 'USD'::currency
                    AND cpd.cost_currency = 'MXN'::currency
                    AND fxd.rate IS NULL THEN true
               WHEN d.metric_currency = 'MXN'::currency
                    AND cpd.cost_currency = 'USD'::currency
                    AND fxd.rate IS NULL THEN true
               WHEN d.metric_currency <> cpd.cost_currency
                    AND NOT (
                        (d.metric_currency = 'USD'::currency
                         AND cpd.cost_currency = 'MXN'::currency)
                        OR (d.metric_currency = 'MXN'::currency
                            AND cpd.cost_currency = 'USD'::currency)
                    ) THEN true
               ELSE false
           END AS sin_fx,
           CASE
               WHEN d.platform = 'amazon_mx'::platform THEN pm.price_i
               WHEN d.platform = 'amazon_us'::platform
                    AND pu.price_currency = d.metric_currency
                   THEN pu.price_i
               ELSE NULL
           END AS price_i
      FROM (
           SELECT DISTINCT ad_group_id, platform, metric_date, metric_currency
             FROM dias_entidad
      ) d
      JOIN vivos_pesos vp
        ON vp.ad_group_id = d.ad_group_id
       AND vp.platform = d.platform
      LEFT JOIN costo_producto_dia cpd
        ON cpd.product_id = vp.product_id
       AND cpd.metric_date = d.metric_date
      LEFT JOIN fx_dia fxd
        ON fxd.metric_date = d.metric_date
      LEFT JOIN precio_mx pm
        ON pm.platform = d.platform
       AND pm.product_id = vp.product_id
      LEFT JOIN precio_us pu
        ON pu.platform = d.platform
       AND pu.product_id = vp.product_id
),
-- Cobertura 100% del catalogo vivo, a grano (grupo, dia, moneda):
--   base: listing + product + cost_i (+ FX si aplica) en CADA dia de la serie
--   US: ademas listing_price
--   MX (D1.mx): price_i ledger obligatorio solo para productos con w_i > 0
--     (un ASIN sin ventas no inventa precio; si tiene peso y no precio → ausente)
cobertura AS (
    SELECT vd.ad_group_id,
           vd.platform,
           vd.metric_date,
           vd.metric_currency,
           COUNT(*) AS n_vivos_dia,
           COUNT(*) FILTER (
               WHERE vd.listing_id IS NOT NULL
                 AND vd.product_id IS NOT NULL
                 AND vd.cost_i IS NOT NULL
                 AND NOT vd.sin_fx
                 AND (
                     (vd.platform = 'amazon_us'::platform
                      AND vd.price_i IS NOT NULL AND vd.price_i > 0)
                     OR vd.platform = 'amazon_mx'::platform
                 )
           ) AS n_completos_base,
           COUNT(*) FILTER (
               WHERE vd.w_i IS NOT NULL
                 AND (vd.price_i IS NULL OR vd.price_i <= 0)
           ) AS n_peso_sin_precio
      FROM vivo_dia vd
     GROUP BY 1, 2, 3, 4
),
-- 0007: la cobertura de 0006 era por (entidad, dia) sobre los dias de la SERIE
-- DE ESA ENTIDAD. La expansion por entidad se hace AQUI via dias_entidad, asi
-- un dia que la entidad no tiene en su serie no la excluye (dos entidades del
-- mismo grupo pueden tener series distintas).
cobertura_entidad AS (
    SELECT dd.ad_entity_id,
           c.ad_group_id,
           c.platform,
           c.metric_date,
           c.n_vivos_dia,
           c.n_completos_base,
           c.n_peso_sin_precio
      FROM cobertura c
      JOIN dias_entidad dd
        ON dd.ad_group_id = c.ad_group_id
       AND dd.platform = c.platform
       AND dd.metric_date = c.metric_date
       AND dd.metric_currency = c.metric_currency
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
             FROM cobertura_entidad c
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
-- ratio_G(d) = SUM w_i * (cost_i(d) / price_i) a grano (grupo, dia, moneda);
-- FX source peor del dia. DEDUP por (grupo, dia, moneda, producto): vivo_dia
-- hereda UNA fila por product_AD, y dos ads del MISMO producto en el grupo
-- duplicaban su termino w_i*(cost/price) — COGS al doble (cazado por el test
-- de integracion 'dos_product_ads_mismo_producto': esperado 7.5, salia -5).
-- cost_i y price_i son identicos entre duplicados (mismo producto, mismo dia),
-- asi que DISTINCT es exacto, no un promedio.
ratio_dia_grupo AS (
    SELECT c.ad_group_id,
           c.platform,
           c.metric_date,
           c.metric_currency,
           SUM(c.w_i * (c.cost_i / c.price_i)) AS ratio_g,
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
      FROM (
           SELECT DISTINCT ad_group_id, platform, metric_date, metric_currency,
                  product_id, w_i, cost_i, price_i, fx_source_dia, sin_fx
             FROM vivo_dia
      ) c
     WHERE c.w_i IS NOT NULL
     GROUP BY 1, 2, 3, 4
),
-- ratio_dia por (entidad, dia): la expansion a entidad via dias_entidad.
-- La restriccion a entidades ok (cobertura_ok + con_mezcla de 0006) solo
-- filtraba filas; el VALOR por (grupo, dia, moneda) no depende de ella.
ratio_dia AS (
    SELECT dd.ad_entity_id,
           dd.metric_date,
           rg.ratio_g,
           rg.fx_source_dia,
           rg.sin_fx_dia,
           rg.hueco_ratio
      FROM ratio_dia_grupo rg
      JOIN dias_entidad dd
        ON dd.ad_group_id = rg.ad_group_id
       AND dd.platform = rg.platform
       AND dd.metric_date = rg.metric_date
       AND dd.metric_currency = rg.metric_currency
      JOIN serie_ok s ON s.ad_entity_id = dd.ad_entity_id
      JOIN cobertura_ok ok ON ok.ad_entity_id = dd.ad_entity_id
      JOIN con_mezcla mx ON mx.ad_entity_id = dd.ad_entity_id
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
       -- 0009: el dinero COMPUTADO (cogs/contrib, de w_i*(cost_i/price_i))
       -- sale redondeado a la escala del schema (NUMERIC(14,4), regla 4);
       -- los CTEs internos siguen exactos. Bug prod: colas de ~40 decimales
       -- en el dashboard.
       -- 0010: contrib se computa del cogs YA REDONDEADO, asi las columnas
       -- publicadas reconcilian exactas al 4o decimal (revenue - cost -
       -- cogs = contrib; el ROUND externo es identidad porque revenue/cost
       -- ya son escala 4). En 0009 se computaba del cogs crudo y podian
       -- descuadrar en 0.0001 (cross-review 1.5).
       ROUND(c.cogs_sin_halo, 4) AS cogs_sin_halo,
       ROUND(c.cogs_con_halo, 4) AS cogs_con_halo,
       ROUND(s.revenue_same_sku_sum - s.cost_sum - ROUND(c.cogs_sin_halo, 4), 4)
           AS contrib_sin_halo,
       ROUND(s.ad_revenue_sum - s.cost_sum - ROUND(c.cogs_con_halo, 4), 4)
           AS contrib_con_halo,
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
       END AS precio_as_of,
       -- 0008 (columna NUEVA AL FINAL): el grupo contiene algun producto US
       -- con 2+ listings a precios distintos y la contribucion uso el MENOR.
       -- 0010: la marca exige que ese producto tenga PESO en la ventana
       -- (su MIN participo del ratio) — presencia sin peso no marca.
       (gm.ad_group_id IS NOT NULL) AS precio_min_multilisting
  FROM serie_ok s
  JOIN cobertura_ok cob ON cob.ad_entity_id = s.ad_entity_id
  JOIN con_mezcla mx ON mx.ad_entity_id = s.ad_entity_id
  JOIN ratio_ok ro ON ro.ad_entity_id = s.ad_entity_id
  JOIN cogs_diario c ON c.ad_entity_id = s.ad_entity_id
  LEFT JOIN grupo_multilisting gm
    ON gm.ad_group_id = s.ad_group_id AND gm.platform = s.platform
  CROSS JOIN ventana v
 -- Par halo siempre presente (ambos contrib_* NOT NULL) o no hay fila.
 WHERE c.cogs_sin_halo IS NOT NULL
   AND c.cogs_con_halo IS NOT NULL;

COMMENT ON VIEW v_contribucion_entidad IS
  'Contribucion PRE-CARGOS por entidad (keyword|product_target). '
  'docs/MARGEN-ENTIDAD.md SELLADO 2026-08-31; enmienda D1.bis sellada por '
  'el dueno 2026-09-01 (0008): producto US con varios listings a precios '
  'distintos publica con el MENOR (MIN listing_price) y la fila sale '
  'marcada — precio_min_multilisting, columna nueva AL FINAL. Antes de 0008 '
  'se exigia precio unico por producto y esas entidades salian ausentes '
  '(medido en prod: 273 entidades, ~4,908 USD de gasto 90d, por los '
  'productos 120/356 con dos ASINs). No hay ponderacion posible: el ledger '
  'US no distingue ASIN y llega sin item_price. '
  '0007 rearmo la cadena de CTEs por perf (medido en prod: ~100s por '
  'consulta): fx_resolve por dia (era LATERAL por fila), sku_cost as-of por '
  '(producto,dia), pesos pre-unidos a vivos y agregacion a grano '
  '(grupo,dia,moneda) ANTES de expandir a entidad. '
  'Grano: kind IN (''keyword'', ''product_target'') — mismo allowlist que '
  'v_tacos 0005/0006 y app/cobertura.py. '
  'Ventana: metric_date IN [D_corte-89, D_corte], D_corte = hoy_UTC-15 via '
  'v_metric_mature (sin ese corte la vista no publica filas maduras). '
  'Ingreso = ad_revenue / revenue_same_sku (metricas; ledger NO atribuye). '
  'Gasto = cost de metricas (fee_type=ads del ledger es OTRA superficie). '
  'COGS = C+B: ratio_G(d) = SUM w_i*(cost_i(d)/price_i); w_i = mezcla ledger '
  'del catalogo del ad_group; cost_i = sku_cost as-of cada metric_date. '
  'D1.mx: price_i MX = promedio ponderado item_price/quantity del ledger '
  '(razon adimensional MXN/MXN). US: MIN(listing_price) con price_currency '
  '= USD; costo MXN→USD = cost/fx_resolve(date,''USD'',''MXN'') — NUNCA el '
  'par invertido. '
  'Cobertura 100% del catalogo vivo (ENABLED|PAUSED) o fila AUSENTE. '
  'sigma ventas = 0 → ausente. Serie con cost NULL o sin par halo → ausente. '
  'Sin FX utilizable → ausente. Par contrib_sin_halo/contrib_con_halo siempre '
  'ambos o no hay fila. rango_invertido si ratio_G > 1. '
  'no_decisoria=true siempre (D1.bis) hasta sello del lead para Fase 2. '
  '0009: cogs/contrib COMPUTADOS salen con ROUND(...,4) en la frontera '
  '(regla 4, escala NUMERIC(14,4)); los CTEs internos siguen exactos — '
  'bug prod 2026-09-01: colas de ~40 decimales en el dashboard. '
  '0010 (cross-review 1.5): la marca precio_min_multilisting exige PESO '
  '(el MIN del producto multilisting participo del ratio; presencia sin '
  'ventas no marca) y contrib se computa del cogs YA redondeado — las '
  'columnas publicadas reconcilian exactas al 4o decimal. '
  'Cargos fee/refund/withholding NO entran (multi-home).';
