-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r4a
-- Descartes de la muestra de envío, por razón y por plataforma. Toda fila
-- descartada se cuenta, nunca se borra en silencio (regla de E.0/AC18).
--
-- Razones:
--   sin_order_id        : el cargo de envío no trae order_id (no se puede
--                          agrupar por orden; se cuenta por FILA, no por
--                          orden).
--   fuente_desconocida   : source_event_id nulo, o la parte 2 viene
--                          vacía, o es 'finance' sin subtipo (parte 6
--                          vacía) — no se puede construir una identidad.
--   fuente_no_reconocida : se construyó una identidad, pero fuera del
--                          vocabulario de las seis fuentes reales medidas
--                          en producción (ver envio-por-producto.sql).
--   sin_fila_de_venta   : la orden tiene cargo de envío pero NINGUNA fila
--                          'sale' en absoluto (con o sin product_id).
--   venta_sin_producto   : la orden SÍ tiene fila(s) 'sale', pero ninguna
--                          trae product_id — split de 'sin_venta_ligada'
--                          en r4a: medido por el lead 2026-09-17, las 148
--                          órdenes que la ronda r3 rotulaba
--                          'sin_venta_ligada' SÍ tenían venta (0 sin
--                          fila); lo que pasaba es que la venta no traía
--                          product_id (86 MX / 62 US) — dos causas
--                          distintas que 'sin_venta_ligada' mezclaba.
--   multi_producto       : la orden liga a más de un product_id distinto.
--   usable               : ninguna de las anteriores; de éstas se informa
--                          aparte cuántas son multi-unidad (NO se
--                          descartan: L_unidad = L_orden / unidades, ver
--                          efecto-margen.sql).
--
-- Prioridad cuando una orden calza en más de una razón: fuente_desconocida
-- > fuente_no_reconocida > sin_fila_de_venta > venta_sin_producto >
-- multi_producto > usable. El criterio de 'usable' NO cambia frente a
-- r3: sigue siendo "tiene una fila de venta con product_id, de un solo
-- product_id" — el mismo criterio que usan envio-por-producto.sql,
-- productos-que-alcanzan-minimo.sql, parpadeo.sql y efecto-margen.sql
-- (todas exigen `orden_producto`, que solo cuenta ventas CON product_id);
-- este archivo solo separó las dos razones por las que una orden puede
-- fallar ese criterio, no tocó el criterio en sí.

with poblacion as (
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select
        *,
        case
            when source_event_id is null or fuente_forma is null then null
            when fuente_forma = 'shipping_label' then 'shipping_label'
            when fuente_forma = 'finance' and fuente_subtipo is not null then 'finance:' || fuente_subtipo
            when fuente_forma = 'finance' then null
            else fuente_forma
        end as identidad_fuente
    from poblacion
    where order_id is not null
),
orden_meta as (
    select
        order_id,
        platform,
        bool_or(identidad_fuente is null) as tiene_fuente_desconocida,
        bool_or(
            identidad_fuente is not null
            and identidad_fuente not in (
                'shipping_label', 'finance:LabmanLabelPurchase', 'finance:MFNPostageFee',
                'finance:ShippingHB', 'finance:ShippingChargeback', 'finance:MFNShippingChargeback'
            )
        ) as tiene_fuente_no_reconocida
    from cargos
    group by order_id, platform
),
ventas_cualquiera as (
    -- CUALQUIER fila 'sale' de la orden, con o sin product_id — para
    -- distinguir 'sin_fila_de_venta' (esto vacío) de 'venta_sin_producto'
    -- (esto tiene filas, pero orden_producto de abajo no).
    select distinct order_id, platform
    from ledger_event
    where kind = 'sale' and order_id is not null
),
ventas_orden as (
    select order_id, platform, product_id, quantity
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
),
orden_producto as (
    select
        order_id,
        platform,
        count(distinct product_id) as n_productos,
        sum(quantity) as unidades
    from ventas_orden
    group by order_id, platform
),
orden_clasificada as (
    select
        om.order_id,
        om.platform,
        op.unidades,
        case
            when om.tiene_fuente_desconocida then 'fuente_desconocida'
            when om.tiene_fuente_no_reconocida then 'fuente_no_reconocida'
            when vc.order_id is null then 'sin_fila_de_venta'
            when op.order_id is null then 'venta_sin_producto'
            when op.n_productos > 1 then 'multi_producto'
            else 'usable'
        end as razon
    from orden_meta om
    left join ventas_cualquiera vc using (order_id, platform)
    left join orden_producto op using (order_id, platform)
)
select platform, razon, count(*) as ordenes,
    count(*) filter (where razon = 'usable' and coalesce(unidades, 1) > 1) as de_las_cuales_multiunidad_no_descartada
from orden_clasificada
group by platform, razon

union all

select platform, 'sin_order_id' as razon, count(*) as ordenes, null as de_las_cuales_multiunidad_no_descartada
from poblacion
where order_id is null
group by platform

order by platform, razon;
