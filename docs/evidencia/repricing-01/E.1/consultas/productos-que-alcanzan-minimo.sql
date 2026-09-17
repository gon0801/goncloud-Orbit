-- ORBIT · fase 8 · repricing-01 · E.1
-- Cuántos productos alcanzan el mínimo de 6 órdenes por plataforma y
-- ventana (90/180/365 días), bajo las dos lecturas de la disputa de E.0.
-- Mismo filtro único y misma reconstrucción de orden que envio-por-producto.sql
-- (repetido literal a propósito, hecho 13).

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
      and le.event_date >= current_date - interval '365 days'
),
cargos as (
    select *
    from poblacion
    where order_id is not null
),
orden_costo as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
        bool_or(fuente_plataforma is null or fuente_tipo is null) as tiene_fuente_desconocida,
        sum(monto_abs) as costo_componentes,
        coalesce(sum(monto_abs) filter (
            where fuente_tipo is distinct from 'LabmanLabelPurchase'
              and fuente_tipo is distinct from 'shipping_label'
        ), 0)
        + greatest(
            coalesce(max(monto_abs) filter (where fuente_tipo = 'LabmanLabelPurchase'), 0),
            coalesce(max(monto_abs) filter (where fuente_tipo = 'shipping_label'), 0)
          ) as costo_duplicado_etiqueta
    from cargos
    group by order_id, platform
),
ventas_orden as (
    select order_id, product_id, sum(quantity) as unidades
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
    group by order_id, product_id
),
orden_producto as (
    select
        order_id,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(unidades) as unidades
    from ventas_orden
    group by order_id
),
orden_final as (
    select
        oc.order_id,
        oc.platform,
        oc.event_date,
        op.product_id,
        oc.costo_componentes,
        oc.costo_duplicado_etiqueta
    from orden_costo oc
    join orden_producto op using (order_id)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
),
ventanas (ventana_dias) as (values (90), (180), (365)),
base_lectura as (
    select order_id, platform, product_id, event_date, 'componentes' as lectura
    from orden_final
    union all
    select order_id, platform, product_id, event_date, 'duplicado_etiqueta' as lectura
    from orden_final
),
conteo as (
    select
        b.product_id,
        b.platform,
        v.ventana_dias,
        b.lectura,
        count(*) as ordenes
    from base_lectura b
    join ventanas v on b.event_date >= current_date - (v.ventana_dias || ' days')::interval
    group by b.product_id, b.platform, v.ventana_dias, b.lectura
)
select
    platform,
    ventana_dias,
    lectura,
    count(*) as productos_totales_con_alguna_orden,
    count(*) filter (where ordenes >= 6) as productos_que_alcanzan_minimo
from conteo
group by platform, ventana_dias, lectura
order by platform, ventana_dias, lectura;
