-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM pg_views WHERE schemaname = 'public' AND viewname = 'v_cambio_bid' AND position('request_payload' in definition) > 0 AND has_table_privilege('app_decide', 'public.v_cambio_bid', 'SELECT') AND has_table_privilege('app_read', 'public.v_cambio_bid', 'SELECT'))
-- 0096 (ARREGLOS 05 B, revision): el historial de pujas publica el bid que el
-- regreso del dueno dejo de verdad en Amazon.
--
-- v_cambio_bid informaba, para cada regreso confirmado, decision.old_value: el
-- bid de la cache al decidir. Desde ARREGLOS 05 B la reversa vuelve al bid que
-- Orbit leyo en Amazon antes del PUT (apply_attempt.bid_previo), que puede
-- diferir de old_value en el redondeo. Cuando difieren, la vista y la respuesta
-- del regreso informaban una puja distinta de la que Amazon confirmo, y
-- recuperacion.cadena_bid leia esa diferencia como una intervencion del dueno
-- que no existio.
--
-- La columna bid_despues de la rama `regreso_del_dueno` pasa a ser el `bid` del
-- payload del intento de reversa que quedo ok: es exactamente lo que se envio y
-- cuya lectura de vuelta confirmo. Si ese payload no trae un monto legible
-- (intento antiguo o corrupto) conserva decision.old_value. Es la misma forma
-- en que la lectura de la vuelta confirma el regreso: cuantizada al wire.
--
-- Sin BEGIN/COMMIT. No re-runnable. Reversa:
-- 0096_reversa_arreglos05_regreso_destino_efectivo.sql. Los privilegios de la
-- vista son los de 0060 y CREATE OR REPLACE los conserva; no hay grants nuevos.

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
       CASE WHEN a.request_payload->>'bid' ~ '^[0-9]+([.][0-9]+)?$'
            THEN (a.request_payload->>'bid')::numeric ELSE d.old_value END,
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
    'Historial de bids aplicados por hoja (0060). Desde ARREGLOS 05 B (0096) la '
    'rama regreso_del_dueno publica como bid_despues el bid del payload del '
    'intento de reversa que quedo ok: el valor que Orbit envio y cuya lectura de '
    'vuelta confirmo. Si el payload no trae un monto legible conserva '
    'decision.old_value. Fuente unica de la racha de recortes y de su regreso.';
