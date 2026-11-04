-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Historia mensual de cada identidad de fuente de `shipping_fee` (kind fee)
-- desde el inicio del ledger: filas, ordenes y primera y ultima fecha. Sirve
-- para fechar cuando empezo a llegar `finance:LabmanLabelPurchase` y si su
-- llegada coincide con un cambio en las otras fuentes. Dos SELECT.

-- (a) por plataforma, identidad y mes
select
    le.platform,
    case when split_part(le.source_event_id, '|', 2) = 'finance'
         then 'finance:' || split_part(le.source_event_id, '|', 6)
         else split_part(le.source_event_id, '|', 2) end as identidad,
    to_char(le.event_date, 'YYYY-MM') as mes,
    count(*) as filas,
    count(distinct le.order_id) as ordenes
from ledger_event le
where le.fee_type = 'shipping_fee' and le.kind = 'fee'
group by 1, 2, 3
order by 1, 2, 3;

-- (b) primera y ultima fecha por plataforma e identidad
select
    le.platform,
    case when split_part(le.source_event_id, '|', 2) = 'finance'
         then 'finance:' || split_part(le.source_event_id, '|', 6)
         else split_part(le.source_event_id, '|', 2) end as identidad,
    min(le.event_date) as primera, max(le.event_date) as ultima, count(*) as filas
from ledger_event le
where le.fee_type = 'shipping_fee' and le.kind = 'fee'
group by 1, 2
order by 1, 2;
