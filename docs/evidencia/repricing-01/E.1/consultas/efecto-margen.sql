-- ORBIT · fase 8 · repricing-01 · E.1
-- Efecto sobre el margen POR UNIDAD (L_unidad = L_orden / unidades_de_la_orden,
-- spec S10 "Por unidad, no por orden") de la decisión de ventana y de
-- percentil, para productos con al menos 10 productos con muestra.
--
-- Dos SELECT (script, ambos solo lectura):
--   1) margen con L = MEDIANA, comparando ventana 90 vs 180 vs 365 días.
--      "que es la decisión real, no p50 vs p75" (fila E.1 del plan).
--   2) margen a ventana fija de 180 días, comparando percentil p50 vs
--      p75 vs p90 (el hallazgo 5 de plan-validacion-ebm.md midió que esto
--      mueve poco el margen, pero se deja medido, no supuesto).
--
-- Ingreso por unidad: amount de la venta (kind='sale') dividido entre
-- quantity de esa fila. Costo del producto: sku_cost vigente a la fecha
-- de la orden (EXCLUDE de sku_cost garantiza una sola vigencia por fecha).
-- Conversión de moneda: fx_resolve(fecha, 'USD', 'MXN') y se opera en MXN;
-- fila marcada fx_pendiente cuando falta la tasa (regla S10 "FX en US":
-- nunca una constante). Esta consulta NO intenta reconstruir comisiones de
-- plataforma ni Ads: es el margen de contribución simplificado
-- (venta - costo - envío) que pide E.1 como insumo, no el margen final de
-- la fase B.
--
-- LECTURA DUAL de la disputa de E.0: 'componentes' y 'duplicado_etiqueta'
-- (ver envio-por-producto.sql). 'duplicado_etiqueta' es hipótesis del lead.
--
-- Supuesto declarado: "FBM" aquí = producto con al menos una orden en la
-- muestra de envío (poblacion/cargos); esta consulta no filtra por canal
-- porque `ledger_event`/`listing` no traen esa columna (el canal vive en
-- `estimacion_oferta_observation`, hallazgo 14 de plan-validacion-ebm.md,
-- fuera del alcance de lectura de E.1).

-- 1) L = mediana, ventana 90 vs 180 vs 365
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_plataforma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_tipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.event_date >= current_date - interval '365 days'
),
cargos as (
    select * from poblacion where order_id is not null
),
orden_costo as (
    select
        order_id, platform, max(event_date) as event_date,
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
    select order_id, product_id, quantity, amount, amount_currency, event_date
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
),
orden_producto as (
    select
        order_id,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(quantity) as unidades,
        sum(amount) as venta_amount,
        max(amount_currency) as venta_currency,
        max(event_date) as venta_event_date
    from ventas_orden
    group by order_id
),
orden_final as (
    select
        oc.order_id, oc.platform, oc.event_date,
        op.product_id, op.unidades, op.venta_amount, op.venta_currency,
        oc.costo_componentes / op.unidades as l_unidad_componentes,
        oc.costo_duplicado_etiqueta / op.unidades as l_unidad_duplicado_etiqueta
    from orden_costo oc
    join orden_producto op using (order_id)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and op.unidades > 0
),
base_lectura as (
    select order_id, platform, product_id, event_date, unidades, venta_amount, venta_currency,
           'componentes' as lectura, l_unidad_componentes as l_unidad
    from orden_final
    union all
    select order_id, platform, product_id, event_date, unidades, venta_amount, venta_currency,
           'duplicado_etiqueta' as lectura, l_unidad_duplicado_etiqueta as l_unidad
    from orden_final
),
venta_mxn as (
    select
        b.*,
        (venta_amount / nullif(unidades, 0)) as ingreso_unidad_original,
        case when venta_currency = 'MXN' then (venta_amount / nullif(unidades, 0))
             else null end as ingreso_unidad_mxn,
        case when venta_currency = 'USD' then fx.rate else null end as fx_rate_venta
    from base_lectura b
    left join lateral fx_resolve(b.event_date, 'USD', 'MXN') fx on venta_currency = 'USD'
),
costo_asof as (
    select
        v.*,
        sc.cost_amount, sc.cost_currency,
        case when sc.cost_currency = 'USD' then fx2.rate else null end as fx_rate_costo
    from venta_mxn v
    left join lateral (
        select sc.cost_amount, sc.cost_currency
        from sku_cost sc
        where sc.product_id = v.product_id
          and sc.valid_from <= v.event_date
          and (sc.valid_to is null or sc.valid_to > v.event_date)
        order by sc.valid_from desc
        limit 1
    ) sc on true
    left join lateral fx_resolve(v.event_date, 'USD', 'MXN') fx2 on sc.cost_currency = 'USD'
),
final_unidad as (
    select
        *,
        coalesce(
            ingreso_unidad_mxn,
            case when venta_currency = 'USD' and fx_rate_venta is not null
                 then ingreso_unidad_original * fx_rate_venta end
        ) as ingreso_unidad_mxn_resuelto,
        case when cost_currency is null then null
             when cost_currency = 'MXN' then cost_amount
             when cost_currency = 'USD' and fx_rate_costo is not null then cost_amount * fx_rate_costo
             else null end as costo_unidad_mxn,
        (venta_currency = 'USD' and fx_rate_venta is null)
            or (cost_currency = 'USD' and fx_rate_costo is null) as fx_pendiente
    from costo_asof
),
ventanas (ventana_dias) as (values (90), (180), (365))
select
    fu.product_id,
    fu.platform,
    v.ventana_dias,
    fu.lectura,
    count(*) as unidades_muestreadas,
    count(distinct fu.order_id) as ordenes,
    bool_or(fu.fx_pendiente) as tiene_fx_pendiente,
    percentile_cont(0.5) within group (order by fu.l_unidad) as l_unidad_mediana,
    avg(fu.ingreso_unidad_mxn_resuelto) as ingreso_unidad_mxn_promedio,
    avg(fu.costo_unidad_mxn) as costo_unidad_mxn_promedio,
    avg(fu.ingreso_unidad_mxn_resuelto) - avg(fu.costo_unidad_mxn)
        - percentile_cont(0.5) within group (order by fu.l_unidad) as margen_unidad_mxn_reconstruido
