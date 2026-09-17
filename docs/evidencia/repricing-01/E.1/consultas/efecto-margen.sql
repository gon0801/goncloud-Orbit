-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1
-- Efecto sobre el margen POR UNIDAD (L_unidad = L_orden / unidades_de_la_orden,
-- spec S10 "Por unidad, no por orden") de la decisión de ventana y de
-- percentil.
--
-- Selección de productos (hallazgo 1, ronda r1 — la versión anterior leía
-- la DoD al revés: pedía "al menos 10 productos", no ">=10 órdenes POR
-- producto", y el `having` borraba en silencio la fila de 90 días de
-- cualquier producto con pocas órdenes en esa ventana): se eligen los 10
-- productos con MÁS órdenes a 365 días, por plataforma, y se emiten
-- SIEMPRE las tres ventanas (90/180/365) para esos mismos 10 productos,
-- aunque en alguna ventana corta `ordenes` sea 2 — con una columna
-- `alcanza_minimo` (>=6) por ventana para que se vea a simple vista.
--
-- Costo y FX a la fecha de la VENTA, no del cargo (hallazgo 2, ronda r1):
-- `sku_cost` es el vigente A LA FECHA DE LA VENTA
-- (migrations/0001_initial.sql l.169, "El costo de una venta es el
-- vigente A SU FECHA"), no a la fecha en que Amazon después cobra el
-- envío. `venta_event_date` (de orden_producto) alimenta el lateral de
-- sku_cost y los dos fx_resolve; `event_date` (del cargo) solo se usa
-- para decidir en qué ventana de 90/180/365 cae la orden — es la misma
-- fecha que usan las demás consultas de E.1 para ventanear.
--
-- Sin imputación silenciosa (hallazgo 3, ronda r1): una orden sin
-- sku_cost vigente, o sin FX resuelto, ya NO desaparece del promedio
-- dejando que el resto cargue con su ausencia. `ordenes_sin_costo` y
-- `ordenes_sin_ingreso` cuentan esas órdenes, y el margen del grupo sale
-- NULL si cualquiera de las dos es > 0 (regla 3 de Orbit: un dato
-- faltante nunca es el promedio de los conocidos).
--
-- Ventana SEMIABIERTA [hoy-N, hoy) en UTC (hallazgo 6).
--
-- Ingreso por unidad: amount de la venta (kind='sale') dividido entre
-- quantity de esa fila. Conversión de moneda: fx_resolve(fecha, 'USD',
-- 'MXN') y se opera en MXN; `tiene_fx_pendiente` marca cuando falta la
-- tasa (regla S10 "FX en US": nunca una constante). Esta consulta NO
-- intenta reconstruir comisiones de plataforma ni Ads ni IVA/ISR: es el
-- margen de contribución SIMPLIFICADO (venta − costo − envío) que pide
-- E.1 como insumo — no es comparable contra un goal de margen de la fase
-- B (ver medicion.md).
--
-- LECTURA DUAL de la disputa de E.0: 'componentes' y 'duplicado_etiqueta'
-- (ver envio-por-producto.sql). 'duplicado_etiqueta' es hipótesis del lead.
--
-- Supuesto declarado: "FBM" aquí = producto con al menos una orden en la
-- muestra de envío (poblacion/cargos); esta consulta no filtra por canal
-- porque `ledger_event`/`listing` no traen esa columna (el canal vive en
-- `estimacion_oferta_observation`, hallazgo 14 de plan-validacion-ebm.md,
-- fuera del alcance de lectura de E.1).
--
-- Columna `ordenes_muestreadas` (hallazgo 18, ronda r1): antes se llamaba
-- `unidades_muestreadas` pero contaba ÓRDENES (`count(*)` sobre
-- `final_unidad`, una fila por orden), no unidades reales. Se agrega
-- además `unidades_totales_muestreadas` = suma de `quantity` real.
--
-- Dos SELECT (script, ambos solo lectura):
--   1) margen con L = MEDIANA, comparando ventana 90 vs 180 vs 365 días.
--      "que es la decisión real, no p50 vs p75" (fila E.1 del plan).
--   2) margen a ventana fija de 180 días, comparando percentil p50 vs
--      p75 vs p90 (el hallazgo 5 de plan-validacion-ebm.md midió que esto
--      mueve poco el margen, pero se deja medido, no supuesto).

