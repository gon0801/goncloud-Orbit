-- =============================================================================
--  AUTO-03 A.3 — Embudo de elegibilidad por plataforma (SOLO LECTURA).
--
--  Salida: una fila `plataforma|etapa|n` por etapa (psql -tA).
--  Transaccion READ ONLY con UTC fijado (los limites de fecha dependen de
--  D = hoy UTC y ninguna consulta puede seguir la TimeZone de la sesion).
--
--  Ventanas del plan decisiones-producto-01:
--    - Pausa (S7/S6): [D-60, D-30), 30 fechas; compras y ventas totales en 0,
--      observadas; clics >= cortes.F_PAUSE (100) y gasto >= bid.PAUSE_COST_MIN
--      (500 MXN / 40 USD). La etapa 6 NO exige margen: la pausa no lo usa
--      (sintesis del plan: los productos sin margen medible tambien entran).
--    - Halo/meta (S7): [D-90, max(metric_date)-3] AMBOS INCLUSIVE (mismo
--      limite que fin_ventana_bids), con la ultima observacion por
--      (asin, sku, fecha), sumando solo fechas con fila; sin madurez exigida
--      (S7: el tramo corto se acepta sin madurez y con motivo).
--    - Madurez de la pausa (spec §Atribucion y madurez): cada fecha de
--      [D-60, D-30) exige SU reobservacion, patron _fecha_madura de
--      evaluacion_catalogo: observed_at >= fecha + 30 dias (UTC); el maximo
--      global de observed_at no vale.
--    - Cobertura (sustituto_sin_d7): antes de D.7 no hay rango pedido
--      guardado; una fecha cuenta cubierta si una corrida written del
--      reporte de productos, cuya ventana por omision es D-31..D-1 respecto
--      a su inicio, la contiene. El sustituto acredita COBERTURA, no
--      madurez: la madurez se valida por fila con observed_at (bloque
--      anterior), asi que una corrida que empezo cuando la fecha era
--      inmadura no la hace candidata por si sola.
--    - Identidad (S5): la fila Ads se liga al producto por ASIN (listing) y
--      se exige advertised_sku = listing.seller_sku.
--    - Meta (S1): gana el goal de campana con target no nulo; etapa 4 mide
--      cuantas campanas de grupos exclusivos tienen goal resuelto enabled y
--      live con target nulo frente a con target.
--
--  Corrida:
--    ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); \
--      docker exec -i orbit-db-1 psql "$DSN" -X -q -tA -v ON_ERROR_STOP=1' \
--      < docs/evidencia/auto-03/plan/elegibilidad.sql \
--      > docs/evidencia/auto-03/plan/elegibilidad.txt
-- =============================================================================

SET TIME ZONE 'UTC';
BEGIN TRANSACTION READ ONLY;

WITH params AS (
    SELECT (now() AT TIME ZONE 'UTC')::date AS hoy
),

-- Colapso bitemporal a la ULTIMA observacion por (plataforma, asin, sku,
-- fecha), el mismo patron del motor (evaluacion_catalogo.colapsa_por_fecha).
colapso AS (
    SELECT DISTINCT ON (platform, advertised_asin, advertised_sku, metric_date)
           platform, advertised_asin, advertised_sku, metric_date, observed_at,
           clicks, cost, purchases30d, sales30d,
           attributed_sales_same_sku_30d, ad_ids
      FROM ads_product_metric_observation
     WHERE platform IN ('amazon_mx', 'amazon_us')
     ORDER BY platform, advertised_asin, advertised_sku, metric_date, observed_at DESC
),

-- S5: la fila se liga al producto por ASIN y se exige el SKU del listing.
-- Conserva observed_at (la observacion colapsada, la mas reciente): la
-- madurez de cada fecha se valida contra ella, no contra el maximo global.
filas AS (
    SELECT c.platform, c.advertised_asin, c.advertised_sku, c.metric_date,
           c.observed_at,
           c.clicks, c.cost, c.purchases30d, c.sales30d,
           c.attributed_sales_same_sku_30d, c.ad_ids,
           li.product_id,
           COALESCE(c.advertised_sku = li.seller_sku, false) AS sku_coincide
      FROM colapso c
      JOIN listing li ON li.platform = c.platform
                     AND li.external_id = c.advertised_asin
),

-- Product ads NO archivados (incluidos PAUSED), con listing o sin el.
anuncios_todos AS (
    SELECT pa.platform, pa.id AS ad_entity_id, pa.external_id AS ad_id_externo,
           pa.parent_id AS grupo_id, pa.first_seen_at, li.product_id
      FROM ad_entity pa
      JOIN ad_entity_state sp ON sp.ad_entity_id = pa.id
      LEFT JOIN listing li ON li.id = pa.listing_id
     WHERE pa.kind = 'product_ad'
       AND pa.platform IN ('amazon_mx', 'amazon_us')
       AND sp.status IS DISTINCT FROM 'ARCHIVED'
),