from final_unidad fu
join ventanas v on fu.event_date >= current_date - (v.ventana_dias || ' days')::interval
group by fu.product_id, fu.platform, v.ventana_dias, fu.lectura
having count(distinct fu.order_id) >= 10
order by fu.product_id, fu.platform, v.ventana_dias, fu.lectura;

-- 2) ventana fija 180 días, percentil p50 vs p75 vs p90 (en vez de mediana)
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_plataforma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_tipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.event_date >= current_date - interval '365 days'
),
cargos as (
    select * from poblacion where order_id is not null
),
orden_costo as (
    select
        order_id, platform, max(event_date) as event_date,
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
    select order_id, product_id, quantity, amount, amount_currency, event_date
    from ledger_event
    where kind = 'sale' and order_id is not null and product_id is not null
),
orden_producto as (
    select
        order_id,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(quantity) as unidades,
        sum(amount) as venta_amount,
        max(amount_currency) as venta_currency
    from ventas_orden
    group by order_id
),
orden_final as (
    select
        oc.order_id, oc.platform, oc.event_date,
        op.product_id, op.unidades, op.venta_amount, op.venta_currency,
        oc.costo_componentes / op.unidades as l_unidad_componentes,
        oc.costo_duplicado_etiqueta / op.unidades as l_unidad_duplicado_etiqueta
    from orden_costo oc
    join orden_producto op using (order_id)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and op.unidades > 0
      and oc.event_date >= current_date - interval '180 days'
),
base_lectura as (
    select product_id, platform, order_id, 'componentes' as lectura, l_unidad_componentes as l_unidad
    from orden_final
    union all
    select product_id, platform, order_id, 'duplicado_etiqueta' as lectura, l_unidad_duplicado_etiqueta as l_unidad
    from orden_final
)
select
    product_id,
    platform,
    lectura,
    count(distinct order_id) as ordenes,
    percentile_cont(0.5) within group (order by l_unidad) as l_unidad_p50,
    percentile_cont(0.75) within group (order by l_unidad) as l_unidad_p75,
    percentile_cont(0.9) within group (order by l_unidad) as l_unidad_p90
from base_lectura
group by product_id, platform, lectura
having count(distinct order_id) >= 10
order by product_id, platform, lectura;
