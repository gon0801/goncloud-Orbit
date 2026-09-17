-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1
-- Rezago de EMISIÓN: entre la fecha real del envío y el event_date con que
-- Amazon cobra el cargo. Distinto del rezago de INGESTA (ver
-- rezago-ingesta.sql).
--
-- CORREGIDO en esta ronda (hallazgo 5): la versión anterior declaraba
-- "no existe columna de fecha de envío" — eso estaba mal. SÍ existe:
-- `spapi_order_observation.fulfillment_status` (migrations/
-- 0031_spapi_orders_bitemporal.sql l.30, poblada como 'fulfillment.
-- fulfillmentStatus' por app/spapi/orders.py l.256). No es una fecha de
-- envío directa, pero la PRIMERA observación en la que una orden aparece
-- 'Shipped' es la mejor aproximación disponible en Orbit a "cuándo se
-- envió": se mide como `event_date` del cargo MENOS esa primera
-- observación 'Shipped'.
--
-- Error declarado (no se esconde): `spapi_order_observation` se re-observa
-- con cadencia DIARIA (comentario de 0031: "la ventana diaria... re-
-- observa los pedidos recientes"), así que "primera observación en
-- Shipped" tiene una resolución de ~1 día — el envío real pudo ocurrir
-- hasta ~1 día antes de que Orbit lo capture como 'Shipped'. El rezago de
-- emisión medido aquí es, por construcción, una sobreestimación de hasta
-- ~1 día del rezago real.
--
-- Cobertura (sin inventar, hallazgo 5): una orden con cargo de envío que
-- NUNCA aparece 'Shipped' en `spapi_order_observation` se cuenta aparte
-- (`ordenes_sin_observacion_shipped`), no se descarta en silencio y no
-- entra al percentil.
--
-- Join por (platform, order_id = amazon_order_id) — hallazgo 16: el
-- order_id de texto podría repetirse entre plataformas.
--
-- Ventana SEMIABIERTA [hoy-N, hoy) en UTC (hallazgo 6/14).

with poblacion as (
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_plataforma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_tipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select * from poblacion where order_id is not null
),
cargo_orden as (
    select order_id, platform, max(event_date) as event_date
    from cargos
    group by order_id, platform
),
primera_shipped as (
    select
        platform,
        amazon_order_id,
        min((observed_at at time zone 'UTC')::date) as shipped_en
    from spapi_order_observation
    where fulfillment_status = 'Shipped'
    group by platform, amazon_order_id
)
select
    co.platform,
    count(*) as ordenes_con_cargo,
    count(*) filter (where ps.shipped_en is not null) as ordenes_con_observacion_shipped,
    count(*) filter (where ps.shipped_en is null) as ordenes_sin_observacion_shipped,
    percentile_cont(0.5) within group (
        order by (co.event_date - ps.shipped_en)
    ) filter (where ps.shipped_en is not null) as rezago_emision_p50_dias,
    percentile_cont(0.9) within group (
        order by (co.event_date - ps.shipped_en)
    ) filter (where ps.shipped_en is not null) as rezago_emision_p90_dias,
    max(co.event_date - ps.shipped_en) filter (where ps.shipped_en is not null) as rezago_emision_max_dias
from cargo_orden co
left join primera_shipped ps
  on ps.platform = co.platform and ps.amazon_order_id = co.order_id
group by co.platform
order by co.platform;
