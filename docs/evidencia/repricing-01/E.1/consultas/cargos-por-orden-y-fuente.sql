-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r4a
-- Distribución de cargos por orden y desglose por fuente. Insumo de la
-- disputa del hecho 14 (¿tres componentes distintos o el mismo cobro
-- informado dos veces?) — esta consulta NO emite veredicto, eso es E.0.
--
-- Identidad de fuente: ver envio-por-producto.sql (medida en producción
-- por el lead). `fuente_no_reconocida:<id>` marca cualquier identidad
-- fuera de las seis conocidas, conservando el valor real para que no se
-- pierda.
--
-- Cinco SELECT independientes (documentados en medicion.md con su propia
-- tabla), cada uno repite literal el CTE poblacion/cargos (hecho 13: un
-- solo filtro):
--   (a) distribución: cuántas órdenes traen 1/2/3... cargos, por plataforma.
--   (b) desglose de monto por orden y por fuente, ACOTADO a las órdenes
--       que de verdad importan a la disputa: más de una fuente distinta,
--       o más de una fila de la MISMA fuente.
--   (c) las órdenes con exactamente 3 cargos, con sus montos por fuente.
--   (d) PARES DE ETIQUETA (r4a): para cada orden con compra de etiqueta
--       por `finance` (`finance:LabmanLabelPurchase` o
--       `finance:MFNPostageFee`) Y fila `shipping_label`, el monto de
--       cada una, su diferencia absoluta y su cociente (mayor/menor).
--       Medido por el lead 2026-09-17: una orden trae
--       `finance:LabmanLabelPurchase` 452.69 y `shipping_label` 449.69
--       (casi iguales) más `finance:ShippingHB` 88.80 — bajo
--       `componentes` esas órdenes cuestan ~990, el doble de los ~540
--       típicos. SIN VEREDICTO: es insumo de E.0, no dice si duplican.
--   (e) resumen por plataforma del (d): órdenes, p50/p90/máximo de la
--       diferencia absoluta y del cociente, y cuántas difieren ≤1%, entre
--       1% y 5%, y >5% (base: diferencia absoluta sobre el monto mayor de
--       los dos). Medido por el lead: 72 de 454 grupos producto-ventana
--       cambian entre lecturas `componentes`/`duplicado_etiqueta`, todos
--       en US.

-- (a) distribución: cuántas órdenes traen 1, 2, 3... cargos
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
    select *
    from poblacion
    where order_id is not null
),
resumen as (
    select order_id, platform, count(*) as n_cargos
    from cargos
    group by order_id, platform
)
select platform, n_cargos, count(*) as ordenes
from resumen
group by platform, n_cargos
order by platform, n_cargos;

-- (b) desglose de monto por orden y por fuente, SOLO órdenes con más de
-- una fuente distinta o con más de una fila de la misma fuente.
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
    select *
    from poblacion
    where order_id is not null
),
por_fuente as (
    select
        order_id, platform,
        case
            when source_event_id is null or fuente_forma is null then 'fuente_desconocida'
            when fuente_forma = 'shipping_label' then 'shipping_label'
            when fuente_forma = 'finance' and fuente_subtipo is not null
                and ('finance:' || fuente_subtipo) in (
                    'finance:LabmanLabelPurchase', 'finance:MFNPostageFee', 'finance:ShippingHB',
                    'finance:ShippingChargeback', 'finance:MFNShippingChargeback'
                )
                then 'finance:' || fuente_subtipo
            when fuente_forma = 'finance' and fuente_subtipo is not null
                then 'fuente_no_reconocida:finance:' || fuente_subtipo
            when fuente_forma = 'finance' then 'fuente_desconocida'
            else 'fuente_no_reconocida:' || fuente_forma
        end as fuente,
        sum(monto_abs) as monto,
        count(*) as filas
    from cargos
    group by order_id, platform, fuente_forma, fuente_subtipo, source_event_id is null
),
ordenes_de_interes as (
    select order_id, platform
    from por_fuente
    group by order_id, platform
    having count(*) > 1 or bool_or(filas > 1)
)
select pf.order_id, pf.platform, pf.fuente, pf.monto, pf.filas
from por_fuente pf
join ordenes_de_interes oi using (order_id, platform)
order by pf.order_id, pf.platform, pf.fuente;

