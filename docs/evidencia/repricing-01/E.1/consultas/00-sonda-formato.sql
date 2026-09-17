-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r1, hallazgo 4
--
-- Sonda de FORMATO de source_event_id. Corre PRIMERO (prefijo 00, orden
-- alfabético con el que correr.sh recorre consultas/*.sql): todas las
-- demás consultas de E.1 asumen que la parte 6 de source_event_id trae
-- literalmente 'ShippingHB', 'LabmanLabelPurchase' o 'shipping_label'
-- (ver medicion.md, "Supuestos declarados"). Si esta sonda muestra otra
-- cosa en producción, esa suposición queda invalidada y
-- 'duplicado_etiqueta' no vale nada hasta corregirse — por eso corre
-- primero: si el supuesto está mal, avisa antes de leer el resto.
--
-- Dos SELECT en este archivo (script, ambos solo lectura).

-- 1) distribución real de las partes 2 y 6 de source_event_id
with poblacion as (
    select
        le.order_id,
        le.platform,
        le.kind,
        le.event_date,
        le.source_event_id,
        split_part(le.source_event_id, '|', 2) as parte_2,
        split_part(le.source_event_id, '|', 6) as parte_6
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
      and le.event_date >= (((now() at time zone 'UTC')::date)) - interval '365 days'
      and le.event_date <  (((now() at time zone 'UTC')::date))
)
select
    platform,
    kind,
    parte_2,
    parte_6,
    count(*) as filas
from poblacion
group by platform, kind, parte_2, parte_6
order by platform, kind, parte_2, parte_6;

-- 2) órdenes cuyo order_id (texto) aparece en más de una plataforma:
-- rompería cualquier join que agrupe solo por order_id sin platform
-- (hallazgo 16).
with poblacion as (
    select le.order_id, le.platform
    from ledger_event le
    where le.fee_type = 'shipping_fee'
      and le.kind = 'fee'
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
