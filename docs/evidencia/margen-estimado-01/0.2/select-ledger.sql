\pset pager off
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT platform, kind, coalesce(fee_type, '[sin fee_type]') AS fee_type,
       count(*) AS filas, count(*) FILTER (WHERE order_id IS NOT NULL) AS con_order_id,
       count(DISTINCT amount_currency) AS monedas, min(event_date), max(event_date)
  FROM ledger_event
 WHERE platform IN ('amazon_mx', 'amazon_us')
   AND kind IN ('fee', 'withholding', 'refund')
 GROUP BY platform, kind, coalesce(fee_type, '[sin fee_type]');

SELECT platform, kind, amount_currency, count(*) AS filas,
       min(event_date), max(event_date)
  FROM ledger_event
 WHERE platform IN ('amazon_mx', 'amazon_us')
 GROUP BY platform, kind, amount_currency;

ROLLBACK;
