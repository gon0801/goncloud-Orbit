-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura
-- Descartes de la ingesta contable por convencion de signos (app/ledger.py,
-- decision 4: fee/refund/withholding con monto > 0, o sale con monto <= 0,
-- NO se escriben y se cuentan en ingest_run.skip_reason como
-- "<n>x viola ledger_convencion_signos"). Orbit solo guarda el conteo por
-- corrida: la plataforma y el fee_type de cada fila descartada NO estan en
-- Orbit (viven en la SQLite de contabilidad que la ingesta lee). Esta
-- consulta da lo visible: por corrida de los ultimos 90 dias, el conteo
-- de signo y el resto del skip_reason. Dias en UTC (r1, grok). Sin diagonal
-- invertida (el corredor rechaza metacomandos de psql). Dos SELECT.

-- (a) por corrida
select
    r.id, (r.started_at at time zone 'UTC')::date as dia, r.ok, r.rows_written, r.rows_skipped,
    coalesce((regexp_match(r.skip_reason, '([0-9]+)x viola ledger_convencion_signos'))[1]::int, 0) as descartes_signo,
    r.skip_reason
from ingest_run r
where r.source = 'accounting_ledger_events'
  and (r.started_at at time zone 'UTC')::date >= (now() at time zone 'UTC')::date - 90
order by r.id;

-- (b) resumen: corridas, promedio y rango del conteo de signo
with c as (
    select coalesce((regexp_match(r.skip_reason, '([0-9]+)x viola ledger_convencion_signos'))[1]::int, 0) as n
    from ingest_run r
    where r.source = 'accounting_ledger_events'
      and (r.started_at at time zone 'UTC')::date >= (now() at time zone 'UTC')::date - 90
)
select count(*) as corridas, min(n) as minimo, round(avg(n), 1) as promedio, max(n) as maximo
from c;
