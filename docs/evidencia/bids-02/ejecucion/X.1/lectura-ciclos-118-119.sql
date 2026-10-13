SELECT c.platform, c.id, c.status, count(d.id) FILTER (WHERE d.kind = 'bid') AS bids,
	count(d.id) FILTER (WHERE d.kind = 'bid' AND d.inputs ? 'caso') AS con_caso
	FROM optimizer_cycle c LEFT JOIN decision d ON d.cycle_id = c.id
	WHERE c.id IN (118, 119) GROUP BY 1, 2, 3;
SELECT t.procedencia, t.target_acos_pct, count(*) FROM target_acos_ciclo t JOIN optimizer_cycle c
	ON c.id = t.cycle_id WHERE c.platform = 'amazon_us' AND c.id IN (118, 119) GROUP BY 1, 2;
SELECT c.platform, d.inputs ->> 'motivo' AS motivo, count(*) FROM decision d JOIN optimizer_cycle c
	ON c.id = d.cycle_id WHERE d.kind = 'bid' AND d.new_value < d.old_value
	AND c.id IN (118, 119) GROUP BY 1, 2;
