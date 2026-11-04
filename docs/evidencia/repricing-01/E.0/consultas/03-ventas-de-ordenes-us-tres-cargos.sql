-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- La venta ligada a cada orden de 02-ordenes-us-tres-cargos-desglose.sql
-- (mismo filtro literal): filas kind sale de la misma orden y plataforma,
-- con fecha, producto, SKU del listing si lo hay, unidades, monto y el
-- cobro de envio al cliente (en US es NULL por el hecho 16, no cero).
-- Un SELECT.
with cargos as (
    select le.order_id
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
    s.order_id, s.event_date, s.product_id,
    (select string_agg(distinct l.seller_sku, ',') from listing l
      where l.product_id = s.product_id and l.platform = s.platform) as seller_sku,
    s.quantity, s.amount, s.amount_currency, s.shipping_price, s.source_event_id
from ledger_event s
join tres t using (order_id)
where s.kind = 'sale' and s.platform = 'amazon_us'
order by s.order_id, s.event_date, s.id;
