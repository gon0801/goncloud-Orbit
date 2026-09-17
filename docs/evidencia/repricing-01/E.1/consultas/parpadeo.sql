-- ORBIT · fase 8 · repricing-01 · E.1
-- Parpadeo del mínimo (hecho 15): seis ventanas móviles de 90 días,
-- cuántos productos entran (>=6 órdenes) y salen (<6) del mínimo a lo
-- largo de ellas. Esta consulta NO aplica histéresis (entra con 6, sale
-- con 3) — eso lo decide E.2; aquí solo se mide si el corte parpadea sin
-- histéresis.
--
-- Supuesto declarado: las seis ventanas se desplazan 30 días entre sí
-- (ventana i cubre [hoy - 90 - 30*i, hoy - 30*i)), como aproximación
-- razonable al método del hallazgo 8 de plan-validacion-ebm.md, que no
-- especifica el desplazamiento exacto. Si el desplazamiento real difiere,
-- el número cambia; la mecánica (agrupar por orden, dos lecturas) no.

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
        op.product_id
    from orden_costo oc
    join orden_producto op using (order_id)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
),
base_lectura as (
    select order_id, platform, product_id, event_date, 'componentes' as lectura from orden_final
    union all
    select order_id, platform, product_id, event_date, 'duplicado_etiqueta' as lectura from orden_final
),
ventanas_moviles (indice, offset_dias) as (
    values (1, 0), (2, 30), (3, 60), (4, 90), (5, 120), (6, 150)
),
-- Grilla completa producto x ventana: un producto que cae a CERO órdenes
-- en una ventana no debe desaparecer de la cuenta (LEFT JOIN + coalesce a
-- 0), o "sale del mínimo" quedaría invisible cuando la caída es total.
productos_lectura as (
    select distinct product_id, platform, lectura from base_lectura
),
grilla as (
    select pl.product_id, pl.platform, pl.lectura, vm.indice, vm.offset_dias
    from productos_lectura pl
    cross join ventanas_moviles vm
),
conteo_ventana as (
    select
        g.product_id, g.platform, g.lectura, g.indice,
        count(b.order_id) as ordenes
    from grilla g
    left join base_lectura b
      on b.product_id = g.product_id
     and b.platform = g.platform
     and b.lectura = g.lectura
     and b.event_date >= current_date - g.offset_dias - interval '90 days'
     and b.event_date <  current_date - g.offset_dias
    group by g.product_id, g.platform, g.lectura, g.indice
),
clasificado as (
    select
        product_id, platform, lectura,
        count(*) filter (where ordenes >= 6) as ventanas_en_minimo,
        count(*) as ventanas_con_datos,
        bool_or(ordenes >= 6) and bool_or(ordenes < 6) as parpadea
    from conteo_ventana
    group by product_id, platform, lectura
)
select
    platform,
    lectura,
    count(*) filter (where ventanas_en_minimo > 0) as productos_que_alcanzan_minimo_en_alguna_ventana,
    count(*) filter (where ventanas_en_minimo = 6) as productos_estables_en_las_seis,
    count(*) filter (where parpadea) as productos_que_parpadean
from clasificado
group by platform, lectura
order by platform, lectura;
