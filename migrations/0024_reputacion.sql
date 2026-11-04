-- REPUTACION 01 A.1 -- esquema v1 (acta 0.5 aprobada 2026-09-08).
--
-- Contrato: E/0.5. Fuentes verificadas en Fase 0:
--   MeLi API oficial (E/0.3): reputacion seller, items+health, reviews,
--   questions, claims dispute. Rating/count Amazon via junglee semanal
--   (E/0.2): stars + reviewsCount, GRANO PADRE (originalAsin pedido,
--   asin padre devuelto).
-- Fuera de v1 (declarado, no bloqueante): texto reviews Amazon (A.3
-- bloqueada: web_wanderer trae 0 items), Keepa (402 sin plan),
-- Account Health (sin endpoint publico).
--
-- Decisiones (ADR en los COMMENT):
--   1. 4 tablas de hechos APPEND-ONLY con prohibir_mutacion desde el
--      dia 1 (leccion de 0023): reputation_snapshot, review_event,
--      seller_reputation_snapshot, meli_question.
--   2. La reputacion de cuenta y las preguntas son TABLAS PROPIAS, no
--      `extra` (acta §5: lo que dispara alertas no vive en JSONB).
--   3. reputation_snapshot.rating Amazon es del PADRE (junglee):
--      external_id = ASIN pedido, parent_asin = asin devuelto
--      (NULL = sin padre distinto / no aplica).
--   4. review_event dedupica por (platform, review_external_id): un
--      review es un hecho unico (ON CONFLICT DO NOTHING).
--   5. reputation_alert NO es append-only: resolved se sella por UPDATE
--      de columna (patron decision_application 0001). Tipos y
--      severidades del acta §2 (D3).
--   6. rating_average=0 con total=0 de MeLi NO es rating (E/0.3):
--      la ingesta escribe rating NULL + review_count 0. El esquema lo
--      permite (NULL) y lo documenta; no puede impedirlo por CHECK.
--
-- Es expansiva: solo CREATE TYPE/TABLE/TRIGGER/GRANTs. No re-runnable.

BEGIN;

CREATE TYPE reputacion_alcance AS ENUM ('listing', 'cuenta');
CREATE TYPE reputacion_alerta_tipo AS ENUM (
    'rating_bajo', 'resena_1', 'reclamos_suben', 'caida_rating',
    'salud_cuenta'
);
CREATE TYPE reputacion_severidad AS ENUM ('info', 'aviso', 'critica');

