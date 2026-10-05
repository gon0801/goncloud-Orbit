-- Universo del roster: productos con algun anuncio ENABLED o PAUSED. Cobertura como el asesor
-- (una MISMA ficha vigente para todos los listings del producto), separando los que solo tienen PAUSED.
WITH pa AS (
  SELECT pa.platform, pa.parent_id AS grupo, s.status, pa.listing_id, l.product_id
    FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status IN ('ENABLED','PAUSED')
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad'),
prod AS (SELECT platform, product_id, bool_or(status = 'ENABLED') AS con_enabled FROM pa WHERE product_id IS NOT NULL GROUP BY 1,2),
elegida AS (
  SELECT p.platform, p.product_id, l.id AS listing_id,
         (SELECT f.id FROM jev_ficha_version f
           WHERE f.producto_id = p.product_id AND f.plataforma = p.platform
             AND f.revisar_antes_de >= now() AND f.listings @> ARRAY[l.id]::bigint[]
             AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)
           ORDER BY f.observado_at DESC, f.created_at DESC LIMIT 1) AS ficha
    FROM prod p JOIN listing l ON l.product_id = p.product_id AND l.platform = p.platform),
cub AS (SELECT platform, product_id, (count(*) FILTER (WHERE ficha IS NULL) = 0 AND count(DISTINCT ficha) = 1) AS cubierto FROM elegida GROUP BY 1,2)
SELECT 'productos' AS que, p.platform, count(*) AS total, count(*) FILTER (WHERE c.cubierto) AS cubiertos,
       count(*) FILTER (WHERE NOT p.con_enabled) AS solo_pausados,
       count(*) FILTER (WHERE NOT p.con_enabled AND NOT c.cubierto) AS solo_pausados_sin_ficha
  FROM prod p JOIN cub c USING (platform, product_id) GROUP BY 2 ORDER BY 2;
-- anuncios PAUSED sin producto, y en cuantos grupos con gasto de busquedas
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date) platform, ad_entity_id, cost
    FROM search_term_observation WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
gasto AS (SELECT platform, ad_entity_id AS grupo FROM ult GROUP BY 1,2 HAVING sum(cost) > 0),
pa AS (
  SELECT pa.platform, pa.parent_id AS grupo, s.status, l.product_id
    FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status IN ('ENABLED','PAUSED')
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform WHERE pa.kind = 'product_ad')
SELECT 'grupos_con_gasto' AS que, g.platform, count(DISTINCT g.grupo) AS grupos,
       count(DISTINCT g.grupo) FILTER (WHERE pa.product_id IS NULL) AS con_activo_sin_producto,
       count(*) FILTER (WHERE pa.product_id IS NULL AND pa.status = 'PAUSED') AS anuncios_pausados_sin_producto
  FROM gasto g JOIN pa ON pa.platform = g.platform AND pa.grupo = g.grupo GROUP BY 2 ORDER BY 2;
