-- =============================================================================
--  ORBIT · MIGRACIÓN 0012 · EL RESIDUO DE v_tacos SE VUELVE FAIL-LOUD
--
--  Cierra el residual de la 0005, subido de prioridad por la cross-review
--  externa del 2026-08-31 (codex, severidad media; grok dio LGTM). Autorizada
--  por el dueño el 2026-09-02 («arranca la alarma»).
--
--  EL AGUJERO. `gasto_ads` suma SOLO `kind IN ('keyword', 'product_target')`
--  — el mismo grano del motor — porque `ads_metric_observation` guarda el
--  mismo costo en la fila `campaign` Y en sus hijas, y sumar ambos inflaba el
--  TACoS ~2x (0005). El precio de ese filtro: si mañana Amazon publica un
--  `keywordType` nuevo que queda fuera de la allowlist de los reportes,
--  `gasto_ads` SUBESTIMA y `tacos_pct` sale bajo — el sesgo optimista de
--  siempre, y en silencio.
--
--  La 0006 (D6) ya expuso el síntoma: `gasto_campaign_sin_contraparte` =
--  SUM(campaign) − SUM(keyword+target) en la misma (platform, mes). Pero
--  quedó como columna informativa: `tacos_pct` se seguía publicando igual.
--  Un número confiado y equivocado es peor que ningún número — la disciplina
--  del resto de la vista (`filas_gasto_sin_tasa`, `filas_venta_sin_tasa`,
--  `filas_gasto_sin_costo` anulan `tacos_pct`) no se aplicaba a éste.
--
--  QUÉ AGREGA 0012, sin cambiar ninguna columna existente:
--    1. `residuo_pct` — columna de SEÑAL, siempre visible: el residuo como %
--       de `gasto_ads`, en valor absoluto. Es lo que se mira para saber si el
--       grano sigue sano, sin tener que dividir a mano.
--    2. `tacos_pct` se anula cuando `residuo_pct` supera el umbral. Misma
--       disciplina que los otros tres contadores.
--
--  EL UMBRAL: 1.00 %. NO es un número inventado (regla 3) — sale de medir la
--  vista en producción el 2026-09-02, seis meses:
--      amazon_mx  may/jun/jul 2026 → 0.0000 %
--      amazon_mx  ago 2026        → 0.0773 %  (4.75 MXN sobre 6,145.57)
--      amazon_us  may–ago 2026    → 0.0000 %  (los cuatro meses)
--  El peor mes observado es 0.0773 %, así que 1 % deja ~13x de holgura sobre
--  el ruido real y sigue muy por debajo de lo que costaría un hueco
--  estructural: un `keywordType` entero fuera de la allowlist mueve la aguja
--  en unidades de por ciento, no en centésimas. Si el umbral se queda corto
--  el síntoma es ruidoso (tacos_pct en NULL), nunca silencioso — el error
--  cae del lado seguro a propósito.
--
--  VALOR ABSOLUTO, no sólo «campaign > hijas». Si las hijas suman MÁS que su
--  campaña, el supuesto de grano también se rompió (doble conteo por el otro
--  lado). Se sospecha en las dos direcciones.
--
--  TRES FORMAS DE CALLAR, no una (las dos últimas las encontró codex en la
--  cross-review del 2026-09-02; ambas eran latentes: ningún mes de producción
--  las tocaba, y arreglarlas no movió un solo número publicado):
--    (a) el residuo SUPERA el umbral — el caso obvio;
--    (b) el residuo es NULL: no hay contraparte a nivel campaña, o su lado
--        tiene hueco de FX o de costo. La reconciliación es IMPOSIBLE, así
--        que el grano no está verificado y el número no se publica;
--    (c) `gasto_ads = 0` con residuo distinto de cero: la razón no se puede
--        formar y el ELSE publicaría `100 * 0 / venta` = 0.00 % mientras la
--        campaña gasta. Cero en las hojas con la campaña gastando es la
--        pérdida TOTAL del grano — el síntoma más grave, no el más benigno.
--
--  ORDEN DE LAS GUARDAS. Los fail-loud previos (`filas_gasto_sin_tasa`,
--  `filas_venta_sin_tasa`, `filas_gasto_sin_costo`) se evalúan ANTES: la
--  regla nueva no oculta ninguno ni lo duplica.
--
--  Re-runnable (`CREATE OR REPLACE VIEW`). Las columnas existentes conservan
--  nombre, orden y tipo; `residuo_pct` entra AL FINAL, así que ningún
--  consumidor por posición se rompe. NO toca datos, GRANTs, triggers ni
--  ninguna otra vista.
-- =============================================================================

BEGIN;

