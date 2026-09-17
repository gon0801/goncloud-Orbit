-- ORBIT · fase 8 · repricing-01 · E.1
-- Distribución de cargos por orden y desglose por fuente. Insumo de la
-- disputa del hecho 14 (¿tres componentes distintos o el mismo cobro
-- informado dos veces?) — esta consulta NO emite veredicto, eso es E.0.
--
-- Tres SELECT independientes, cada uno repite literal el CTE poblacion/
-- cargos (hecho 13: un solo filtro).

-- 1) distribución: cuántas órdenes traen 1, 2, 3... cargos
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
resumen as (
    select order_id, platform, count(*) as n_cargos
    from cargos
    group by order_id, platform
)
select platform, n_cargos, count(*) as ordenes
from resumen
group by platform, n_cargos
order by platform, n_cargos;

-- 2) desglose de monto por orden y por fuente
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
)
select
    order_id,
    platform,
    coalesce(fuente_tipo, 'fuente_desconocida') as fuente,
    sum(monto_abs) as monto
from cargos
group by order_id, platform, coalesce(fuente_tipo, 'fuente_desconocida')
order by order_id, fuente;

-- 3) órdenes con exactamente 3 cargos, con sus montos por fuente
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
tres_cargos as (
    select order_id
    from cargos
    group by order_id
    having count(*) = 3
)
select
    c.order_id,
    c.platform,
    coalesce(c.fuente_tipo, 'fuente_desconocida') as fuente,
    c.monto_abs
from cargos c
join tres_cargos t using (order_id)
order by c.order_id, fuente;
