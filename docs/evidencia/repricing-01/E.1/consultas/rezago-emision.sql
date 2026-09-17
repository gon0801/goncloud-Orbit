-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r4a
-- Rezago de EMISIÓN: distinto del rezago de INGESTA (ver
-- rezago-ingesta.sql). Reescrita en r4a con CUATRO medidas separadas,
-- cada una en su propio SELECT, ninguna afirma "fecha de envío" — eso lo
-- confirma E.0.
--
-- CONCLUSIÓN medida por el lead 2026-09-17 (rol orbit_read, BEGIN READ
-- ONLY): el "rezago de emisión" de p50 27 días MX / 22 US, p90 57-59,
-- máximo 73 que el plan (hecho 15) da por medido **NO SE REPRODUCE con
-- ninguna fecha disponible en Orbit** — ni cargo-venta, ni cargo-
-- purchase_date, ni cargo-parte4-de-shipping_label, ni cargo-Shipped dan
-- algo parecido a esos números. Esta consulta mide lo que SÍ hay, no
-- inventa ni ajusta nada para acercarse al hecho 15. **Decidir qué pasa
-- con `precio_envio_rezago_dias` es trabajo de E.2, no de esta consulta**
-- — aquí no se propone ningún valor.
--
-- (a) PRINCIPAL: cargo − VENTA del ledger, misma orden, mismo event_date
--     (medido por el lead 2026-09-17: MX 682 órdenes, 236 con diferencia
--     NEGATIVA, p50 0, p90 0, máx 2 con el primer cargo; US 536, 174
--     negativas, p50 0, p90 0, máx 3). Dos variantes en la misma tabla
--     (columna `variante`): `primer_cargo` (el más viejo de los cargos de
--     la orden) y `ultimo_cargo` (el más nuevo, el que usa el resto de
--     E.1) — ninguna de las dos parece un "rezago de emisión" real: son
--     mayormente 0, con una cola muy corta.
-- (b) cargo (el "último", igual que el resto de E.1) − `purchase_date` de
--     `spapi_order_observation` (medido por el lead: cobertura PARCIAL,
--     solo 84 órdenes MX / 86 US tienen observación; de esas, p50 0, p90
--     0, máx 0; 37 negativas en cada plataforma).
-- (c) DIAGNÓSTICO (no una medida de rezago independiente): cargo − fecha
--     de la parte 4 de la fila `shipping_label`. Medido por el lead: esa
--     fecha ES el `event_date` de la propia fila `shipping_label` (no
--     aporta una fecha distinta) — mín. −1 MX / −3 US, resto ~0. Por eso
--     NO se usa como medida principal de rezago: es circular con el
--     propio cargo.
-- (d) cargo − primer `last_updated_time` con `fulfillment_status =
--     'Shipped'` de `spapi_order_observation` (medido por el lead:
--     cobertura CERO sobre estas órdenes hoy — se declara así, la
--     consulta se deja escrita por si la cobertura mejora).
--
-- En LAS CUATRO: p50/p90/máximo por plataforma; los resultados NEGATIVOS
-- NO entran al percentil y se cuentan aparte
-- (`ordenes_con_rezago_negativo`); cobertura (con/sin dato) en la misma
-- fila.

-- (a) PRINCIPAL: cargo (primer/último) − venta del ledger, misma orden.
with poblacion as (
    select
        le.order_id,
        le.platform,
        le.event_date,
        le.observed_at,
        abs(le.amount) as monto_abs,
        le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select * from poblacion where order_id is not null
),
cargo_orden as (
    select
        order_id, platform,
        min(event_date) as primer_cargo,
        max(event_date) as ultimo_cargo
    from cargos
    group by order_id, platform
),
venta_orden as (
    -- VENTA del ledger: cualquier fila kind='sale' de la orden, CON o SIN
    -- product_id (hallazgo del lead: hay órdenes con venta pero sin
    -- product_id — ver descartes.sql, razón 'venta_sin_producto'). Para
    -- "cargo vs venta" el product_id no importa, solo la fecha.
    select order_id, platform, min(event_date) as venta_event_date
    from ledger_event
    where kind = 'sale' and order_id is not null
    group by order_id, platform
),
principal as (
    select
        co.platform, 'primer_cargo' as variante,
        (vo.venta_event_date is not null) as tiene_venta,
        co.primer_cargo - vo.venta_event_date as rezago_dias
    from cargo_orden co
    left join venta_orden vo using (order_id, platform)
    union all
    select
        co.platform, 'ultimo_cargo' as variante,
        (vo.venta_event_date is not null) as tiene_venta,
        co.ultimo_cargo - vo.venta_event_date as rezago_dias
    from cargo_orden co
    left join venta_orden vo using (order_id, platform)
)
select
    platform,
    variante,
    count(*) as ordenes_con_cargo,
    count(*) filter (where tiene_venta) as ordenes_con_venta,
    count(*) filter (where not tiene_venta) as ordenes_sin_venta,
    count(*) filter (where tiene_venta and rezago_dias < 0) as ordenes_con_rezago_negativo,
    percentile_cont(0.5) within group (order by rezago_dias)
        filter (where tiene_venta and rezago_dias >= 0) as rezago_p50_dias,
    percentile_cont(0.9) within group (order by rezago_dias)
        filter (where tiene_venta and rezago_dias >= 0) as rezago_p90_dias,
    max(rezago_dias) filter (where tiene_venta and rezago_dias >= 0) as rezago_max_dias
from principal
group by platform, variante
order by platform, variante;

