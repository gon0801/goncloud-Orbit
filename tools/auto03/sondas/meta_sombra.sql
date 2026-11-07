-- Bloque D: la meta por producto queda en sombra en los dos mercados (la convergencia de
-- tools/despliegue/interruptores.env la escribio con productos-modo). Un ciclo que ya corrio
-- con ella guardo tantas evaluaciones como productos conto en notes.productos y sin error.
SELECT CASE WHEN s->>'ads_producto_meta_amazon_mx' = 'shadow'
             AND s->>'ads_producto_meta_amazon_us' = 'shadow'
       THEN 't' ELSE 'f' END
       || '|ultima config_version con la meta de amazon_mx y amazon_us en shadow'
  FROM (SELECT coalesce((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                        '{}'::jsonb) AS s) c;
SELECT CASE WHEN count(*) FILTER (WHERE malo) = 0 THEN 't' ELSE 'f' END
       || '|ciclos con notes.productos: '
       || count(*) || ' revisados, ' || count(*) FILTER (WHERE malo) || ' con error o con '
       || 'evaluaciones distintas de productos_evaluados'
  FROM (SELECT (c.notes::jsonb -> 'productos' ? 'error')
               OR (SELECT count(*) FROM evaluacion_producto_ciclo e WHERE e.cycle_id = c.id)
                  IS DISTINCT FROM (c.notes::jsonb -> 'productos' ->> 'productos_evaluados')::bigint
               AS malo
          FROM optimizer_cycle c
         WHERE c.motor = 'ads_optimizer' AND c.status = 'done'
           AND c.notes::jsonb ? 'productos') r;
