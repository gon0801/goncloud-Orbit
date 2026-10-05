-- JEV ADS 02, tarea 0.2: cobertura de fichas. SOLO LECTURA (orbit_read).
-- Un producto cuenta como cubierto solo si una ficha vigente cubre TODOS sus
-- listings de la plataforma (el asesor cuenta tambien los de anuncios
-- pausados o archivados).
\echo == productos con anuncio ENABLED: total y con ficha vigente que cubre todos sus listings
WITH anunciados AS (
  SELECT DISTINCT pa.platform, l.product_id
    FROM ad_entity pa
    JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad' AND l.product_id IS NOT NULL),
vig AS (
  SELECT f.producto_id, f.plataforma, f.listings FROM jev_ficha_version f
   WHERE f.revisar_antes_de >= now()
     AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)),
cub AS (
  SELECT a.platform, a.product_id,
         NOT EXISTS (SELECT 1 FROM listing l WHERE l.product_id = a.product_id AND l.platform = a.platform
                        AND NOT EXISTS (SELECT 1 FROM vig v WHERE v.producto_id = a.product_id
                                           AND v.plataforma = a.platform AND v.listings @> ARRAY[l.id]::bigint[])) AS cubierto
    FROM anunciados a)
SELECT platform, count(*) AS productos, count(*) FILTER (WHERE cubierto) AS cubiertos FROM cub GROUP BY 1 ORDER BY 1;
\echo == grupos con anuncios ENABLED: todos ligados a producto, y ademas con ficha de todos
WITH pa AS (
  SELECT pa.platform, pa.parent_id AS grupo, l.product_id,
         EXISTS (SELECT 1 FROM jev_ficha_version f
                  WHERE f.producto_id = l.product_id AND f.plataforma = pa.platform
                    AND f.revisar_antes_de >= now() AND f.listings @> ARRAY[pa.listing_id]::bigint[]
                    AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)) AS cubierto
    FROM ad_entity pa
    JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad'),
grp AS (SELECT platform, grupo, bool_and(product_id IS NOT NULL) AS ligado, bool_and(cubierto) AS cubierto FROM pa GROUP BY 1,2)
SELECT platform, count(*) AS grupos, count(*) FILTER (WHERE ligado) AS ligados,
       count(*) FILTER (WHERE ligado AND cubierto) AS ligados_y_cubiertos FROM grp GROUP BY 1 ORDER BY 1;
\echo == versiones de ficha vigentes y fecha en que vence la primera
SELECT plataforma, count(*) AS versiones, count(DISTINCT producto_id) AS productos, min(revisar_antes_de)::date AS primera_en_vencer
  FROM jev_ficha_version f WHERE f.revisar_antes_de >= now()
   AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id) GROUP BY 1 ORDER BY 1;
