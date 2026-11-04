-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Cuantas ordenes traen 1, 2, 3... cargos de `shipping_fee` (kind fee) en
-- la ventana [hoy - 90, hoy] UTC, por plataforma, y con que combinacion de
-- fuentes. Reproduce el conteo del hecho 14 del plan (18 ordenes de US con
-- tres cargos el 2026-09-16); el numero que salga es el que se escribe.
-- Dos SELECT.

-- (a) ordenes por numero de cargos
with cargos as (
    select le.order_id, le.platform
    from ledger_event le
    where le.fee_type = 'shipping_fee' and le.kind = 'fee' and le.order_id is not null
      and le.event_date >= ((now() at time zone 'UTC')::date) - 90
      and le.event_date <= ((now() at time zone 'UTC')::date)
)
select platform, n_cargos, count(*) as ordenes
from (select order_id, platform, count(*) as n_cargos from cargos group by 1, 2) t
group by 1, 2
order by 1, 2;

-- (b) ordenes por combinacion de fuentes (identidades ordenadas)
with cargos as (
    select le.order_id, le.platform,
        case when split_part(le.source_event_id, '|', 2) = 'finance'
             then 'finance:' || split_part(le.source_event_id, '|', 6)
             else coalesce(nullif(split_part(le.source_event_id, '|', 2), ''), 'fuente_desconocida') end as identidad
    from ledger_event le
    where le.fee_type = 'shipping_fee' and le.kind = 'fee' and le.order_id is not null
      and le.event_date >= ((now() at time zone 'UTC')::date) - 90
      and le.event_date <= ((now() at time zone 'UTC')::date)
)
select platform, combinacion, count(*) as ordenes
from (
    select order_id, platform, string_agg(identidad, ' + ' order by identidad) as combinacion
    from cargos group by 1, 2
) t
group by 1, 2
order by 1, 3 desc, 2;
