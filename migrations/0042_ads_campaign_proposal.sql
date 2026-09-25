-- ADS PROTECCION C.4: episodios economicos de campana, solo propuestas.
-- No hay FK a apply_queue ni mutacion de Amazon Ads.
BEGIN;
CREATE TABLE ads_campaign_proposal (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campaign_id BIGINT NOT NULL REFERENCES ad_entity(id),
    platform platform NOT NULL,
    campaign_external_id TEXT NOT NULL,
    profile_id BIGINT,
    risk_type TEXT NOT NULL CHECK (risk_type = 'exceso_economico'),
    status TEXT NOT NULL CHECK (status IN (
        'open', 'resolved', 'dismissed', 'paused_observed', 'paused_external'
    )),
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    reset_at TIMESTAMPTZ,
    close_reason TEXT,
    close_evidence JSONB,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    cost money_amount NOT NULL,
    revenue money_amount NOT NULL,
    currency currency NOT NULL,
    target_pct NUMERIC NOT NULL,
    target_source TEXT NOT NULL,
    excess money_amount NOT NULL,
    acos_pct NUMERIC,
    campaign_status TEXT NOT NULL,
    status_synced_at TIMESTAMPTZ NOT NULL,
    evidence JSONB NOT NULL,
    aviso_estado TEXT NOT NULL DEFAULT 'pending'
        CHECK (aviso_estado IN ('pending', 'sent')),
    aviso_intentos INTEGER NOT NULL DEFAULT 0 CHECK (aviso_intentos >= 0),
    aviso_enviado_at TIMESTAMPTZ,
    CONSTRAINT proposal_cierre_coherente CHECK ((status = 'open') = (closed_at IS NULL)),
    CONSTRAINT proposal_dinero_sano CHECK (cost >= 0 AND revenue >= 0 AND target_pct > 0),
    CONSTRAINT proposal_ventana_coherente CHECK (window_start <= window_end),
    CONSTRAINT proposal_aviso_coherente CHECK (
        (aviso_estado = 'sent') = (aviso_enviado_at IS NOT NULL)
    ),
    CONSTRAINT proposal_moneda_plataforma CHECK (
        (platform = 'amazon_us' AND currency = 'USD') OR
        (platform = 'amazon_mx' AND currency = 'MXN')
    )
);

CREATE UNIQUE INDEX ads_campaign_proposal_abierta
    ON ads_campaign_proposal (campaign_id, risk_type) WHERE status = 'open';
CREATE INDEX ads_campaign_proposal_por_campana
    ON ads_campaign_proposal (campaign_id, risk_type, id DESC);
CREATE INDEX ads_campaign_proposal_pendientes
    ON ads_campaign_proposal (last_seen_at DESC) WHERE status = 'open';
CREATE INDEX ads_campaign_proposal_avisos
    ON ads_campaign_proposal (id) WHERE status = 'open' AND aviso_estado = 'pending';

COMMENT ON TABLE ads_campaign_proposal IS
  'Una fila por episodio de riesgo de spCampaigns. El motor actualiza evidencia '
  'de una propuesta abierta, la cierra si ya no cruza el limite y solo abre '
  'otra cuando se observo una ventana sin riesgo tras el cierre. No hay '
  'escritura de campana en Amazon ni entrada de apply_queue.';
COMMENT ON COLUMN ads_campaign_proposal.reset_at IS
  'Primera observacion sin riesgo despues de cerrar el episodio; habilita '
  'un episodio nuevo si el riesgo vuelve. NULL en dismissed o paused_observed '
  'mantiene la barrera contra reabrir por un cron repetido.';
COMMENT ON COLUMN ads_campaign_proposal.profile_id IS
  'Perfil de Amazon Ads para el readback de C.5 (campaignId/profile). NULL '
  'hasta que C.5 lo resuelva por /v2/profiles: la fase de decision no hace '
  'HTTP y el profile_id no se inventa (regla 3).';
COMMENT ON COLUMN ads_campaign_proposal.aviso_estado IS
  'Contrato de entrega del aviso Telegram de propuesta nueva: pending al '
  'abrir, sent solo tras HTTP 2xx. Un fallo deja pending (+1 intento) y el '
  'ciclo siguiente reintenta sin duplicar; jamas se marca sent sin enviar.';

GRANT SELECT ON ads_campaign_proposal TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON ads_campaign_proposal TO app_decide;
GRANT UPDATE (last_seen_at, closed_at, reset_at, close_reason, close_evidence,
              window_start, window_end, observed_at, cost, revenue, currency,
              target_pct, target_source, excess, acos_pct, campaign_status,
              status_synced_at, evidence, status, profile_id,
              aviso_estado, aviso_intentos, aviso_enviado_at)
    ON ads_campaign_proposal TO app_decide;
GRANT USAGE ON SEQUENCE ads_campaign_proposal_id_seq TO app_decide;

-- Candado de privilegios (patron 0036, solo has_* para no exigir membresia
-- de rol al migrar: la prueba de SET ROLE vive en test_propuestas_campana).
DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'ads_campaign_proposal', 'INSERT') THEN
        RAISE EXCEPTION '0042: app_decide debe poder abrir propuestas';
    END IF;
    IF NOT has_column_privilege(
        'app_decide', 'ads_campaign_proposal', 'aviso_estado', 'UPDATE'
    ) THEN
        RAISE EXCEPTION '0042: app_decide debe poder marcar avisos enviados';
    END IF;
    IF NOT has_column_privilege(
        'app_decide', 'ads_campaign_proposal', 'profile_id', 'UPDATE'
    ) THEN
        RAISE EXCEPTION '0042: app_decide debe poder anotar profile_id';
    END IF;
    IF has_table_privilege('app_read', 'ads_campaign_proposal', 'INSERT') THEN
        RAISE EXCEPTION '0042: app_read no debe poder abrir propuestas';
    END IF;
    IF has_table_privilege('app_read', 'ads_campaign_proposal', 'UPDATE') THEN
        RAISE EXCEPTION '0042: app_read no debe poder tocar propuestas';
    END IF;
    IF has_table_privilege('app_ingest', 'ads_campaign_proposal', 'INSERT') THEN
        RAISE EXCEPTION '0042: app_ingest no debe poder abrir propuestas';
    END IF;
END $$;

COMMIT;
