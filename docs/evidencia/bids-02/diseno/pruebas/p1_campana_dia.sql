SET statement_timeout = '30s';
COPY (
 SELECT e.platform, e.external_id, e.name, v.metric_date, v.cost, v.impressions, v.clicks
   FROM v_metric_latest v JOIN ad_entity e ON e.id = v.ad_entity_id
  WHERE e.kind = 'campaign' AND v.metric_date >= DATE '2026-08-01'
  ORDER BY 1,3,4
) TO STDOUT WITH CSV HEADER;
