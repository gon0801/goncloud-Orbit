#!/usr/bin/env bash
# REPRICING 01 D.2 — ventas MX en el ledger: volumen y mapeo a producto (SOLO LECTURA, orbit_read).
set -euo pipefail
ssh goncloud 'bash -s' <<'REMOTO'
set -euo pipefail
DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off -v ON_ERROR_STOP=1 <<'SQL'
\echo '== 1. ventas MX ultimos 75 dias: con y sin product_id'
SELECT (product_id IS NOT NULL) AS con_producto, count(*) AS eventos,
       sum(quantity) AS unidades, count(DISTINCT product_id) AS productos
FROM ledger_event
WHERE platform = 'amazon_mx' AND kind = 'sale' AND event_date >= current_date - 75
GROUP BY 1 ORDER BY 1;

\echo '== 2. top 15 MX por unidades en 75 dias (cualquier volumen)'
WITH v AS (
  SELECT product_id,
    coalesce(sum(quantity) FILTER (WHERE event_date BETWEEN current_date-15 AND current_date-1), 0) AS u15,
    coalesce(sum(quantity) FILTER (WHERE event_date BETWEEN current_date-75 AND current_date-16), 0) AS u60
  FROM ledger_event WHERE platform = 'amazon_mx' AND kind = 'sale' AND quantity IS NOT NULL
    AND product_id IS NOT NULL AND event_date >= current_date - 75
  GROUP BY product_id
)
SELECT v.product_id, v.u15, v.u60, string_agg(l.id || ':' || l.seller_sku, ' ') AS listings
FROM v LEFT JOIN listing l ON l.product_id = v.product_id AND l.platform = 'amazon_mx'
GROUP BY v.product_id, v.u15, v.u60
ORDER BY v.u15 + v.u60 DESC LIMIT 15;
SQL
REMOTO
