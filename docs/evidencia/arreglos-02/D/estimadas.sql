WITH est AS (
  SELECT platform, order_id FROM ledger_event
   WHERE split_part(source_event_id, '|', 2) = 'fee_estimate' AND order_id IS NOT NULL
   GROUP BY 1, 2),
prod AS (
  SELECT l.platform, l.order_id, count(DISTINCT l.product_id) AS productos
    FROM ledger_event l JOIN est e ON e.platform = l.platform AND e.order_id = l.order_id
   WHERE l.kind = 'sale'
   GROUP BY 1, 2)
SELECT e.platform, count(*) AS pedidos_con_estimada, count(*) FILTER (WHERE p.productos > 1) AS con_varios_productos
  FROM est e LEFT JOIN prod p ON p.platform = e.platform AND p.order_id = e.order_id
 GROUP BY 1 ORDER BY 1;
