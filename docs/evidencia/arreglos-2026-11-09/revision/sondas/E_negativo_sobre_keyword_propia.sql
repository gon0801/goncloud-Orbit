SET statement_timeout='60s';
SELECT d.id, d.decided_at::date AS decidido, d.search_term, q.estado, q.modo, k.id AS keyword_id, s.status AS keyword_status
  FROM decision d
  JOIN ad_entity k ON k.parent_id = d.ad_entity_id AND k.kind='keyword' AND k.match_type='EXACT'
                  AND lower(btrim(k.keyword_text)) = lower(btrim(d.search_term))
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
  LEFT JOIN apply_queue q ON q.decision_id = d.id
 WHERE d.kind='negative'
 ORDER BY d.id;
