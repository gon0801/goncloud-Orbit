-- Consulta de control de P.3a (guia, "Comprueba"): arma la racha y las
-- ventanas desde las tablas base, SIN el lector. Esperado: `numeros.py`
-- lista las mismas hojas, con los mismos `recortes`, `pedidos_antes` y
-- clics (regla 11: el DoD compara contra esta consulta sobre la misma
-- copia, no contra los numeros del 2026-10-09).
-- Uso: psql "$DSN_COPIA" -v plataforma=amazon_mx -f control.sql
-- La zona fija UTC para que `::date` cuadre con el lector, que convierte
-- a UTC explicito.
SET TIME ZONE 'UTC';

WITH hoy AS (
	SELECT max(m.metric_date) AS d FROM v_metric_latest m JOIN v_hoja_activa h ON h.hoja_id = m.ad_entity_id
	 WHERE h.platform = :'plataforma'
), cambios AS (
	SELECT c.hoja_id, c.confirmado_el, c.bid_antes, c.bid_despues
	  FROM v_cambio_bid c JOIN v_hoja_activa h USING (hoja_id)
	 WHERE h.platform = :'plataforma' AND c.bid_antes IS NOT NULL AND c.bid_despues <> c.bid_antes
), racha AS (
	SELECT c.hoja_id, min(c.confirmado_el)::date AS inicio, count(*) AS recortes FROM cambios c
	 WHERE c.bid_despues < c.bid_antes
	   AND NOT EXISTS (SELECT 1 FROM cambios s WHERE s.hoja_id = c.hoja_id
	                    AND s.bid_despues > s.bid_antes AND s.confirmado_el > c.confirmado_el)
	 GROUP BY 1
), medida AS (
	SELECT r.hoja_id, r.recortes,
	       sum(m.orders) FILTER (WHERE m.metric_date >= r.inicio - 90 AND m.metric_date < r.inicio) AS pedidos_antes,
	       sum(m.clicks) FILTER (WHERE m.metric_date >= r.inicio - 14 AND m.metric_date < r.inicio) AS clics_antes,
	       coalesce(sum(m.clicks) FILTER (WHERE m.metric_date BETWEEN hoy.d - 15 AND hoy.d - 2), 0) AS clics_ahora
	  FROM racha r CROSS JOIN hoy LEFT JOIN v_metric_latest m ON m.ad_entity_id = r.hoja_id GROUP BY 1, 2)
SELECT * FROM medida WHERE pedidos_antes >= 1 AND clics_antes > 0 AND clics_ahora * 10 < clics_antes * 3 ORDER BY 1
