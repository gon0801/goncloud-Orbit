-- JEV ADS 02, tarea 0.3. SOLO LECTURA (orbit_read).
-- Usage del prototipo: las 15 solicitudes de
-- docs/evidencia/jev-ads-02/diseno/consultas/prototipo-jev-analisis.sql
-- La primera fila repite el cruce de esa consulta (termino del censo contra
-- resultado) y suma input_tokens y output_tokens. La segunda suma directo
-- sobre jev_par_evento de esas solicitudes. Solo agregados.
\echo == usage del prototipo
WITH ids(solicitud) AS (
  VALUES
    ('6fd47bb8-00a1-4cf1-a774-b6b0f2d0fe70'::uuid),
    ('48e4796e-790a-4a1e-b035-3e52c706a9b7'::uuid),
    ('e543d308-27d9-4a79-9fcf-8c8127d9d9f8'::uuid),
    ('056241ca-4d39-4287-ad7d-ee200c43f530'::uuid),
    ('0d15f711-f55e-4a14-8de2-d9d34b013f81'::uuid),
    ('6fb1f64f-ba86-4dc5-b32e-b3f65d0a8dfc'::uuid),
    ('3c78e1d7-ee46-4175-b327-c35f07fc2f15'::uuid),
    ('7688b6da-e4a4-40cf-b2d3-16c235dbcfbd'::uuid),
    ('bdd41e9e-1806-4657-aa90-a503f4f67189'::uuid),
    ('16fb280b-3e09-4518-a706-e9c07813434a'::uuid),
    ('569e1abc-7291-4601-8e01-bb544aaa99dc'::uuid),
    ('78be8f8c-82ab-4fdb-8a6e-64d6faf85630'::uuid),
    ('1c4ee084-412a-4c8e-a616-25e2efbaf743'::uuid),
    ('dfb874a9-5a8e-4ad4-8b27-daaa056ad55a'::uuid),
    ('c6c348d0-ad07-49db-9e5c-1eeabcb36478'::uuid)
),
rv AS (
  SELECT solicitud, plan_canonico->>'grupo_id' AS grupo,
         plan_canonico->>'plataforma' AS plat, censos
    FROM jev_revision
   WHERE solicitud IN (SELECT solicitud FROM ids)
),
tt AS (
  SELECT rv.solicitud, rv.plat, rv.grupo, t.t AS termino,
         encode(sha256(convert_to(t.t, 'UTF8')), 'hex') AS h
    FROM rv,
         jsonb_array_elements_text(rv.censos->'terminos') AS t(t)
),
por_termino AS (
  SELECT coalesce(sum((e.usage->>'input_tokens')::numeric), 0) AS tok_in,
         coalesce(sum((e.usage->>'output_tokens')::numeric), 0) AS tok_out,
         count(e.id) AS eventos
    FROM tt
    LEFT JOIN jev_par_evento e
      ON e.revision_id = tt.solicitud
     AND e.termino_sha256 = tt.h
     AND e.tipo = 'resultado'
   GROUP BY tt.plat, tt.grupo, tt.termino
),
directa AS (
  SELECT count(DISTINCT revision_id) AS filas,
         sum((usage->>'input_tokens')::numeric) AS tok_in,
         sum((usage->>'output_tokens')::numeric) AS tok_out,
         count(*) AS eventos,
         count(*) FILTER (WHERE respuesta IS NOT NULL) AS con_respuesta,
         count(*) FILTER (WHERE error IS NOT NULL) AS con_error,
         count(*) FILTER (WHERE usage IS NULL) AS sin_usage
    FROM jev_par_evento
   WHERE tipo = 'resultado'
     AND revision_id IN (SELECT solicitud FROM ids)
),
huecos AS (
  SELECT count(*) AS sin_termino
    FROM jev_par_evento e
   WHERE e.tipo = 'resultado'
     AND e.revision_id IN (SELECT solicitud FROM ids)
     AND NOT EXISTS (
       SELECT 1 FROM tt
        WHERE tt.solicitud = e.revision_id AND tt.h = e.termino_sha256
     )
),
repetidos AS (
  SELECT count(*) AS n
    FROM (
      SELECT solicitud, termino
        FROM tt
       GROUP BY solicitud, termino
      HAVING count(*) > 1
    ) d
)
SELECT 'analisis' AS fuente,
       count(*)::bigint AS filas,
       sum(tok_in) AS input_tokens,
       sum(tok_out) AS output_tokens,
       sum(eventos)::bigint AS eventos,
       NULL::bigint AS con_respuesta,
       NULL::bigint AS con_error,
       NULL::bigint AS sin_usage,
       (SELECT sin_termino FROM huecos) AS sin_termino_en_censo,
       (SELECT n FROM repetidos) AS terminos_repetidos
  FROM por_termino
UNION ALL
SELECT 'directa',
       filas,
       tok_in,
       tok_out,
       eventos,
       con_respuesta,
       con_error,
       sin_usage,
       NULL,
       NULL
  FROM directa;
