-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1
-- Cuántos productos alcanzan el mínimo de 6 órdenes por plataforma y
-- ventana (90/180/365 días), bajo las dos lecturas de la disputa de E.0.
-- Mismo filtro único, misma reconstrucción de orden y misma ventana
-- semiabierta [hoy-N, hoy) en UTC que envio-por-producto.sql (repetido
-- literal a propósito, hecho 13).

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
sum_por_fuente as (
    select
        order_id,
        platform,
        fuente_tipo,
        sum(monto_abs) as monto_fuente,
        count(*) as filas
    from cargos
    group by order_id, platform, fuente_tipo
),
orden_meta as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
        bool_or(fuente_plataforma is null or fuente_tipo is null) as tiene_fuente_desconocida,
        bool_or(
            fuente_tipo is not null
            and fuente_tipo not in ('ShippingHB', 'LabmanLabelPurchase', 'shipping_label')
        ) as tiene_fuente_no_reconocida
    from cargos
    group by order_id, platform
),
orden_costo as (
    select
        sf.order_id,
        sf.platform,
        om.event_date,
        om.tiene_fuente_desconocida,
        om.tiene_fuente_no_reconocida,
        sum(sf.monto_fuente) as costo_componentes,
        coalesce(sum(sf.monto_fuente) filter (
            where sf.fuente_tipo is distinct from 'LabmanLabelPurchase'
              and sf.fuente_tipo is distinct from 'shipping_label'
        ), 0)
        + greatest(
            coalesce(sum(sf.monto_fuente) filter (where sf.fuente_tipo = 'LabmanLabelPurchase'), 0),
            coalesce(sum(sf.monto_fuente) filter (where sf.fuente_tipo = 'shipping_label'), 0)
          ) as costo_duplicado_etiqueta
    from sum_por_fuente sf
    join orden_meta om using (order_id, platform)
    group by sf.order_id, sf.platform, om.event_date, om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida
),
ventas_orden as (
    select order_id, platform, product_id, quantity, amount, amount_currency, event_date
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
),
orden_producto as (
    select
        order_id,
        platform,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(quantity) as unidades,
        sum(amount) as venta_amount,
        max(amount_currency) as venta_currency,
        max(event_date) as venta_event_date
    from ventas_orden
    group by order_id, platform
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
    join orden_producto op using (order_id, platform)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and not oc.tiene_fuente_no_reconocida
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
    join ventanas v
      on b.event_date >= (((now() at time zone 'UTC')::date)) - (v.ventana_dias || ' days')::interval
     and b.event_date <  (((now() at time zone 'UTC')::date))
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
