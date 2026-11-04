-- JEV ADS 02, tarea 0.2: cobertura de fichas. SOLO LECTURA (orbit_read).
-- Cuenta como el asesor (app/jev_asesor._enriquecer): para CADA listing del
-- producto en la plataforma se elige la ficha vigente que lo cubre (sin
-- revocar, sin vencer, la de observado_at mas reciente); el producto cuenta
-- como cubierto solo si TODOS sus listings eligen LA MISMA ficha. Dos fichas
-- distintas que entre las dos cubren todo NO cuentan.
\echo == productos con anuncio ENABLED: total y cubiertos por una sola ficha vigente
WITH anunciados AS (
  SELECT DISTINCT pa.platform, l.product_id
    FROM ad_entity pa
    JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad' AND l.product_id IS NOT NULL),
elegida AS (
  SELECT a.platform, a.product_id, l.id AS listing_id,
         (SELECT f.id FROM jev_ficha_version f
           WHERE f.producto_id = a.product_id AND f.plataforma = a.platform
             AND f.revisar_antes_de >= now() AND f.listings @> ARRAY[l.id]::bigint[]
             AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)
           ORDER BY f.observado_at DESC, f.created_at DESC LIMIT 1) AS ficha
    FROM anunciados a JOIN listing l ON l.product_id = a.product_id AND l.platform = a.platform),
cub AS (
  SELECT platform, product_id,
         (count(*) FILTER (WHERE ficha IS NULL) = 0 AND count(DISTINCT ficha) = 1) AS cubierto
    FROM elegida GROUP BY 1, 2)
SELECT platform, count(*) AS productos, count(*) FILTER (WHERE cubierto) AS cubiertos FROM cub GROUP BY 1 ORDER BY 1;
\echo == grupos con anuncios ENABLED: todos ligados a producto, y ademas todos sus productos cubiertos
WITH pa AS (
  SELECT pa.platform, pa.parent_id AS grupo, l.product_id
    FROM ad_entity pa
    JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad'),
elegida AS (
  SELECT DISTINCT p.platform, p.product_id, l.id AS listing_id,
         (SELECT f.id FROM jev_ficha_version f
           WHERE f.producto_id = p.product_id AND f.plataforma = p.platform
             AND f.revisar_antes_de >= now() AND f.listings @> ARRAY[l.id]::bigint[]
             AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)
           ORDER BY f.observado_at DESC, f.created_at DESC LIMIT 1) AS ficha
    FROM (SELECT DISTINCT platform, product_id FROM pa WHERE product_id IS NOT NULL) p
    JOIN listing l ON l.product_id = p.product_id AND l.platform = p.platform),
cub AS (SELECT platform, product_id, (count(*) FILTER (WHERE ficha IS NULL) = 0 AND count(DISTINCT ficha) = 1) AS cubierto
          FROM elegida GROUP BY 1, 2),
grp AS (SELECT pa.platform, pa.grupo, bool_and(pa.product_id IS NOT NULL) AS ligado,
               bool_and(coalesce(c.cubierto, false)) AS cubierto
          FROM pa LEFT JOIN cub c ON c.platform = pa.platform AND c.product_id = pa.product_id GROUP BY 1, 2)
SELECT platform, count(*) AS grupos, count(*) FILTER (WHERE ligado) AS ligados,
       count(*) FILTER (WHERE ligado AND cubierto) AS ligados_y_cubiertos FROM grp GROUP BY 1 ORDER BY 1;
\echo == versiones de ficha vigentes y fecha en que vence la primera
SELECT plataforma, count(*) AS versiones, count(DISTINCT producto_id) AS productos, min(revisar_antes_de)::date AS primera_en_vencer
  FROM jev_ficha_version f WHERE f.revisar_antes_de >= now()
   AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id) GROUP BY 1 ORDER BY 1;
