-- ORBIT · fase 8 · repricing-01 · E.1
-- Rezago de INGESTA: entre event_date del cargo y observed_at (la corrida
-- que lo trajo a Orbit). p50/p90/máximo por plataforma. Distinto del
-- rezago de EMISIÓN (ver rezago-emision.sql) — v1 mezclaba las dos
-- (hallazgo 7 de plan-validacion-ebm.md).

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
)
select
    platform,
    count(*) as filas,
    percentile_cont(0.5) within group (order by (observed_at::date - event_date)) as rezago_ingesta_p50_dias,
    percentile_cont(0.9) within group (order by (observed_at::date - event_date)) as rezago_ingesta_p90_dias,
    max(observed_at::date - event_date) as rezago_ingesta_max_dias
from poblacion
group by platform
order by platform;
