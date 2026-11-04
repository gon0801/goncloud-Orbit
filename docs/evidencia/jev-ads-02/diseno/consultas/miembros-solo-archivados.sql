-- Grupos con gasto de busquedas: cuantos tienen algun miembro (producto o anuncio suelto) solo-ARCHIVED
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, cost
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
gasto AS (SELECT platform, ad_entity_id AS grupo FROM ult GROUP BY 1,2 HAVING sum(cost) > 0),
pa AS (
  SELECT pa.platform, pa.parent_id AS grupo, coalesce(l.product_id::text, 'ad:' || pa.id::text) AS miembro, s.status
    FROM ad_entity pa LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad'),
m AS (SELECT platform, grupo, miembro, bool_or(status IN ('ENABLED','PAUSED')) AS activo, bool_and(status IS NOT NULL) AS con_estado FROM pa GROUP BY 1,2,3),
g AS (SELECT platform, grupo, count(*) AS miembros, count(*) FILTER (WHERE activo) AS activos,
             count(*) FILTER (WHERE NOT activo AND con_estado) AS solo_archivados FROM m GROUP BY 1,2)
SELECT g.platform, count(*) AS grupos_con_gasto, count(*) FILTER (WHERE solo_archivados > 0) AS con_algun_miembro_solo_archivado,
       sum(solo_archivados) AS miembros_solo_archivados, sum(activos) AS miembros_activos
  FROM g JOIN gasto USING (platform, grupo) GROUP BY 1 ORDER BY 1;
