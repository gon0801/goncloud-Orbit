-- K.1: el cargo externo (liquidacion de Amazon y cargos de MeLi).
-- Solo lectura. v_ledger_cargo (0086) descarta la comision estimada cuando ya
-- llego la real del mismo pedido, pero deja la estimada cuando no hay real.
-- Para evidencia de "cargo externo" se exige ademas que la fila NO sea
-- estimada (split_part(source_event_id, '|', 2) <> 'fee_estimate') y las
-- estimadas se cuentan aparte.

-- == A. Filas de cargo en el libro, estimadas frente a reales [platform|filas|estimadas]
SELECT platform, count(*) AS filas,
       count(*) FILTER (WHERE split_part(coalesce(source_event_id, ''), '|', 2) = 'fee_estimate') AS estimadas
  FROM ledger_event
 WHERE kind = 'fee'
 GROUP BY 1
 ORDER BY 1;

-- == A2. Comisiones referral_fee en v_ledger_cargo: reales y estimadas [platform|filas|estimadas|reales]
SELECT platform, count(*) AS filas,
       count(*) FILTER (WHERE split_part(coalesce(source_event_id, ''), '|', 2) = 'fee_estimate') AS estimadas,
       count(*) FILTER (WHERE split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate') AS reales
  FROM v_ledger_cargo
 WHERE kind = 'fee' AND fee_type = 'referral_fee'
 GROUP BY 1
 ORDER BY 1;

-- == B. Tipos de cargo, moneda, signo y ventana [platform|currency|kind|fee_type|n|desde|hasta|total]
SELECT platform, amount_currency, kind, coalesce(fee_type, '(null)') AS fee_type,
       count(*) AS n, min(event_date) AS desde, max(event_date) AS hasta,
       round(sum(amount)::numeric, 2) AS total
  FROM ledger_event
 WHERE event_date >= DATE '2026-01-01'
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 3, 4;

-- == C. Razon cobrada de la comision variable por pedido, solo cargos efectivos, por plataforma y mes [platform|currency|mes|pares|mediana|pmin|pmax]
WITH v AS (
    SELECT platform, order_id, sum(amount) AS sale, amount_currency AS cur, min(event_date) AS fecha
      FROM ledger_event
     WHERE kind = 'sale' AND order_id IS NOT NULL
     GROUP BY 1, 2, 4
), c AS (
    SELECT platform, order_id, sum(-amount) AS fee
      FROM v_ledger_cargo
     WHERE kind = 'fee' AND fee_type = 'referral_fee' AND order_id IS NOT NULL
       AND split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'
     GROUP BY 1, 2
)
SELECT v.platform, v.cur, date_trunc('month', v.fecha)::date AS mes, count(*) AS pares,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY c.fee / v.sale)::numeric, 6) AS mediana,
       round(min(c.fee / v.sale)::numeric, 6) AS pmin,
       round(max(c.fee / v.sale)::numeric, 6) AS pmax
  FROM v
  JOIN c ON c.platform = v.platform AND c.order_id = v.order_id
 WHERE v.sale > 0 AND c.fee > 0
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

