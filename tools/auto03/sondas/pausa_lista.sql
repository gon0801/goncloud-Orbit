-- Bloque I: la pausa por producto queda lista para vivo. La ultima config_version trae las dos
-- claves de pausa en shadow o live, y una en live solo con sus dos claves de limites (S8). Toda
-- fila pause de product ad en applied tiene la intencion de retiro de su decision cerrada con
-- retiro_id, un apply_attempt con la cuota cobrada y finished_at, y decision_application con
-- verify_ok; ninguna sigue applying sin intencion abierta (la reconciliacion de I.3 la cierra).
-- Sin filas applied todavia, la segunda fila es t|sin filas.
SELECT CASE WHEN mx IN ('shadow', 'live') AND us IN ('shadow', 'live')
             AND (mx <> 'live' OR (s ? 'ads_producto_pausa_clics_amazon_mx'
                                   AND s ? 'ads_producto_pausa_gasto_amazon_mx'))
             AND (us <> 'live' OR (s ? 'ads_producto_pausa_clics_amazon_us'
                                   AND s ? 'ads_producto_pausa_gasto_amazon_us'))
       THEN 't' ELSE 'f' END
       || '|ultima config_version con la pausa de amazon_mx en ' || coalesce(mx, 'off')
       || ' y la de amazon_us en ' || coalesce(us, 'off')
       || ' (shadow o live; en live con sus dos limites)'
  FROM (SELECT s, s->>'ads_producto_pausa_amazon_mx' AS mx, s->>'ads_producto_pausa_amazon_us' AS us
          FROM (SELECT coalesce((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                                '{}'::jsonb) AS s) c) v;
SELECT CASE
         WHEN count(*) = 0 THEN 't|sin filas'
         WHEN count(*) FILTER (WHERE NOT con_retiro OR NOT con_intento OR NOT verificada) > 0
           THEN 'f|' || count(*) || ' pausas de product ad applied: '
                || count(*) FILTER (WHERE NOT con_retiro) || ' sin intencion cerrada con retiro, '
                || count(*) FILTER (WHERE NOT con_intento)
                || ' sin apply_attempt con cuota y finished_at, '
                || count(*) FILTER (WHERE NOT verificada) || ' sin verify_ok'
         ELSE 't|' || count(*) || ' pausas de product ad applied, todas con intencion cerrada con '
              || 'retiro, apply_attempt con cuota y finished_at y verify_ok'
       END
  FROM (SELECT EXISTS (SELECT 1 FROM anuncio_retiro_intencion i
                        WHERE i.decision_id = q.decision_id AND i.cerrada_el IS NOT NULL
                          AND i.retiro_id IS NOT NULL) AS con_retiro,
               EXISTS (SELECT 1 FROM apply_attempt a
                        WHERE a.decision_id = q.decision_id AND a.tipo = 'normal'
                          AND a.quota_cobrada AND a.finished_at IS NOT NULL) AS con_intento,
               EXISTS (SELECT 1 FROM decision_application da
                        WHERE da.decision_id = q.decision_id AND da.verify_ok) AS verificada
          FROM apply_queue q
          JOIN ad_entity e ON e.id = q.ad_entity_id AND e.kind = 'product_ad'
         WHERE q.kind = 'pause' AND q.estado = 'applied') f;
SELECT CASE WHEN count(*) = 0 THEN 't|ninguna pausa de product ad applying sin intencion abierta'
            ELSE 'f|' || count(*) || ' pausas de product ad applying sin intencion abierta' END
  FROM apply_queue q
  JOIN ad_entity e ON e.id = q.ad_entity_id AND e.kind = 'product_ad'
 WHERE q.kind = 'pause' AND q.estado = 'applying'
   AND NOT EXISTS (SELECT 1 FROM anuncio_retiro_intencion i
                    WHERE i.decision_id = q.decision_id AND i.cerrada_el IS NULL);
