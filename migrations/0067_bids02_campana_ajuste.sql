-- 0067: ajustes de campana del dueno (V.3): tabla campana_ajuste y tercera
-- rama de v_cambio_bid (origen ajuste_de_campana). Una fila por ajuste,
-- escrita ANTES del HTTP y confirmada con el readback (confirmado_el).
BEGIN;

CREATE TABLE campana_ajuste (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campana_id       BIGINT      NOT NULL REFERENCES ad_entity(id),
    platform         platform    NOT NULL,
    clase            TEXT        NOT NULL CHECK (clase IN ('fuera_de_amazon', 'ajuste_ubicacion', 'presupuesto')),
    antes_config_id  BIGINT      NOT NULL REFERENCES ads_campana_config_observation(id),
    despues          JSONB       NOT NULL,
    huella           TEXT        NOT NULL UNIQUE,
    actor            TEXT        NOT NULL CHECK (actor <> ''),
    go_literal       TEXT        NOT NULL,
    regresa_a        BIGINT      NULL REFERENCES campana_ajuste(id),
    creado_el        TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmado_el    TIMESTAMPTZ NULL,
    CONSTRAINT campana_ajuste_un_regreso UNIQUE (regresa_a)
);

-- Append-only salvo el sello: el unico UPDATE legitimo confirma una fila
-- pendiente (NULL -> valor en confirmado_el, sin tocar lo demas).
CREATE FUNCTION campana_ajuste_0067_solo_confirmado() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.campana_id IS DISTINCT FROM NEW.campana_id
       OR OLD.platform IS DISTINCT FROM NEW.platform
       OR OLD.clase IS DISTINCT FROM NEW.clase
       OR OLD.antes_config_id IS DISTINCT FROM NEW.antes_config_id
       OR OLD.despues IS DISTINCT FROM NEW.despues
       OR OLD.huella IS DISTINCT FROM NEW.huella
       OR OLD.actor IS DISTINCT FROM NEW.actor
       OR OLD.go_literal IS DISTINCT FROM NEW.go_literal
       OR OLD.regresa_a IS DISTINCT FROM NEW.regresa_a
       OR OLD.creado_el IS DISTINCT FROM NEW.creado_el
       OR OLD.confirmado_el IS NOT DISTINCT FROM NEW.confirmado_el
       OR OLD.confirmado_el IS NOT NULL THEN
        RAISE EXCEPTION
            'campana_ajuste: SOLO-CONFIRMADO: el unico UPDATE valido sella '
            'confirmado_el de una fila pendiente, sin tocar lo demas'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campana_ajuste_update_solo_confirmado
    BEFORE UPDATE ON campana_ajuste
    FOR EACH ROW EXECUTE FUNCTION campana_ajuste_0067_solo_confirmado();
CREATE TRIGGER campana_ajuste_append_only
    BEFORE DELETE ON campana_ajuste
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER campana_ajuste_append_only_truncate
    BEFORE TRUNCATE ON campana_ajuste
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Tercera rama de v_cambio_bid: por cada ajuste CONFIRMADO de clase
-- ajuste_ubicacion, una fila por hoja ACTIVA de esa campana. bid_antes =
-- bid_despues = bid vigente de la hoja (el ajuste mueve el precio del
-- clic, no el bid); decision_id NULL (no nace de una decision del motor).
-- El cambio de presupuesto no entra: no mueve el precio.
CREATE OR REPLACE VIEW v_cambio_bid AS
SELECT d.ad_entity_id                         AS hoja_id,
       da.confirmed_at                        AS confirmado_el,
       d.old_value                            AS bid_antes,
       d.new_value                            AS bid_despues,
       d.value_currency                       AS moneda,
       CASE WHEN d.inputs->>'motivo' = 'regreso_por_desplome'
            THEN 'regreso_por_desplome' ELSE 'motor' END AS origen,
       d.id                                   AS decision_id
  FROM decision_application da
  JOIN decision d         ON d.id = da.decision_id AND d.kind = 'bid'
  JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
 WHERE da.verify_ok IS TRUE
   AND d.old_value IS NOT NULL AND d.new_value IS NOT NULL AND d.new_value <> d.old_value
