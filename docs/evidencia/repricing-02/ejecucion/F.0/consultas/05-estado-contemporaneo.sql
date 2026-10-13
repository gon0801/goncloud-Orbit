-- ORBIT · repricing-02 · F.0 punto 3 (r3) · solo lectura
-- Llego en la ronda de arreglo r3 (revision R01-r2): para los SKU FBM
-- cuya ultima cantidad es 0, el status observado mas cercano EN EL TIEMPO
-- a esa observacion de cantidad (la foto de cantidad es unica y vieja;
-- cruzarla contra el status de hoy mide reposiciones, no disponibilidad).
with ultima_disponibilidad as (
    select distinct on (platform, seller_sku)
        platform, seller_sku, quantity, observed_at
    from disponibilidad_observation
    where fuente = 'fbm'
    order by platform, seller_sku, observed_at desc
),
en_cero as (
    select platform, seller_sku, observed_at from ultima_disponibilidad where quantity = 0
),
cercano as (
    select distinct on (ec.platform, ec.seller_sku)
        ec.platform, ec.seller_sku, e.status, e.observed_at as obs_estado,
        abs(extract(epoch from (e.observed_at - ec.observed_at))) / 86400.0 as dias
    from en_cero ec
    join spapi_listing_estado_observation e
      on e.seller_sku = ec.seller_sku and e.platform = ec.platform
    order by ec.platform, ec.seller_sku, abs(extract(epoch from (e.observed_at - ec.observed_at)))
)
select platform, status, obs_estado::date as dia_estado, round(max(dias)::numeric, 1) as dias_max, count(*)
from cercano
group by 1, 2, 3
order by 1, 2, 3;
