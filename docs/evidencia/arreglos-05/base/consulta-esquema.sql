SELECT table_name, string_agg(column_name, ',' ORDER BY ordinal_position)
FROM information_schema.columns
WHERE table_schema='public' AND table_name IN
 ('optimizer_cycle','apply_attempt','apply_queue','keyword_archivo_manual',
  'decision_application','harvest_job','ads_optimizer_goal','sku_cost','estimacion_venta')
GROUP BY table_name ORDER BY table_name;
