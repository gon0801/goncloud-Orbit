-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0096 (ARREGLOS 05 B).
--
-- Devuelve v_cambio_bid a la definicion de 0093: la rama regreso_del_dueno
-- vuelve a publicar decision.old_value como bid_despues. No toca datos: solo
-- reemplaza la vista. Con el binario anterior a ARREGLOS 05 B la reversa
-- siempre escribe old_value, asi que la vista vuelve a coincidir con lo enviado.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_cambio_bid AS
SELECT d.ad_entity_id AS hoja_id,
       da.confirmed_at AS confirmado_el,
       d.old_value AS bid_antes,
       d.new_value AS bid_despues,
       d.value_currency AS moneda,
       CASE WHEN d.inputs->>'motivo' = 'regreso_por_desplome'
            THEN 'regreso_por_desplome'
            WHEN d.inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja',
                                         'sale_temporada_baja')
            THEN 'temporada'
            ELSE 'motor' END AS origen,
       d.id AS decision_id,
       CASE d.inputs->>'motivo' WHEN 'entra_temporada_baja' THEN 'entra'
                                WHEN 'ajusta_temporada_baja' THEN 'ajusta'
                                WHEN 'sale_temporada_baja' THEN 'sale' END AS motivo_temporada,
       (d.inputs->>'temporada_episodio_id')::bigint AS episodio_id
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
       d.id,
       NULL::text,
       NULL::bigint
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
       NULL::bigint,
       NULL::text,
       NULL::bigint
  FROM campana_ajuste a
  JOIN v_hoja_activa h ON h.campana_id = a.campana_id AND h.current_bid IS NOT NULL
 WHERE a.confirmado_el IS NOT NULL AND a.clase = 'ajuste_ubicacion';

COMMENT ON VIEW v_cambio_bid IS
    'Historial de bids aplicados por hoja (0060). Definicion de 0093: la rama '
    'regreso_del_dueno publica decision.old_value como bid_despues.';
