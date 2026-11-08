-- ORBIT 07 M0/M1: SELECT de lectura que acredita el dato real antes de fijar
-- invariantes. Ejecutar con el rol de lectura `orbit_read` (solo SELECT) y
-- jamas imprimir credenciales. Es el mismo `v_metric_latest` que ya consume el
-- ciclo; no se copia ninguna tabla.
--
-- CONTRATO REAL (app/optimizer/windows.py): la ventana de cortes de cada hoja
-- termina en min(max(metric_date) - 3, hoy_UTC - 10) y dura 30 dias inclusive
-- (window_end - 29). No es la ventana fija D-39..D-10. El ancla de la
-- proteccion es la metric_date mas antigua con pedido en D-9..D-1.
--
-- MUESTRA YA EJECUTADA (2026-11-08, revision Astra, orbit_read): la consulta
-- con la ventana fija D-39..D-10 devolvio dos hojas por mercado con pedidos
-- recientes y cero pedidos maduros; el resultado esta en
-- produccion-2026-11-08.json. Esa muestra acredita que el disparador existe en
-- produccion, NO que haya pausas elegibles ni perdidas: la ventana fija no es
-- el contrato por hoja y no aplica todos los filtros del ciclo.
--
-- Esta version corregida aun no se ha ejecutado: este encargo no tiene acceso
-- concedido a la base de produccion (el catalogo del buzon solo habilita
-- `ci-github`). Debe correrse con `orbit_read` y registrarse sin credenciales.

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
ultima AS (
    SELECT k.id AS ad_entity_id,
           max(v.metric_date) AS ultima_fila
      FROM ad_entity k
      JOIN v_metric_latest v ON v.ad_entity_id = k.id
     WHERE k.platform = 'amazon_us'
       AND k.kind IN ('keyword', 'product_target')
     GROUP BY k.id
),
ventana AS (
    SELECT u.ad_entity_id,
           u.ultima_fila,
           least(u.ultima_fila - 3, (SELECT d FROM hoy) - 10) AS window_end
      FROM ultima u
),
cortes AS (
    SELECT w.ad_entity_id,
           sum(m.orders)::bigint AS pedidos_maduros,
           sum(m.clicks)::bigint AS clics_maduros,
           sum(m.cost) AS gasto_maduro
      FROM ventana w
      JOIN v_metric_latest m ON m.ad_entity_id = w.ad_entity_id
      JOIN ad_entity k ON k.id = m.ad_entity_id
     WHERE k.platform = 'amazon_us'
       AND k.kind IN ('keyword', 'product_target')
       AND m.metric_currency = 'USD'
       AND m.metric_date BETWEEN w.window_end - 29 AND w.window_end
     GROUP BY w.ad_entity_id
)
SELECT r.ad_entity_id,
       r.pedidos_recientes,
       r.ancla,
       r.completa,
       w.ultima_fila,
       w.window_end,
       c.pedidos_maduros,
       c.clics_maduros,
       c.gasto_maduro
  FROM recientes r
  JOIN ventana w ON w.ad_entity_id = r.ad_entity_id
  LEFT JOIN cortes c ON c.ad_entity_id = r.ad_entity_id
 WHERE r.pedidos_recientes > 0
 ORDER BY r.ancla DESC, r.ad_entity_id;

-- Repetir con platform = 'amazon_mx' y metric_currency = 'MXN'.
-- `pedidos_maduros = 0` es condicion NECESARIA de la pausa por umbral; el
-- ciclo exige ademas los umbrales de clics/costo y los filtros de settings,
-- goals, inertes y veto. No convertir esta consulta en un conteo de pausas.
--
-- Despues de desplegar 0081, para auditar el episodio persistido se agrega:
--   LEFT JOIN proteccion_venta_reciente e
--     ON e.platform = 'amazon_us'
--    AND e.ad_entity_id = r.ad_entity_id
--    AND e.version = 'proteccion_venta_v1'
-- y se proyectan e.ancla AS ancla_episodio, e.vence_el.
