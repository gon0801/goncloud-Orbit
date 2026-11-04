\echo == P3 frescura: anuncios por estado y dia de synced_at frente a la ultima corrida de estructura
WITH u AS (SELECT max(started_at) AS ini FROM ingest_run WHERE source = 'amazon_ads_structure_v2' AND ok)
SELECT pa.platform, s.status, count(*) AS anuncios,
       count(*) FILTER (WHERE s.synced_at >= u.ini) AS vistos_en_ultima,
       count(*) FILTER (WHERE s.synced_at < u.ini) AS no_vistos, min(s.synced_at)::date AS mas_viejo, max(u.ini)::timestamp(0) AS inicio_ultima
  FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id CROSS JOIN u
 WHERE pa.kind = 'product_ad' GROUP BY 1,2 ORDER BY 1,2;
\echo == anuncios sin fila de estado
SELECT pa.platform, count(*) FROM ad_entity pa LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id WHERE pa.kind='product_ad' AND s.ad_entity_id IS NULL GROUP BY 1;
\echo == ultimas corridas de estructura
SELECT id, started_at::timestamp(0), finished_at::timestamp(0), rows_written, rows_skipped, skip_reason, ok, llamadas FROM ingest_run WHERE source='amazon_ads_structure_v2' ORDER BY id DESC LIMIT 4;
\echo == grupos con gasto de busquedas en la ventana: limpios, y con ficha de todos
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, search_term, cost, clicks, orders
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
gasto AS (SELECT platform, ad_entity_id AS grupo, sum(cost) AS c, count(DISTINCT search_term) AS terminos FROM ult GROUP BY 1,2 HAVING sum(cost) > 0),
pa AS (
  SELECT pa.platform, pa.parent_id AS grupo, s.status, l.product_id,
         EXISTS (SELECT 1 FROM jev_ficha_version f WHERE f.producto_id = l.product_id AND f.plataforma = pa.platform
                    AND f.revisar_antes_de >= now() AND f.listings @> ARRAY[pa.listing_id]::bigint[]
                    AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)) AS cubierto
    FROM ad_entity pa LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad'),
g AS (SELECT platform, grupo,
             count(*) FILTER (WHERE status IN ('ENABLED','PAUSED')) AS activos,
             count(*) FILTER (WHERE status IN ('ENABLED','PAUSED') AND product_id IS NULL) AS activos_sin_producto,
             count(*) FILTER (WHERE status IN ('ENABLED','PAUSED') AND NOT cubierto) AS activos_sin_ficha,
             count(*) FILTER (WHERE status IS NULL) AS sin_estado,
             count(DISTINCT product_id) FILTER (WHERE status IN ('ENABLED','PAUSED')) AS productos
        FROM pa GROUP BY 1,2)
SELECT gasto.platform, count(*) AS grupos_con_gasto, round(sum(gasto.c),0) AS gasto,
       count(*) FILTER (WHERE g.activos_sin_producto = 0 AND g.sin_estado = 0) AS ligados,
       count(*) FILTER (WHERE g.activos_sin_producto = 0 AND g.sin_estado = 0 AND g.activos_sin_ficha = 0) AS ligados_y_con_ficha,
       round(100 * sum(gasto.c) FILTER (WHERE g.activos_sin_producto = 0 AND g.sin_estado = 0 AND g.activos_sin_ficha = 0) / sum(gasto.c), 1) AS pct_gasto_evaluable,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY g.productos) AS mediana_productos, max(g.productos) AS max_productos
  FROM gasto JOIN g ON g.platform = gasto.platform AND g.grupo = gasto.grupo GROUP BY 1 ORDER BY 1;
