-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Sonda de formato e identidad de fuente de `shipping_fee`, ventana
-- [hoy - 90, hoy] en UTC (hoy = fecha UTC al correr). Corre primero.
-- Formas medidas en E.1 (docs/evidencia/repricing-01/E.1/medicion.md):
--   finance, 6 partes:        <plataforma>|finance|fee|<order_id>|<sku o vacio>|<subtipo>
--   shipping_label, 4 partes: <plataforma>|shipping_label|<order_id>|<fecha YYYY-MM-DD>
-- Dos SELECT.

-- (a) por plataforma, kind e identidad: filas, ordenes, suma y promedio
with poblacion as (
    select
        le.platform, le.kind, le.order_id, le.amount, le.amount_currency,
        split_part(le.source_event_id, '|', 2) as parte_2,
        split_part(le.source_event_id, '|', 6) as parte_6,
        array_length(string_to_array(le.source_event_id, '|'), 1) as n_partes
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.event_date >= ((now() at time zone 'UTC')::date) - 90
      and le.event_date <= ((now() at time zone 'UTC')::date)
)
select
    platform, kind,
    case when parte_2 = 'finance' then 'finance:' || parte_6 else parte_2 end as identidad,
    n_partes, amount_currency,
    count(*) as filas,
    count(distinct order_id) as ordenes,
    count(*) filter (where order_id is null) as filas_sin_orden,
    sum(amount) as suma,
    round(avg(amount), 2) as promedio
from poblacion
group by 1, 2, 3, 4, 5
order by 1, 2, 3, 4, 5;

-- (b) un ejemplo literal de source_event_id por identidad y plataforma
-- (el de menor id), para leer su forma completa
select distinct on (le.platform, split_part(le.source_event_id, '|', 2), split_part(le.source_event_id, '|', 6))
    le.platform,
    case when split_part(le.source_event_id, '|', 2) = 'finance'
         then 'finance:' || split_part(le.source_event_id, '|', 6)
         else split_part(le.source_event_id, '|', 2) end as identidad,
    le.source_event_id, le.event_date, le.amount, le.amount_currency
from ledger_event le
where le.fee_type = 'shipping_fee'
  and le.event_date >= ((now() at time zone 'UTC')::date) - 90
  and le.event_date <= ((now() at time zone 'UTC')::date)
order by le.platform, split_part(le.source_event_id, '|', 2), split_part(le.source_event_id, '|', 6), le.id;
