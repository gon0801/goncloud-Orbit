-- A.3: episodio de fallo o atraso de ingesta principal y acuses Telegram.
-- Una fila por scope/tipo mientras el episodio esta abierto.
CREATE TABLE ads_ingest_incident (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id BIGINT,
    platform platform,
    tipo TEXT NOT NULL CHECK (tipo IN ('fallo', 'atraso')),
    opened_run_id BIGINT REFERENCES ingest_run(id),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    alert_attempts INTEGER NOT NULL DEFAULT 0,
    alert_sent_at TIMESTAMPTZ,
    recovered_run_id BIGINT REFERENCES ingest_run(id),
    recovered_at TIMESTAMPTZ,
    recovery_attempts INTEGER NOT NULL DEFAULT 0,
    recovery_sent_at TIMESTAMPTZ,
    recovery_cancelled_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    CONSTRAINT ads_ingest_incident_scope CHECK (
        (profile_id IS NULL AND platform IS NULL)
        OR (profile_id IS NOT NULL AND platform IS NOT NULL)
    ),
    CONSTRAINT ads_ingest_incident_recovery CHECK (
        recovery_sent_at IS NULL OR (recovered_at IS NOT NULL AND alert_sent_at IS NOT NULL)
    ),
    CONSTRAINT ads_ingest_incident_recovery_terminal CHECK (
        recovery_cancelled_at IS NULL
        OR (recovered_at IS NOT NULL AND recovery_sent_at IS NULL AND closed_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ads_ingest_incident_abierto_idx
    ON ads_ingest_incident (profile_id, platform, tipo) NULLS NOT DISTINCT
    WHERE closed_at IS NULL;

COMMENT ON TABLE ads_ingest_incident IS
    'A.3: aviso principal por episodio. pending equivale a alert_sent_at NULL; '
    'solo acuse Telegram ok=true lo vuelve sent. Un exito real cierra sin '
    'recovery si el aviso original nunca salio.';

GRANT SELECT ON ads_ingest_incident TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT, UPDATE ON ads_ingest_incident TO app_ingest;
GRANT USAGE, SELECT ON SEQUENCE ads_ingest_incident_id_seq TO app_ingest;
