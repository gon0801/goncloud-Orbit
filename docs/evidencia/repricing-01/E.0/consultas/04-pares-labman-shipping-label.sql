-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Los pares `finance:LabmanLabelPurchase` / `shipping_label` de la misma
-- orden y plataforma, con la MISMA ventana de E.1 que dio el hecho 22
-- (365 dias, [hoy - 365, hoy) UTC: 53 de 54 pares difieren <= 1 %), para
-- leer el par completo: montos, fechas de cada fuente, si la orden trae
-- ademas `finance:ShippingHB`, y su venta. Sin veredicto: es insumo.
-- Un SELECT.
with cargos as (
    select le.order_id, le.platform, le.event_date, abs(le.amount) as monto,
        case when split_part(le.source_event_id, '|', 2) = 'finance'
             then 'finance:' || split_part(le.source_event_id, '|', 6)
             else split_part(le.source_event_id, '|', 2) end as identidad
    from ledger_event le
    where le.fee_type = 'shipping_fee' and le.kind = 'fee' and le.order_id is not null
      and le.event_date >= ((now() at time zone 'UTC')::date) - 365
      and le.event_date <  ((now() at time zone 'UTC')::date)
),
por as (
    select order_id, platform, identidad, sum(monto) as monto, count(*) as filas,
        min(event_date) as fecha_min, max(event_date) as fecha_max
    from cargos group by 1, 2, 3
),
venta as (
    select order_id, platform, min(event_date) as fecha_venta,
        string_agg(distinct product_id::text, ',') as productos, sum(quantity) as unidades
    from ledger_event where kind = 'sale' group by 1, 2
)
select
    lb.order_id, lb.platform,
    lb.monto as labman, sl.monto as shipping_label,
    abs(lb.monto - sl.monto) as dif_abs,
    round(abs(lb.monto - sl.monto) / nullif(greatest(lb.monto, sl.monto), 0), 4) as dif_pct,
    lb.fecha_min as fecha_labman, sl.fecha_min as fecha_label,
    (sl.fecha_min - lb.fecha_min) as dias_label_menos_labman,
    hb.monto as shippinghb,
    v.fecha_venta, v.productos, v.unidades,
    lb.filas as filas_labman, sl.filas as filas_label
from por lb
join por sl on sl.order_id = lb.order_id and sl.platform = lb.platform and sl.identidad = 'shipping_label'
left join por hb on hb.order_id = lb.order_id and hb.platform = lb.platform and hb.identidad = 'finance:ShippingHB'
left join venta v on v.order_id = lb.order_id and v.platform = lb.platform
where lb.identidad = 'finance:LabmanLabelPurchase'
order by lb.platform, dif_pct, lb.order_id;
