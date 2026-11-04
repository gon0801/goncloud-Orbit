SET statement_timeout = '60s';
\pset pager off
\echo == A) duracion real de los ultimos ciclos (segundos)
SELECT platform, count(*) ciclos,
       round(min(extract(epoch FROM finished_at-started_at))::numeric,1) minimo,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at-started_at)))::numeric,1) mediana,
       round(max(extract(epoch FROM finished_at-started_at))::numeric,1) maximo
  FROM optimizer_cycle
 WHERE motor='ads_optimizer' AND status='done' AND started_at >= now() - interval '21 days'
 GROUP BY 1 ORDER BY 1;
\echo == B) por dia, ultimos 8
SELECT platform, started_at::date dia, mode, decisions_count, applied_count,
       round(extract(epoch FROM finished_at-started_at)::numeric,1) segundos
  FROM optimizer_cycle WHERE motor='ads_optimizer' AND started_at >= now() - interval '5 days' ORDER BY started_at;
\echo == C) lectura nueva por plataforma: tramos de 90 dias de TODAS las hojas, su ad group y la cuenta (una consulta)
\timing on
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
WITH hoja AS (
  SELECT e.id, e.parent_id AS ad_group_id FROM ad_entity e
   WHERE e.platform='amazon_mx' AND e.kind IN ('keyword','product_target')
), filas AS (
  SELECT h.id, h.ad_group_id,
         CASE WHEN v.metric_date >= (now() AT TIME ZONE 'UTC')::date - 50 THEN 1 ELSE 2 END AS tramo,
         v.impressions, v.clicks, v.cost, v.orders, v.ad_revenue
    FROM hoja h JOIN v_metric_latest v ON v.ad_entity_id = h.id
   WHERE v.metric_date BETWEEN (now() AT TIME ZONE 'UTC')::date - 90 AND (now() AT TIME ZONE 'UTC')::date - 10
)
SELECT id, ad_group_id, tramo, count(*) fechas, sum(impressions) impr, sum(clicks) clics, sum(cost) gasto,
       sum(orders) pedidos, sum(ad_revenue) venta
  FROM filas GROUP BY 1,2,3;
\echo == D) misma lectura para US
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
WITH hoja AS (
  SELECT e.id, e.parent_id AS ad_group_id FROM ad_entity e
   WHERE e.platform='amazon_us' AND e.kind IN ('keyword','product_target')
), filas AS (
  SELECT h.id, h.ad_group_id,
         CASE WHEN v.metric_date >= (now() AT TIME ZONE 'UTC')::date - 50 THEN 1 ELSE 2 END AS tramo,
         v.impressions, v.clicks, v.cost, v.orders, v.ad_revenue
    FROM hoja h JOIN v_metric_latest v ON v.ad_entity_id = h.id
   WHERE v.metric_date BETWEEN (now() AT TIME ZONE 'UTC')::date - 90 AND (now() AT TIME ZONE 'UTC')::date - 10
)
SELECT id, ad_group_id, tramo, count(*), sum(impressions), sum(clicks), sum(cost), sum(orders), sum(ad_revenue)
  FROM filas GROUP BY 1,2,3;
\echo == E) lectura diaria de 14 dias de todas las hojas (para el efecto del ultimo cambio), MX
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT v.ad_entity_id, v.metric_date, v.impressions, v.clicks, v.cost
  FROM v_metric_latest v JOIN ad_entity e ON e.id=v.ad_entity_id
 WHERE e.platform='amazon_mx' AND e.kind IN ('keyword','product_target')
   AND v.metric_date >= (now() AT TIME ZONE 'UTC')::date - 30;
