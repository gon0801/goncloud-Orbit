-- ADS PROTECCION C.5: descarte humano de propuestas por el endpoint
-- autenticado (app/api_write.py, DSN admin -> rol app_admin).
-- app_admin ya tiene SELECT (0042); aqui recibe UPDATE SOLO en las 5
-- columnas del cierre humano: status, closed_at, last_seen_at,
-- close_reason, close_evidence. Sin INSERT (no abre propuestas), sin
-- UPDATE de dinero, ventanas, profile_id ni avisos (dueno del motor).
-- Decisiones 2 del runbook: grant nuevo justificado y con revision.
BEGIN;
GRANT UPDATE (status, closed_at, last_seen_at, close_reason, close_evidence)
    ON ads_campaign_proposal TO app_admin;

-- Candado de privilegios (patron 0042: solo has_* para no exigir
-- membresia de rol al migrar; la prueba de SET ROLE vive en
-- test_cierre_campana via el DSN del rol temporal miembro de app_admin).
DO $$
BEGIN
    IF NOT has_column_privilege(
        'app_admin', 'ads_campaign_proposal', 'status', 'UPDATE'
    ) THEN
        RAISE EXCEPTION '0043: app_admin debe poder cerrar propuestas';
    END IF;
    IF NOT has_column_privilege(
        'app_admin', 'ads_campaign_proposal', 'close_evidence', 'UPDATE'
    ) THEN
        RAISE EXCEPTION '0043: app_admin debe poder anotar el actor';
    END IF;
    IF has_table_privilege('app_admin', 'ads_campaign_proposal', 'INSERT') THEN
        RAISE EXCEPTION '0043: app_admin no debe poder abrir propuestas';
    END IF;
    IF has_column_privilege(
        'app_admin', 'ads_campaign_proposal', 'cost', 'UPDATE'
    ) THEN
        RAISE EXCEPTION '0043: app_admin no debe poder tocar el dinero medido';
    END IF;
END $$;

COMMIT;
