-- ORBIT · repricing-02 · F.0 punto 3 (r3) · solo lectura
-- Control (revision R01-r2): para CADA SKU FBM de la ultima foto de
-- cantidad, el status mas cercano en el tiempo y el status de hoy,
-- separado por cantidad en cero o mayor que cero. La ultima linea cuenta
-- cuantas fotos de cantidad FBM existen en toda la historia.
with ultima_disponibilidad as (
    select distinct on (platform, seller_sku)
        platform, seller_sku, quantity, observed_at
    from disponibilidad_observation
    where fuente = 'fbm'
    order by platform, seller_sku, observed_at desc
),
cercano as (
    select distinct on (ud.platform, ud.seller_sku)
        ud.platform, ud.seller_sku, ud.quantity, e.status
    from ultima_disponibilidad ud
    join spapi_listing_estado_observation e
      on e.seller_sku = ud.seller_sku and e.platform = ud.platform
    order by ud.platform, ud.seller_sku, abs(extract(epoch from (e.observed_at - ud.observed_at)))
),
hoy as (
    select distinct on (seller_sku, platform) seller_sku, platform, status
    from spapi_listing_estado_observation
    order by seller_sku, platform, observed_at desc
)
select c.platform,
       case when c.quantity is null then 'nulo' when c.quantity = 0 then 'cero' else 'con existencias' end as cantidad,
       c.status as status_cercano,
       h.status as status_hoy,
       count(*)
from cercano c
join hoy h on h.seller_sku = c.seller_sku and h.platform = c.platform
group by 1, 2, 3, 4
order by 1, 2, 3, 4;

select count(*), min(observed_at), max(observed_at), count(distinct observed_at)
from disponibilidad_observation where fuente = 'fbm';
