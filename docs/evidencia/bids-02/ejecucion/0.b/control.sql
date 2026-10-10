-- Consulta de control de 0.b (guia, "Comprueba"): arma la hoja activa desde
-- las tablas base, SIN la vista. Esperado: `difieren` da 0 y `control` es
-- igual a `vista` (regla 11: el DoD compara contra esta consulta sobre la
-- misma copia, no contra los numeros del 2026-10-09).
WITH control AS (
	SELECT k.id FROM ad_entity k
	  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
	  JOIN ad_entity c ON c.id = ag.parent_id AND c.kind = 'campaign'
	 WHERE k.kind IN ('keyword', 'product_target')
	   AND (SELECT status FROM ad_entity_state WHERE ad_entity_id = k.id) = 'ENABLED'
	   AND (SELECT status FROM ad_entity_state WHERE ad_entity_id = ag.id) = 'ENABLED'
	   AND (SELECT status FROM ad_entity_state WHERE ad_entity_id = c.id) = 'ENABLED')
SELECT (SELECT count(*) FROM control) AS control, (SELECT count(*) FROM v_hoja_activa) AS vista,
       (SELECT count(*) FROM control x FULL JOIN v_hoja_activa v ON v.hoja_id = x.id
         WHERE x.id IS NULL OR v.hoja_id IS NULL) AS difieren
