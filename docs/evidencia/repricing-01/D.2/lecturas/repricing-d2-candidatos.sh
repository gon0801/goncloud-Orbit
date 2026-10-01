#!/usr/bin/env bash
# REPRICING 01 D.2 — candidatos con ventas (SOLO LECTURA, rol orbit_read).
set -euo pipefail
ssh goncloud 'bash -s' <<'REMOTO'
set -euo pipefail
echo "== 0. usuario ssh y donde vive el cron de precio"
whoami
grep -rl 'app.cli precio' /etc/crontab /etc/cron.d 2>/dev/null || echo "(no esta en /etc/cron*)"
DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ)
docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off -v ON_ERROR_STOP=1 <<'SQL'
\echo '== 1. cobertura del ledger MX (ultimo dia cargado por un ingest ok)'
SELECT max(e.event_date) AS cubierto
FROM ledger_event e JOIN ingest_run r ON r.id = e.ingest_run_id
WHERE e.platform = 'amazon_mx' AND e.kind = 'sale' AND r.ok
  AND r.source = 'accounting_ledger_events';

\echo '== 2. ventas de los tres goals shadow (u15 = [hoy-15,hoy-1], u60 = [hoy-75,hoy-16])'
SELECT l.id AS listing_id, l.seller_sku, l.product_id,
  coalesce(sum(e.quantity) FILTER (WHERE e.event_date BETWEEN current_date-15 AND current_date-1), 0) AS u15,
  coalesce(sum(e.quantity) FILTER (WHERE e.event_date BETWEEN current_date-75 AND current_date-16), 0) AS u60,
  max(e.event_date) AS ultima_venta
FROM listing l
LEFT JOIN ledger_event e ON e.product_id = l.product_id AND e.platform = l.platform AND e.kind = 'sale'
WHERE l.platform = 'amazon_mx' AND l.id IN (1213, 1284, 1295)
GROUP BY l.id, l.seller_sku, l.product_id ORDER BY l.id;

\echo '== 3. top 15 MX por unidades u60 (candidatos; activo = ultima observacion BUYABLE)'
WITH v AS (
  SELECT product_id,
    sum(quantity) FILTER (WHERE event_date BETWEEN current_date-15 AND current_date-1) AS u15,
    sum(quantity) FILTER (WHERE event_date BETWEEN current_date-75 AND current_date-16) AS u60
  FROM ledger_event WHERE platform = 'amazon_mx' AND kind = 'sale' AND quantity IS NOT NULL
  GROUP BY product_id
), est AS (
  SELECT DISTINCT ON (seller_sku) seller_sku, status
  FROM spapi_listing_estado_observation WHERE platform = 'amazon_mx'
  ORDER BY seller_sku, observed_at DESC
)
SELECT l.id AS listing_id, l.seller_sku, l.product_id, coalesce(v.u15,0) AS u15, v.u60, est.status,
  (SELECT d.resultado || ' ' || coalesce(d.motivo,'') FROM precio_decision d
    WHERE d.listing_id = l.id AND d.platform = l.platform ORDER BY d.decision_date DESC LIMIT 1) AS ultima_decision
FROM listing l JOIN v ON v.product_id = l.product_id
LEFT JOIN est ON est.seller_sku = l.seller_sku
WHERE l.platform = 'amazon_mx' AND v.u60 >= 20
ORDER BY v.u60 DESC LIMIT 15;
SQL
REMOTO
