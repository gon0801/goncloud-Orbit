-- ORBIT · fase 10 · repricing-01 · E.0a · solo lectura · NO CORRIDA en la Fase 10
-- Clasifica por plataforma, event_type y fee_category las filas de la base
-- de contabilidad que la ingesta de Orbit descarta por convencion de signos
-- (app/ledger.py, plan_eventos: fee/refund/withholding con monto > 0, o
-- sale_gross con monto <= 0). Replica el orden de app/ledger.py: primero
-- quedan fuera las plataformas que la ingesta ya excluye por otra razon
-- (meli y cualquiera fuera de amazon / amazon_us) y los event_type que no
-- conoce. No replica los demas saltos basicos (moneda, fecha, decimales):
-- puede contar de mas las filas que ademas fallan uno de esos, y por eso
-- se compara contra el total de ingest_run (07-descartes-por-signo.sql).
--
-- Base: /mnt/data/appdata/accounting/data/accounting.db en goncloud, que
-- NO es la base de Orbit: la preaprobacion de la Fase 10 no la cubre y el
-- lead no la corrio. La corre el dueno, en solo lectura:
--   ! ssh goncloud "sqlite3 -readonly /mnt/data/appdata/accounting/data/accounting.db" < docs/evidencia/repricing-01/E.0/consultas-contabilidad/descartes-por-signo.sqlite.sql
.headers on
.mode list
select
    platform,
    event_type,
    coalesce(fee_category, '(nulo)') as fee_category,
    count(*) as filas,
    round(sum(amount), 2) as suma,
    min(event_date) as primera,
    max(event_date) as ultima
from ledger_events
where platform in ('amazon', 'amazon_us')
  and (
        (event_type = 'sale_gross' and amount <= 0)
     or (event_type in ('fee', 'refund') and amount > 0)
  )
group by platform, event_type, fee_category
order by filas desc, platform, event_type, fee_category;
