-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1
-- Distribución de cargos por orden y desglose por fuente. Insumo de la
-- disputa del hecho 14 (¿tres componentes distintos o el mismo cobro
-- informado dos veces?) — esta consulta NO emite veredicto, eso es E.0.
--
-- Tres SELECT independientes (documentados en medicion.md con su propia
-- tabla), cada uno repite literal el CTE poblacion/cargos (hecho 13: un
-- solo filtro):
--   (a) distribución: cuántas órdenes traen 1/2/3... cargos, por plataforma.
--   (b) desglose de monto por orden y por fuente, ACOTADO (hallazgo 19,
--       ronda r1) a las órdenes que de verdad importan a la disputa: más
--       de una fuente distinta, o más de una fila de la MISMA fuente
--       (hallazgo 7) — no todas las órdenes usables, que no aportan nada
--       a la disputa.
--   (c) las órdenes con exactamente 3 cargos, con sus montos por fuente.
--
-- fuente_no_reconocida (hallazgo 8): cuando fuente_tipo trae un valor no
-- vacío pero fuera de {ShippingHB, LabmanLabelPurchase, shipping_label},
-- el desglose lo marca con el prefijo 'fuente_no_reconocida:' y conserva
-- el valor real, para que no se pierda entre las tres fuentes conocidas.

-- (a) distribución: cuántas órdenes traen 1, 2, 3... cargos
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
por_fuente as (
    select
        order_id, platform,
        case
            when fuente_tipo is null then 'fuente_desconocida'
            when fuente_tipo not in ('ShippingHB', 'LabmanLabelPurchase', 'shipping_label')
                then 'fuente_no_reconocida:' || fuente_tipo
            else fuente_tipo
        end as fuente,
        sum(monto_abs) as monto,
        count(*) as filas
    from cargos
    group by order_id, platform, fuente_tipo
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
        when c.fuente_tipo is null then 'fuente_desconocida'
        when c.fuente_tipo not in ('ShippingHB', 'LabmanLabelPurchase', 'shipping_label')
            then 'fuente_no_reconocida:' || c.fuente_tipo
        else c.fuente_tipo
    end as fuente,
    c.monto_abs
from cargos c
join tres_cargos t using (order_id, platform)
order by c.order_id, c.platform, fuente;