-- Snapshot rating/count por listing (o cuenta MeLi). Append-only.
CREATE TABLE reputation_snapshot (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform      platform NOT NULL,
    external_id   TEXT,
    alcance       reputacion_alcance NOT NULL,
    metric_date   DATE NOT NULL,
    -- Regla 3: NULL = sin dato, JAMAS un rating inventado.
    rating        NUMERIC(3,2),
    review_count  BIGINT,
    -- Grano padre Amazon (E/0.2): NULL = sin padre distinto o N/A.
    parent_asin   TEXT,
    -- Frescura del dato EN EL ORIGEN. La observacion de Orbit es observed_at.
    fetched_at    TIMESTAMPTZ NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Solo metricas que NO disparan alertas (health MeLi, niveles por
    -- estrella). Lo que dispara alerta es columna o tabla propia.
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

-- Evento de review con texto (v1: MeLi; Amazon si A.3 se desbloquea).
-- Append-only, un hecho unico por plataforma.
CREATE TABLE review_event (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform           platform NOT NULL,
    external_id        TEXT NOT NULL,
    review_external_id TEXT NOT NULL,
    rating             SMALLINT,
    titulo             TEXT,
    texto              TEXT,
    publicada          BOOLEAN NOT NULL,
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

-- Reputacion de cuenta MeLi, diaria. Tabla propia (acta §5):
-- dispara `reclamos_suben` y `salud_cuenta`. Append-only.
CREATE TABLE seller_reputation_snapshot (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform         platform NOT NULL,
    metric_date      DATE NOT NULL,
    level_id         TEXT,
    power_seller     TEXT,
    tx_total         BIGINT,
    tx_completed     BIGINT,
    tx_canceled      BIGINT,
    ratings_positive BIGINT,
    ratings_neutral  BIGINT,
    ratings_negative BIGINT,
    disputas_total   INTEGER,
    disputas_abiertas INTEGER,
    fetched_at       TIMESTAMPTZ NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT seller_reputation_conteos_no_negativos CHECK (
        (tx_total IS NULL OR tx_total >= 0)
        AND (tx_completed IS NULL OR tx_completed >= 0)
        AND (tx_canceled IS NULL OR tx_canceled >= 0)
        AND (ratings_positive IS NULL OR ratings_positive >= 0)
        AND (ratings_neutral IS NULL OR ratings_neutral >= 0)
        AND (ratings_negative IS NULL OR ratings_negative >= 0)
        AND (disputas_total IS NULL OR disputas_total >= 0)
        AND (disputas_abiertas IS NULL OR disputas_abiertas >= 0)
    ),
    CONSTRAINT seller_reputation_anti_duplicado UNIQUE
        (platform, metric_date, observed_at)
);

-- Preguntas MeLi, lectura. Tabla propia (acta §5 + D5): append-only
-- con estado; el conteo de pendientes sale de aqui. Solo MeLi.
CREATE TABLE meli_question (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform             platform NOT NULL DEFAULT 'meli',
    external_id          TEXT NOT NULL,
    question_external_id TEXT NOT NULL,
    estado               TEXT NOT NULL,
    texto                TEXT,
    respuesta            TEXT,
    asked_at             TIMESTAMPTZ,
    answered_at          TIMESTAMPTZ,
    fetched_at           TIMESTAMPTZ NOT NULL,
    observed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT meli_question_solo_meli CHECK (platform = 'meli'),
    CONSTRAINT meli_question_estado_valido CHECK (
        estado IN ('ANSWERED', 'UNANSWERED')
    ),
    CONSTRAINT meli_question_listing_existe FOREIGN KEY
        (platform, external_id) REFERENCES listing (platform, external_id),
    CONSTRAINT meli_question_anti_duplicado UNIQUE
        (external_id, question_external_id)
);

CREATE INDEX meli_question_pendientes
    ON meli_question (external_id) WHERE estado = 'UNANSWERED';

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
CREATE TRIGGER seller_reputation_append_only
    BEFORE UPDATE OR DELETE ON seller_reputation_snapshot
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER seller_reputation_append_only_truncate
    BEFORE TRUNCATE ON seller_reputation_snapshot
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER meli_question_append_only
    BEFORE UPDATE OR DELETE ON meli_question
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER meli_question_append_only_truncate
    BEFORE TRUNCATE ON meli_question
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE reputation_snapshot IS
    'REPUTACION 01 A.1: rating + conteo por (platform, listing|cuenta, '
    'metric_date), append-only. rating Amazon = del PADRE (parent_asin; '
    'E/0.2 junglee). rating NULL = fuente sin dato (regla 3): MeLi trae '
    'avg=0 con total=0 y ese 0 NO se escribe como rating (E/0.3).';
COMMENT ON COLUMN reputation_snapshot.parent_asin IS
    'E/0.2: junglee devuelve el asin padre; NULL = sin padre distinto '
    '(MeLi, cuenta, o pedido==padre). Conciliacion por `input` del run, '
    'no por este campo.';
COMMENT ON COLUMN reputation_snapshot.extra IS
    'Solo metricas que NO disparan alertas (health MeLi, niveles). Lo '
    'que dispara alerta es columna o tabla propia (acta §5).';
COMMENT ON TABLE review_event IS
    'REPUTACION 01 A.1: un review = un hecho (platform, review_external_id), '
    'append-only. v1: MeLi (texto oficial). Amazon si A.3 se desbloquea. '
    'La ingesta inserta una vez con ON CONFLICT DO NOTHING.';
COMMENT ON TABLE seller_reputation_snapshot IS
    'REPUTACION 01 A.1: cuenta MeLi diaria (level, power seller, tx, '
    'disputas), append-only. Dispara reclamos_suben y salud_cuenta '
    '(acta §2). Tabla propia, no extra (acta §5).';
COMMENT ON TABLE meli_question IS
    'REPUTACION 01 A.1: preguntas MeLi solo-lectura (D5), append-only. '
    'Pendiente = UNANSWERED + 48h (acta §1). Solo meli por CHECK.';
COMMENT ON TABLE reputation_alert IS
    'REPUTACION 01 A.1: MUTABLE solo para sellar resolved/resolved_at '
    '(patron decision_application 0001). Tipos D3/acta §2. NULL '
    'platform/external_id = alerta de cuenta.';
COMMENT ON CONSTRAINT reputation_snapshot_alcance_coherente
    ON reputation_snapshot IS
    'listing exige external_id; cuenta exige NULL. Sin tercer estado.';
COMMENT ON CONSTRAINT reputation_alert_resuelta_coherente
    ON reputation_alert IS
    'resolved=TRUE exige resolved_at y viceversa: no hay alerta resuelta '
    'sin cuando ni fecha sin marca.';

GRANT SELECT ON reputation_snapshot, review_event, seller_reputation_snapshot,
    meli_question, reputation_alert TO app_read;
GRANT SELECT ON reputation_snapshot, review_event, seller_reputation_snapshot,
    meli_question, reputation_alert TO app_ingest, app_decide, app_admin;
GRANT INSERT ON reputation_snapshot, review_event, seller_reputation_snapshot,
    meli_question TO app_ingest;
GRANT INSERT ON reputation_alert TO app_decide;
GRANT UPDATE (resolved, resolved_at) ON reputation_alert TO app_decide;
GRANT USAGE ON SEQUENCE reputation_snapshot_id_seq, review_event_id_seq,
    seller_reputation_snapshot_id_seq, meli_question_id_seq,
    reputation_alert_id_seq TO app_ingest, app_decide, app_admin;

COMMIT;
