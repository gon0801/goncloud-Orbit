WITH ventana AS (SELECT CURRENT_DATE - 105 AS desde, CURRENT_DATE - 15 AS hasta),
ventas AS (
  SELECT l.platform, l.order_id, l.amount,
         CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency THEN c.cost_amount * l.quantity END AS cogs_linea
    FROM ledger_event l CROSS JOIN ventana v
    LEFT JOIN sku_cost c ON c.product_id = l.product_id AND l.event_date >= c.valid_from AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
   WHERE l.kind = 'sale' AND l.event_date >= v.desde AND l.event_date < v.hasta),
oc AS (SELECT DISTINCT platform, order_id FROM ventas WHERE cogs_linea IS NOT NULL AND order_id IS NOT NULL),
est AS (
  SELECT l.platform, l.order_id, l.amount,
         EXISTS (SELECT 1 FROM ledger_event f WHERE f.platform = l.platform AND f.order_id = l.order_id
                   AND f.kind = 'fee' AND f.fee_type = 'referral_fee' AND split_part(f.source_event_id, '|', 2) <> 'fee_estimate') AS con_real
    FROM ledger_event l JOIN oc o ON o.platform = l.platform AND o.order_id = l.order_id
   WHERE l.source_event_id LIKE 'amazon|fee_estimate|%'),
vc AS (SELECT platform, sum(amount) FILTER (WHERE cogs_linea IS NOT NULL) AS cubierta FROM ventas GROUP BY 1)
SELECT t.platform, round(t.margen_neto_pct, 2) AS margen_hoy, vc.cubierta,
       coalesce(sum(e.amount) FILTER (WHERE e.con_real), 0) AS dup_monto, count(*) FILTER (WHERE e.con_real) AS dup_n,
       coalesce(sum(e.amount) FILTER (WHERE NOT e.con_real), 0) AS est_sin_real_monto, count(*) FILTER (WHERE NOT e.con_real) AS est_sin_real_n,
       round(t.margen_neto_pct - 100.0 * coalesce(sum(e.amount) FILTER (WHERE e.con_real), 0) / vc.cubierta, 2) AS margen_sin_dup
  FROM v_target_margen_plataforma t JOIN vc USING (platform) LEFT JOIN est e USING (platform)
 GROUP BY t.platform, t.margen_neto_pct, vc.cubierta ORDER BY 1;