anuncios_identificados AS (
    SELECT * FROM anuncios_todos WHERE product_id IS NOT NULL
),

-- (1) Productos con margen > 0 y ledger <= 3 dias.
etapa1 AS (
    SELECT m.platform, m.product_id
      FROM v_margen_producto m
     WHERE m.platform IN ('amazon_mx', 'amazon_us')
       AND m.margen_neto_pct > 0
       AND m.ledger_fresco_at >= now() - interval '3 days'
),

-- (2) De esos, con product ads no archivados que tienen listing.
etapa2 AS (
    SELECT a.platform, a.product_id
      FROM anuncios_identificados a
      JOIN etapa1 e ON e.platform = a.platform AND e.product_id = a.product_id
     GROUP BY a.platform, a.product_id
),

-- (3) Grupos exclusivos: todos los product ads no archivados del grupo
-- (incluidos los PAUSED) tienen listing y apuntan al mismo producto.
grupos_exclusivos AS (
    SELECT platform, grupo_id
      FROM anuncios_todos
     GROUP BY platform, grupo_id
    HAVING count(*) = count(product_id)
       AND count(DISTINCT product_id) = 1
),

hojas_exclusivas AS (
    SELECT h.platform, h.id
      FROM ad_entity h
      JOIN ad_entity_state sh ON sh.ad_entity_id = h.id
      JOIN grupos_exclusivos g ON g.platform = h.platform
                              AND g.grupo_id = h.parent_id
     WHERE h.kind IN ('keyword', 'product_target')
       AND sh.status = 'ENABLED'
),

-- (4) Campana de grupo exclusivo con goal resuelto enabled y live:
-- gana el goal de campana si existe (aunque este deshabilitado), si no el
-- de plataforma; separado por target nulo frente a con target (S1).
campanas_exclusivas AS (
    SELECT DISTINCT ag.parent_id AS campana_id, ag.platform
      FROM grupos_exclusivos g
      JOIN ad_entity ag ON ag.id = g.grupo_id AND ag.kind = 'ad_group'
     WHERE ag.parent_id IS NOT NULL
),
goal_resuelto AS (
    SELECT ce.campana_id, ce.platform,
           r.target_acos_pct, r.enabled, r.mode
      FROM campanas_exclusivas ce
      LEFT JOIN ads_optimizer_goal gc
             ON gc.scope = 'campaign' AND gc.ad_entity_id = ce.campana_id
      LEFT JOIN ads_optimizer_goal gp
             ON gc.id IS NULL AND gp.scope = 'platform' AND gp.platform = ce.platform
      CROSS JOIN LATERAL (
          SELECT COALESCE(gc.target_acos_pct, gp.target_acos_pct) AS target_acos_pct,
                 COALESCE(gc.enabled, gp.enabled) AS enabled,
                 COALESCE(gc.mode, gp.mode) AS mode
      ) r
),
goal_resuelto_live AS (
    SELECT * FROM goal_resuelto WHERE enabled IS TRUE AND mode = 'live'
),

-- (5) Halo 0 en la ventana S7 [D-90, max(metric_date)-3]: productos de la
-- etapa 2 con al menos una fila en la ventana, sin impedimento
-- sku_distinto_en_asin en ella y suma con veneno de
-- sales30d - attributed_sales_same_sku_30d igual a cero.
halo_ventana AS (
    SELECT m.platform, m.max_fecha
      FROM (
          SELECT platform, max(metric_date) AS max_fecha
            FROM ads_product_metric_observation
           WHERE platform IN ('amazon_mx', 'amazon_us')
           GROUP BY platform
      ) m
),
etapa5 AS (
    SELECT f.platform, f.product_id
      FROM filas f
      JOIN etapa2 e ON e.platform = f.platform AND e.product_id = f.product_id
      JOIN halo_ventana h ON h.platform = f.platform
      CROSS JOIN params p
     WHERE f.metric_date >= p.hoy - 90
       AND f.metric_date <= h.max_fecha - 3
     GROUP BY f.platform, f.product_id
    HAVING bool_and(f.sku_coincide)
       AND bool_and(f.sales30d IS NOT NULL
                    AND f.attributed_sales_same_sku_30d IS NOT NULL)
       AND SUM(f.sales30d) = SUM(f.attributed_sales_same_sku_30d)
),