-- == D. Razon por pedido en la ventana, solo cargos efectivos; las estimadas se cuentan aparte [platform|pares_reales|p10|p50|p90|exactos_0150|exactos_0195|pedidos_solo_estimada]
WITH ventana AS (
    SELECT 'amazon_mx'::text AS platform, DATE '2026-09-01' AS desde
    UNION ALL SELECT 'amazon_us', DATE '2026-10-15'
    UNION ALL SELECT 'meli', DATE '2026-09-01'
), v AS (
    SELECT l.platform, l.order_id, sum(l.amount) AS sale
      FROM ledger_event l JOIN ventana w ON w.platform = l.platform::text
     WHERE l.kind = 'sale' AND l.order_id IS NOT NULL AND l.event_date >= w.desde
     GROUP BY 1, 2
), c AS (
    SELECT platform, order_id,
           sum(-amount) FILTER (WHERE split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate') AS fee_real,
           count(*) FILTER (WHERE split_part(coalesce(source_event_id, ''), '|', 2) = 'fee_estimate') AS n_est,
           count(*) FILTER (WHERE split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate') AS n_real
      FROM v_ledger_cargo
     WHERE kind = 'fee' AND fee_type = 'referral_fee' AND order_id IS NOT NULL
     GROUP BY 1, 2
), p AS (
    SELECT v.platform, c.fee_real / v.sale AS r
      FROM v JOIN c ON c.platform = v.platform AND c.order_id = v.order_id
     WHERE v.sale > 0 AND c.fee_real IS NOT NULL AND c.fee_real > 0
), solo_est AS (
    SELECT v.platform, count(*) AS n
      FROM v JOIN c ON c.platform = v.platform AND c.order_id = v.order_id
     WHERE v.sale > 0 AND c.n_real = 0 AND c.n_est > 0
     GROUP BY 1
)
SELECT p.platform, count(*) AS pares_reales,
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY r)::numeric, 6) AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY r)::numeric, 6) AS p50,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY r)::numeric, 6) AS p90,
       count(*) FILTER (WHERE r BETWEEN 0.1499 AND 0.1501) AS exactos_0150,
       count(*) FILTER (WHERE r BETWEEN 0.1949 AND 0.1951) AS exactos_0195,
       (SELECT coalesce(max(n), 0) FROM solo_est s WHERE s.platform = p.platform) AS pedidos_solo_estimada
  FROM p
 GROUP BY 1
 ORDER BY 1;

-- == E. Razon cobrado/cotizado por producto, solo cargos efectivos, en MXN [platform|productos|pares|mediana_cobrada|mediana_cotizada|mediana_razon|pmin|pmax|en_banda_116|en_banda_100]
WITH cob AS (
    SELECT v.platform, v.product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c.fee / v.sale) AS tc,
           count(*) AS pares
      FROM (
            SELECT platform, order_id, min(product_id) AS product_id, sum(amount) AS sale
              FROM ledger_event
             WHERE kind = 'sale' AND order_id IS NOT NULL AND product_id IS NOT NULL
             GROUP BY 1, 2
           ) v
      JOIN (
            SELECT platform, order_id, sum(-amount) AS fee
              FROM v_ledger_cargo
             WHERE kind = 'fee' AND fee_type = 'referral_fee' AND order_id IS NOT NULL
               AND split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'
             GROUP BY 1, 2
           ) c ON c.platform = v.platform AND c.order_id = v.order_id
     WHERE v.sale > 0 AND c.fee > 0
     GROUP BY 1, 2
), cot AS (
    SELECT f.platform, l.product_id,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY (d->>'final_fee')::numeric / f.quoted_price_amount) AS tq
      FROM estimacion_fee_observation f
      JOIN listing l ON l.id = f.listing_id,
           jsonb_array_elements(f.fee_details) d
     WHERE f.estado = 'success' AND d->>'fee_type' IN ('ReferralFee', 'SaleFee')
       AND f.quoted_price_currency = 'MXN'
     GROUP BY 1, 2
), r AS (
    SELECT cob.platform, cob.pares, cob.tc, cot.tq, cob.tc / cot.tq AS razon
      FROM cob JOIN cot ON cot.platform = cob.platform AND cot.product_id = cob.product_id
)
SELECT platform, count(*) AS productos, sum(pares) AS pares,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY tc)::numeric, 6) AS mediana_cobrada,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY tq)::numeric, 6) AS mediana_cotizada,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY razon)::numeric, 6) AS mediana_razon,
       round(min(razon)::numeric, 6) AS pmin, round(max(razon)::numeric, 6) AS pmax,
       count(*) FILTER (WHERE razon BETWEEN 1.150 AND 1.170) AS en_banda_116,
       count(*) FILTER (WHERE razon BETWEEN 0.990 AND 1.010) AS en_banda_100
  FROM r
 GROUP BY 1
 ORDER BY 1;

