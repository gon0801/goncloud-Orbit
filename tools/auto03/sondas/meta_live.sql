-- Bloque F: la meta por producto queda en vivo en los dos mercados. Solo cuenta los ciclos cuyas
-- evaluaciones se calcularon con la meta de su plataforma en live (config_version de la
-- evaluacion); un ciclo con la meta en shadow nunca se compara. En esos ciclos, las filas
-- margen_producto de target_acos_ciclo son exactamente las hojas cuya ResolucionMargen congelada
-- en resultado.sombra_hojas trae aplicado (las hojas de grupo apto sin target de goal: el ciclo
-- solo las congela ahi). Una abstencion sin ancla o sin destino deja aplicado NULL y no cuenta.
SELECT CASE WHEN EXISTS (
         SELECT 1 FROM pg_constraint
          WHERE conname = 'target_acos_ciclo_procedencia_check'
            AND pg_get_constraintdef(oid) LIKE '%margen_producto%')
       THEN 't' ELSE 'f' END
       || '|el CHECK de procedencia de target_acos_ciclo admite margen_producto';
SELECT CASE WHEN EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'decision'
            AND column_name = 'evaluacion_producto_id')
       THEN 't' ELSE 'f' END
       || '|existe decision.evaluacion_producto_id';
SELECT CASE WHEN s->>'ads_producto_meta_amazon_mx' = 'live'
             AND s->>'ads_producto_meta_amazon_us' = 'live'
       THEN 't' ELSE 'f' END
       || '|ultima config_version con la meta de amazon_mx y amazon_us en live'
  FROM (SELECT coalesce((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                        '{}'::jsonb) AS s) c;
WITH ciclos AS (
  SELECT DISTINCT e.cycle_id
    FROM evaluacion_producto_ciclo e
    JOIN config_version cv ON cv.id = e.config_version_id
   WHERE cv.settings ->> ('ads_producto_meta_' || e.platform::text) = 'live'
), esperadas AS (
  SELECT e.cycle_id, (h ->> 'hoja_id')::bigint AS hoja
    FROM evaluacion_producto_ciclo e
    JOIN ciclos USING (cycle_id)
   CROSS JOIN LATERAL jsonb_array_elements(coalesce(e.resultado -> 'sombra_hojas', '[]')) h
   WHERE h -> 'meta' ->> 'aplicado' IS NOT NULL
), congeladas AS (
  SELECT t.cycle_id, t.ad_entity_id AS hoja
    FROM target_acos_ciclo t
    JOIN ciclos USING (cycle_id)
   WHERE t.procedencia = 'margen_producto'
), dif AS (
  (SELECT * FROM esperadas EXCEPT SELECT * FROM congeladas)
  UNION ALL
  (SELECT * FROM congeladas EXCEPT SELECT * FROM esperadas)
)
SELECT CASE
         WHEN NOT EXISTS (SELECT 1 FROM ciclos)
           THEN 't|sin ciclo con meta live todavia'
         WHEN NOT EXISTS (SELECT 1 FROM dif)
           THEN 't|' || (SELECT count(*) FROM ciclos) || ' ciclos con la meta en live: '
                || (SELECT count(*) FROM congeladas)
                || ' hojas margen_producto, todas con su meta congelada'
         ELSE 'f|' || (SELECT count(*) FROM dif)
              || ' hojas difieren entre target_acos_ciclo margen_producto y la meta congelada'
              || ' (ciclos ' || (SELECT string_agg(DISTINCT cycle_id::text, ',') FROM dif) || ')'
       END;
-- Contraste independiente del ciclo: toda hoja de un grupo apto (entrada de la evaluacion) que
-- congelo un target que no es de goal paso por la meta y debe estar en sombra_hojas.
WITH ciclos AS (
  SELECT DISTINCT e.cycle_id
    FROM evaluacion_producto_ciclo e
    JOIN config_version cv ON cv.id = e.config_version_id
   WHERE cv.settings ->> ('ads_producto_meta_' || e.platform::text) = 'live'
), aptas AS (
  SELECT e.cycle_id, (h #>> '{}')::bigint AS hoja, e.resultado
    FROM evaluacion_producto_ciclo e
    JOIN ciclos USING (cycle_id)
   CROSS JOIN LATERAL jsonb_array_elements(coalesce(e.entrada -> 'grupos_aptos_para_meta', '[]')) gr
   CROSS JOIN LATERAL jsonb_array_elements(gr -> 'hojas') h
), fuera AS (
  SELECT a.cycle_id, a.hoja
    FROM aptas a
    JOIN target_acos_ciclo t ON t.cycle_id = a.cycle_id AND t.ad_entity_id = a.hoja
   WHERE t.procedencia NOT IN ('goal_campana', 'goal_plataforma')
     AND NOT EXISTS (
           SELECT 1 FROM jsonb_array_elements(coalesce(a.resultado -> 'sombra_hojas', '[]')) s
            WHERE (s ->> 'hoja_id')::bigint = a.hoja)
)
SELECT CASE WHEN count(*) = 0 THEN 't' ELSE 'f' END
       || '|' || count(*) || ' hojas de grupo apto con target sin goal que no pasaron por la meta'
  FROM fuera;
SELECT CASE WHEN count(*) = 0 THEN 't' ELSE 'f' END
       || '|' || count(*) || ' decisiones con procedencia margen_producto sin evaluacion_producto_id'
  FROM decision
 WHERE evaluacion_producto_id IS NULL
   AND inputs ->> 'target_procedencia' = 'margen_producto';
