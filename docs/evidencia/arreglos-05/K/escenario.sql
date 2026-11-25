-- K.1: el escenario de la estimacion y la politica vigente.
-- Solo lectura. Un statement por marcador "-- ==".

-- == A. Escenarios por plataforma, formula y estado [platform|formula|estado|n|desde|hasta]
SELECT platform, formula_version, estado, count(*) AS n,
       min(valoracion_date) AS desde, max(valoracion_date) AS hasta
  FROM estimacion_escenario
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

-- == B. Motivos registrados, incluida la abstencion impuesto_fee_pendiente [platform|formula|motivo|n]
SELECT platform, formula_version, motivo, count(*) AS n
  FROM estimacion_escenario e,
       LATERAL jsonb_array_elements_text(e.motivos) AS motivo
 GROUP BY 1, 2, 3
 ORDER BY 1, 3;

-- == C. S4: la comision estimada suma el 16% de ReferralFee una sola vez [escenarios|con_iva_ref|iva_ref_es_referral_x_016|fee_total_es_total_mas_iva]
WITH e AS (
    SELECT id, fee_observation_id, componentes
      FROM estimacion_escenario
     WHERE estado = 'disponible' AND formula_version = 'S4' AND platform = 'amazon_mx'
), ft AS (
    SELECT e.id, e.fee_observation_id,
           (SELECT (x->>'importe_normalizado')::numeric
              FROM jsonb_array_elements(e.componentes) x
             WHERE x->>'nombre' = 'fee_total') AS fee_total,
           (SELECT (x->>'importe_normalizado')::numeric
              FROM jsonb_array_elements(e.componentes) x
             WHERE x->>'nombre' = 'fee_iva_referral') AS iva_ref,
           (SELECT (x->>'importe_normalizado')::numeric
              FROM jsonb_array_elements(e.componentes) x
             WHERE x->>'nombre' = 'fee_detalle:ReferralFee') AS referral
      FROM e
)
SELECT count(*) AS escenarios,
       count(*) FILTER (WHERE ft.iva_ref IS NOT NULL) AS con_iva_ref,
       count(*) FILTER (WHERE ft.iva_ref IS NOT NULL
                          AND abs(round(ft.iva_ref, 4) - round(ft.referral * 0.16, 4)) < 0.0001) AS iva_ref_es_referral_x_016,
       count(*) FILTER (WHERE ft.iva_ref IS NOT NULL
                          AND abs(round(ft.fee_total::numeric, 4) - round(f.total_fees + ft.iva_ref, 4)) < 0.0001) AS fee_total_es_total_mas_iva
  FROM ft
  JOIN estimacion_fee_observation f ON f.id = ft.fee_observation_id;

-- == D. MeLi S4: sin componente de impuesto sobre la comision [escenarios|con_iva_ref]
SELECT count(*) AS escenarios,
       count(*) FILTER (WHERE componentes::text LIKE '%fee_iva_referral%') AS con_iva_ref
  FROM estimacion_escenario
 WHERE estado = 'disponible' AND formula_version = 'S4' AND platform = 'meli';

-- == E. La comision estimada con S4 contra la cobrada, por producto en Amazon MX [productos|pares|mediana_tasa_s4|mediana_razon|productos_a_1pct|productos_a_01pct]
WITH cob AS (
    SELECT v.product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c.fee / v.sale) AS tc,
           count(*) AS pares
      FROM (
            SELECT platform, order_id, min(product_id) AS product_id, sum(amount) AS sale
              FROM ledger_event
             WHERE kind = 'sale' AND order_id IS NOT NULL AND product_id IS NOT NULL
               AND platform = 'amazon_mx'
             GROUP BY 1, 2
           ) v
      JOIN (
            SELECT platform, order_id, sum(-amount) AS fee
              FROM v_ledger_cargo
             WHERE kind = 'fee' AND fee_type = 'referral_fee' AND order_id IS NOT NULL
               AND platform = 'amazon_mx'
               AND split_part(coalesce(source_event_id, ''), '|', 2) <> 'fee_estimate'
             GROUP BY 1, 2
           ) c ON c.platform = v.platform AND c.order_id = v.order_id
     WHERE v.sale > 0 AND c.fee > 0
     GROUP BY v.product_id
), cot AS (
    SELECT l.product_id,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY 1.16 * (d->>'final_fee')::numeric / f.quoted_price_amount) AS tq_s4
      FROM estimacion_fee_observation f
      JOIN listing l ON l.id = f.listing_id,
           jsonb_array_elements(f.fee_details) d
     WHERE f.estado = 'success' AND d->>'fee_type' = 'ReferralFee'
       AND f.platform = 'amazon_mx'
     GROUP BY l.product_id
), r AS (
    SELECT cob.product_id, cob.pares, cob.tc, cot.tq_s4, cob.tc / cot.tq_s4 AS razon
      FROM cob JOIN cot ON cot.product_id = cob.product_id
)
SELECT count(*) AS productos, sum(pares) AS pares,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY tq_s4)::numeric, 6) AS mediana_tasa_s4,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY razon)::numeric, 6) AS mediana_razon,
       count(*) FILTER (WHERE razon BETWEEN 0.99 AND 1.01) AS productos_a_1pct,
       count(*) FILTER (WHERE razon BETWEEN 0.999 AND 1.001) AS productos_a_01pct
  FROM r;

-- == F. MeLi: la comision cotizada contra la cobrada, por producto [productos|mediana_razon|productos_a_01pct]
WITH cob AS (
    SELECT v.product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY c.fee / v.sale) AS tc,
           count(*) AS pares
      FROM (
            SELECT platform, order_id, min(product_id) AS product_id, sum(amount) AS sale
              FROM ledger_event
             WHERE kind = 'sale' AND order_id IS NOT NULL AND product_id IS NOT NULL
               AND platform = 'meli'
             GROUP BY 1, 2
           ) v
      JOIN (
            SELECT platform, order_id, sum(-amount) AS fee
              FROM v_ledger_cargo
             WHERE kind = 'fee' AND fee_type = 'referral_fee' AND order_id IS NOT NULL
               AND platform = 'meli'
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
     WHERE f.estado = 'success' AND d->>'fee_type' = 'SaleFee' AND f.platform = 'meli'
     GROUP BY l.product_id
)
SELECT count(*) AS productos,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY cob.tc / cot.tq)::numeric, 6) AS mediana_razon,
       count(*) FILTER (WHERE cob.tc / cot.tq BETWEEN 0.999 AND 1.001) AS productos_a_01pct
  FROM cob JOIN cot ON cot.product_id = cob.product_id;

-- == G. Una cotizacion completa por plataforma [id|platform|canal|precio|currency|total|estimada_en|fee_details]
SELECT id, platform, canal, quoted_price_amount, quoted_price_currency, total_fees,
       fees_estimated_at, fee_details::text
  FROM (
        SELECT DISTINCT ON (platform) id, platform, canal, quoted_price_amount,
               quoted_price_currency, total_fees, fees_estimated_at, fee_details
          FROM estimacion_fee_observation
         WHERE estado = 'success'
         ORDER BY platform, id DESC
       ) ultimas
 ORDER BY platform;