CREATE OR REPLACE VIEW v_tacos AS
WITH gasto AS (
    -- Gasto maduro (corte D-15 de v_metric_mature, UTC fijado), convertido
    -- POR FILA a la moneda canónica (MXN).
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
    -- El MISMO gasto maduro pero al grano de campaña: la contraparte contra
    -- la que se reconcilia el grano de hojas (0006, D6).
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
    -- VENTANA SIMÉTRICA con el gasto: corte D-15 UTC con la MISMA expresión
    -- que v_metric_mature. TODAS las ventas, con o sin order_id, convertidas
    -- por fila a MXN.
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
), base AS (
    -- Los contadores conservan aqui su nombre de origen (gasto_sin_tasa,
    -- ventas_sin_tasa, gasto_sin_costo) A PROPOSITO: el CASE de tacos_pct de
    -- abajo los referencia con ESOS nombres, que es lo que vigila el candado
    -- sellado test_v_tacos_fail_loud_con_huecos_parciales_de_fx sobre el
    -- subarbol del target. Renombrarlos aqui habria dejado el candado
    -- mirando nombres que ya no existen — verde y ciego.
    SELECT COALESCE(g.platform, v.platform, c.platform) AS platform,
           COALESCE(g.mes,    v.mes, c.mes)             AS mes,
           g.gasto_ads                       AS gasto_ads,
           v.venta_total                     AS venta_total,
           COALESCE(g.gasto_sin_tasa, 0)     AS gasto_sin_tasa,
           COALESCE(v.ventas_sin_tasa, 0)    AS ventas_sin_tasa,
           COALESCE(g.gasto_sin_costo, 0)    AS gasto_sin_costo,
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
       AND c.mes = COALESCE(g.mes, v.mes)
)
SELECT b.platform                AS platform,
       b.mes                     AS mes,
       b.gasto_ads               AS gasto_ads,
       b.venta_total             AS venta_total,
       b.gasto_sin_tasa          AS filas_gasto_sin_tasa,
       b.ventas_sin_tasa         AS filas_venta_sin_tasa,
       b.gasto_sin_costo         AS filas_gasto_sin_costo,
       CASE
           WHEN b.gasto_ads IS NULL OR NULLIF(b.venta_total, 0) IS NULL THEN NULL
           WHEN b.gasto_sin_tasa > 0
                OR b.ventas_sin_tasa > 0
                OR b.gasto_sin_costo > 0 THEN NULL
           -- 0012: el residuo del grano también calla el número.
           --
           -- (a) RECONCILIACIÓN IMPOSIBLE. Si el residuo es NULL — no hay
           --     contraparte a nivel campaña, o su lado tiene hueco de FX o
           --     de costo — nadie pudo verificar el grano. Publicar aquí es
           --     publicar con aplomo un número no verificable, justo lo
           --     contrario de la disciplina del resto de la vista.
           WHEN b.gasto_campaign_sin_contraparte IS NULL THEN NULL
           -- (b) PÉRDIDA TOTAL DEL GRANO. Con gasto_ads = 0 la razón del
           --     residuo no se puede formar (división por cero), y el ELSE
           --     publicaría 100 * 0 / venta = 0.00 % mientras la campaña
           --     quema dinero: el TACoS falsamente óptimo que esta migración
           --     existe para impedir. Cero gasto en hojas con residuo ≠ 0 es
           --     el síntoma más grave, no el más benigno.
           WHEN b.gasto_ads = 0 AND b.gasto_campaign_sin_contraparte <> 0 THEN NULL
           -- (c) El umbral. Ver cabecera: 1.00 % medido, ~13x sobre el peor
           --     mes real (0.0773 %), en valor absoluto.
           WHEN NULLIF(b.gasto_ads, 0) IS NOT NULL
                AND ABS(100 * b.gasto_campaign_sin_contraparte / b.gasto_ads) > 1.00
               THEN NULL
           ELSE ROUND(100 * b.gasto_ads / b.venta_total, 2)
       END                       AS tacos_pct,
       b.gasto_campaign_sin_contraparte AS gasto_campaign_sin_contraparte,
       -- 0012: la señal, siempre visible aunque tacos_pct sobreviva.
       CASE
           WHEN b.gasto_campaign_sin_contraparte IS NULL THEN NULL
           WHEN NULLIF(b.gasto_ads, 0) IS NULL THEN NULL
           ELSE ROUND(ABS(100 * b.gasto_campaign_sin_contraparte / b.gasto_ads), 4)
       END                       AS residuo_pct
  FROM base b;

COMMENT ON VIEW v_tacos IS
  'TACoS POR PLATAFORMA (no por moneda). '
  'GRANO UNICO (0005/0006): gasto_ads suma SOLO kind IN (''keyword'', '
  '''product_target''). ads_metric_observation duplica el costo en '
  'kind=''campaign'' y en sus hijas — sumar ambos inflaba ~2x. '
  'RESIDUAL (0006, D6): gasto_campaign_sin_contraparte = SUM(campaign) - '
  'SUM(keyword+target) en la misma (platform, mes), convertido a MXN con '
  'fx_resolve. NULL si hay hueco de FX o cost en cualquiera de los dos lados. '
  'FAIL-LOUD DEL RESIDUO (0012): residuo_pct = |residual| / gasto_ads en %, '
  'siempre visible. tacos_pct se ANULA en TRES casos: (a) el residuo pasa de '
  '1.00 % (umbral medido: el peor mes real es 0.0773 % — MX ago-2026 — y el '
  'resto 0.0000 %); (b) el residuo es NULL (sin contraparte campaign, o hueco '
  'de FX/costo de ese lado): la reconciliacion es IMPOSIBLE y el grano no '
  'quedo verificado; (c) gasto_ads = 0 con residuo distinto de cero: la razon '
  'no se puede formar y el ELSE publicaria 0.00 % mientras la campana gasta — '
  'perdida TOTAL del grano. (b) y (c) los hallo codex en la cross-review del '
  '2026-09-02. Es el '
  'sintoma de que la allowlist de kinds se quedo corta y gasto_ads '
  'SUBESTIMA: un numero confiado y equivocado es peor que ningun numero. '
  'Se mira en VALOR ABSOLUTO: hijas por encima de su campana rompe el '
  'supuesto de grano igual que al reves. '
  'Fail-loud de tacos_pct intacto: filas_gasto_sin_tasa / '
  'filas_venta_sin_tasa / filas_gasto_sin_costo lo anulan igual que antes. '
  'Ventanas simetricas D-15 UTC. Meta declarada: 8-12%.';

COMMIT;