-- (c) órdenes con exactamente 3 cargos, con sus montos por fuente
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
    select *
    from poblacion
    where order_id is not null
),
tres_cargos as (
    select order_id, platform
    from cargos
    group by order_id, platform
    having count(*) = 3
)
select
    c.order_id,
    c.platform,
    case
        when c.source_event_id is null or c.fuente_forma is null then 'fuente_desconocida'
        when c.fuente_forma = 'shipping_label' then 'shipping_label'
        when c.fuente_forma = 'finance' and c.fuente_subtipo is not null
            and ('finance:' || c.fuente_subtipo) in (
                'finance:LabmanLabelPurchase', 'finance:MFNPostageFee', 'finance:ShippingHB',
                'finance:ShippingChargeback', 'finance:MFNShippingChargeback'
            )
            then 'finance:' || c.fuente_subtipo
        when c.fuente_forma = 'finance' and c.fuente_subtipo is not null
            then 'fuente_no_reconocida:finance:' || c.fuente_subtipo
        when c.fuente_forma = 'finance' then 'fuente_desconocida'
        else 'fuente_no_reconocida:' || c.fuente_forma
    end as fuente,
    c.monto_abs
from cargos c
join tres_cargos t using (order_id, platform)
order by c.order_id, c.platform, fuente;

-- (d) pares de etiqueta: finance-etiqueta vs shipping_label, misma
-- orden. SIN VEREDICTO (ver cabecera).
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
    select order_id, platform, identidad_fuente, sum(monto_abs) as monto
    from cargos
    group by order_id, platform, identidad_fuente
),
finance_etiqueta as (
    select order_id, platform, sum(monto) as monto_finance_etiqueta
    from sum_por_fuente
    where identidad_fuente in ('finance:LabmanLabelPurchase', 'finance:MFNPostageFee')
    group by order_id, platform
),
shipping_label_monto as (
    select order_id, platform, monto as monto_shipping_label
    from sum_por_fuente
    where identidad_fuente = 'shipping_label'
),
pares as (
    select
        fe.order_id, fe.platform,
        fe.monto_finance_etiqueta, sl.monto_shipping_label,
        abs(fe.monto_finance_etiqueta - sl.monto_shipping_label) as diferencia_absoluta,
        greatest(fe.monto_finance_etiqueta, sl.monto_shipping_label)
            / nullif(least(fe.monto_finance_etiqueta, sl.monto_shipping_label), 0) as cociente
    from finance_etiqueta fe
    join shipping_label_monto sl using (order_id, platform)
)
select order_id, platform, monto_finance_etiqueta, monto_shipping_label, diferencia_absoluta, cociente
from pares
order by order_id, platform;

-- (e) resumen por plataforma de los pares de etiqueta
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
    select order_id, platform, identidad_fuente, sum(monto_abs) as monto
    from cargos
    group by order_id, platform, identidad_fuente
),
finance_etiqueta as (
    select order_id, platform, sum(monto) as monto_finance_etiqueta
    from sum_por_fuente
    where identidad_fuente in ('finance:LabmanLabelPurchase', 'finance:MFNPostageFee')
    group by order_id, platform
),
shipping_label_monto as (
    select order_id, platform, monto as monto_shipping_label
    from sum_por_fuente
    where identidad_fuente = 'shipping_label'
),
pares as (
    select
        fe.order_id, fe.platform,
        fe.monto_finance_etiqueta, sl.monto_shipping_label,
        abs(fe.monto_finance_etiqueta - sl.monto_shipping_label) as diferencia_absoluta,
        greatest(fe.monto_finance_etiqueta, sl.monto_shipping_label)
            / nullif(least(fe.monto_finance_etiqueta, sl.monto_shipping_label), 0) as cociente,
        -- diferencia porcentual sobre el monto MAYOR de los dos, solo
        -- para clasificar en los tres baldes de abajo
        abs(fe.monto_finance_etiqueta - sl.monto_shipping_label)
            / nullif(greatest(fe.monto_finance_etiqueta, sl.monto_shipping_label), 0) as diferencia_pct
    from finance_etiqueta fe
    join shipping_label_monto sl using (order_id, platform)
)
select
    platform,
    count(*) as ordenes,
    percentile_cont(0.5) within group (order by diferencia_absoluta) as diferencia_absoluta_p50,
    percentile_cont(0.9) within group (order by diferencia_absoluta) as diferencia_absoluta_p90,
    max(diferencia_absoluta) as diferencia_absoluta_max,
    percentile_cont(0.5) within group (order by cociente) as cociente_p50,
    percentile_cont(0.9) within group (order by cociente) as cociente_p90,
    max(cociente) as cociente_max,
    count(*) filter (where diferencia_pct <= 0.01) as difieren_hasta_1pct,
    count(*) filter (where diferencia_pct > 0.01 and diferencia_pct <= 0.05) as difieren_1_a_5pct,
    count(*) filter (where diferencia_pct > 0.05) as difieren_mas_5pct
from pares
group by platform
order by platform;
