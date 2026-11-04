\echo == P3 frescura del roster: anuncios ENABLED/PAUSED por antiguedad de synced_at frente a la ultima corrida de estructura
WITH ult AS (SELECT platform, max(started_at) AS ini, max(finished_at) AS fin FROM ingest_run WHERE source ~ 'struct' AND ok GROUP BY 1)
SELECT pa.platform, s.status, count(*) AS anuncios,
       count(*) FILTER (WHERE s.synced_at >= u.ini) AS vistos_en_ultima,
       count(*) FILTER (WHERE s.synced_at < u.ini AND s.synced_at >= u.ini - interval '2 days') AS hasta_2d_antes,
       count(*) FILTER (WHERE s.synced_at < u.ini - interval '2 days') AS mas_viejos,
       min(s.synced_at)::date AS mas_viejo, max(u.ini)::timestamp(0) AS inicio_ultima
  FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id JOIN ult u ON u.platform = pa.platform
 WHERE pa.kind = 'product_ad' GROUP BY 1,2 ORDER BY 1,2;
\echo == fuentes de ingest_run y ultima corrida
SELECT source, platform, count(*) AS corridas, count(*) FILTER (WHERE NOT ok) AS fallidas, max(started_at)::timestamp(0) AS ultima FROM ingest_run GROUP BY 1,2 ORDER BY 1,2;
\echo == P4 anuncios ENABLED sin producto: con o sin listing_id
SELECT pa.platform, count(*) AS sin_producto,
       count(*) FILTER (WHERE pa.listing_id IS NULL) AS listing_id_nulo,
       count(*) FILTER (WHERE pa.listing_id IS NOT NULL AND l.id IS NULL) AS listing_de_otra_plataforma,
       count(*) FILTER (WHERE l.id IS NOT NULL AND l.product_id IS NULL) AS listing_sin_producto,
       count(DISTINCT pa.parent_id) AS grupos_afectados
  FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
  LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
 WHERE pa.kind = 'product_ad' AND (l.id IS NULL OR l.product_id IS NULL) GROUP BY 1 ORDER BY 1;
\echo == P4b grupos US: anuncios ENABLED sin producto por grupo (top) y si el grupo tiene gasto reciente
WITH g AS (
  SELECT pa.platform, pa.parent_id AS grupo, count(*) AS anuncios, count(*) FILTER (WHERE l.product_id IS NULL) AS sin_producto
    FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad' GROUP BY 1,2),
gasto AS (SELECT platform, ad_entity_id, sum(cost) AS c FROM (
   SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, cost FROM search_term_observation
    WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC) x GROUP BY 1,2)
SELECT g.platform, count(*) AS grupos, count(*) FILTER (WHERE sin_producto = 0) AS limpios,
       count(*) FILTER (WHERE sin_producto > 0 AND sin_producto < anuncios) AS mixtos, count(*) FILTER (WHERE sin_producto = anuncios) AS todos_sin_producto,
       round(100 * sum(coalesce(gasto.c,0)) FILTER (WHERE sin_producto = 0) / nullif(sum(coalesce(gasto.c,0)),0), 1) AS pct_gasto_en_grupos_limpios
  FROM g LEFT JOIN gasto ON gasto.platform = g.platform AND gasto.ad_entity_id = g.grupo GROUP BY 1 ORDER BY 1;
\echo == P5 de donde saldria "negativo ya puesto"
SELECT 'apply_queue negative applied' AS fuente, count(*) FROM apply_queue WHERE kind='negative' AND estado='applied'
UNION ALL SELECT 'apply_queue harvest applied', count(*) FROM apply_queue WHERE kind='harvest' AND estado='applied'
UNION ALL SELECT 'ad_entity kinds: ' || string_agg(DISTINCT kind::text, ','), count(*) FROM ad_entity;
