-- =============================================================================
--  ORBIT - MIGRACION 0014 - ledger keyword_archivo_manual (BIDS 01) - PostgreSQL 16
--
--  Ledger de archivos MANUALES de keywords inertes con la identidad completa
--  para la REVERSA (regla 7): cada fila guarda campana, grupo, texto, match
--  type y bid con su moneda, mas el go literal del dueno que autorizo el
--  lote. El archivo en Amazon (POST /sp/keywords/delete) es ARCHIVED y no
--  se deshace: la vuelta atras es volver a crear la keyword (--reponer del
--  tool tools/archiva_inertes.py, que lee ESTA tabla).
--
--  Estados: 'planeado' (intencion durable ANTES del HTTP) -> 'applied' o
--  'failed' (con readback) -> 'repuesto' (con el external nuevo). El plan
--  sale de v_entidad_inerte (0013); el bid y su moneda, de ad_entity_state
--  (cache con CHECK parejo: ambos NULL o ambos valor, regla 4).
-- =============================================================================

CREATE TABLE keyword_archivo_manual (
  id                 BIGSERIAL PRIMARY KEY,
  lote               TEXT        NOT NULL,
  ad_entity_id       BIGINT      NOT NULL REFERENCES ad_entity(id),
  platform           platform    NOT NULL,
  campaign_external  TEXT        NOT NULL,
  ad_group_external  TEXT        NOT NULL,
  keyword_external   TEXT        NOT NULL,
  keyword_text       TEXT        NOT NULL,
  match_type         TEXT        NOT NULL,
  bid                NUMERIC(14,4),
  bid_currency       currency,
  clasificacion      TEXT        NOT NULL,
  dias_sin_impresiones INT,
  go_literal         TEXT        NOT NULL,
  intentado_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ack                JSONB,
  readback_estado    TEXT,
  estado             TEXT        NOT NULL
    CHECK (estado IN ('planeado','applied','failed','repuesto')),
  CONSTRAINT archivo_bid_con_moneda
    CHECK ((bid IS NULL) = (bid_currency IS NULL)),
  CONSTRAINT archivo_match_cerrado
    CHECK (match_type IN ('EXACT','PHRASE','BROAD')),
  -- Cada estado carga su evidencia (PR #134): applied exige el ack y el
  -- readback que lo confirmaron; repuesto exige el sello completo mas lo
  -- heredado de applied. planeado/failed sin requisitos (failed puede no
  -- traer readback si el POST lanzo).
  CONSTRAINT archivo_evidencia_applied
    CHECK (estado <> 'applied'
           OR (ack IS NOT NULL AND readback_estado IS NOT NULL)),
  CONSTRAINT archivo_evidencia_repuesto
    CHECK (estado <> 'repuesto'
           OR (repuesto_at IS NOT NULL AND repuesto_external IS NOT NULL
               AND repuesto_ack IS NOT NULL
               AND ack IS NOT NULL AND readback_estado IS NOT NULL)),
  repuesto_at        TIMESTAMPTZ,
  repuesto_external  TEXT,
  repuesto_ack       JSONB
);

COMMENT ON TABLE keyword_archivo_manual IS
  'BIDS 01 (D5): ledger de archivos MANUALES de keywords inertes con la '
  'identidad completa para la reversa (--reponer). Regla 7.';

GRANT SELECT ON keyword_archivo_manual
    TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT, UPDATE ON keyword_archivo_manual TO app_admin;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest, app_decide, app_admin;