-- Cobertura del sustituto_sin_d7: corridas written del reporte de productos
-- con ingest_run.ok; su ventana por omision [inicio-31, inicio-1].
corridas_productos AS (
    SELECT r.platform, i.started_at::date AS inicio
      FROM ads_report_result r
      JOIN ingest_run i ON i.id = r.ingest_run_id
     WHERE r.report_name = 'productos'
       AND r.status = 'written'
       AND i.ok IS TRUE
       AND r.platform IN ('amazon_mx', 'amazon_us')
),
cobertura AS (
    SELECT c.platform, d::date AS fecha
      FROM corridas_productos c
      CROSS JOIN LATERAL generate_series(c.inicio - 31, c.inicio - 1, interval '1 day') d
     GROUP BY c.platform, d
),
ventana_pausa AS (
    SELECT p.hoy - 60 AS desde, p.hoy - 30 AS hasta FROM params p
),
fechas_pausa AS (
    SELECT p.platform, v.fecha
      FROM (SELECT DISTINCT platform FROM filas WHERE platform IN ('amazon_mx','amazon_us')) p
      CROSS JOIN ventana_pausa w
      CROSS JOIN LATERAL generate_series(w.desde, w.hasta - 1, interval '1 day') v(fecha)
),
cobertura_completa AS (
    SELECT fp.platform
      FROM fechas_pausa fp
      LEFT JOIN cobertura c ON c.platform = fp.platform AND c.fecha = fp.fecha
     GROUP BY fp.platform
    HAVING count(*) FILTER (WHERE c.platform IS NULL) = 0
),

-- (6) Candidatos a pausa: ventana [D-60, D-30) completa segun el sustituto,
-- compras y ventas totales en 0 observadas (suma con veneno: un NULL en una
-- metrica deja el producto fuera, patron _suma), madurez POR FECHA (la
-- observacion colapsada de cada fecha trajo observed_at >= fecha+30, patron
-- _fecha_madura; el sustituto de cobertura no la acredita), clics >= 100,
-- gasto >= piso (500 MXN / 40 USD), sin fila de otro SKU en el ASIN
-- (bool_and sku_coincide) y con product ads identificados. El EXISTS de
-- anuncios NO multiplica las sumas.
candidatos_pausa AS (
    SELECT f.platform, f.product_id
      FROM filas f
      JOIN cobertura_completa cc ON cc.platform = f.platform
      CROSS JOIN ventana_pausa w
      CROSS JOIN params p
     WHERE f.metric_date >= p.hoy - 60
       AND f.metric_date <  p.hoy - 30
       AND EXISTS (
           SELECT 1 FROM anuncios_identificados a
            WHERE a.platform = f.platform AND a.product_id = f.product_id)
     GROUP BY f.platform, f.product_id
    HAVING bool_and(f.purchases30d IS NOT NULL AND f.sales30d IS NOT NULL
                    AND f.clicks IS NOT NULL AND f.cost IS NOT NULL)
       AND bool_and(f.observed_at
                    >= ((f.metric_date + 30)::timestamp AT TIME ZONE 'UTC'))
       AND SUM(f.purchases30d) = 0
       AND SUM(f.sales30d) = 0
       AND SUM(f.clicks) >= 100
       AND SUM(f.cost) >= CASE f.platform
                              WHEN 'amazon_mx' THEN 500
                              WHEN 'amazon_us' THEN 40
                          END
       AND bool_and(f.sku_coincide)
),

-- Impulso abierto en el ad group: mismo predicado de
-- retiro_anuncios._SQL_ANUNCIOS (sin lectura es_final).
impulso_abierto_grupo AS (
    SELECT DISTINCT r.ad_group_ad_entity_id AS grupo_id
      FROM campana_grupo_rol r
      JOIN impulso i ON i.grupo_id = r.grupo_id
      LEFT JOIN impulso_lectura l ON l.impulso_id = i.id AND l.es_final
     WHERE l.impulso_id IS NULL
),

