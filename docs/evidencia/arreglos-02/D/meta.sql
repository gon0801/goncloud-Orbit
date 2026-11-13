SELECT c.id, c.platform, (c.notes::jsonb)->'target'->>'margen_neto_pct' AS margen_neto_pct,
       (c.notes::jsonb)->'target'->>'target_aplicado' AS target_aplicado
  FROM optimizer_cycle c
 WHERE c.id IN (SELECT max(id) FROM optimizer_cycle
                WHERE platform IN ('amazon_mx', 'amazon_us')
                  AND CASE WHEN notes LIKE '{%' THEN (notes::jsonb) ? 'target' ELSE false END
                GROUP BY platform)
 ORDER BY c.platform;
