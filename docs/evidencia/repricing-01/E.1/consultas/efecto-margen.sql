-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r4a
-- Efecto sobre el margen POR UNIDAD (L_unidad = L_orden / unidades_de_la_orden,
-- spec S10 "Por unidad, no por orden") de la decisión de ventana y de
-- percentil.
--
-- `dias_con_datos` (r4a, ver envio-por-producto.sql y
-- 01-historia-disponible.sql): medido por el lead que "365 días" son en
-- realidad ~287 días de historia — esta columna dice, por fila, cuántos
-- días de esa ventana están realmente cubiertos por datos.
--
-- Selección de productos: se eligen los 10 productos con MÁS órdenes a
-- 365 días, por plataforma (top10), calculado sobre la población COMPLETA
-- de 365 días en LOS DOS SELECT de este archivo (hallazgo 20, ronda r3 —
-- la versión anterior calculaba el top10 del segundo SELECT sobre un
-- conjunto ya recortado a 180 días, contradiciendo su propio comentario
-- que decía "365"; ahora ambos usan el mismo `top10` de 365 días, y solo
-- la ESTADÍSTICA del segundo SELECT se acota a 180 días).
--
-- Grilla completa (hallazgo 20): `top10 × ventanas × lecturas` con CROSS
-- JOIN, y las órdenes reales entran por LEFT JOIN (mismo patrón que
-- parpadeo.sql) — la fila de un producto del top10 SIEMPRE sale, con
-- `ordenes = 0` y `alcanza_minimo = false` cuando no tiene órdenes en esa
-- ventana, en vez de desaparecer de la tabla.
--
-- Costo y FX a la fecha de la VENTA, no del cargo: `sku_cost` es el
-- vigente A LA FECHA DE LA VENTA (migrations/0001_initial.sql l.169).
-- `venta_event_date` alimenta el lateral de `sku_cost` y los dos
-- `fx_resolve`; `event_date` (del cargo) solo decide en qué ventana de
-- 90/180/365 cae la orden.
--
-- Sin imputación silenciosa: `ordenes_sin_costo` y `ordenes_sin_ingreso`
-- cuentan las órdenes sin dato resuelto; el margen del grupo sale `NULL`
-- si cualquiera de las dos es > 0 (regla 3 de Orbit: un dato faltante
-- nunca es el promedio de los conocidos).
--
-- Ventana SEMIABIERTA [hoy-N, hoy) en UTC.
--
-- IDENTIDAD DE FUENTE: ver envio-por-producto.sql (medida en producción
-- por el lead, hallazgo alta de la ronda r3). LECTURA DUAL de la disputa
-- de E.0: 'componentes' y 'duplicado_etiqueta' (hipótesis del lead, NO
-- veredicto de E.0).
--
-- Ingreso por unidad: amount de la venta (kind='sale') dividido entre
-- quantity de esa fila. Conversión de moneda: fx_resolve(fecha, 'USD',
-- 'MXN') y se opera en MXN; `tiene_fx_pendiente` marca cuando falta la
-- tasa (regla S10 "FX en US": nunca una constante), con
-- `coalesce(bool_or(...), false)` (hallazgo 25) para que un producto sin
-- ninguna orden en la ventana muestre `false`, no `NULL`. Esta consulta NO
-- intenta reconstruir comisiones de plataforma ni Ads ni IVA/ISR: es el
-- margen de contribución SIMPLIFICADO (venta − costo − envío) que pide
-- E.1 como insumo — no es comparable contra un goal de margen de la fase
-- B (ver medicion.md).
--
-- Columnas: `ordenes` cuenta órdenes; `unidades_totales_muestreadas` es
-- `sum(quantity)` real. La ronda r1 tenía una columna `ordenes_muestreadas`
-- IDÉNTICA a `ordenes` (`count(distinct order_id)` repetido dos veces);
-- se eliminó (hallazgo 26, ronda r3).
--
-- Supuesto declarado: "FBM" aquí = producto con al menos una orden en la
-- muestra de envío (poblacion/cargos); esta consulta no filtra por canal
-- porque `ledger_event`/`listing` no traen esa columna.
--
-- Dos SELECT (script, ambos solo lectura):
--   1) margen con L = MEDIANA, comparando ventana 90 vs 180 vs 365 días.
--   2) margen a ventana fija de 180 días, comparando percentil p50 vs
--      p75 vs p90.

