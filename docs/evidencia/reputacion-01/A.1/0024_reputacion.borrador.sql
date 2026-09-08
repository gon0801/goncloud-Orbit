-- REPUTACION 01 1.1 -- esquema de monitoreo reputacional (solo lectura).
--
-- Fuentes verificadas en Fase 0 (2026-09-07, plans/reputacion-01.md):
--   MeLi Items/Questions/Claims + reputacion de usuario (API directa, 200).
--   Amazon: SP-API Account Health (LWA grant 200) + rating por ASIN via
--   Apify scraping controlado (200). SP-API no expone reviews de producto
--   para sellers: sin Apify no hay rating Amazon, y eso se declara, no se
--   inventa (regla 3).
--
-- Decisiones (ADR en los COMMENT):
--   1. reputation_snapshot y review_event son APPEND-ONLY con trigger
--      prohibir_mutacion desde el dia 1 (leccion de 0023: el candado por
--      motor, no solo por GRANTs).
--   2. La identidad del listing es (platform, external_id) con FK a
--      listing: la ingesta solo escribe listings del catalogo (el plan
--      filtra; lo sin match es skip contado, patron de 0022).
--   3. review_event dedupica por (platform, review_external_id): un review
--      es un hecho unico, se inserta una vez (ON CONFLICT DO NOTHING).
--   4. reputation_alert NO es append-only: resolved se sella por UPDATE de
--      columna (patron decision_application de 0001), con CHECK que ata
--      resolved <-> resolved_at.
--   5. Metrica de cuenta (reputacion vendedor MeLi, Account Health Amazon)
--      vive en reputation_snapshot con alcance='cuenta' y external_id NULL;
--      el UNIQUE usa NULLS NOT DISTINCT para que no duplique silencioso.
--
-- Es expansiva: solo CREATE TYPE/TABLE/TRIGGER/GRANTs. No re-runnable.

BEGIN;

CREATE TYPE reputacion_alcance AS ENUM ('listing', 'cuenta');
CREATE TYPE reputacion_alerta_tipo AS ENUM (
    'rating_bajo', 'resena_1_2', 'reclamos_suben', 'caida_sostenida',
    'salud_cuenta'
);
CREATE TYPE reputacion_severidad AS ENUM ('info', 'aviso', 'critica');

-- Snapshot diario append-only: corregir es insertar fila nueva (regla 5).
CREATE TABLE reputation_snapshot (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform      platform NOT NULL,
    external_id   TEXT,
    alcance       reputacion_alcance NOT NULL,
    metric_date   DATE NOT NULL,
    -- Regla 3: NULL = sin dato, JAMAS un rating inventado.
    rating        NUMERIC(3,2),
    review_count  BIGINT,
    -- Frescura del dato EN EL ORIGEN. La observacion de Orbit es observed_at.
    fetched_at    TIMESTAMPTZ NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Lo que no tiene columna: color/reclamos MeLi, Buy Box %, Account
    -- Health, tendencia. JSONB, nunca columnas inventadas por fuente.
    extra         JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT reputation_snapshot_alcance_coherente CHECK (
        (alcance = 'listing' AND external_id IS NOT NULL)
        OR (alcance = 'cuenta' AND external_id IS NULL)
    ),
    CONSTRAINT reputation_snapshot_rating_rango CHECK (
        rating IS NULL OR (rating >= 1 AND rating <= 5)
    ),
    CONSTRAINT reputation_snapshot_conteo_no_negativo CHECK (
        review_count IS NULL OR review_count >= 0
    ),
    CONSTRAINT reputation_snapshot_listing_existe FOREIGN KEY
        (platform, external_id) REFERENCES listing (platform, external_id),
    CONSTRAINT reputation_snapshot_anti_duplicado UNIQUE NULLS NOT DISTINCT
        (platform, external_id, metric_date, observed_at)
);

CREATE INDEX reputation_snapshot_por_listing
    ON reputation_snapshot (platform, external_id, metric_date DESC);

