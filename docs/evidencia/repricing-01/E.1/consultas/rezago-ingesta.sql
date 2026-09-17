-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r5a
-- Rezago de INGESTA: entre event_date del cargo y observed_at (la corrida
-- que lo trajo a Orbit). Distinto del rezago de EMISIÓN (ver
-- rezago-emision.sql).
--
-- Medido por el lead 2026-09-17: la carga inicial del ledger fue el
-- 2026-08-31 (1600 filas de shipping_fee con event_date entre 2025-12-04
-- y 2026-08-27); después hay ~10 días de ingesta incremental (2026-09-04:
-- 66 filas de eventos 08-19..09-03; luego 16, 22, 9, 7...). Mezclar la
-- carga inicial con la incremental infla p50 a ~134 días: la carga
-- inicial no mide "cuánto tarda Orbit en enterarse", mide "cuánto tiempo
-- pasó desde que existe el dato hasta que se hizo el backfill", una sola
-- vez. Por eso el primer SELECT reporta DOS filas por plataforma: `todas`
-- (incluye la carga inicial, para que el número quede documentado, no
-- escondido) y `solo_incremental` (filas cuyo DÍA DE INGESTA en UTC es
-- POSTERIOR al primer día de ingesta de `shipping_fee` — calculado desde
-- los datos, ninguna fecha literal en este archivo).
--
-- Ambas fechas se comparan en UTC ((columna AT TIME ZONE 'UTC')::date en
-- vez de un cast implícito a la zona horaria de la sesión).
--
-- Hallazgo 30 (ronda r5a, baja): `es_incremental = dia_ingesta >
-- primer_dia` SUPONE que la carga inicial tomó UN solo día; si tomara
-- dos, el segundo día de carga se cuela en `solo_incremental` sin que
-- nada lo delate. Dos arreglos:
--   (i) el primer SELECT ahora también cuenta aparte los rezagos
--       NEGATIVOS (un evento cuya fecha es POSTERIOR a su propio día de
--       ingesta — cronológicamente imposible salvo reloj/zona horaria
--       desalineados) y los excluye del percentil, igual que
--       rezago-emision.sql.
--   (ii) un TERCER SELECT nuevo, por `(platform, dia_ingesta)`: filas y
--       `event_date` mínimo/máximo de cada día de ingesta — si la carga
--       inicial tomó más de un día, aparece como dos (o más) días con
--       muchas filas y un rango de `event_date` ancho, en vez de
--       perderse dentro de `primer_dia_global`.

-- 1) rezago de ingesta, todas las filas vs solo incremental
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
con_dia_ingesta as (
    select
        platform,
        event_date,
        (observed_at at time zone 'UTC')::date as dia_ingesta
    from poblacion
),
primer_dia_global as (
    -- el primer día de ingesta se CALCULA sobre los datos, no se escribe
    -- literal: es el mínimo dia_ingesta de toda la población.
    select min(dia_ingesta) as primer_dia from con_dia_ingesta
),
clasificado as (
    select
        c.platform,
        c.event_date,
        c.dia_ingesta,
        (c.dia_ingesta - c.event_date) as rezago_dias,
        (c.dia_ingesta > pdg.primer_dia) as es_incremental
    from con_dia_ingesta c
    cross join primer_dia_global pdg
)
select
    platform,
    'todas' as alcance,
    count(*) as filas,
    count(*) filter (where rezago_dias < 0) as filas_con_rezago_negativo,
    percentile_cont(0.5) within group (order by rezago_dias) filter (where rezago_dias >= 0) as rezago_ingesta_p50_dias,
    percentile_cont(0.9) within group (order by rezago_dias) filter (where rezago_dias >= 0) as rezago_ingesta_p90_dias,
    max(rezago_dias) filter (where rezago_dias >= 0) as rezago_ingesta_max_dias
from clasificado
group by platform

union all

select
    platform,
    'solo_incremental' as alcance,
    count(*) as filas,
    count(*) filter (where rezago_dias < 0) as filas_con_rezago_negativo,
    percentile_cont(0.5) within group (order by rezago_dias) filter (where rezago_dias >= 0) as rezago_ingesta_p50_dias,
    percentile_cont(0.9) within group (order by rezago_dias) filter (where rezago_dias >= 0) as rezago_ingesta_p90_dias,
    max(rezago_dias) filter (where rezago_dias >= 0) as rezago_ingesta_max_dias
from clasificado
where es_incremental
group by platform

order by platform, alcance;

-- 2) cuántos días de ingesta hay y cuál fue el primero (calculado, sin
-- fechas literales)
with poblacion as (
    select
        le.observed_at
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
con_dia_ingesta as (
    select (observed_at at time zone 'UTC')::date as dia_ingesta
    from poblacion
)
select
    count(distinct dia_ingesta) as dias_de_ingesta,
    min(dia_ingesta) as primer_dia_de_ingesta,
    max(dia_ingesta) as ultimo_dia_de_ingesta
from con_dia_ingesta;

-- 3) por día de ingesta (hallazgo 30): filas y event_date mínimo/máximo.
-- Si la carga inicial tomó más de un día, se ve aquí como dos filas
-- consecutivas con muchas filas y un rango de event_date ancho — no
-- desaparece dentro de "el primer día".
with poblacion as (
    select
        le.platform,
        le.event_date,
        le.observed_at
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
con_dia_ingesta as (
    select
        platform,
        event_date,
        (observed_at at time zone 'UTC')::date as dia_ingesta
    from poblacion
)
select
    platform,
    dia_ingesta,
    count(*) as filas,
    min(event_date) as event_date_minimo,
    max(event_date) as event_date_maximo
from con_dia_ingesta
group by platform, dia_ingesta
order by platform, dia_ingesta;
