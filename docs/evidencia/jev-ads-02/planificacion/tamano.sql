-- JEV ADS 02, planificacion: tamano de cada opcion. SOLO LECTURA (orbit_read).
-- Ventana de cortes como la del motor: 30 dias inclusive que terminan en
-- D-10 (app/optimizer/windows.py: DIAS_VENTANA, DIAS_MADUREZ_CORTES).
-- "Ficha vigente" como app/jev_catalogo.ficha_vigente: sin revocacion, sin
-- vencer y que cubra ESE listing.
\echo == 1. cola historica completa por tipo, modo y estado
SELECT platform, kind, modo, estado, count(*) FROM apply_queue GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;
\echo == 2. decisiones negative/harvest historicas: filas y pares grupo-busqueda distintos
SELECT e.platform, d.kind, count(*) AS decisiones,
       count(DISTINCT (d.ad_entity_id, d.search_term)) AS pares_distintos,
       count(DISTINCT d.search_term) AS busquedas_distintas
  FROM decision d JOIN ad_entity e ON e.id = d.ad_entity_id
 WHERE d.kind IN ('negative','harvest') GROUP BY 1,2 ORDER BY 1,2;
\echo == 3. anuncios ENABLED: ligados a producto y cubiertos por ficha vigente
WITH pa AS (
  SELECT pa.platform, pa.id, pa.parent_id AS grupo, pa.listing_id, l.product_id,
         EXISTS (SELECT 1 FROM jev_ficha_version f
                  WHERE f.producto_id = l.product_id AND f.plataforma = pa.platform
                    AND f.revisar_antes_de >= now() AND f.listings @> ARRAY[pa.listing_id]::bigint[]
                    AND NOT EXISTS (SELECT 1 FROM jev_ficha_revocacion r WHERE r.ficha_version_id = f.id)) AS cubierto
    FROM ad_entity pa
    JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad'),
prod AS (SELECT platform, product_id, bool_and(cubierto) AS cubierto FROM pa WHERE product_id IS NOT NULL GROUP BY 1,2),
grp AS (SELECT platform, grupo, count(DISTINCT product_id) AS productos,
               bool_and(product_id IS NOT NULL) AS ligado, bool_and(cubierto) AS cubierto FROM pa GROUP BY 1,2)
SELECT a.platform, a.anuncios, a.sin_producto, p.productos, p.productos_cubiertos,
       g.grupos, g.grupos_ligados, g.grupos_cubiertos, g.mediana_productos, g.max_productos
  FROM (SELECT platform, count(*) AS anuncios, count(*) FILTER (WHERE product_id IS NULL) AS sin_producto FROM pa GROUP BY 1) a
  JOIN (SELECT platform, count(*) AS productos, count(*) FILTER (WHERE cubierto) AS productos_cubiertos FROM prod GROUP BY 1) p USING (platform)
  JOIN (SELECT platform, count(*) AS grupos, count(*) FILTER (WHERE ligado) AS grupos_ligados,
               count(*) FILTER (WHERE ligado AND cubierto) AS grupos_cubiertos,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY productos) AS mediana_productos, max(productos) AS max_productos
          FROM grp GROUP BY 1) g USING (platform)
 ORDER BY 1;
\echo == 4. pares grupo-busqueda de texto en la ventana de cortes (ultima observacion por dia)
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date)
         platform, ad_entity_id, search_term, metric_date, cost, clicks, orders
    FROM search_term_observation
   WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
t AS (SELECT platform, ad_entity_id, search_term, sum(cost) AS cost, sum(clicks) AS clicks,
             sum(orders) AS orders, bool_or(orders IS NULL) AS orders_nulo
        FROM ult GROUP BY 1,2,3 HAVING sum(cost) > 0)
SELECT platform, count(*) AS pares_con_gasto,
       count(*) FILTER (WHERE orders_nulo) AS con_orders_nulo,
       count(*) FILTER (WHERE orders = 0 AND NOT orders_nulo) AS sin_venta,
       count(*) FILTER (WHERE orders = 0 AND NOT orders_nulo AND clicks >= 3) AS sin_venta_3_clics,
       round(100 * sum(cost) FILTER (WHERE orders = 0 AND NOT orders_nulo) / sum(cost), 1) AS pct_gasto_sin_venta,
       round(100 * sum(cost) FILTER (WHERE orders = 0 AND NOT orders_nulo AND clicks >= 3) / sum(cost), 1) AS pct_gasto_sin_venta_3_clics
  FROM t GROUP BY 1 ORDER BY 1;
\echo == 5. llamadas para evaluar las busquedas sin venta con 3 clics o mas contra los productos de su grupo
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date)
         platform, ad_entity_id, search_term, metric_date, cost, clicks, orders
    FROM search_term_observation
   WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC),
c AS (SELECT platform, ad_entity_id, search_term FROM ult GROUP BY 1,2,3
      HAVING sum(cost) > 0 AND sum(orders) = 0 AND NOT bool_or(orders IS NULL) AND sum(clicks) >= 3),
gp AS (
  SELECT DISTINCT pa.platform, pa.parent_id AS grupo, l.product_id
    FROM ad_entity pa
    JOIN ad_entity_state s ON s.ad_entity_id = pa.id AND s.status = 'ENABLED'
    JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
   WHERE pa.kind = 'product_ad' AND l.product_id IS NOT NULL)
SELECT c.platform, count(DISTINCT c.search_term) AS busquedas_distintas,
       count(*) AS pares_sin_reutilizar, count(DISTINCT (c.search_term, gp.product_id)) AS pares_reutilizando
  FROM c JOIN gp ON gp.platform = c.platform AND gp.grupo = c.ad_entity_id GROUP BY 1 ORDER BY 1;
\echo == 6. bibliotecas de la fabrica
SELECT 'keyword_biblioteca' AS tabla, count(*) FROM keyword_biblioteca UNION ALL SELECT 'negative_biblioteca', count(*) FROM negative_biblioteca;
