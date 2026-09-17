-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1
-- Descartes de la muestra de envío, por razón. Toda fila descartada se
-- cuenta, nunca se borra en silencio (regla de E.0/AC18).
--
-- Razones:
--   sin_order_id        : el cargo de envío no trae order_id (no se puede
--                          agrupar por orden; se cuenta por FILA, no por
--                          orden).
--   fuente_desconocida   : source_event_id no trae las partes esperadas
--                          (partes 2 o 6 ausentes) — hueco declarado en
--                          plan-validacion-ebm.md, "Disputa abierta"; E.0
--                          no cierra mientras existan.
--   fuente_no_reconocida : la parte 6 SÍ está presente pero no es
--                          'ShippingHB'/'LabmanLabelPurchase'/
--                          'shipping_label' (hallazgo 8, ronda r1): un
--                          valor nuevo no puede entrar en silencio a
--                          'usable' ni fingir ser 'fuente_desconocida'.
--   sin_venta_ligada     : la orden tiene cargo de envío pero ninguna fila
--                          'sale' con product_id en ese (order_id,
--                          platform).
--   multi_producto       : la orden liga a más de un product_id distinto.
--   usable               : ninguna de las anteriores; de éstas se informa
--                          aparte cuántas son multi-unidad (NO se
--                          descartan: L_unidad = L_orden / unidades, ver
--                          efecto-margen.sql).
--
-- Prioridad cuando una orden calza en más de una razón (p.ej.
-- fuente_desconocida Y multi_producto a la vez): fuente_desconocida >
-- fuente_no_reconocida > sin_venta_ligada > multi_producto > usable — la
-- imposibilidad de clasificar el cargo pesa más que cualquier otro
-- defecto de la orden.

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
    select *
    from poblacion
    where order_id is not null
),
orden_meta as (
    select
        order_id,
        platform,
        bool_or(fuente_plataforma is null or fuente_tipo is null) as tiene_fuente_desconocida,
        bool_or(
            fuente_tipo is not null
            and fuente_tipo not in ('ShippingHB', 'LabmanLabelPurchase', 'shipping_label')
        ) as tiene_fuente_no_reconocida
    from cargos
    group by order_id, platform
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
            when op.order_id is null then 'sin_venta_ligada'
            when op.n_productos > 1 then 'multi_producto'
            else 'usable'
        end as razon
    from orden_meta om
    left join orden_producto op using (order_id, platform)
)
select razon, count(*) as ordenes,
    count(*) filter (where razon = 'usable' and coalesce(unidades, 1) > 1) as de_las_cuales_multiunidad_no_descartada
from orden_clasificada
group by razon

union all

select 'sin_order_id' as razon, count(*) as ordenes, null as de_las_cuales_multiunidad_no_descartada
from poblacion
where order_id is null

order by razon;
