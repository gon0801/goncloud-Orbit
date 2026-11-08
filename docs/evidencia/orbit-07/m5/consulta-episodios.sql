-- ORBIT 07 M5: episodios economicos del ultimo cambio de bid por hoja.
--
-- READ ONLY, con el rol de lectura (orbit_read). No se ejecuto en este
-- encargo: falta el acceso de lectura a produccion. La consulta reproduce las
-- reglas de `app/lecturas_caso._episodio_economico` para medir cobertura:
-- ventanas [x-7, x-1] y [x+1, x+7], madurez x + 7 <= D - 10, cobertura de
-- fechas de PLATAFORMA, eventos en ventana (cambio de bid, configuracion de
-- campana, ajustes, precios y anuncios nuevos) y aplicabilidad (ningun evento
-- posterior a x). Una fecha sin ingesta deja `dias < 7` y el episodio no es
-- comparable; no se rellena con cero.
WITH hoy AS (
    SELECT (now() AT TIME ZONE 'UTC')::date AS d
),
ultimo AS (
    SELECT DISTINCT ON (c.hoja_id)
           c.hoja_id,
           k.platform,
           (c.confirmado_el AT TIME ZONE 'UTC')::date AS x,
           c.bid_antes,
           c.bid_despues,
           c.origen,
           c.confirmado_el
      FROM v_cambio_bid c
      JOIN ad_entity k ON k.id = c.hoja_id
     WHERE c.bid_antes IS DISTINCT FROM c.bid_despues
       AND c.origen <> 'ajuste_de_campana'
     ORDER BY c.hoja_id, c.confirmado_el DESC, c.decision_id DESC
),
cambio_ventana AS (
    SELECT DISTINCT u.hoja_id
      FROM ultimo u
      JOIN v_cambio_bid v ON v.hoja_id = u.hoja_id
     WHERE (v.confirmado_el AT TIME ZONE 'UTC')::date BETWEEN u.x - 7 AND u.x + 7
       AND NOT (
           v.confirmado_el = u.confirmado_el
           AND v.bid_antes IS NOT DISTINCT FROM u.bid_antes
           AND v.bid_despues IS NOT DISTINCT FROM u.bid_despues
       )
),
cambio_posterior AS (
    SELECT DISTINCT u.hoja_id
      FROM ultimo u
      JOIN v_cambio_bid v ON v.hoja_id = u.hoja_id
     WHERE (v.confirmado_el AT TIME ZONE 'UTC')::date > u.x
       AND NOT (
           v.confirmado_el = u.confirmado_el
           AND v.bid_antes IS NOT DISTINCT FROM u.bid_antes
           AND v.bid_despues IS NOT DISTINCT FROM u.bid_despues
       )
),
otros AS (
    SELECT h.hoja_id, (o.observed_at AT TIME ZONE 'UTC')::date AS fecha
      FROM ads_campana_config_observation o
      JOIN ad_entity c ON c.id = o.ad_entity_id AND c.kind = 'campaign'
      JOIN v_hoja_activa h ON h.campana_id = c.id
    UNION ALL
    SELECT h.hoja_id, (a.confirmado_el AT TIME ZONE 'UTC')::date
      FROM campana_ajuste a
      JOIN v_hoja_activa h ON h.campana_id = a.campana_id
     WHERE a.confirmado_el IS NOT NULL
    UNION ALL
    SELECT h.hoja_id, (pc.enviado_at AT TIME ZONE 'UTC')::date
      FROM precio_cambio pc
      JOIN ad_entity pa ON pa.platform = pc.platform
                       AND pa.kind = 'product_ad'
                       AND pa.listing_id = pc.listing_id
      JOIN v_hoja_activa h ON h.ad_group_id = pa.parent_id
     WHERE pc.aplicado IS TRUE
       AND pc.estado IN ('enviado', 'confirmado')
       AND pc.enviado_at IS NOT NULL
    UNION ALL
    SELECT h.hoja_id, (a.first_seen_at AT TIME ZONE 'UTC')::date
      FROM ad_entity a
      JOIN v_hoja_activa h ON h.ad_group_id = a.parent_id
     WHERE a.kind = 'product_ad'
       AND a.first_seen_at IS NOT NULL
),
otros_ventana AS (
    SELECT DISTINCT u.hoja_id
      FROM ultimo u
      JOIN otros o ON o.hoja_id = u.hoja_id
     WHERE o.fecha BETWEEN u.x - 7 AND u.x + 7
),
otros_posterior AS (
    SELECT DISTINCT u.hoja_id
      FROM ultimo u
      JOIN otros o ON o.hoja_id = u.hoja_id
     WHERE o.fecha > u.x
),
cobertura AS (
    SELECT k.platform, m.metric_date
      FROM v_metric_latest m
      JOIN ad_entity k ON k.id = m.ad_entity_id
     WHERE k.kind IN ('keyword', 'product_target')
     GROUP BY k.platform, m.metric_date
),
pre_cob AS (
    SELECT u.hoja_id, count(*) AS dias
      FROM ultimo u
      JOIN cobertura c ON c.platform = u.platform
                      AND c.metric_date BETWEEN u.x - 7 AND u.x - 1
     GROUP BY u.hoja_id
),
post_cob AS (
    SELECT u.hoja_id, count(*) AS dias
      FROM ultimo u
      JOIN cobertura c ON c.platform = u.platform
                      AND c.metric_date BETWEEN u.x + 1 AND u.x + 7
     GROUP BY u.hoja_id
),
pre_met AS (
    SELECT u.hoja_id,
           count(DISTINCT m.metric_date) AS filas,
           CASE WHEN bool_and(m.cost IS NOT NULL AND m.cost >= 0)
                THEN sum(m.cost) END AS gasto,
           CASE WHEN bool_and(m.ad_revenue IS NOT NULL AND m.ad_revenue >= 0)
                THEN sum(m.ad_revenue) END AS venta,
           CASE WHEN bool_and(m.revenue_same_sku IS NOT NULL AND m.revenue_same_sku >= 0)
                THEN sum(m.revenue_same_sku) END AS venta_mismo_sku,
           CASE WHEN bool_and(m.orders IS NOT NULL AND m.orders >= 0)
                THEN sum(m.orders) END AS pedidos,
           CASE WHEN bool_and(m.clicks IS NOT NULL AND m.clicks >= 0)
                THEN sum(m.clicks) END AS clics
      FROM ultimo u
      JOIN v_metric_latest m ON m.ad_entity_id = u.hoja_id
                            AND m.metric_date BETWEEN u.x - 7 AND u.x - 1
     GROUP BY u.hoja_id
),
post_met AS (
    SELECT u.hoja_id,
           count(DISTINCT m.metric_date) AS filas,
           CASE WHEN bool_and(m.cost IS NOT NULL AND m.cost >= 0)
                THEN sum(m.cost) END AS gasto,
           CASE WHEN bool_and(m.ad_revenue IS NOT NULL AND m.ad_revenue >= 0)
                THEN sum(m.ad_revenue) END AS venta,
           CASE WHEN bool_and(m.revenue_same_sku IS NOT NULL AND m.revenue_same_sku >= 0)
                THEN sum(m.revenue_same_sku) END AS venta_mismo_sku,
           CASE WHEN bool_and(m.orders IS NOT NULL AND m.orders >= 0)
                THEN sum(m.orders) END AS pedidos,
           CASE WHEN bool_and(m.clicks IS NOT NULL AND m.clicks >= 0)
                THEN sum(m.clicks) END AS clics
      FROM ultimo u
      JOIN v_metric_latest m ON m.ad_entity_id = u.hoja_id
                            AND m.metric_date BETWEEN u.x + 1 AND u.x + 7
     GROUP BY u.hoja_id
),
pre AS (
    SELECT u.hoja_id,
           coalesce(c.dias, 0) AS dias,
           coalesce(m.filas, 0) AS filas,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.gasto END AS gasto,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.venta END AS venta,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.venta_mismo_sku END AS venta_mismo_sku,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.pedidos END AS pedidos,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.clics END AS clics
      FROM ultimo u
      LEFT JOIN pre_cob c USING (hoja_id)
      LEFT JOIN pre_met m USING (hoja_id)
),
post AS (
    SELECT u.hoja_id,
           coalesce(c.dias, 0) AS dias,
           coalesce(m.filas, 0) AS filas,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.gasto END AS gasto,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.venta END AS venta,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.venta_mismo_sku END AS venta_mismo_sku,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.pedidos END AS pedidos,
           CASE WHEN coalesce(m.filas, 0) = 0 THEN 0 ELSE m.clics END AS clics
      FROM ultimo u
      LEFT JOIN post_cob c USING (hoja_id)
      LEFT JOIN post_met m USING (hoja_id)
),
marcas AS (
    SELECT u.hoja_id,
           u.platform,
           u.x AS fecha_cambio,
           u.bid_antes,
           u.bid_despues,
           u.origen,
           EXISTS (SELECT 1 FROM cambio_ventana cv WHERE cv.hoja_id = u.hoja_id)
               OR EXISTS (SELECT 1 FROM otros_ventana ov WHERE ov.hoja_id = u.hoja_id)
               AS en_ventana,
           EXISTS (SELECT 1 FROM cambio_posterior cp WHERE cp.hoja_id = u.hoja_id)
               OR EXISTS (SELECT 1 FROM otros_posterior op WHERE op.hoja_id = u.hoja_id)
               AS posterior,
           pre.dias AS dias_pre,
           post.dias AS dias_post,
           pre.filas AS filas_pre,
           post.filas AS filas_post,
           pre.gasto AS gasto_pre,
           pre.venta AS venta_pre,
           pre.pedidos AS pedidos_pre,
           pre.clics AS clics_pre,
           post.gasto AS gasto_post,
           post.venta AS venta_post,
           post.pedidos AS pedidos_post,
           post.clics AS clics_post
      FROM ultimo u
      JOIN pre USING (hoja_id)
      JOIN post USING (hoja_id)
)
SELECT m.hoja_id,
       m.platform,
       m.fecha_cambio,
       m.bid_antes,
       m.bid_despues,
       m.origen,
       CASE
           WHEN m.fecha_cambio + 7 > h.d - 10 THEN 'inmaduro'
           WHEN m.en_ventana THEN 'incomparable'
           WHEN m.dias_pre <> 7 OR m.dias_post <> 7
             OR m.gasto_pre IS NULL OR m.venta_pre IS NULL
             OR m.pedidos_pre IS NULL OR m.clics_pre IS NULL
             OR m.gasto_post IS NULL OR m.venta_post IS NULL
             OR m.pedidos_post IS NULL OR m.clics_post IS NULL
           THEN 'incomparable'
           ELSE 'maduro'
       END AS estado,
       CASE
           WHEN m.fecha_cambio + 7 > h.d - 10 THEN 'post_inmaduro'
           WHEN m.en_ventana THEN 'cambio_en_ventana'
           WHEN m.dias_pre <> 7 OR m.dias_post <> 7
             OR m.gasto_pre IS NULL OR m.venta_pre IS NULL
             OR m.pedidos_pre IS NULL OR m.clics_pre IS NULL
             OR m.gasto_post IS NULL OR m.venta_post IS NULL
             OR m.pedidos_post IS NULL OR m.clics_post IS NULL
           THEN 'cobertura_incompleta'
           ELSE NULL
       END AS motivo,
       NOT m.posterior AS aplicable,
       CASE WHEN m.posterior THEN 'cambio_posterior' ELSE NULL END AS motivo_aplicabilidad,
       m.dias_pre,
       m.dias_post,
       m.filas_pre,
       m.filas_post,
       m.gasto_pre,
       m.venta_pre,
       m.pedidos_pre,
       m.clics_pre,
       m.gasto_post,
       m.venta_post,
       m.pedidos_post,
       m.clics_post
  FROM marcas m
  CROSS JOIN hoy h
 ORDER BY m.fecha_cambio DESC, m.hoja_id;
