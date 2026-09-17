-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r3
-- Sonda de FORMATO de source_event_id. Corre PRIMERO (prefijo 00, orden
-- alfabético con el que correr.sh recorre consultas/*.sql): todas las
-- demás consultas de E.1 dependen del formato real de `source_event_id`
-- (ver medicion.md, "Formato real medido por el lead"). Si el formato
-- vuelve a cambiar, esta sonda lo muestra ANTES de leer el resto.
--
-- Corrección r3 (hallazgo 22): SIN el filtro `kind = 'fee'` — a propósito,
-- solo aquí, para ver también filas 'refund'/'sale' con
-- `fee_type = 'shipping_fee'` si las hubiera (las demás consultas SÍ
-- filtran por `kind = 'fee'`, porque ellas miden el costo, no auditan el
-- ledger completo). Residual declarado (hallazgo 27): esta sonda
-- INFORMA, no frena la corrida — correr.sh no interpreta su salida ni
-- aborta si el formato cambió; el freno es humano (quien lea `salidas/
-- 00-sonda-formato.txt` antes de confiar en las demás tablas). Un freno
-- automático necesitaría parsear la salida de psql desde bash, que es
-- más frágil que el ojo humano para este volumen de filas.
--
-- Tres SELECT en este archivo (script, todos de solo lectura).

-- 1) distribución real de las partes 2 y 6 de source_event_id, y CUÁNTAS
-- partes trae cada combinación (hallazgo 22): las dos formas reales
-- ('finance' con 6 partes, 'shipping_label' con 4) se distinguen por esto,
-- no solo por el valor de la parte 2.
with poblacion as (
    select
        le.order_id,
        le.platform,
        le.kind,
        le.event_date,
        le.source_event_id,
        split_part(le.source_event_id, '|', 2) as parte_2,
        split_part(le.source_event_id, '|', 6) as parte_6,
        array_length(string_to_array(le.source_event_id, '|'), 1) as n_partes
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
)
select
    platform,
    kind,
    parte_2,
    parte_6,
    n_partes,
    count(*) as filas
from poblacion
group by platform, kind, parte_2, parte_6, n_partes
order by platform, kind, parte_2, parte_6, n_partes;

-- 2) órdenes cuyo order_id (texto) aparece en más de una plataforma:
-- rompería cualquier join que agrupe solo por order_id sin platform
-- (hallazgo 16).
with poblacion as (
    select le.order_id, le.platform
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.order_id is not null
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
)
select count(*) as ordenes_en_mas_de_una_plataforma
from (
    select order_id
    from poblacion
    group by order_id
    having count(distinct platform) > 1
) t;

-- 3) cobertura de spapi_order_observation contra las órdenes con cargo de
-- envío (hallazgo 22): distingue "la orden no tiene ninguna observación
-- SP-API" de "el join está roto" — si el primer número es bajo, es un
-- problema de datos/cobertura; si el segundo es cero pero el primero no,
-- el problema es que ninguna trae 'Shipped' todavía, no que el join falle.
with poblacion as (
    select le.order_id, le.platform
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.order_id is not null
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
),
ordenes_con_cargo as (
    select distinct order_id, platform from poblacion
),
observaciones as (
    select distinct amazon_order_id, platform,
        bool_or(fulfillment_status = 'Shipped') over (partition by amazon_order_id, platform) as alguna_shipped
    from spapi_order_observation
)
select
    count(*) as ordenes_con_cargo,
    count(o.amazon_order_id) as ordenes_con_alguna_observacion,
    count(*) filter (where o.alguna_shipped) as ordenes_con_observacion_shipped
from ordenes_con_cargo c
left join observaciones o
  on o.amazon_order_id = c.order_id and o.platform = c.platform;