-- (b) cargo (último) − purchase_date de spapi_order_observation.
-- Cobertura DECLARADA PARCIAL (medido por el lead: 84 MX / 86 US con
-- observación).
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select * from poblacion where order_id is not null
),
cargo_orden as (
    select order_id, platform, max(event_date) as ultimo_cargo
    from cargos
    group by order_id, platform
),
purchase_por_orden as (
    select
        platform, amazon_order_id,
        min((purchase_date at time zone 'UTC')::date) as purchase_en
    from spapi_order_observation
    where purchase_date is not null
    group by platform, amazon_order_id
),
contraste_purchase as (
    select
        co.platform,
        (pp.purchase_en is not null) as tiene_dato,
        co.ultimo_cargo - pp.purchase_en as rezago_dias
    from cargo_orden co
    left join purchase_por_orden pp
      on pp.platform = co.platform and pp.amazon_order_id = co.order_id
)
select
    platform,
    count(*) as ordenes_con_cargo,
    count(*) filter (where tiene_dato) as ordenes_con_dato,
    count(*) filter (where not tiene_dato) as ordenes_sin_dato,
    count(*) filter (where tiene_dato and rezago_dias < 0) as ordenes_con_rezago_negativo,
    percentile_cont(0.5) within group (order by rezago_dias)
        filter (where tiene_dato and rezago_dias >= 0) as rezago_p50_dias,
    percentile_cont(0.9) within group (order by rezago_dias)
        filter (where tiene_dato and rezago_dias >= 0) as rezago_p90_dias,
    max(rezago_dias) filter (where tiene_dato and rezago_dias >= 0) as rezago_max_dias
from contraste_purchase
group by platform
order by platform;

-- (c) DIAGNÓSTICO (no es una medida de rezago independiente, ver
-- cabecera): cargo (último) − fecha de la parte 4 de la fila
-- shipping_label. Medido por el lead: esa fecha ES el event_date de la
-- propia fila (mín −1 MX / −3 US, resto ~0).
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select
        *,
        case
            when source_event_id is null or fuente_forma is null then null
            when fuente_forma = 'shipping_label' then 'shipping_label'
            when fuente_forma = 'finance' and fuente_subtipo is not null then 'finance:' || fuente_subtipo
            when fuente_forma = 'finance' then null
            else fuente_forma
        end as identidad_fuente
    from poblacion
    where order_id is not null
),
cargo_orden as (
    select order_id, platform, max(event_date) as ultimo_cargo
    from cargos
    group by order_id, platform
),
shipping_label_por_orden as (
    select
        order_id, platform,
        min(nullif(split_part(source_event_id, '|', 4), '')::date) as fecha_reporte
    from cargos
    where identidad_fuente = 'shipping_label'
    group by order_id, platform
),
diagnostico as (
    select
        co.platform,
        (slo.fecha_reporte is not null) as tiene_shipping_label,
        co.ultimo_cargo - slo.fecha_reporte as diff_dias
    from cargo_orden co
    left join shipping_label_por_orden slo using (order_id, platform)
)
select
    platform,
    count(*) as ordenes_con_cargo,
    count(*) filter (where tiene_shipping_label) as ordenes_con_shipping_label,
    count(*) filter (where not tiene_shipping_label) as ordenes_sin_shipping_label,
    count(*) filter (where tiene_shipping_label and diff_dias < 0) as ordenes_con_diff_negativa,
    min(diff_dias) filter (where tiene_shipping_label) as diff_min_dias,
    percentile_cont(0.5) within group (order by diff_dias)
        filter (where tiene_shipping_label and diff_dias >= 0) as diff_p50_dias,
    percentile_cont(0.9) within group (order by diff_dias)
        filter (where tiene_shipping_label and diff_dias >= 0) as diff_p90_dias,
    max(diff_dias) filter (where tiene_shipping_label and diff_dias >= 0) as diff_max_dias
from diagnostico
group by platform
order by platform;

-- (d) cargo (último) − primer last_updated_time con fulfillment_status =
-- 'Shipped'. Cobertura DECLARADA: medida por el lead en CERO hoy para
-- estas órdenes; se deja escrita por si mejora.
with poblacion as (
    select
        le.order_id, le.platform, le.event_date, le.observed_at,
        abs(le.amount) as monto_abs, le.source_event_id,
        nullif(split_part(le.source_event_id, '|', 2), '') as fuente_forma,
        nullif(split_part(le.source_event_id, '|', 6), '') as fuente_subtipo
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
cargos as (
    select * from poblacion where order_id is not null
),
cargo_orden as (
    select order_id, platform, max(event_date) as ultimo_cargo
    from cargos
    group by order_id, platform
),
primera_shipped as (
    select
        platform,
        amazon_order_id,
        min((last_updated_time at time zone 'UTC')::date) as shipped_en
    from spapi_order_observation
    where fulfillment_status = 'Shipped'
    group by platform, amazon_order_id
),
contraste_shipped as (
    select
        co.platform,
        (ps.shipped_en is not null) as tiene_dato,
        co.ultimo_cargo - ps.shipped_en as rezago_dias
    from cargo_orden co
    left join primera_shipped ps
      on ps.platform = co.platform and ps.amazon_order_id = co.order_id
)
select
    platform,
    count(*) as ordenes_con_cargo,
    count(*) filter (where tiene_dato) as ordenes_con_dato,
    count(*) filter (where not tiene_dato) as ordenes_sin_dato,
    count(*) filter (where tiene_dato and rezago_dias < 0) as ordenes_con_rezago_negativo,
    percentile_cont(0.5) within group (order by rezago_dias)
        filter (where tiene_dato and rezago_dias >= 0) as rezago_p50_dias,
    percentile_cont(0.9) within group (order by rezago_dias)
        filter (where tiene_dato and rezago_dias >= 0) as rezago_p90_dias,
    max(rezago_dias) filter (where tiene_dato and rezago_dias >= 0) as rezago_max_dias
from contraste_shipped
group by platform
order by platform;