-- 1) L = mediana, ventana 90 vs 180 vs 365, top 10 productos por plataforma
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
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
sum_por_fuente as (
    select order_id, platform, fuente_tipo, sum(monto_abs) as monto_fuente, count(*) as filas
    from cargos
    group by order_id, platform, fuente_tipo
),
orden_meta as (
    select
        order_id, platform, max(event_date) as event_date,
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
        sf.order_id, sf.platform, om.event_date,
        om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida,
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
        order_id, platform,
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
        oc.order_id, oc.platform, oc.event_date,
        op.product_id, op.unidades, op.venta_amount, op.venta_currency, op.venta_event_date,
        oc.costo_componentes / op.unidades as l_unidad_componentes,
        oc.costo_duplicado_etiqueta / op.unidades as l_unidad_duplicado_etiqueta
    from orden_costo oc
    join orden_producto op using (order_id, platform)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and not oc.tiene_fuente_no_reconocida
      and op.unidades > 0
),
base_lectura as (
    select order_id, platform, product_id, event_date, venta_event_date, unidades, venta_amount, venta_currency,
           'componentes' as lectura, l_unidad_componentes as l_unidad
    from orden_final
    union all
    select order_id, platform, product_id, event_date, venta_event_date, unidades, venta_amount, venta_currency,
           'duplicado_etiqueta' as lectura, l_unidad_duplicado_etiqueta as l_unidad
    from orden_final
),
top10 as (
    -- los 10 productos con más órdenes a 365 días, por plataforma
    -- (hallazgo 1). El conteo no depende de la lectura: se calcula sobre
    -- 'componentes' (ambas lecturas comparten el mismo conjunto de
    -- órdenes, solo cambia el costo).
    select platform, product_id
    from (
        select
            platform, product_id,
            row_number() over (
                partition by platform
                order by count(distinct order_id) desc, product_id
            ) as posicion
        from base_lectura
        where lectura = 'componentes'
        group by platform, product_id
    ) t
    where posicion <= 10
),
venta_mxn as (
    select
        b.*,
        (venta_amount / nullif(unidades, 0)) as ingreso_unidad_original,
        case when venta_currency = 'MXN' then (venta_amount / nullif(unidades, 0))
             else null end as ingreso_unidad_mxn,
        case when venta_currency = 'USD' then fx.rate else null end as fx_rate_venta
    from base_lectura b
    join top10 t on t.platform = b.platform and t.product_id = b.product_id
    left join lateral fx_resolve(b.venta_event_date, 'USD', 'MXN') fx on venta_currency = 'USD'
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
          and sc.valid_from <= v.venta_event_date
          and (sc.valid_to is null or sc.valid_to > v.venta_event_date)
        order by sc.valid_from desc
        limit 1
    ) sc on true
    left join lateral fx_resolve(v.venta_event_date, 'USD', 'MXN') fx2 on sc.cost_currency = 'USD'
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
    count(distinct fu.order_id) as ordenes,
    (count(distinct fu.order_id) >= 6) as alcanza_minimo,
    sum(fu.unidades) as unidades_totales_muestreadas,
    count(distinct fu.order_id) as ordenes_muestreadas,
    count(*) filter (where fu.costo_unidad_mxn is null) as ordenes_sin_costo,
    count(*) filter (where fu.ingreso_unidad_mxn_resuelto is null) as ordenes_sin_ingreso,
    bool_or(fu.fx_pendiente) as tiene_fx_pendiente,
    percentile_cont(0.5) within group (order by fu.l_unidad) as l_unidad_mediana,
    case when count(*) filter (where fu.costo_unidad_mxn is null) = 0
         then avg(fu.costo_unidad_mxn) end as costo_unidad_mxn_promedio,
    case when count(*) filter (where fu.ingreso_unidad_mxn_resuelto is null) = 0
         then avg(fu.ingreso_unidad_mxn_resuelto) end as ingreso_unidad_mxn_promedio,
    case when count(*) filter (where fu.costo_unidad_mxn is null) = 0
          and count(*) filter (where fu.ingreso_unidad_mxn_resuelto is null) = 0
         then avg(fu.ingreso_unidad_mxn_resuelto) - avg(fu.costo_unidad_mxn)
              - percentile_cont(0.5) within group (order by fu.l_unidad)
    end as margen_unidad_mxn_reconstruido
