-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r3
-- Parpadeo del mínimo (hecho 15): seis ventanas móviles de 90 días,
-- cuántos productos entran (>=6 órdenes) y salen (<6) del mínimo a lo
-- largo de ellas.
--
-- Identidad de fuente: ver envio-por-producto.sql (medida en producción,
-- hallazgo alta de la ronda r3). Cobertura por fuente: publicada solo en
-- envio-por-producto.sql (hallazgo 23), no repetida aquí.
--
-- Tres conteos (hallazgo 28, ronda r3 — separa "salida" de "reentrada",
-- antes era una sola columna que mezclaba ambas):
--   productos_que_parpadean               : corte seco, sin histéresis —
--                                            cualquier cruce de 6 cuenta.
--   productos_con_salida_con_histeresis   : hubo al menos una salida
--                                            EXPLÍCITA (ordenes < 3)
--                                            justo después de haber
--                                            estado evaluado.
--   productos_con_reentrada_con_histeresis: hubo al menos una entrada
--                                            (ordenes >= 6) que ocurre
--                                            DESPUÉS de una salida
--                                            explícita ya contada — no
--                                            cualquier primera entrada,
--                                            sino un ciclo completo
--                                            entra→sale→reentra.
--
-- Ventana SEMIABIERTA [hoy-N, hoy) en UTC (hallazgo 6), igual que las
-- demás consultas de E.1.
--
-- Supuesto declarado: las seis ventanas se desplazan 30 días entre sí
-- (ventana i cubre [hoy - 90 - 30*i, hoy - 30*i)), como aproximación
-- razonable al método del hallazgo 8 de plan-validacion-ebm.md, que no
-- especifica el desplazamiento exacto. Si el desplazamiento real difiere,
-- el número cambia; la mecánica (agrupar por orden, dos lecturas, grilla
-- completa) no.

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
sum_por_fuente as (
    select
        order_id,
        platform,
        identidad_fuente,
        sum(monto_abs) as monto_fuente,
        count(*) as filas
    from cargos
    group by order_id, platform, identidad_fuente
),
orden_meta as (
    select
        order_id,
        platform,
        max(event_date) as event_date,
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
        sf.order_id,
        sf.platform,
        om.event_date,
        om.tiene_fuente_desconocida,
        om.tiene_fuente_no_reconocida,
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
        op.product_id
    from orden_costo oc
    join orden_producto op using (order_id, platform)
    where op.n_productos = 1
      and not oc.tiene_fuente_desconocida
      and not oc.tiene_fuente_no_reconocida
),
base_lectura as (
    select order_id, platform, product_id, event_date, 'componentes' as lectura from orden_final
    union all
    select order_id, platform, product_id, event_date, 'duplicado_etiqueta' as lectura from orden_final
),
-- índice ascendente = ventana más VIEJA primero (offset_dias mayor),
-- necesario para recorrer con histéresis de vieja a nueva.
ventanas_moviles (indice, offset_dias) as (
    values (1, 150), (2, 120), (3, 90), (4, 60), (5, 30), (6, 0)
),
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
     and b.event_date >= (((now() at time zone 'UTC')::date)) - g.offset_dias - interval '90 days'
     and b.event_date <  (((now() at time zone 'UTC')::date)) - g.offset_dias
    group by g.product_id, g.platform, g.lectura, g.indice
),
-- histéresis: recorre índice 1→6 (vieja→nueva) con un estado
-- evaluado/envio_sin_historia que solo cambia a evaluado con >=6 y solo
-- cambia a envio_sin_historia con <3; entre 3 y 5 conserva el estado
-- anterior. window function con frame por fila (no hay agregado nativo de
-- "estado con memoria" en SQL puro).
con_estado as (
    select
        product_id, platform, lectura, indice, ordenes,
        (
            select case
                when ordenes >= 6 then true
                when ordenes < 3 then false
                else null
            end
        ) as transicion
    from conteo_ventana
),
-- arrastra el último estado no-nulo hacia adelante (last_value con IGNORE
-- NULLS no existe en Postgres < 18; se emula con una subconsulta
-- correlacionada sobre el índice, dataset pequeño por producto: 6 filas).
con_arrastre as (
    select
        ce.*,
        coalesce(
            ce.transicion,
            (
                select ce2.transicion
                from con_estado ce2
                where ce2.product_id = ce.product_id
                  and ce2.platform = ce.platform
                  and ce2.lectura = ce.lectura
                  and ce2.indice < ce.indice
                  and ce2.transicion is not null
                order by ce2.indice desc
                limit 1
            ),
            false  -- antes de la primera vez que llega a 6, está fuera
        ) as estado_con_histeresis
    from con_estado ce
),
-- estado_previo: el estado YA ARRASTRADO en la ventana anterior (más
-- vieja), para detectar una salida REAL (no solo "todavía no había
-- entrado"): una fila con transicion=false (ordenes<3 explícito) cuya
-- ventana anterior ya estaba evaluado (estado_previo=true) es una salida
-- genuina, distinta de "el producto simplemente no tenía historia antes".
con_cambio as (
    select
        ca.*,
        lag(ca.estado_con_histeresis) over (
            partition by ca.product_id, ca.platform, ca.lectura
            order by ca.indice
        ) as estado_previo
    from con_arrastre ca
),
con_salida as (
    select
        cc.*,
        (cc.transicion = false and cc.estado_previo = true) as es_salida
    from con_cambio cc
),
-- hubo_salida_previa: ¿ya ocurrió una salida real en una ventana MÁS
-- VIEJA que esta? (frame excluye la fila actual). Distingue la PRIMERA
-- entrada (nunca hubo salida antes: no es "reentrada") de una reentrada
-- genuina después de un ciclo completo entra→sale.
con_reentrada as (
    select
        cs.*,
        coalesce(bool_or(cs.es_salida) over (
            partition by cs.product_id, cs.platform, cs.lectura
            order by cs.indice
            rows between unbounded preceding and 1 preceding
        ), false) as hubo_salida_previa
    from con_salida cs
),
clasificado as (
    select
        product_id, platform, lectura,
        count(*) filter (where ordenes >= 6) as ventanas_en_minimo,
        -- corte seco: cualquier cruce de 6, sin distinguir "nunca había
        -- entrado" de "entró y salió" (sobreestima el parpadeo real).
        bool_or(ordenes >= 6) and bool_or(ordenes < 6) as parpadea,
        bool_or(es_salida) as tiene_salida_con_histeresis,
        bool_or(transicion = true and hubo_salida_previa) as tiene_reentrada_con_histeresis
    from con_reentrada
    group by product_id, platform, lectura
)
select
    platform,
    lectura,
    count(*) filter (where ventanas_en_minimo > 0) as productos_que_alcanzan_minimo_en_alguna_ventana,
    count(*) filter (where ventanas_en_minimo = 6) as productos_estables_en_las_seis,
    count(*) filter (where parpadea) as productos_que_parpadean,
    count(*) filter (where tiene_salida_con_histeresis) as productos_con_salida_con_histeresis,
    count(*) filter (where tiene_reentrada_con_histeresis) as productos_con_reentrada_con_histeresis
from clasificado
group by platform, lectura
order by platform, lectura;