-- == F. Amazon US en su ventana, solo cargos efectivos: razon por producto con la cotizacion en USD y el cargo en MXN [productos|pares|mediana_cobrada|mediana_cotizada|mediana_razon|pmin|pmax|en_banda_116|en_banda_100]
WITH cob AS (
    SELECT v.product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c.fee / v.sale) AS tc,
           count(*) AS pares
      FROM (
            SELECT platform, order_id, min(product_id) AS product_id, sum(amount) AS sale
              FROM ledger_event
             WHERE kind = 'sale' AND order_id IS NOT NULL AND product_id IS NOT NULL
               AND platform = 'amazon_us' AND event_date >= DATE '2026-10-15'
             GROUP BY 1, 2
           ) v
      JOIN (
            SELECT platform, order_id, sum(-amount) AS fee
              FROM v_ledger_cargo
             WHERE kind = 'fee' AND fee_type = 'referral_fee' AND order_id IS NOT NULL
               AND platform = 'amazon_us'
               AND split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'
             GROUP BY 1, 2
           ) c ON c.platform = v.platform AND c.order_id = v.order_id
     WHERE v.sale > 0 AND c.fee > 0
     GROUP BY v.product_id
), cot AS (
    SELECT l.product_id,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY (d->>'final_fee')::numeric / f.quoted_price_amount) AS tq
      FROM estimacion_fee_observation f
      JOIN listing l ON l.id = f.listing_id,
           jsonb_array_elements(f.fee_details) d
     WHERE f.estado = 'success' AND d->>'fee_type' = 'ReferralFee' AND f.platform = 'amazon_us'
     GROUP BY l.product_id
), r AS (
    SELECT cob.product_id, cob.pares, cob.tc, cot.tq, cob.tc / cot.tq AS razon
      FROM cob JOIN cot ON cot.product_id = cob.product_id
)
SELECT count(*) AS productos, sum(pares) AS pares,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY tc)::numeric, 6) AS mediana_cobrada,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY tq)::numeric, 6) AS mediana_cotizada,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY razon)::numeric, 6) AS mediana_razon,
       round(min(razon)::numeric, 6) AS pmin, round(max(razon)::numeric, 6) AS pmax,
       count(*) FILTER (WHERE razon BETWEEN 1.150 AND 1.170) AS en_banda_116,
       count(*) FILTER (WHERE razon BETWEEN 0.990 AND 1.010) AS en_banda_100
  FROM r;

-- == G. Un pedido MX con la comision cobrada al 15% y su diferencia contra la tasa cotizada 12.931% [order_id|venta|comision|tasa|razon|diferencia]
WITH v AS (
    SELECT order_id, sum(amount) AS sale
      FROM ledger_event
     WHERE kind = 'sale' AND platform = 'amazon_mx' AND order_id IS NOT NULL
       AND event_date >= DATE '2026-09-01'
     GROUP BY 1
), c AS (
    SELECT order_id, sum(-amount) AS fee
      FROM v_ledger_cargo
     WHERE kind = 'fee' AND fee_type = 'referral_fee' AND platform = 'amazon_mx'
       AND order_id IS NOT NULL
       AND split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'
     GROUP BY 1
)
SELECT v.order_id, round(v.sale, 4) AS venta, round(c.fee, 4) AS comision,
       round(c.fee / v.sale, 6) AS tasa,
       round((c.fee / v.sale) / 0.129310, 6) AS razon,
       round(c.fee - v.sale * 0.129310, 4) AS diferencia
  FROM v JOIN c ON c.order_id = v.order_id
 WHERE v.sale > 0 AND (c.fee / v.sale) BETWEEN 0.1499 AND 0.1501
 ORDER BY v.sale DESC
 LIMIT 3;