-- Evento de review: un hecho unico por plataforma. Append-only.
CREATE TABLE review_event (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform           platform NOT NULL,
    external_id        TEXT NOT NULL,
    review_external_id TEXT NOT NULL,
    rating             SMALLINT,
    titulo             TEXT,
    texto              TEXT,
    published_at       TIMESTAMPTZ,
    fetched_at         TIMESTAMPTZ NOT NULL,
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT review_event_rating_rango CHECK (
        rating IS NULL OR (rating >= 1 AND rating <= 5)
    ),
    CONSTRAINT review_event_listing_existe FOREIGN KEY
        (platform, external_id) REFERENCES listing (platform, external_id),
    CONSTRAINT review_event_anti_duplicado UNIQUE
        (platform, review_external_id)
);

CREATE INDEX review_event_por_listing
    ON review_event (platform, external_id, published_at DESC NULLS LAST);

-- Alerta: MUTABLE solo para sellar resolved/resolved_at (patron
-- decision_application). platform/external_id NULL = alerta de cuenta.
CREATE TABLE reputation_alert (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform    platform,
    external_id TEXT,
    tipo        reputacion_alerta_tipo NOT NULL,
    severidad   reputacion_severidad NOT NULL,
    mensaje     TEXT NOT NULL,
    resolved    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    CONSTRAINT reputation_alert_con_listing_exige_platform CHECK (
        external_id IS NULL OR platform IS NOT NULL
    ),
    CONSTRAINT reputation_alert_resuelta_coherente CHECK (
        resolved = (resolved_at IS NOT NULL)
    ),
    -- Comparacion entre columnas, no contra now(): no depende de timezone.
    CONSTRAINT reputation_alert_resuelta_despues CHECK (
        resolved_at IS NULL OR resolved_at >= created_at
    ),
    CONSTRAINT reputation_alert_listing_existe FOREIGN KEY
        (platform, external_id) REFERENCES listing (platform, external_id)
);

CREATE INDEX reputation_alert_abiertas
    ON reputation_alert (platform, external_id) WHERE NOT resolved;

-- Append-only por motor desde el dia 1 (leccion de 0023).
CREATE TRIGGER reputation_snapshot_append_only
    BEFORE UPDATE OR DELETE ON reputation_snapshot
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER reputation_snapshot_append_only_truncate
    BEFORE TRUNCATE ON reputation_snapshot
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER review_event_append_only
    BEFORE UPDATE OR DELETE ON review_event
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER review_event_append_only_truncate
    BEFORE TRUNCATE ON review_event
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE reputation_snapshot IS
    'REPUTACION 01 1.1: rating + conteo por (platform, listing|cuenta, '
    'metric_date), append-only. rating/review_count NULL = fuente sin dato '
    '(regla 3). extra lleva color/reclamos MeLi, Buy Box %, Account Health.';
COMMENT ON COLUMN reputation_snapshot.extra IS
    'JSONB para metricas sin columna propia. Lo que crece a invariante se '
    'promueve a columna con su migracion; no se lee negocio desde JSONB '
    'ad hoc.';
COMMENT ON TABLE review_event IS
    'REPUTACION 01 1.1: un review = un hecho (platform, review_external_id), '
    'append-only. La ingesta inserta una vez con ON CONFLICT DO NOTHING.';
COMMENT ON TABLE reputation_alert IS
    'REPUTACION 01 1.1: MUTABLE solo para sellar resolved/resolved_at '
    '(patron decision_application 0001). NULL platform/external_id = alerta '
    'de cuenta, no de listing.';
COMMENT ON CONSTRAINT reputation_snapshot_alcance_coherente
    ON reputation_snapshot IS
    'listing exige external_id; cuenta exige NULL. Sin tercer estado.';
COMMENT ON CONSTRAINT reputation_alert_resuelta_coherente
    ON reputation_alert IS
    'resolved=TRUE exige resolved_at y viceversa: no hay alerta resuelta '
    'sin cuando ni fecha sin marca.';

GRANT SELECT ON reputation_snapshot, review_event, reputation_alert TO app_read;
GRANT SELECT ON reputation_snapshot, review_event, reputation_alert
    TO app_ingest, app_decide, app_admin;
GRANT INSERT ON reputation_snapshot, review_event TO app_ingest;
GRANT INSERT ON reputation_alert TO app_decide;
GRANT UPDATE (resolved, resolved_at) ON reputation_alert TO app_decide;
GRANT USAGE ON SEQUENCE reputation_snapshot_id_seq, review_event_id_seq,
    reputation_alert_id_seq TO app_ingest, app_decide, app_admin;

COMMIT;
