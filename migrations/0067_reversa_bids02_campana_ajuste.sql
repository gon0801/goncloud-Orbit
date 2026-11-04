-- Reversa de 0067: restaura v_cambio_bid de la 0060 (dos ramas) y borra
-- campana_ajuste con sus filas (re-derivables: un ajuste confirmado vive
-- tambien en Amazon y en la observacion de configuracion que sello el
-- readback).
BEGIN;

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
 WHERE a.tipo = 'reversa' AND a.resultado = 'ok';

COMMENT ON VIEW v_cambio_bid IS
  'BIDS 02 0.b (0060): historial UNION de bids por hoja. Rama motor: bids '
  'confirmados por un ciclo live (filtros del cooldown). Rama regreso_del_'
  'dueno: reversas ok del ledger, con bid_despues = old_value revertido (el '
  'bid previo al regreso lo deriva el lector del cambio anterior).';

DROP TABLE campana_ajuste;
DROP FUNCTION campana_ajuste_0067_solo_confirmado();

-- La 0067 otorgo INSERT sobre la observacion de V.1 y USAGE en su
-- secuencia a app_admin (el readback confirmado): se revocan para que
-- el esquema quede EXACTO al base (el ensayo lo exige).
REVOKE INSERT ON ads_campana_config_observation FROM app_admin;
REVOKE USAGE, SELECT ON SEQUENCE ads_campana_config_observation_id_seq FROM app_admin;

COMMIT;
