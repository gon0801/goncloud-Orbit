-- ORBIT 07 M0/M1: SELECT de lectura que acredita el dato real antes de fijar
-- invariantes. Ejecutar con el rol de lectura `orbit_read` (solo SELECT) y
-- jamas imprimir credenciales. Es el mismo `v_metric_latest` que ya consume el
-- ciclo; no se copia ninguna tabla. La fecha se fija en UTC explicito.
--
-- CAUSA DECLARADA (2026-11-07): este encargo no tiene acceso concedido a la
-- base de produccion (el catalogo del buzon solo habilita `ci-github`). El SQL
-- queda concreto; su resultado debe registrarse aqui cuando exista el acceso.
-- La ventana D-9..D-1 y el grano ya se usan en produccion por
-- app/lecturas_caso.py (`DIAS_INMADUROS_DESDE = 9`).

WITH hoy AS (
    SELECT (now() AT TIME ZONE 'UTC')::date AS d
),
recientes AS (
    SELECT v.ad_entity_id,
           sum(v.orders)::bigint AS pedidos_recientes,
           min(v.metric_date) FILTER (WHERE v.orders > 0) AS ancla,
           bool_and(v.orders IS NOT NULL AND v.orders >= 0) AS completa,
           max(v.observed_at) AS observado_max
      FROM v_metric_latest v
      JOIN ad_entity k ON k.id = v.ad_entity_id
      CROSS JOIN hoy
     WHERE k.platform = 'amazon_us'
       AND k.kind IN ('keyword', 'product_target')
       AND v.metric_currency = 'USD'
       AND v.metric_date BETWEEN hoy.d - 9 AND hoy.d - 1
     GROUP BY v.ad_entity_id
),
cortes AS (
    SELECT c.ad_entity_id,
           sum(c.orders)::bigint AS pedidos_maduros,
           sum(c.clicks)::bigint AS clics_maduros,
           sum(c.cost) AS gasto_maduro
      FROM v_metric_latest c
      JOIN ad_entity k ON k.id = c.ad_entity_id
      CROSS JOIN hoy
     WHERE k.platform = 'amazon_us'
       AND k.kind IN ('keyword', 'product_target')
       AND c.metric_currency = 'USD'
       AND c.metric_date BETWEEN hoy.d - 39 AND hoy.d - 10
     GROUP BY c.ad_entity_id
)
SELECT r.ad_entity_id,
       r.pedidos_recientes,
       r.ancla,
       r.completa,
       c.pedidos_maduros,
       c.clics_maduros,
       c.gasto_maduro
  FROM recientes r
  JOIN cortes c ON c.ad_entity_id = r.ad_entity_id
 WHERE r.pedidos_recientes > 0
   AND c.pedidos_maduros = 0
 ORDER BY r.ancla DESC, r.ad_entity_id;

-- Repetir con platform = 'amazon_mx' y metric_currency = 'MXN'.
--
-- Despues de desplegar 0081, para auditar el episodio persistido se agrega:
--   LEFT JOIN proteccion_venta_reciente e
--     ON e.platform = 'amazon_us'
--    AND e.ad_entity_id = r.ad_entity_id
--    AND e.version = 'proteccion_venta_v1'
-- y se proyectan e.ancla AS ancla_episodio, e.vence_el.
