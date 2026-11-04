WITH rv AS (SELECT solicitud, plan_canonico->>'grupo_id' AS grupo, plan_canonico->>'plataforma' AS plat, censos FROM jev_revision WHERE solicitud IN ('6fd47bb8-00a1-4cf1-a774-b6b0f2d0fe70','48e4796e-790a-4a1e-b035-3e52c706a9b7','e543d308-27d9-4a79-9fcf-8c8127d9d9f8','056241ca-4d39-4287-ad7d-ee200c43f530','0d15f711-f55e-4a14-8de2-d9d34b013f81','6fb1f64f-ba86-4dc5-b32e-b3f65d0a8dfc','3c78e1d7-ee46-4175-b327-c35f07fc2f15','7688b6da-e4a4-40cf-b2d3-16c235dbcfbd','bdd41e9e-1806-4657-aa90-a503f4f67189','16fb280b-3e09-4518-a706-e9c07813434a','569e1abc-7291-4601-8e01-bb544aaa99dc','78be8f8c-82ab-4fdb-8a6e-64d6faf85630','1c4ee084-412a-4c8e-a616-25e2efbaf743','dfb874a9-5a8e-4ad4-8b27-daaa056ad55a','c6c348d0-ad07-49db-9e5c-1eeabcb36478')),
tt AS (SELECT rv.solicitud, rv.grupo, rv.plat, t.t AS termino, encode(sha256(convert_to(t.t, 'UTF8')), 'hex') AS h
         FROM rv, jsonb_array_elements_text(rv.censos->'terminos') AS t(t))
SELECT 'R', tt.plat, tt.grupo, tt.termino,
       count(*) FILTER (WHERE e.respuesta->>'relacion' = 'satisface') AS si,
       count(*) FILTER (WHERE e.respuesta->>'relacion' = 'no_satisface') AS no,
       count(*) FILTER (WHERE e.respuesta->>'relacion' = 'informacion_insuficiente') AS insuf,
       count(*) FILTER (WHERE e.error IS NOT NULL) AS fallo,
       coalesce(sum((e.usage->>'input_tokens')::int),0) AS tok_in
  FROM tt LEFT JOIN jev_par_evento e ON e.revision_id = tt.solicitud AND e.termino_sha256 = tt.h AND e.tipo = 'resultado'
 GROUP BY 2,3,4 ORDER BY 2,3,4;
