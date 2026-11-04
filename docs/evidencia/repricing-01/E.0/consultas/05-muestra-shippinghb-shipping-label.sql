-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Muestra de 20 pares `finance:ShippingHB` / `shipping_label` de la misma
-- orden (ventana de E.1, 365 dias, como el hecho 22: 521 pares, 100 %
-- difieren > 5 %). Muestra deterministica: las 20 primeras por md5 del
-- order_id, para que una re-corrida traiga las mismas ordenes. Mas un
-- resumen del par completo para ver si ShippingHB acompana a la etiqueta
-- o la sustituye. Sin veredicto: es insumo. Dos SELECT.

-- (a) muestra de 20
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
    select order_id, platform, identidad, sum(monto) as monto, min(event_date) as fecha
    from cargos group by 1, 2, 3
),
venta as (
    select order_id, platform, string_agg(distinct product_id::text, ',') as productos,
        sum(quantity) as unidades, sum(amount) as venta
    from ledger_event where kind = 'sale' group by 1, 2
)
select
    hb.order_id, hb.platform, hb.monto as shippinghb, sl.monto as shipping_label,
    round(hb.monto / nullif(sl.monto, 0), 4) as hb_sobre_label,
    hb.fecha as fecha_hb, sl.fecha as fecha_label,
    lb.monto as labman, v.productos, v.unidades, v.venta
from por hb
join por sl on sl.order_id = hb.order_id and sl.platform = hb.platform and sl.identidad = 'shipping_label'
left join por lb on lb.order_id = hb.order_id and lb.platform = hb.platform and lb.identidad = 'finance:LabmanLabelPurchase'
left join venta v on v.order_id = hb.order_id and v.platform = hb.platform
where hb.identidad = 'finance:ShippingHB'
order by md5(hb.order_id)
limit 20;

-- (b) resumen por plataforma: cuantas ordenes traen cada fuente sola o
-- junto con las otras (365 dias, mismo filtro)
with cargos as (
    select le.order_id, le.platform,
        case when split_part(le.source_event_id, '|', 2) = 'finance'
             then 'finance:' || split_part(le.source_event_id, '|', 6)
             else split_part(le.source_event_id, '|', 2) end as identidad
    from ledger_event le
    where le.fee_type = 'shipping_fee' and le.kind = 'fee' and le.order_id is not null
      and le.event_date >= ((now() at time zone 'UTC')::date) - 365
      and le.event_date <  ((now() at time zone 'UTC')::date)
),
o as (
    select order_id, platform,
        bool_or(identidad = 'finance:ShippingHB') as hb,
        bool_or(identidad = 'shipping_label') as label,
        bool_or(identidad = 'finance:LabmanLabelPurchase') as labman,
        bool_or(identidad = 'finance:MFNPostageFee') as postage
    from cargos group by 1, 2
)
select platform, hb, label, labman, postage, count(*) as ordenes
from o group by 1, 2, 3, 4, 5
order by 1, 6 desc;
