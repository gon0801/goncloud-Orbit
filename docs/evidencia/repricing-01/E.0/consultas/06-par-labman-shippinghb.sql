-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- El tercer par: `finance:LabmanLabelPurchase` / `finance:ShippingHB` de la
-- misma orden (365 dias, filtro de E.1). Resumen por plataforma de la
-- diferencia relativa en los mismos baldes que E.1 (<= 1 %, 1-5 %, > 5 %).
-- Un SELECT.
with cargos as (
    select le.order_id, le.platform, abs(le.amount) as monto,
        case when split_part(le.source_event_id, '|', 2) = 'finance'
             then 'finance:' || split_part(le.source_event_id, '|', 6)
             else split_part(le.source_event_id, '|', 2) end as identidad
    from ledger_event le
    where le.fee_type = 'shipping_fee' and le.kind = 'fee' and le.order_id is not null
      and le.event_date >= ((now() at time zone 'UTC')::date) - 365
      and le.event_date <  ((now() at time zone 'UTC')::date)
),
por as (
    select order_id, platform, identidad, sum(monto) as monto from cargos group by 1, 2, 3
),
pares as (
    select lb.platform, lb.monto as labman, hb.monto as hb,
        abs(lb.monto - hb.monto) / nullif(greatest(lb.monto, hb.monto), 0) as dif_pct
    from por lb
    join por hb on hb.order_id = lb.order_id and hb.platform = lb.platform
     and hb.identidad = 'finance:ShippingHB'
    where lb.identidad = 'finance:LabmanLabelPurchase'
)
select platform, count(*) as ordenes,
    count(*) filter (where dif_pct <= 0.01) as hasta_1pct,
    count(*) filter (where dif_pct > 0.01 and dif_pct <= 0.05) as de_1_a_5pct,
    count(*) filter (where dif_pct > 0.05) as mas_5pct,
    round(avg(labman), 2) as labman_prom, round(avg(hb), 2) as hb_prom
from pares group by 1 order by 1;