-- Anuncios S4 de los candidatos: ENABLED, con identidad, first_seen_at
-- anterior a la ventana, fuera de impulso abierto, con goal resuelto
-- enabled y live en su campana, y cuyo adId aparece en ad_ids de la ventana.
ad_ids_ventana AS (
    SELECT f.platform, f.product_id, je.ad_id
      FROM filas f
      CROSS JOIN params p
      CROSS JOIN LATERAL jsonb_array_elements_text(f.ad_ids) je(ad_id)
     WHERE f.ad_ids IS NOT NULL
       AND jsonb_typeof(f.ad_ids) = 'array'
       AND f.metric_date >= p.hoy - 60
       AND f.metric_date <  p.hoy - 30
),
goal_live_campana AS (
    SELECT ce.campana_id, ce.platform
      FROM campanas_exclusivas ce
      JOIN goal_resuelto_live gr ON gr.campana_id = ce.campana_id
),
anuncios_s4 AS (
    SELECT DISTINCT a.platform, a.product_id, a.ad_entity_id
      FROM anuncios_identificados a
      JOIN ad_entity_state sp ON sp.ad_entity_id = a.ad_entity_id AND sp.status = 'ENABLED'
      JOIN ad_entity ag ON ag.id = a.grupo_id AND ag.kind = 'ad_group'
      JOIN ad_entity cm ON cm.id = ag.parent_id AND cm.kind = 'campaign'
      LEFT JOIN impulso_abierto_grupo ig ON ig.grupo_id = ag.id
      LEFT JOIN ads_optimizer_goal gc
             ON gc.scope = 'campaign' AND gc.ad_entity_id = cm.id
      LEFT JOIN ads_optimizer_goal gp
             ON gc.id IS NULL AND gp.scope = 'platform' AND gp.platform = a.platform
      CROSS JOIN ventana_pausa w
      CROSS JOIN params p
     WHERE ig.grupo_id IS NULL
       AND COALESCE(gc.enabled, gp.enabled) IS TRUE
       AND COALESCE(gc.mode, gp.mode) = 'live'
       AND a.first_seen_at < p.hoy - 60
       AND EXISTS (
           SELECT 1 FROM ad_ids_ventana v
            WHERE v.platform = a.platform
              AND v.product_id = a.product_id
              AND v.ad_id = a.ad_id_externo
       )
       AND EXISTS (
           SELECT 1 FROM candidatos_pausa cp
            WHERE cp.platform = a.platform AND cp.product_id = a.product_id
       )
),

-- (7) Goals vivos con target, por origen fabrica/impulso.
origen_goals AS (
    SELECT g.platform,
           CASE WHEN i.grupo_id IS NOT NULL THEN 'impulso' ELSE 'fabrica' END AS origen
      FROM ads_optimizer_goal g
      JOIN campana_grupo_rol r ON r.ad_entity_id = g.ad_entity_id
      LEFT JOIN impulso i ON i.grupo_id = r.grupo_id
     WHERE g.scope = 'campaign'
       AND g.target_acos_pct IS NOT NULL
       AND g.enabled IS TRUE
       AND g.mode = 'live'
       AND g.platform IN ('amazon_mx', 'amazon_us')
),

-- (8) Hechos que el plan da por ciertos, t/f por plataforma.
hecho_ad_ids AS (
    SELECT f.platform,
           bool_and(
               jsonb_typeof(f.ad_ids) = 'array'
               AND NOT EXISTS (
                   SELECT 1 FROM jsonb_array_elements(f.ad_ids) e
                    WHERE jsonb_typeof(e.value) NOT IN ('number', 'string')
               )
           ) AS ok
      FROM filas f
     WHERE f.ad_ids IS NOT NULL
     GROUP BY f.platform
),
hecho_written AS (
    SELECT r.platform, count(*) > 0 AS ok
      FROM ads_report_result r
     WHERE r.report_name = 'productos'
       AND r.status = 'written'
       AND r.platform IN ('amazon_mx', 'amazon_us')
     GROUP BY r.platform
),
hecho_sku_distinto AS (
    SELECT f.platform,
           count(*) FILTER (WHERE NOT f.sku_coincide) AS filas_malas,
           count(DISTINCT f.product_id) FILTER (WHERE NOT f.sku_coincide) AS productos_malos,
           count(*) FILTER (WHERE NOT f.sku_coincide) > 0 AS hay
      FROM filas f
     GROUP BY f.platform
),
hecho_first_seen AS (
    SELECT pa.platform, count(*) FILTER (WHERE pa.first_seen_at IS NULL) AS nulos
      FROM ad_entity pa
     WHERE pa.kind = 'product_ad'
       AND pa.platform IN ('amazon_mx', 'amazon_us')
     GROUP BY pa.platform
),
hecho_min_fecha AS (
    SELECT platform, min(metric_date) AS min_fecha
      FROM ads_product_metric_observation
     WHERE platform IN ('amazon_mx', 'amazon_us')
     GROUP BY platform
),

-- Etiquetas de plataforma para LEFT JOINs que pueden salir vacios.
plataformas AS (
    SELECT 'amazon_mx' AS platform
    UNION ALL SELECT 'amazon_us'
),

