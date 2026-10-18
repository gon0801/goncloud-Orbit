-- JEV ADS 03 B.2: forma mas compleja de una senal ajena en produccion.
-- Solo lectura. Sin nombres de cliente: ids internos, conteos y la forma del JSON.
-- El termino no ASCII se pide en el encargo; no se listan otros terminos.

\pset pager off

SELECT 'roster_ajena_mas_productos' AS medida,
       v.plataforma::text,
       v.ad_group_id,
       v.senal_id::text,
       v.vigente,
       s.gasto::text,
       s.moneda::text,
       jsonb_array_length(r.miembros->'censo') AS productos,
       jsonb_typeof(r.miembros->'censo'->0) AS tipo_miembro,
       jsonb_typeof(r.miembros->'censo'->0->0) AS tipo_anuncios,
       jsonb_array_length(r.miembros->'censo'->0->0) AS n_anuncios_primero,
       jsonb_typeof(r.miembros->'censo'->0->1) AS tipo_producto_id,
       jsonb_typeof(r.miembros->'censo'->0->2) AS tipo_listings,
       jsonb_typeof(r.miembros->'censo'->0->3) AS tipo_estados,
       jsonb_typeof(r.miembros->'fichas') AS tipo_fichas,
       jsonb_array_length(r.miembros->'fichas') AS n_fichas,
       (SELECT count(*) FROM jsonb_array_elements(r.miembros->'fichas') f WHERE f = 'null'::jsonb) AS fichas_null,
       cardinality(r.ficha_version_ids) AS ficha_version_ids
  FROM jev_senal_vigente v
  JOIN jev_senal s ON s.id = v.senal_id
  JOIN jev_roster r ON r.sha256 = s.roster_sha256
 WHERE s.lectura = 'ajena'
 ORDER BY jsonb_array_length(r.miembros->'censo') DESC, v.senal_id
 LIMIT 1;

SELECT 'producto_sin_ficha' AS medida,
       v.plataforma::text,
       (r.miembros->'censo'->ord.i->1)::text AS producto_id,
       (r.miembros->'fichas'->ord.i)::text AS ficha
  FROM jev_senal_vigente v
  JOIN jev_senal s ON s.id = v.senal_id
  JOIN jev_roster r ON r.sha256 = s.roster_sha256
  CROSS JOIN LATERAL generate_series(0, jsonb_array_length(r.miembros->'censo') - 1) AS ord(i)
 WHERE s.lectura = 'ajena'
   AND r.miembros->'fichas'->ord.i = 'null'::jsonb
 LIMIT 1;

SELECT 'termino_no_ascii' AS medida,
       v.plataforma::text,
       v.vigente,
       v.termino,
       octet_length(v.termino) AS bytes,
       char_length(v.termino) AS chars
  FROM jev_senal_vigente v
  JOIN jev_senal s ON s.id = v.senal_id
 WHERE s.lectura = 'ajena'
   AND v.termino ~ '[^\x01-\x7F]'
 ORDER BY v.plataforma, v.senal_id
 LIMIT 3;

SELECT 'sin_ficha_en_ajena' AS medida,
       count(*) AS miembros_sin_ficha
  FROM jev_senal s
  JOIN jev_roster r ON r.sha256 = s.roster_sha256
  CROSS JOIN LATERAL generate_series(0, jsonb_array_length(r.miembros->'censo') - 1) AS ord(i)
 WHERE s.lectura = 'ajena'
   AND r.miembros->'fichas'->ord.i = 'null'::jsonb;

SELECT 'una_senal_por_plataforma' AS medida,
       v.plataforma::text,
       v.lectura,
       v.vigente,
       v.senal_id::text,
       octet_length(v.termino) AS bytes_termino,
       (v.termino ~ '[^\x01-\x7F]') AS no_ascii,
       jsonb_array_length(r.miembros->'censo') AS productos
  FROM jev_senal_vigente v
  JOIN jev_senal s ON s.id = v.senal_id
  JOIN jev_roster r ON r.sha256 = s.roster_sha256
 WHERE v.lectura = 'ajena' AND v.vigente
   AND v.senal_id IN (
       SELECT DISTINCT ON (plataforma) senal_id
         FROM jev_senal_vigente
        WHERE lectura = 'ajena' AND vigente
        ORDER BY plataforma, senal_id
   );

SELECT 'senal_por_plataforma' AS medida,
       v.plataforma::text,
       v.lectura,
       v.vigente,
       count(*) AS senales,
       bool_or(v.termino ~ '[^\x01-\x7F]') AS hay_no_ascii
  FROM jev_senal_vigente v
 GROUP BY v.plataforma, v.lectura, v.vigente
 ORDER BY v.plataforma, v.lectura, v.vigente;
