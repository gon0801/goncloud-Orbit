-- ORBIT · fase 8 · repricing-01 · E.1 · ronda de corrección r4a
-- Historia disponible por plataforma. Corre SEGUNDO (prefijo 01, justo
-- después de la sonda de formato 00): antes de leer cualquier tabla que
-- hable de "365 días", hay que saber cuántos días de datos existen de
-- verdad.
--
-- Medido por el lead 2026-09-17: `shipping_fee` tiene datos desde
-- 2025-12-04 (MX hasta 09-14, US hasta 09-15); las ventas desde
-- 2025-11-14 (MX) y 2025-11-20 (US). Una ventana de "365 días" son en
-- realidad ~287 días de datos — el resto de la ventana está vacío por
-- construcción, no porque no haya envíos. Esta consulta calcula esos
-- números desde los datos (nada literal), para que la ventana truncada
-- se vea en cada corrida futura, no solo en esta medición puntual.

select
    'shipping_fee' as fuente,
    le.platform,
    min(le.event_date) as primer_event_date,
    max(le.event_date) as ultimo_event_date,
    count(*) as filas,
    (((now() at time zone 'UTC')::date) - min(le.event_date)) as dias_de_historia_disponible
from ledger_event le
where le.fee_type = 'shipping_fee'
  and le.kind = 'fee'
group by le.platform

union all

select
    'venta' as fuente,
    le.platform,
    min(le.event_date) as primer_event_date,
    max(le.event_date) as ultimo_event_date,
    count(*) as filas,
    (((now() at time zone 'UTC')::date) - min(le.event_date)) as dias_de_historia_disponible
from ledger_event le
where le.kind = 'sale'
group by le.platform

order by fuente, platform;