salida AS (
    -- (1) margen > 0 y ledger <= 3 dias
    SELECT '1_margen_y_ledger' AS etapa, e.platform::text, count(*)::text AS n
      FROM etapa1 e GROUP BY e.platform
    UNION ALL
    SELECT '2_con_anuncios_con_listing', e.platform::text, count(*)::text
      FROM etapa2 e GROUP BY e.platform
    UNION ALL
    SELECT '3_grupos_exclusivos', g.platform::text, count(*)::text
      FROM grupos_exclusivos g GROUP BY g.platform
    UNION ALL
    SELECT '3_hojas_enabled_exclusivas', h.platform::text, count(*)::text
      FROM hojas_exclusivas h GROUP BY h.platform
    UNION ALL
    SELECT '4_campana_goal_target_nulo', g.platform::text, count(*)::text
      FROM goal_resuelto_live g
     WHERE g.target_acos_pct IS NULL
     GROUP BY g.platform
    UNION ALL
    SELECT '4_campana_goal_con_target', g.platform::text, count(*)::text
      FROM goal_resuelto_live g
     WHERE g.target_acos_pct IS NOT NULL
     GROUP BY g.platform
    UNION ALL
    SELECT '5_halo_cero_ventana_hojas', e.platform::text, count(*)::text
      FROM etapa5 e GROUP BY e.platform
    UNION ALL
    SELECT '6_candidatos_pausa', c.platform::text, count(*)::text
      FROM candidatos_pausa c GROUP BY c.platform
    UNION ALL
    SELECT '6_anuncios_s4_candidatos', a.platform::text, count(*)::text
      FROM anuncios_s4 a GROUP BY a.platform
    UNION ALL
    SELECT '7_goal_fabrica_con_target', o.platform::text, count(*)::text
      FROM origen_goals o WHERE o.origen = 'fabrica' GROUP BY o.platform
    UNION ALL
    SELECT '7_goal_impulso_con_target', o.platform::text, count(*)::text
      FROM origen_goals o WHERE o.origen = 'impulso' GROUP BY o.platform
    UNION ALL
    SELECT '8_hecho_ad_ids_jsonb_lista', h.platform::text,
           CASE WHEN COALESCE(h.ok, false) THEN 't' ELSE 'f' END
      FROM plataformas p LEFT JOIN hecho_ad_ids h ON h.platform::text = p.platform
    UNION ALL
    SELECT '8_hecho_written_productos', h.platform::text,
           CASE WHEN COALESCE(h.ok, false) THEN 't' ELSE 'f' END
      FROM plataformas p LEFT JOIN hecho_written h ON h.platform::text = p.platform
    UNION ALL
    SELECT '8_hecho_sku_distinto', h.platform::text,
           CASE WHEN COALESCE(h.hay, false) THEN 't' ELSE 'f' END
      FROM plataformas p LEFT JOIN hecho_sku_distinto h ON h.platform::text = p.platform
    UNION ALL
    SELECT '8_hecho_first_seen_no_nulo', h.platform::text,
           CASE WHEN COALESCE(h.nulos, 0) = 0 THEN 't' ELSE 'f' END
      FROM plataformas p LEFT JOIN hecho_first_seen h ON h.platform::text = p.platform
    UNION ALL
    SELECT '8_min_metric_date_productos', h.platform::text, h.min_fecha::text
      FROM hecho_min_fecha h
),
etiquetado AS (
    SELECT s.etapa, s.platform, s.n,
           CASE WHEN s.etapa LIKE '1%' THEN 1
                WHEN s.etapa LIKE '2%' THEN 2
                WHEN s.etapa LIKE '3%' THEN 3
                WHEN s.etapa LIKE '4%' THEN 4
                WHEN s.etapa LIKE '5%' THEN 5
                WHEN s.etapa LIKE '6%' THEN 6
                WHEN s.etapa LIKE '7%' THEN 7
                ELSE 8 END AS orden,
           CASE WHEN s.etapa LIKE '4_campana_goal_target_nulo' THEN 1
                WHEN s.etapa LIKE '4_campana_goal_con_target' THEN 2
                WHEN s.etapa LIKE '3_grupos%' THEN 1
                WHEN s.etapa LIKE '3_hojas%' THEN 2
                WHEN s.etapa LIKE '6_candidatos%' THEN 1
                WHEN s.etapa LIKE '6_anuncios%' THEN 2
                WHEN s.etapa LIKE '7_goal_fabrica%' THEN 1
                WHEN s.etapa LIKE '7_goal_impulso%' THEN 2
                ELSE 0 END AS sub
      FROM salida s
)
SELECT platform || '|' || etapa || '|' || n
  FROM etiquetado
 ORDER BY platform, orden, sub, etapa;
COMMIT;
