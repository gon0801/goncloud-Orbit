-- X.1 paso 7: lee el primer ciclo. SOLO LECTURA, no frena nada. El lead
-- la corre a la manana siguiente del encendido. Consultas literales del
-- paso. Para garantizar solo-lectura, correr con
-- PGOPTIONS='--default-transaction-read-only=on' o en una transaccion
-- READ ONLY: cualquier escritura fallaria en vez de mutar.

SELECT c.platform, c.id, c.status, count(d.id) FILTER (WHERE d.kind = 'bid') AS bids,
	count(d.id) FILTER (WHERE d.kind = 'bid' AND d.inputs ? 'caso') AS con_caso
	FROM optimizer_cycle c LEFT JOIN decision d ON d.cycle_id = c.id
	WHERE c.started_at >= current_date AND c.platform IN ('amazon_mx', 'amazon_us') GROUP BY 1, 2, 3;

SELECT t.procedencia, t.target_acos_pct, count(*) FROM target_acos_ciclo t JOIN optimizer_cycle c
	ON c.id = t.cycle_id WHERE c.platform = 'amazon_us' AND c.started_at >= current_date GROUP BY 1, 2;

SELECT c.platform, d.inputs ->> 'motivo' AS motivo, count(*) FROM decision d JOIN optimizer_cycle c
	ON c.id = d.cycle_id WHERE d.kind = 'bid' AND d.new_value < d.old_value
	AND c.started_at >= current_date GROUP BY 1, 2;
