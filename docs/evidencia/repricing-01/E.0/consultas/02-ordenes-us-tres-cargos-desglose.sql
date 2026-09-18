-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Las ordenes de amazon_us con EXACTAMENTE tres cargos de `shipping_fee`
-- (kind fee) en la ventana [hoy - 90, hoy] UTC, cada cargo con su
-- source_event_id completo, fee_type, monto, moneda, event_date, cuando se
-- observo y de que corrida de ingesta vino. Es la lista literal que E.0a
-- pide desglosar y la que el documento de origen tiene que resolver.
-- El source_event_id va en la ULTIMA columna (trae '|' y psql -tA separa
-- con '|'); `observado` es el dia UTC de observed_at (r1, grok).
-- Un SELECT.
with cargos as (
    select le.*
    from ledger_event le
    where le.fee_type = 'shipping_fee' and le.kind = 'fee' and le.order_id is not null
      and le.platform = 'amazon_us'
      and le.event_date >= ((now() at time zone 'UTC')::date) - 90
      and le.event_date <= ((now() at time zone 'UTC')::date)
),
tres as (
    select order_id from cargos group by order_id having count(*) = 3
)
select
    c.order_id,
    case when split_part(c.source_event_id, '|', 2) = 'finance'
         then 'finance:' || split_part(c.source_event_id, '|', 6)
         else split_part(c.source_event_id, '|', 2) end as identidad,
    c.fee_type, c.amount, c.amount_currency, c.event_date,
    (c.observed_at at time zone 'UTC')::date as observado, c.ingest_run_id,
    c.source_event_id
from cargos c
join tres t using (order_id)
order by c.order_id, identidad;
