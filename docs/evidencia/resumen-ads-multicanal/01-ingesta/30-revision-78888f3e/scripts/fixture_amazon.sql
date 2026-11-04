-- Fixture Amazon fijo para el perf de /: 30 dias x amazon_mx + amazon_us.
-- Requiere el esquema completo (87 tablas) y un ingest_run de fixture.
INSERT INTO ingest_run (source, ok, finished_at)
VALUES ('fixture_amazon', true, now())
RETURNING id;

DO $$
DECLARE
  run_id bigint;
  ent_mx bigint;
  ent_us bigint;
BEGIN
  SELECT id INTO run_id FROM ingest_run
   WHERE source = 'fixture_amazon' ORDER BY id DESC LIMIT 1;
  INSERT INTO ad_entity (platform, kind, external_id, name)
  VALUES ('amazon_mx', 'campaign', 'fixture-mx', 'Fixture MX')
  RETURNING id INTO ent_mx;
  INSERT INTO ad_entity (platform, kind, external_id, name)
  VALUES ('amazon_us', 'campaign', 'fixture-us', 'Fixture US')
  RETURNING id INTO ent_us;
  INSERT INTO ads_metric_observation
    (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,
     impressions, clicks, orders, source_report_id, ingest_run_id)
  SELECT ent_mx, (now() AT TIME ZONE 'UTC')::date - d, now(), 'MXN',
         100 + d, 400 + d * 2, 1000 + d, 50 + d, 5 + d,
         'fixture-mx-' || d, run_id
    FROM generate_series(1, 30) d;
  INSERT INTO ads_metric_observation
    (ad_entity_id, metric_date, observed_at, metric_currency, cost, ad_revenue,
     impressions, clicks, orders, source_report_id, ingest_run_id)
  SELECT ent_us, (now() AT TIME ZONE 'UTC')::date - d, now(), 'USD',
         10 + d, 50 + d * 3, 200 + d, 10 + d, 1 + d,
         'fixture-us-' || d, run_id
    FROM generate_series(1, 30) d;
END $$;
