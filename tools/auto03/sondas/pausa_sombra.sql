-- Bloque H: la pausa por producto queda en practica en los dos mercados (la convergencia de
-- tools/despliegue/interruptores.env la escribio con productos-modo). Toda decision de pausa
-- sobre un product ad tiene su evaluacion y una fila de cola shadow, y ninguna fila de product
-- ad nace live. Sin decisiones todavia, la segunda fila es t|sin filas.
SELECT CASE WHEN s->>'ads_producto_pausa_amazon_mx' = 'shadow'
             AND s->>'ads_producto_pausa_amazon_us' = 'shadow'
       THEN 't' ELSE 'f' END
       || '|ultima config_version con la pausa de amazon_mx y amazon_us en shadow'
  FROM (SELECT coalesce((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                        '{}'::jsonb) AS s) c;
SELECT CASE
         WHEN count(*) = 0 THEN 't|sin filas'
         WHEN count(*) FILTER (WHERE d.evaluacion_producto_id IS NULL) > 0
           THEN 'f|' || count(*) FILTER (WHERE d.evaluacion_producto_id IS NULL)
                || ' decisiones de pausa de product ad sin evaluacion_producto_id'
         WHEN count(*) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM apply_queue q WHERE q.decision_id = d.id AND q.modo = 'shadow')) > 0
           THEN 'f|' || count(*) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM apply_queue q WHERE q.decision_id = d.id AND q.modo = 'shadow'))
                || ' decisiones de pausa de product ad sin fila de cola shadow'
         ELSE 't|' || count(*) || ' decisiones de pausa de product ad, todas con evaluacion y '
              || 'fila de cola shadow'
       END
  FROM decision d
  JOIN ad_entity e ON e.id = d.ad_entity_id AND e.kind = 'product_ad'
 WHERE d.kind = 'pause';
SELECT CASE WHEN count(*) = 0 THEN 't|ninguna fila live de product_ad en la cola'
            ELSE 'f|' || count(*) || ' filas live de product_ad en la cola' END
  FROM apply_queue q
  JOIN ad_entity e ON e.id = q.ad_entity_id AND e.kind = 'product_ad'
 WHERE q.modo = 'live';
