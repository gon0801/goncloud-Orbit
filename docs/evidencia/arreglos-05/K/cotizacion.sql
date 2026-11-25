-- K.1: la cotizacion integra (Product Fees para Amazon, CotizadorMeli para MeLi).
-- Solo lectura. Cada bloque empieza con un marcador "-- ==" que
-- lee_previa.sh usa para ejecutar un statement por invocacion.

-- == A. Volumen por plataforma, moneda y estado [platform|currency|estado|n|desde|hasta]
SELECT platform, quoted_price_currency, estado, count(*) AS n,
       min(fees_estimated_at)::date AS desde, max(fees_estimated_at)::date AS hasta
  FROM estimacion_fee_observation
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

-- == B. Impuesto explicito en la cotizacion; el escritor solo guarda "tax_amount" cuando es distinto de cero [platform|currency|cotizaciones|con_tax_no_cero|sin_detalle|sin_total]
SELECT platform, quoted_price_currency,
       count(*) AS cotizaciones,
       count(*) FILTER (WHERE fee_details::text LIKE '%"tax_amount"%') AS con_tax_no_cero,
       count(*) FILTER (WHERE jsonb_array_length(fee_details) = 0) AS sin_detalle,
       count(*) FILTER (WHERE total_fees IS NULL) AS sin_total
  FROM estimacion_fee_observation
 WHERE estado = 'success'
 GROUP BY 1, 2
 ORDER BY 1;

-- == C. Tasa cotizada de cada componente sobre el precio cotizado [platform|currency|tipo|n|mediana|pmin|pmax]
SELECT f.platform, f.quoted_price_currency, d->>'fee_type' AS tipo, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (
             ORDER BY (d->>'final_fee')::numeric / f.quoted_price_amount)::numeric, 6) AS mediana,
       round(min((d->>'final_fee')::numeric / f.quoted_price_amount)::numeric, 6) AS pmin,
       round(max((d->>'final_fee')::numeric / f.quoted_price_amount)::numeric, 6) AS pmax
  FROM estimacion_fee_observation f, jsonb_array_elements(f.fee_details) d
 WHERE f.estado = 'success'
 GROUP BY 1, 2, 3
 ORDER BY 1, 3;

-- == D. Canal de las cotizaciones de Amazon MX y presencia de FBAFees [canal|n|con_fba]
SELECT canal, count(*) AS n,
       count(*) FILTER (WHERE fee_details::text LIKE '%FBAFees%') AS con_fba
  FROM estimacion_fee_observation
 WHERE estado = 'success' AND platform = 'amazon_mx'
 GROUP BY 1
 ORDER BY 1;

-- == E. FBAFees cotizado por producto contra fba_fee real por unidad; fuera de la comision, se mide para descartar un patron 1.16 [productos|mediana_razon|p10|p90|a_1pct|a_116]
WITH q AS (
    SELECT l.product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (d->>'final_fee')::numeric) AS cotizado
      FROM estimacion_fee_observation f
      JOIN listing l ON l.id = f.listing_id,
           jsonb_array_elements(f.fee_details) d
     WHERE f.estado = 'success' AND f.platform = 'amazon_mx'
       AND d->>'fee_type' = 'FBAFees'
     GROUP BY l.product_id
), r AS (
    SELECT v.product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c.fee / v.unidades) AS cobrado
      FROM (
            SELECT order_id, min(product_id) AS product_id, sum(quantity) AS unidades
              FROM ledger_event
             WHERE kind = 'sale' AND platform = 'amazon_mx' AND order_id IS NOT NULL
               AND quantity IS NOT NULL AND event_date >= DATE '2026-09-01'
             GROUP BY 1
           ) v
      JOIN (
            SELECT order_id, sum(-amount) AS fee
              FROM v_ledger_cargo
             WHERE kind = 'fee' AND fee_type = 'fba_fee' AND platform = 'amazon_mx'
               AND order_id IS NOT NULL
             GROUP BY 1
           ) c ON c.order_id = v.order_id
     WHERE v.unidades > 0 AND c.fee > 0
     GROUP BY v.product_id
)
SELECT count(*) AS productos,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY r.cobrado / q.cotizado)::numeric, 6) AS mediana_razon,
       round(percentile_cont(0.1) WITHIN GROUP (ORDER BY r.cobrado / q.cotizado)::numeric, 6) AS p10,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY r.cobrado / q.cotizado)::numeric, 6) AS p90,
       count(*) FILTER (WHERE r.cobrado / q.cotizado BETWEEN 0.99 AND 1.01) AS a_1pct,
       count(*) FILTER (WHERE r.cobrado / q.cotizado BETWEEN 1.15 AND 1.17) AS a_116
  FROM q
  JOIN r ON r.product_id = q.product_id;
