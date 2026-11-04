-- P.6: consultas de control de la pantalla de busquedas que gastan sin
-- vender. Parten de las tablas base y no usan el codigo del paso.
-- Uso (las fechas que imprime numeros.py; con --hoy 2026-10-07):
--   psql "$DSN" -v plataforma=amazon_mx -v desde=2026-07-06 -v hasta=2026-10-04 -v minimo=350 -f control.sql
-- minimo = gasto para concluir de la plataforma (settings vigentes; si
-- NULL, 350 en amazon_mx y 36 en amazon_us).

-- 1) ASIN: gasto, clics y pedidos por ASIN en toda la cuenta. Los ASIN
-- de numeros.py son las filas con pedidos 0 y gasto >= minimo (mismo
-- gasto, mismos clics, mismo propio); a medio camino son las demas con
-- pedidos 0 y gasto desde la mitad del minimo.
WITH ultima AS (
    SELECT DISTINCT ON (o.ad_entity_id, o.search_term, o.metric_date)
           o.ad_entity_id, o.search_term, o.is_asin_like, o.cost, o.clicks, o.orders, c.id AS campana_id
      FROM search_term_observation o
      JOIN ad_entity ag ON ag.id = o.ad_entity_id
      JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
     WHERE o.platform = :'plataforma' AND o.metric_date BETWEEN :'desde' AND :'hasta'
     ORDER BY o.ad_entity_id, o.search_term, o.metric_date, o.observed_at DESC, o.source_report_id DESC NULLS LAST
), busqueda AS (
    SELECT ad_entity_id, search_term, campana_id, bool_or(is_asin_like) AS es_asin,
           sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos
      FROM ultima GROUP BY 1, 2, 3
    HAVING bool_and(cost IS NOT NULL AND clicks IS NOT NULL AND orders IS NOT NULL))
SELECT upper(btrim(search_term)) AS asin, sum(gasto) AS gasto, sum(clics) AS clics, sum(pedidos) AS pedidos,
       count(DISTINCT campana_id) AS campanas,
       bool_or(upper(btrim(search_term)) IN (SELECT upper(external_id) FROM listing
                                              WHERE platform IN ('amazon_mx', 'amazon_us'))) AS propio
  FROM busqueda WHERE es_asin GROUP BY 1 ORDER BY 2 DESC, 1;

-- 2) Referencia de azar: insumos sumados por busqueda. visto_pct y
-- azar_pct son los de numeros.py (si pedidos o clics es 0, el contrato
-- da None en las dos partes).
WITH ultima AS (
    SELECT DISTINCT ON (o.ad_entity_id, o.search_term, o.metric_date)
           o.ad_entity_id, o.search_term, o.is_asin_like, o.cost, o.clicks, o.orders, c.id AS campana_id
      FROM search_term_observation o
      JOIN ad_entity ag ON ag.id = o.ad_entity_id
      JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
     WHERE o.platform = :'plataforma' AND o.metric_date BETWEEN :'desde' AND :'hasta'
     ORDER BY o.ad_entity_id, o.search_term, o.metric_date, o.observed_at DESC, o.source_report_id DESC NULLS LAST
), busqueda AS (
    SELECT ad_entity_id, search_term, campana_id, bool_or(is_asin_like) AS es_asin,
           sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos
      FROM ultima GROUP BY 1, 2, 3
    HAVING bool_and(cost IS NOT NULL AND clicks IS NOT NULL AND orders IS NOT NULL))
, cuenta AS (
    SELECT sum(gasto) AS gasto, sum(clics) AS clics, sum(pedidos) AS pedidos,
           sum(pedidos) / NULLIF(sum(clics), 0) AS p FROM busqueda)
SELECT c.gasto, c.clics, c.pedidos, sum(b.gasto) FILTER (WHERE b.pedidos = 0) AS gasto_sin_pedido,
       round(100 * sum(b.gasto) FILTER (WHERE b.pedidos = 0) / NULLIF(c.gasto, 0)) AS visto_pct,
       round(100 * sum(b.gasto * power(1 - c.p, b.clics)) / NULLIF(c.gasto, 0)) AS azar_pct
  FROM busqueda b CROSS JOIN cuenta c GROUP BY c.gasto, c.clics, c.pedidos, c.p;

-- 3) Palabras sin pedido con al menos la mitad del minimo. Las
-- palabras de numeros.py son las filas con gasto >= minimo (mismas
-- busquedas, clics y gasto); a medio camino son las demas filas.
WITH ultima AS (
    SELECT DISTINCT ON (o.ad_entity_id, o.search_term, o.metric_date)
           o.ad_entity_id, o.search_term, o.is_asin_like, o.cost, o.clicks, o.orders, c.id AS campana_id
      FROM search_term_observation o
      JOIN ad_entity ag ON ag.id = o.ad_entity_id
      JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
      JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
     WHERE o.platform = :'plataforma' AND o.metric_date BETWEEN :'desde' AND :'hasta'
     ORDER BY o.ad_entity_id, o.search_term, o.metric_date, o.observed_at DESC, o.source_report_id DESC NULLS LAST
), busqueda AS (
    SELECT ad_entity_id, search_term, campana_id, bool_or(is_asin_like) AS es_asin,
           sum(cost) AS gasto, sum(clicks) AS clics, sum(orders) AS pedidos
      FROM ultima GROUP BY 1, 2, 3
    HAVING bool_and(cost IS NOT NULL AND clicks IS NOT NULL AND orders IS NOT NULL))
, ficha AS (
    SELECT b.ad_entity_id, b.search_term, b.gasto, b.clics, b.pedidos, t.n, t.m[1] AS palabra
      FROM busqueda b
     CROSS JOIN LATERAL regexp_matches(lower(b.search_term), '[a-záéíóúñü0-9]+', 'g') WITH ORDINALITY AS t(m, n)
     WHERE NOT b.es_asin
), entrada AS (
    SELECT DISTINCT ad_entity_id, search_term, gasto, clics, pedidos, palabra FROM ficha
     WHERE length(palabra) > 2 AND palabra <> ALL (string_to_array(
           'de la el los las para y con en un una por del a al que the of for and to in on with', ' '))
    UNION ALL
    SELECT DISTINCT a.ad_entity_id, a.search_term, a.gasto, a.clics, a.pedidos, a.palabra || ' ' || s.palabra
      FROM ficha a JOIN ficha s ON s.ad_entity_id = a.ad_entity_id AND s.search_term = a.search_term AND s.n = a.n + 1)
SELECT palabra, count(DISTINCT search_term) AS busquedas, sum(clics) AS clics, sum(gasto) AS gasto
  FROM entrada GROUP BY 1 HAVING sum(pedidos) = 0 AND sum(gasto) >= :'minimo'::numeric / 2 ORDER BY 4 DESC, 1;
