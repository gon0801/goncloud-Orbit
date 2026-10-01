#!/usr/bin/env bash
# REPRICING 01 D.2 — escenarios disponibles en el dia y canal de los que venden (SOLO LECTURA, orbit_read).
set -euo pipefail
ssh goncloud 'bash -s' <<'REMOTO'
set -euo pipefail
DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off -v ON_ERROR_STOP=1 <<'SQL'
\echo '== 1. escenarios MX por hora UTC y estado, ultimas 48 h'
SELECT date_trunc('hour', observed_at) AS hora, estado, count(DISTINCT listing_id)
FROM estimacion_escenario WHERE platform = 'amazon_mx' AND observed_at > now() - interval '48 hours'
GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 2. los que mas venden: tienen escenario alguna vez? inventario FBA?'
SELECT l.id AS listing_id, l.seller_sku,
  (SELECT count(*) FROM estimacion_escenario e WHERE e.listing_id = l.id) AS escenarios,
  (SELECT max(metric_date) FROM spapi_inventario_observation i
     WHERE i.seller_sku = l.seller_sku AND i.platform = 'amazon_mx') AS ultimo_inventario_fba,
  (SELECT status FROM spapi_listing_estado_observation s
     WHERE s.seller_sku = l.seller_sku AND s.platform = 'amazon_mx' ORDER BY observed_at DESC LIMIT 1) AS estado
FROM listing l
WHERE l.platform = 'amazon_mx' AND l.id IN (1204,1200,1206,1142,1133,1150,1256,1260,1265,1302,1258)
ORDER BY l.id;

\echo '== 3. MX con escenario DISPONIBLE en los ultimos 3 dias y alguna venta en 75 dias'
WITH d AS (
  SELECT DISTINCT ON (listing_id) listing_id, contribucion_pct, observed_at
  FROM estimacion_escenario
  WHERE platform = 'amazon_mx' AND estado = 'disponible' AND observed_at > now() - interval '3 days'
  ORDER BY listing_id, observed_at DESC),
v AS (
  SELECT product_id,
    coalesce(sum(quantity) FILTER (WHERE event_date BETWEEN current_date-15 AND current_date-1), 0) AS u15,
    coalesce(sum(quantity) FILTER (WHERE event_date BETWEEN current_date-75 AND current_date-16), 0) AS u60
  FROM ledger_event WHERE platform = 'amazon_mx' AND kind = 'sale' AND product_id IS NOT NULL
    AND event_date >= current_date - 75 GROUP BY product_id)
SELECT d.listing_id, l.seller_sku, l.product_id, round(d.contribucion_pct, 2) AS m_pct, v.u15, v.u60
FROM d JOIN listing l ON l.id = d.listing_id AND l.platform = 'amazon_mx'
JOIN v ON v.product_id = l.product_id
ORDER BY v.u15 + v.u60 DESC LIMIT 15;
SQL
REMOTO
