-- Bloque D: la meta por producto queda en sombra en los dos mercados (la convergencia de
-- tools/despliegue/interruptores.env la escribio con productos-modo). Si el ultimo ciclo done
-- de un mercado ya corrio con ella, guardo tantas evaluaciones como productos conto en
-- notes.productos y sin error. Solo el ultimo ciclo de cada mercado: notes es TEXT y los ciclos
-- viejos pueden traer texto plano.
SELECT CASE WHEN s->>'ads_producto_meta_amazon_mx' = 'shadow'
             AND s->>'ads_producto_meta_amazon_us' = 'shadow'
       THEN 't' ELSE 'f' END
       || '|ultima config_version con la meta de amazon_mx y amazon_us en shadow'
  FROM (SELECT coalesce((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                        '{}'::jsonb) AS s) c;
SELECT CASE
         WHEN u.id IS NULL OR u.notes IS NULL OR u.notes NOT LIKE '{%'
              OR NOT (u.notes::jsonb ? 'productos')
           THEN 't|' || p.platform || ': sin ciclo con la meta en sombra todavia'
         WHEN u.notes::jsonb -> 'productos' ? 'error'
           THEN 'f|' || p.platform || ': ciclo ' || u.id || ' con notes.productos.error'
         WHEN (SELECT count(*) FROM evaluacion_producto_ciclo e WHERE e.cycle_id = u.id)
              IS DISTINCT FROM (u.notes::jsonb -> 'productos' ->> 'productos_evaluados')::bigint
           THEN 'f|' || p.platform || ': ciclo ' || u.id
                || ' guardo otro numero de evaluaciones que productos_evaluados'
         ELSE 't|' || p.platform || ': ciclo ' || u.id || ' guardo '
              || (u.notes::jsonb -> 'productos' ->> 'productos_evaluados') || ' evaluaciones'
       END
  FROM (VALUES ('amazon_mx'), ('amazon_us')) AS p(platform)
  LEFT JOIN LATERAL (
        SELECT c.id, c.notes FROM optimizer_cycle c
         WHERE c.motor = 'ads_optimizer' AND c.status = 'done'
           AND c.platform = p.platform::platform
         ORDER BY c.id DESC LIMIT 1) u ON true;
