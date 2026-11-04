-- ORBIT · repricing-02 · F.0 punto 3 · solo lectura
-- FBM sin existencias: la ultima observacion de disponibilidad con
-- cantidad 0 cruzada contra el ultimo status del mismo SKU en
-- spapi_listing_estado_observation. La respuesta dice si un listing FBM
-- sin existencias sigue BUYABLE (guia F.0 punto 3; riesgo del diseno
-- "Un listing FBM sin existencias deja de ser BUYABLE?").
-- El SKU NUNCA sale en la salida: cada fila va por sus ids internos
-- (listing.id, product.id). Dos SELECT.

-- (a) un fila por SKU en cero: ids internos, fecha del cero y ultimo status
with ultima_disponibilidad as (
    select distinct on (platform, seller_sku)
        platform, seller_sku, quantity, metric_date, observed_at
    from disponibilidad_observation
    where fuente = 'fbm'
    order by platform, seller_sku, observed_at desc
),
en_cero as (
    select platform, seller_sku, quantity, metric_date, observed_at
    from ultima_disponibilidad
    where quantity = 0
),
ultimo_estado as (
    select distinct on (seller_sku, platform)
        seller_sku, platform, status, observed_at
    from spapi_listing_estado_observation
    order by seller_sku, platform, observed_at desc
)
select
    ec.platform,
    l.id as listing_id,
    l.product_id,
    ec.metric_date as fecha_cero,
    ec.observed_at as observado_disponibilidad,
    ue.status,
    ue.observed_at as observado_estado
from en_cero ec
join ultimo_estado ue on ue.seller_sku = ec.seller_sku and ue.platform = ec.platform
left join listing l on l.seller_sku = ec.seller_sku and l.platform = ec.platform
order by ec.platform, l.product_id nulls last, ec.seller_sku;

-- (b) resumen: cuantos en cero por plataforma y status
with ultima_disponibilidad as (
    select distinct on (platform, seller_sku)
        platform, seller_sku, quantity
    from disponibilidad_observation
    where fuente = 'fbm'
    order by platform, seller_sku, observed_at desc
),
en_cero as (
    select platform, seller_sku
    from ultima_disponibilidad
    where quantity = 0
),
ultimo_estado as (
    select distinct on (seller_sku, platform)
        seller_sku, platform, status
    from spapi_listing_estado_observation
    order by seller_sku, platform, observed_at desc
)
select
    ec.platform,
    ue.status,
    count(*) as skus_en_cero
from en_cero ec
join ultimo_estado ue on ue.seller_sku = ec.seller_sku and ue.platform = ec.platform
group by 1, 2
order by 1, 2;
