-- Lector, solo agregados por plataforma; estados ausentes preservados.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
WITH por_grupo AS (
 SELECT g.platform, g.id,
  count(a.id) AS anuncios_conocidos,
  count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS anuncios_ep,
  count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED') AND l.id IS NULL) AS ep_sin_listing,
  count(DISTINCT l.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS publicaciones_ep,
  count(a.id) FILTER (WHERE s.ad_entity_id IS NULL OR s.status IS NULL) AS sin_estado,
  count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED') AND s.synced_at < now()-interval '48 hours') AS ep_obsoletos,
  min(s.synced_at) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS ep_sync_min,
  max(s.synced_at) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS ep_sync_max
 FROM ad_entity g
 LEFT JOIN ad_entity a ON a.parent_id=g.id AND a.platform=g.platform AND a.kind='product_ad'
 LEFT JOIN ad_entity_state s ON s.ad_entity_id=a.id
 LEFT JOIN listing l ON l.id=a.listing_id AND l.platform=a.platform
 WHERE g.kind='ad_group'
 GROUP BY g.platform,g.id
)
SELECT platform, count(*) AS grupos_conocidos,
 count(*) FILTER (WHERE anuncios_conocidos=0) AS grupos_sin_anuncios_observados,
 count(*) FILTER (WHERE anuncios_ep>0) AS grupos_con_ep,
 count(*) FILTER (WHERE anuncios_ep>0 AND publicaciones_ep=0) AS grupos_ep_sin_catalogo,
 count(*) FILTER (WHERE anuncios_ep>0 AND ep_sin_listing>0) AS grupos_ep_catalogo_incompleto,
 count(*) FILTER (WHERE sin_estado>0) AS grupos_con_estado_ausente,
 sum(anuncios_ep) AS anuncios_ep, sum(ep_sin_listing) AS ep_sin_listing,
 sum(sin_estado) AS anuncios_sin_estado, sum(ep_obsoletos) AS ep_obsoletos,
 min(ep_sync_min) AS ep_sync_min, max(ep_sync_max) AS ep_sync_max
FROM por_grupo GROUP BY platform;
ROLLBACK;
