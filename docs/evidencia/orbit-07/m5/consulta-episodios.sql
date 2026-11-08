-- ORBIT 07 M5: episodios economicos del ultimo cambio de bid por hoja.
--
-- READ ONLY, con el rol de lectura (orbit_read). No se ejecuto en este
-- encargo: falta el acceso de lectura a produccion. La consulta es el SQL
-- concreto para medir cobertura; sus resultados no se afirman aqui.
--
-- Ventanas [x-7, x-1] y [x+1, x+7], sin el dia del cambio. Madurez
-- x + 7 <= D - 10. Las ventanas se miden solo con fechas que existen en
-- v_metric_latest; una fecha sin ingesta deja dias < 7 y el episodio no es
-- comparable (no se rellena con cero).
WITH hoy AS (
    SELECT (now() AT TIME ZONE 'UTC')::date AS d
),
ultimo AS (
    SELECT DISTINCT ON (c.hoja_id)
           c.hoja_id,
           (c.confirmado_el AT TIME ZONE 'UTC')::date AS x,
           c.bid_antes,
           c.bid_despues,
           c.origen,
           c.confirmado_el
      FROM v_cambio_bid c
     WHERE c.bid_antes IS DISTINCT FROM c.bid_despues
       AND c.origen <> 'ajuste_de_campana'
     ORDER BY c.hoja_id, c.confirmado_el DESC, c.decision_id DESC
),
pre AS (
    SELECT u.hoja_id,
           count(DISTINCT m.metric_date) AS dias,
           sum(m.cost) AS gasto,
           sum(m.ad_revenue) AS venta,
           sum(m.revenue_same_sku) AS venta_mismo_sku,
           sum(m.orders) AS pedidos,
           sum(m.clicks) AS clics
      FROM ultimo u
      JOIN v_metric_latest m ON m.ad_entity_id = u.hoja_id
     WHERE m.metric_date BETWEEN u.x - 7 AND u.x - 1
     GROUP BY u.hoja_id
),
post AS (
    SELECT u.hoja_id,
           count(DISTINCT m.metric_date) AS dias,
           sum(m.cost) AS gasto,
           sum(m.ad_revenue) AS venta,
           sum(m.revenue_same_sku) AS venta_mismo_sku,
           sum(m.orders) AS pedidos,
           sum(m.clicks) AS clics
      FROM ultimo u
      JOIN v_metric_latest m ON m.ad_entity_id = u.hoja_id
     WHERE m.metric_date BETWEEN u.x + 1 AND u.x + 7
     GROUP BY u.hoja_id
)
SELECT u.hoja_id,
       u.x AS fecha_cambio,
       u.bid_antes,
       u.bid_despues,
       u.origen,
       CASE WHEN u.x + 7 <= h.d - 10 THEN 'maduro' ELSE 'inmaduro' END AS madurez,
       pre.dias AS dias_pre,
       post.dias AS dias_post,
       pre.gasto AS gasto_pre,
       pre.venta AS venta_pre,
       pre.pedidos AS pedidos_pre,
       post.gasto AS gasto_post,
       post.venta AS venta_post,
       post.pedidos AS pedidos_post,
       EXISTS (
           SELECT 1
             FROM v_cambio_bid v
            WHERE v.hoja_id = u.hoja_id
              AND (v.confirmado_el AT TIME ZONE 'UTC')::date
                  BETWEEN u.x - 7 AND u.x + 7
              AND NOT (
                  v.confirmado_el = u.confirmado_el
                  AND v.bid_antes IS NOT DISTINCT FROM u.bid_antes
                  AND v.bid_despues IS NOT DISTINCT FROM u.bid_despues
              )
       ) AS cambio_en_ventana,
       EXISTS (
           SELECT 1
             FROM ads_campana_config_observation o
             JOIN ad_entity k ON k.id = o.ad_entity_id AND k.kind = 'campaign'
             JOIN v_hoja_activa h ON h.campana_id = k.id AND h.hoja_id = u.hoja_id
            WHERE (o.observed_at AT TIME ZONE 'UTC')::date
                  BETWEEN u.x - 7 AND u.x + 7
       ) AS config_en_ventana,
       EXISTS (
           SELECT 1
             FROM campana_ajuste a
             JOIN v_hoja_activa h ON h.campana_id = a.campana_id AND h.hoja_id = u.hoja_id
            WHERE a.confirmado_el IS NOT NULL
              AND (a.confirmado_el AT TIME ZONE 'UTC')::date
                  BETWEEN u.x - 7 AND u.x + 7
       ) AS ajuste_en_ventana,
       EXISTS (
           SELECT 1
             FROM precio_cambio pc
             JOIN ad_entity pa ON pa.platform = pc.platform
                              AND pa.kind = 'product_ad'
                              AND pa.listing_id = pc.listing_id
             JOIN v_hoja_activa h ON h.ad_group_id = pa.parent_id AND h.hoja_id = u.hoja_id
            WHERE pc.aplicado IS TRUE
              AND pc.estado IN ('enviado', 'confirmado')
              AND pc.enviado_at IS NOT NULL
              AND (pc.enviado_at AT TIME ZONE 'UTC')::date
                  BETWEEN u.x - 7 AND u.x + 7
       ) AS precio_en_ventana
  FROM ultimo u
  CROSS JOIN hoy h
  LEFT JOIN pre USING (hoja_id)
  LEFT JOIN post USING (hoja_id)
 ORDER BY u.x DESC, u.hoja_id;