UNION ALL
SELECT d.ad_entity_id,
       a.finished_at,
       NULL::numeric,
       d.old_value,
       d.value_currency,
       'regreso_del_dueno',
       d.id
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id AND d.kind = 'bid'
 WHERE a.tipo = 'reversa' AND a.resultado = 'ok'
UNION ALL
SELECT h.hoja_id,
       a.confirmado_el,
       h.current_bid,
       h.current_bid,
       h.bid_currency,
       'ajuste_de_campana',
       NULL::bigint
  FROM campana_ajuste a
  JOIN v_hoja_activa h ON h.campana_id = a.campana_id
 WHERE a.confirmado_el IS NOT NULL AND a.clase = 'ajuste_ubicacion';

COMMENT ON TABLE campana_ajuste IS
  'BIDS 02 V.3 (0067): ajustes de campana que aprueba el dueno. Una fila '
  'por ajuste, escrita ANTES del HTTP (apply.aplica_ajuste_campana) y '
  'confirmada con el readback (confirmado_el). antes_config_id apunta a '
  'la vigente al planear; despues guarda la ConfigCampana destino como '
  'JSON; regresa_a marca la fila como regreso de otra (un solo regreso).';
COMMENT ON VIEW v_cambio_bid IS
  'BIDS 02 0.b (0060) + V.3 (0067): historial UNION de bids por hoja. Rama '
  'motor, rama regreso_del_dueno (0060) y rama ajuste_de_campana (0067: '
  'ajustes confirmados de clase ajuste_ubicacion, una fila por hoja '
  'activa con el bid vigente en bid_antes y bid_despues).';

GRANT SELECT ON campana_ajuste TO app_read, app_decide, app_admin;
GRANT INSERT ON campana_ajuste TO app_admin;
GRANT UPDATE (confirmado_el) ON campana_ajuste TO app_admin;
GRANT INSERT ON ads_campana_config_observation TO app_admin;
GRANT USAGE, SELECT ON SEQUENCE campana_ajuste_id_seq TO app_admin;
GRANT USAGE, SELECT ON SEQUENCE ads_campana_config_observation_id_seq TO app_admin;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'campana_ajuste', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'campana_ajuste', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_ajuste', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'campana_ajuste', 'INSERT')
       OR NOT has_column_privilege('app_admin', 'campana_ajuste', 'confirmado_el', 'UPDATE')
       OR NOT has_table_privilege('app_admin', 'ads_campana_config_observation', 'INSERT')
       OR has_table_privilege('app_read', 'campana_ajuste', 'INSERT')
       OR has_table_privilege('app_read', 'campana_ajuste', 'UPDATE')
       OR has_table_privilege('app_read', 'campana_ajuste', 'DELETE')
       OR has_table_privilege('app_decide', 'campana_ajuste', 'INSERT')
       OR has_table_privilege('app_decide', 'campana_ajuste', 'UPDATE')
       OR has_table_privilege('app_decide', 'campana_ajuste', 'DELETE')
       OR has_table_privilege('app_ingest', 'campana_ajuste', 'INSERT')
       OR has_table_privilege('app_ingest', 'campana_ajuste', 'UPDATE')
       OR has_table_privilege('app_ingest', 'campana_ajuste', 'DELETE') THEN
        RAISE EXCEPTION '0067: privilegios de campana_ajuste invalidos';
    END IF;
    IF NOT has_sequence_privilege('app_admin', 'campana_ajuste_id_seq', 'USAGE')
       OR NOT has_sequence_privilege('app_admin', 'campana_ajuste_id_seq', 'SELECT')
       OR NOT has_sequence_privilege(
           'app_admin', 'ads_campana_config_observation_id_seq', 'USAGE')
       OR NOT has_sequence_privilege(
           'app_admin', 'ads_campana_config_observation_id_seq', 'SELECT') THEN
        RAISE EXCEPTION '0067: privilegios de secuencia invalidos';
    END IF;
END
$$;

COMMIT;
