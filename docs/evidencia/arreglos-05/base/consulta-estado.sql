SELECT 'momento_utc' AS fuente, (now() AT TIME ZONE 'UTC')::text AS dato
UNION ALL
SELECT 'ciclos_por_modo_estado', jsonb_agg(to_jsonb(x))::text FROM
 (SELECT mode,status,count(*) AS n FROM optimizer_cycle GROUP BY 1,2 ORDER BY 1,2) x
UNION ALL
SELECT 'colas_por_modo_estado', jsonb_agg(to_jsonb(x))::text FROM
 (SELECT modo,estado,count(*) AS n FROM apply_queue GROUP BY 1,2 ORDER BY 1,2) x
UNION ALL
SELECT 'goals', jsonb_agg(to_jsonb(x))::text FROM
 (SELECT platform,mode,enabled,count(*) AS n FROM ads_optimizer_goal GROUP BY 1,2,3 ORDER BY 1,2,3) x
UNION ALL
SELECT 'reposicion_estados', jsonb_agg(to_jsonb(x))::text FROM
 (SELECT estado,count(*) AS n,count(repuesto_external) AS con_id FROM keyword_archivo_manual GROUP BY 1 ORDER BY 1) x
UNION ALL
SELECT 'reposicion_ids_duplicados',count(*)::text FROM
 (SELECT platform,repuesto_external FROM keyword_archivo_manual WHERE repuesto_external IS NOT NULL
  GROUP BY 1,2 HAVING count(*)>1) x
UNION ALL
SELECT 'settings_seleccionados',jsonb_object_agg(k,v)::text
FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) c,
 LATERAL jsonb_each(c.settings) e(k,v)
WHERE k ~ '^(ads_bid_politica_|ads_apply_cap_|ads_temporada_|precio_modo|ads_producto_)'
UNION ALL
SELECT 'negatives_reconciliados',jsonb_build_object('n',count(*),'sin_ack',count(*) FILTER (WHERE a.ack IS NULL))::text
FROM apply_attempt a JOIN decision d ON d.id=a.decision_id
WHERE d.kind='negative' AND a.resultado='ok:reconciliado';
