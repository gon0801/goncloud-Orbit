\echo == muestra contra los dos contratos
WITH muestra AS (
  SELECT p->>'termino_sha256' AS termino_sha256,
         p->>'ficha_version_id' AS ficha_version_id,
         p->>'relacion_normal' AS congelada
    FROM jev_revision r
    CROSS JOIN LATERAL jsonb_array_elements(r.censos->'pares') AS p
   WHERE r.solicitud = '952333c1-0cdd-53c4-8a88-aea65124c2d2'
),
normal AS (
  SELECT DISTINCT ON (termino_sha256, ficha_version_id)
         termino_sha256,
         ficha_version_id::text AS ficha_version_id,
         respuesta->>'relacion' AS relacion
    FROM jev_par_evento
   WHERE tipo = 'resultado'
     AND respuesta IS NOT NULL
     AND contrato_sha256 = '0fe322c8aa063997c65e101a8835e616cf00275dd022f0061e30ef227c7443e6'
     AND revision_id <> '952333c1-0cdd-53c4-8a88-aea65124c2d2'
   ORDER BY termino_sha256, ficha_version_id, created_at, id
),
invertido AS (
  SELECT termino_sha256,
         ficha_version_id::text AS ficha_version_id,
         respuesta->>'relacion' AS relacion,
         (respuesta IS NULL) AS fallo
    FROM jev_par_evento
   WHERE revision_id = '952333c1-0cdd-53c4-8a88-aea65124c2d2'
     AND tipo = 'resultado'
     AND contrato_sha256 = '9a155982ddacdb4445fa2f3444a56a3fc6b814e9a23bef520da55753e6b62700'
)
SELECT count(*) AS muestra,
       count(*) FILTER (WHERE m.congelada = 'satisface') AS congelada_si,
       count(*) FILTER (WHERE m.congelada = 'no_satisface') AS congelada_no,
       count(*) FILTER (WHERE m.congelada IS DISTINCT FROM n.relacion) AS congelada_difiere_del_primer_exito,
       count(*) FILTER (
         WHERE m.congelada IN ('satisface', 'no_satisface')
           AND i.relacion IN ('satisface', 'no_satisface')
       ) AS ambos_decisivos,
       count(*) FILTER (
         WHERE m.congelada IN ('satisface', 'no_satisface')
           AND i.relacion IN ('satisface', 'no_satisface')
           AND m.congelada = i.relacion
       ) AS iguales,
       count(*) FILTER (
         WHERE m.congelada IN ('satisface', 'no_satisface')
           AND i.relacion IN ('satisface', 'no_satisface')
           AND m.congelada <> i.relacion
       ) AS cambiaron,
       count(*) FILTER (
         WHERE m.congelada = 'satisface' AND i.relacion = 'no_satisface'
       ) AS si_a_no,
       count(*) FILTER (
         WHERE m.congelada = 'no_satisface' AND i.relacion = 'satisface'
       ) AS no_a_si,
       count(*) FILTER (WHERE i.relacion = 'informacion_insuficiente') AS abstencion_invertida,
       count(*) FILTER (WHERE COALESCE(i.fallo, true)) AS fallo_invertido,
       count(*) FILTER (WHERE n.relacion IS NULL) AS sin_normal
  FROM muestra m
  LEFT JOIN normal n USING (termino_sha256, ficha_version_id)
  LEFT JOIN invertido i USING (termino_sha256, ficha_version_id);

\echo == cruce de respuestas
WITH muestra AS (
  SELECT p->>'termino_sha256' AS termino_sha256,
         p->>'ficha_version_id' AS ficha_version_id,
         p->>'relacion_normal' AS congelada
    FROM jev_revision r
    CROSS JOIN LATERAL jsonb_array_elements(r.censos->'pares') AS p
   WHERE r.solicitud = '952333c1-0cdd-53c4-8a88-aea65124c2d2'
),
invertido AS (
  SELECT termino_sha256,
         ficha_version_id::text AS ficha_version_id,
         respuesta->>'relacion' AS relacion
    FROM jev_par_evento
   WHERE revision_id = '952333c1-0cdd-53c4-8a88-aea65124c2d2'
     AND tipo = 'resultado'
)
SELECT m.congelada, i.relacion AS invertida, count(*) AS pares
  FROM muestra m
  LEFT JOIN invertido i USING (termino_sha256, ficha_version_id)
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo == usage y reloj de la medicion
SELECT count(*) FILTER (WHERE tipo = 'intencion') AS intenciones,
       count(*) FILTER (WHERE tipo = 'resultado') AS resultados,
       count(*) FILTER (WHERE tipo = 'resultado' AND respuesta IS NOT NULL) AS con_respuesta,
       count(*) FILTER (WHERE tipo = 'resultado' AND error IS NOT NULL) AS con_error,
       count(*) FILTER (WHERE tipo = 'resultado' AND usage IS NULL) AS sin_usage,
       count(*) FILTER (
         WHERE tipo = 'resultado'
           AND usage IS NOT NULL
           AND ((usage->>'input_tokens') IS NULL OR (usage->>'output_tokens') IS NULL)
       ) AS usage_incompleto,
       sum((usage->>'input_tokens')::numeric) FILTER (WHERE tipo = 'resultado') AS input_tokens,
       sum((usage->>'output_tokens')::numeric) FILTER (WHERE tipo = 'resultado') AS output_tokens,
       min(created_at) FILTER (WHERE tipo = 'intencion') AS desde,
       max(created_at) FILTER (WHERE tipo = 'resultado') AS hasta
  FROM jev_par_evento
 WHERE revision_id = '952333c1-0cdd-53c4-8a88-aea65124c2d2';

\echo == pares decisivos del contrato vigente sobre ficha que gana
SELECT count(*) AS elegibles,
       count(*) FILTER (WHERE e.relacion = 'satisface') AS si,
       count(*) FILTER (WHERE e.relacion = 'no_satisface') AS no
  FROM (
    SELECT DISTINCT ON (termino_sha256, ficha_version_id)
           termino_sha256,
           ficha_version_id,
           respuesta->>'relacion' AS relacion
      FROM jev_par_evento
     WHERE tipo = 'resultado'
       AND respuesta IS NOT NULL
       AND contrato_sha256 = '0fe322c8aa063997c65e101a8835e616cf00275dd022f0061e30ef227c7443e6'
     ORDER BY termino_sha256, ficha_version_id, created_at, id
  ) e
  JOIN (
    SELECT v.id
      FROM jev_ficha_version v
      LEFT JOIN jev_ficha_revocacion rv ON rv.ficha_version_id = v.id
     WHERE rv.id IS NULL
       AND v.revisar_antes_de >= now()
       AND NOT EXISTS (
         SELECT 1
           FROM unnest(v.listings) AS l(id)
           JOIN jev_ficha_version o
             ON o.producto_id = v.producto_id
            AND o.plataforma = v.plataforma
            AND o.id <> v.id
            AND o.listings @> ARRAY[l.id]::bigint[]
            AND o.revisar_antes_de >= now()
            AND NOT EXISTS (
              SELECT 1 FROM jev_ficha_revocacion ro
               WHERE ro.ficha_version_id = o.id
            )
            AND (o.observado_at, o.created_at) > (v.observado_at, v.created_at)
       )
  ) g ON g.id = e.ficha_version_id
 WHERE e.relacion IN ('satisface', 'no_satisface');