-- 1) L = mediana, ventana 90 vs 180 vs 365, top 10 productos por plataforma
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
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
sum_por_fuente as (
    select order_id, platform, identidad_fuente, sum(monto_abs) as monto_fuente, count(*) as filas
    from cargos
    group by order_id, platform, identidad_fuente
),
orden_meta as (
    select
        order_id, platform, max(event_date) as event_date,
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
orden_costo as (
    select
        sf.order_id, sf.platform, om.event_date,
        om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida,
        sum(sf.monto_fuente) as costo_componentes,
        greatest(
            coalesce(sum(sf.monto_fuente) filter (
                where sf.identidad_fuente in ('finance:LabmanLabelPurchase', 'finance:MFNPostageFee')
            ), 0),
            coalesce(sum(sf.monto_fuente) filter (where sf.identidad_fuente = 'shipping_label'), 0)
        )
        + coalesce(sum(sf.monto_fuente) filter (
            where sf.identidad_fuente in ('finance:ShippingHB', 'finance:ShippingChargeback', 'finance:MFNShippingChargeback')
          ), 0) as costo_duplicado_etiqueta
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
    -- los 10 productos con más órdenes a 365 días, por plataforma. El
    -- conteo no depende de la lectura: se calcula sobre 'componentes'.
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
ventanas (ventana_dias) as (values (90), (180), (365)),
lecturas (lectura) as (values ('componentes'), ('duplicado_etiqueta')),
grilla as (
    select t.platform, t.product_id, v.ventana_dias, l.lectura
    from top10 t
    cross join ventanas v
    cross join lecturas l
),
agregados as (
    select
        g.platform, g.product_id, g.ventana_dias, g.lectura,
        fu.order_id, fu.unidades, fu.costo_unidad_mxn, fu.ingreso_unidad_mxn_resuelto,
        fu.l_unidad, fu.fx_pendiente
    from grilla g
    left join final_unidad fu
      on fu.platform = g.platform
     and fu.product_id = g.product_id
     and fu.lectura = g.lectura
     and fu.event_date >= (((now() at time zone 'UTC')::date)) - (g.ventana_dias || ' days')::interval
     and fu.event_date <  (((now() at time zone 'UTC')::date))
),
-- dias_con_datos (r4a): ver envio-por-producto.sql.
historia_platform as (
    select platform, min(event_date) as primer_event_date
    from ledger_event
    where fee_type = 'shipping_fee' and kind = 'fee'
    group by platform
)
select
    ag.product_id,
    ag.platform,
    ag.ventana_dias,
    ag.lectura,
    count(ag.order_id) as ordenes,
    (count(ag.order_id) >= 6) as alcanza_minimo,
    coalesce(sum(ag.unidades), 0) as unidades_totales_muestreadas,
    count(*) filter (where ag.order_id is not null and ag.costo_unidad_mxn is null) as ordenes_sin_costo,
    count(*) filter (where ag.order_id is not null and ag.ingreso_unidad_mxn_resuelto is null) as ordenes_sin_ingreso,
    coalesce(bool_or(ag.fx_pendiente), false) as tiene_fx_pendiente,
    percentile_cont(0.5) within group (order by ag.l_unidad) as l_unidad_mediana,
    case when count(*) filter (where ag.order_id is not null and ag.costo_unidad_mxn is null) = 0
         then avg(ag.costo_unidad_mxn) end as costo_unidad_mxn_promedio,
    case when count(*) filter (where ag.order_id is not null and ag.ingreso_unidad_mxn_resuelto is null) = 0
         then avg(ag.ingreso_unidad_mxn_resuelto) end as ingreso_unidad_mxn_promedio,
    case when count(*) filter (where ag.order_id is not null and ag.costo_unidad_mxn is null) = 0
          and count(*) filter (where ag.order_id is not null and ag.ingreso_unidad_mxn_resuelto is null) = 0
         then avg(ag.ingreso_unidad_mxn_resuelto) - avg(ag.costo_unidad_mxn)
              - percentile_cont(0.5) within group (order by ag.l_unidad)
    end as margen_unidad_mxn_reconstruido,
    (((now() at time zone 'UTC')::date)
        - greatest(
            ((((now() at time zone 'UTC')::date)) - (ag.ventana_dias || ' days')::interval)::date,
            hp.primer_event_date
          )) as dias_con_datos
from agregados ag
left join historia_platform hp on hp.platform = ag.platform
group by ag.product_id, ag.platform, ag.ventana_dias, ag.lectura, hp.primer_event_date
order by ag.product_id, ag.platform, ag.ventana_dias, ag.lectura;

-- 2) ventana FIJA de 180 días (solo para la ESTADÍSTICA), pero el top10
-- se calcula sobre la MISMA población de 365 días que el SELECT 1
-- (hallazgo 20 — antes este bloque calculaba su propio top10 ya recortado
-- a 180 días, contradiciendo su comentario). Percentil p50 vs p75 vs p90
-- (en vez de mediana). Misma grilla completa (top10 × lecturas) que el
-- bloque 1: la fila de un producto del top10 sale siempre.
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
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
sum_por_fuente as (
    select order_id, platform, identidad_fuente, sum(monto_abs) as monto_fuente, count(*) as filas
    from cargos
    group by order_id, platform, identidad_fuente
),
orden_meta as (
    select
        order_id, platform, max(event_date) as event_date,
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
orden_costo as (
    select
        sf.order_id, sf.platform, om.event_date,
        om.tiene_fuente_desconocida, om.tiene_fuente_no_reconocida,
        sum(sf.monto_fuente) as costo_componentes,
        greatest(
            coalesce(sum(sf.monto_fuente) filter (
                where sf.identidad_fuente in ('finance:LabmanLabelPurchase', 'finance:MFNPostageFee')
            ), 0),
            coalesce(sum(sf.monto_fuente) filter (where sf.identidad_fuente = 'shipping_label'), 0)
        )
        + coalesce(sum(sf.monto_fuente) filter (
            where sf.identidad_fuente in ('finance:ShippingHB', 'finance:ShippingChargeback', 'finance:MFNShippingChargeback')
          ), 0) as costo_duplicado_etiqueta
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
),
base_lectura as (
    select order_id, platform, product_id, event_date, 'componentes' as lectura, l_unidad_componentes as l_unidad
    from orden_final
    union all
    select order_id, platform, product_id, event_date, 'duplicado_etiqueta' as lectura, l_unidad_duplicado_etiqueta as l_unidad
    from orden_final
),
top10 as (
    -- MISMA base de 365 días (sin recortar a 180) que el SELECT 1 —
    -- hallazgo 20: el top10 no depende de la ventana de la estadística.
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
lecturas (lectura) as (values ('componentes'), ('duplicado_etiqueta')),
grilla as (
    select t.platform, t.product_id, l.lectura
    from top10 t
    cross join lecturas l
),
agregados as (
    select
        g.platform, g.product_id, g.lectura,
        bl.order_id, bl.l_unidad
    from grilla g
    left join base_lectura bl
      on bl.platform = g.platform
     and bl.product_id = g.product_id
     and bl.lectura = g.lectura
     and bl.event_date >= (((now() at time zone 'UTC')::date)) - interval '180 days'
     and bl.event_date <  (((now() at time zone 'UTC')::date))
),
-- dias_con_datos (r4a): ver envio-por-producto.sql. Ventana fija de 180
-- días en este bloque.
historia_platform as (
    select platform, min(event_date) as primer_event_date
    from ledger_event
    where fee_type = 'shipping_fee' and kind = 'fee'
    group by platform
)
select
    ag.product_id,
    ag.platform,
    ag.lectura,
    count(ag.order_id) as ordenes,
    (count(ag.order_id) >= 6) as alcanza_minimo,
    percentile_cont(0.5) within group (order by ag.l_unidad) as l_unidad_p50,
    percentile_cont(0.75) within group (order by ag.l_unidad) as l_unidad_p75,
    percentile_cont(0.9) within group (order by ag.l_unidad) as l_unidad_p90,
    (((now() at time zone 'UTC')::date)
        - greatest(
            ((((now() at time zone 'UTC')::date)) - interval '180 days')::date,
            hp.primer_event_date
          )) as dias_con_datos
from agregados ag
left join historia_platform hp on hp.platform = ag.platform
group by ag.product_id, ag.platform, ag.lectura, hp.primer_event_date
order by ag.product_id, ag.platform, ag.lectura;
