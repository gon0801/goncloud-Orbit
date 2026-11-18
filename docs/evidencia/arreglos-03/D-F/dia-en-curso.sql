-- ARREGLOS 03 D-F.5: ¿la ultima copia de Mercado Libre Ads ya no guardo el dia de Mexico que
-- iba en curso? SOLO LECTURA. Mira la ultima corrida ok de MeLi Ads que escribio filas: un `t`
-- quiere decir que ninguna de sus filas es del dia local en curso al copiarla; `f`, que si.
-- Antes del despliegue da `f` (la corrida 1556 del 12-nov 08:15 UTC escribio el 12-nov).
-- Lo corre dia-en-curso.sh, programado con `at` en goncloud para las 08:45 UTC siguientes al
-- despliegue (la copia de MeLi Ads corre a las 08:15), desde /mnt/data/appdata/orbit/recordatorios/.
BEGIN READ ONLY;
SELECT count(*) = 0 FROM ads_metric_observation o JOIN ad_entity e ON e.id = o.ad_entity_id
 WHERE e.platform = 'meli'
   AND o.ingest_run_id = (SELECT max(r.id) FROM ingest_run r
                           WHERE r.source = 'accounting_meli_ads_campaigns' AND r.ok AND r.rows_written > 0)
   AND o.metric_date >= (o.observed_at AT TIME ZONE 'America/Mexico_City')::date;
ROLLBACK;
