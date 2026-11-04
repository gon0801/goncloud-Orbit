-- E/A.7 verificacion.sql — lecturas usadas en el deploy 2026-09-08
-- (lead, solo SELECT; salidas en salidas.md, sin secretos).

-- 1. Tablas reputacion (esperado: 5 filas)
SELECT table_name FROM information_schema.tables WHERE table_schema='public'
AND table_name IN ('reputation_snapshot','review_event',
'seller_reputation_snapshot','meli_question','reputation_alert') ORDER BY 1;

-- 2. Triggers append-only (esperado: 8 = 4 hechos x row+truncate)
SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal
AND (tgname LIKE '%reput%' OR tgname LIKE '%review_event%' OR tgname LIKE '%meli_question%');

-- 3. GRANTs tabla reputation_alert (decide INSERT; todos SELECT)
SELECT grantee, privilege_type FROM information_schema.role_table_grants
WHERE table_name='reputation_alert' AND grantee LIKE 'app_%' ORDER BY 1,2;

-- 4. UPDATE por columna (solo app_decide resolved/resolved_at; orbit = dueno)
SELECT grantee, column_name, privilege_type
FROM information_schema.role_column_grants
WHERE table_name='reputation_alert' AND privilege_type='UPDATE';

-- 5. UNIQUEs re-observacion 0026/0027 (esperado: 2 filas)
SELECT indexname FROM pg_indexes WHERE schemaname='public'
AND tablename IN ('meli_question','review_event') AND indexname LIKE '%anti_duplicado%';

-- 6. Runs reputacion (117/118 ok MeLi; 119 huerfano pre-fix tx; 120 failed sellado)
SELECT id, ok, rows_written, left(skip_reason, 40) FROM ingest_run
WHERE source IN ('reputacion_meli','reputacion_amazon') ORDER BY id;

-- 7. Conteos estreno MeLi (esperado: 65/119/1/51)
SELECT 'snapshot', count(*) FROM reputation_snapshot UNION ALL
SELECT 'review', count(*) FROM review_event UNION ALL
SELECT 'seller', count(*) FROM seller_reputation_snapshot UNION ALL
SELECT 'question', count(*) FROM meli_question;