from final_unidad fu
join ventanas v
  on fu.event_date >= (((now() at time zone 'UTC')::date)) - (v.ventana_dias || ' days')::interval
 and fu.event_date <  (((now() at time zone 'UTC')::date))
group by fu.product_id, fu.platform, v.ventana_dias, fu.lectura
order by fu.product_id, fu.platform, v.ventana_dias, fu.lectura;

-- 2) top 10 por plataforma (365 días), ventana fija 180 días, percentil
-- p50 vs p75 vs p90 (en vez de mediana)
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
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
sum_por_fuente as (
    select order_id, platform, fuente_tipo, sum(monto_abs) as monto_fuente, count(*) as filas
    from cargos
    group by order_id, platform, fuente_tipo
),
orden_meta as (
    select
        order_id, platform, max(event_date) as event_date,
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
        sf.order_id, sf.platform, om.event_date,
        om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida,
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
        order_id, platform,
        count(distinct product_id) as n_productos,
        max(product_id) as product_id,
        sum(quantity) as unidades
    from ventas_orden
    group by order_id, platform
),
orden_final as (
    select
        oc.order_id, oc.platform, oc.event_date,
        op.product_id, op.unidades,
        oc.costo_componentes / op.unidades as l_unidad_componentes,
        oc.costo_duplicado_etiqueta / op.unidades as l_unidad_duplicado_etiqueta
    from orden_costo oc
    join orden_producto op using (order_id, platform)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and not oc.tiene_fuente_no_reconocida
      and op.unidades > 0
      and oc.event_date >= (((now() at time zone 'UTC')::date)) - interval '180 days'
      and oc.event_date <  (((now() at time zone 'UTC')::date))
),
base_lectura as (
    select product_id, platform, order_id, 'componentes' as lectura, l_unidad_componentes as l_unidad
    from orden_final
    union all
    select product_id, platform, order_id, 'duplicado_etiqueta' as lectura, l_unidad_duplicado_etiqueta as l_unidad
    from orden_final
),
top10 as (
    select platform, product_id
    from (
        select
            platform, product_id,
            row_number() over (
                partition by platform
                order by count(distinct order_id) desc, product_id
            ) as posicion
        from base_lectura
        where lectura = 'componentes'
        group by platform, product_id
    ) t
    where posicion <= 10
)
select
    bl.product_id,
    bl.platform,
    bl.lectura,
    count(distinct bl.order_id) as ordenes,
    (count(distinct bl.order_id) >= 6) as alcanza_minimo,
    percentile_cont(0.5) within group (order by bl.l_unidad) as l_unidad_p50,
    percentile_cont(0.75) within group (order by bl.l_unidad) as l_unidad_p75,
    percentile_cont(0.9) within group (order by bl.l_unidad) as l_unidad_p90
from base_lectura bl
join top10 t on t.platform = bl.platform and t.product_id = bl.product_id
group by bl.product_id, bl.platform, bl.lectura
order by bl.product_id, bl.platform, bl.lectura;
