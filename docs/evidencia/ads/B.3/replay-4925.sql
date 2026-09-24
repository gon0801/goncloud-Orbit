-- Replay de lectura para la keyword 4925, ciclos US del 11 al 19-sep-2026.
-- Cada vintage se limita al inicio real del ciclo antes de seleccionar una
-- version por fecha. No usar v_metric_latest actual para este contrafactual.
WITH ciclos AS (
    SELECT id, started_at, (notes::jsonb->'target'->>'target_aplicado')::numeric AS target_pct,
           notes::jsonb->'target'->>'procedencia' AS target_fuente
      FROM optimizer_cycle
     WHERE platform = 'amazon_us' AND motor = 'ads_optimizer'
       AND (started_at AT TIME ZONE 'UTC')::date
           BETWEEN DATE '2026-09-11' AND DATE '2026-09-19'
),
vintage AS (
    SELECT c.id AS ciclo_id, m.ad_entity_id, m.metric_date, m.metric_currency,
           m.cost, m.ad_revenue, m.clicks, m.orders, m.observed_at,
           m.source_report_id,
           row_number() OVER (
               PARTITION BY c.id, m.ad_entity_id, m.metric_date
               ORDER BY m.observed_at DESC, m.source_report_id DESC
           ) AS rn
      FROM ciclos c
      JOIN ads_metric_observation m ON m.observed_at <= c.started_at
      JOIN ad_entity e ON e.id = m.ad_entity_id
     WHERE e.parent_id = (SELECT parent_id FROM ad_entity WHERE id = 4925)
       AND e.kind IN ('keyword', 'product_target')
       AND e.first_seen_at <= c.started_at
       AND m.metric_date BETWEEN (c.started_at AT TIME ZONE 'UTC')::date - 120
                             AND (c.started_at AT TIME ZONE 'UTC')::date
),
ultima AS (SELECT * FROM vintage WHERE rn = 1),
ancla AS (
    SELECT ciclo_id, max(metric_date) AS max_fecha
      FROM ultima WHERE ad_entity_id = 4925 GROUP BY ciclo_id
),
limite AS (
    SELECT c.id AS ciclo_id, c.started_at,
           least(a.max_fecha - 3, (c.started_at AT TIME ZONE 'UTC')::date - 10) AS fin_corte,
           c.target_pct, c.target_fuente
      FROM ciclos c JOIN ancla a ON a.ciclo_id = c.id
),
hoja AS (
    SELECT l.ciclo_id, l.started_at, l.fin_corte, l.target_pct, l.target_fuente,
           count(DISTINCT u.metric_date) AS fechas,
           min(u.metric_currency::text) AS moneda,
           CASE WHEN bool_and(u.cost IS NOT NULL) THEN sum(u.cost) END AS cost,
           CASE WHEN bool_and(u.ad_revenue IS NOT NULL) THEN sum(u.ad_revenue) END AS revenue,
           CASE WHEN bool_and(u.clicks IS NOT NULL) THEN sum(u.clicks) END AS clicks,
           CASE WHEN bool_and(u.orders IS NOT NULL) THEN sum(u.orders) END AS orders,
           max(u.observed_at) AS observado_hasta,
           array_agg(DISTINCT u.source_report_id ORDER BY u.source_report_id) AS report_ids
      FROM limite l JOIN ultima u ON u.ciclo_id = l.ciclo_id
       AND u.ad_entity_id = 4925
       AND u.metric_date BETWEEN l.fin_corte - 29 AND l.fin_corte
     GROUP BY l.ciclo_id, l.started_at, l.fin_corte, l.target_pct, l.target_fuente
),
grupo AS (
    SELECT c.id AS ciclo_id, count(DISTINCT u.metric_date) AS fechas,
           CASE WHEN bool_and(u.clicks IS NOT NULL) THEN sum(u.clicks) END AS clicks,
           CASE WHEN bool_and(u.orders IS NOT NULL) THEN sum(u.orders) END AS orders
      FROM ciclos c JOIN ultima u ON u.ciclo_id = c.id
       AND u.metric_date BETWEEN (c.started_at AT TIME ZONE 'UTC')::date - 90
                             AND (c.started_at AT TIME ZONE 'UTC')::date - 10
     GROUP BY c.id
),
applies AS (
    SELECT c.id AS ciclo_id,
           bool_or(d.kind = 'bid' AND a.verify_ok IS TRUE
               AND a.confirmed_at > c.started_at - interval '7 days'
               AND a.confirmed_at <= c.started_at) AS bid_cooldown,
           bool_or(d.kind = 'pause' AND a.verify_ok IS TRUE
               AND a.confirmed_at > c.started_at - interval '7 days'
               AND a.confirmed_at <= c.started_at) AS pause_cooldown
      FROM ciclos c LEFT JOIN decision d ON d.ad_entity_id = 4925
      LEFT JOIN decision_application a ON a.decision_id = d.id
     GROUP BY c.id
)
SELECT h.ciclo_id, h.started_at, h.fin_corte, h.fechas, h.moneda,
       h.cost, h.revenue, h.clicks, h.orders,
       greatest(100, CASE WHEN g.fechas >= 14 AND g.orders >= 3 AND g.clicks >= 60
                          THEN ceil(1.5 * g.clicks::numeric / g.orders)::int END) AS umbral,
       h.target_pct, h.target_fuente,
       coalesce(a.bid_cooldown, false) AS bid_cooldown,
       coalesce(a.pause_cooldown, false) AS pause_cooldown,
       (h.fechas >= 7 AND h.moneda = 'USD' AND h.cost >= 40
        AND h.orders = 0 AND h.clicks >= greatest(100,
            CASE WHEN g.fechas >= 14 AND g.orders >= 3 AND g.clicks >= 60
                 THEN ceil(1.5 * g.clicks::numeric / g.orders)::int END)
        AND NOT coalesce(a.bid_cooldown, false)
        AND NOT coalesce(a.pause_cooldown, false)) AS califica_era_real,
       (h.fechas >= 7 AND h.moneda = 'USD' AND h.cost >= 40
        AND h.orders = 0 AND h.clicks >= greatest(100,
            CASE WHEN g.fechas >= 14 AND g.orders >= 3 AND g.clicks >= 60
                 THEN ceil(1.5 * g.clicks::numeric / g.orders)::int END)
        AND NOT coalesce(a.pause_cooldown, false)) AS califica_b2_aislado,
       h.observado_hasta, h.report_ids,
       d.id AS decision_real, d.kind AS kind_real,
       q.id AS cola_real, q.estado AS cola_estado, q.vence_el,
       da.confirmed_at AS apply_confirmado, da.verify_ok AS apply_verificado
  FROM hoja h JOIN grupo g ON g.ciclo_id = h.ciclo_id
  JOIN applies a ON a.ciclo_id = h.ciclo_id
  LEFT JOIN decision d ON d.cycle_id = h.ciclo_id AND d.ad_entity_id = 4925
  LEFT JOIN apply_queue q ON q.decision_id = d.id
  LEFT JOIN decision_application da ON da.decision_id = d.id
 ORDER BY h.ciclo_id;
