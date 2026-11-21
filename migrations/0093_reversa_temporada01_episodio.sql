-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0093 (TEMPORADA 01 B.1).
--
-- Devuelve el esquema al estado de 0092: restaura la vista v_cambio_bid con el
-- TEXTO EXACTO de 0067 (tres ramas, sin motivo_temporada ni episodio_id: DROP
-- y CREATE, porque CREATE OR REPLACE VIEW no permite quitar columnas), el
-- CHECK de decision_sin_aplicar de 0081 (sin bid_cambiado_fuera) y borra las
-- cuatro tablas de temporada.
--
-- Orden resuelto:
--   1) ABORTA si ya hubo episodios o decisiones con los motivos nuevos: borrar
--      la identidad durable de una baja reescribiria historia (regla 5). El
--      mensaje dice como salir («desactiva la temporada y conserva el rastro»).
--   2) CHECK de decision_sin_aplicar sin bid_cambiado_fuera (los motivos de
--      0081; el codigo anterior del bloque B ya no corre tras el rollback del
--      esquema).
--   3) DROP de las cuatro tablas: PRIMERO temporada_fin_reduccion y
--      temporada_exclusion (apuntan al episodio), luego temporada_efecto y
--      temporada_episodio; despues las dos funciones solo-cierre (sus triggers
--      se van con cada tabla).
--   4) DROP + CREATE de v_cambio_bid con el texto de 0067 y sus GRANT de
--      SELECT (DROP VIEW borra los permisos).
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM temporada_episodio) THEN
        RAISE EXCEPTION
            'reversa 0093: ya hubo episodios de temporada; desactiva la '
            'temporada y conserva el rastro (borrarlos reescribiria historia)';
    END IF;
    IF EXISTS (
        SELECT 1 FROM decision
         WHERE inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja',
                                     'sale_temporada_baja')
    ) THEN
        RAISE EXCEPTION
            'reversa 0093: hay decisiones con motivos de temporada; desactiva '
            'la temporada y conserva el rastro (borrarlas reescribiria historia)';
    END IF;
END
$$;

ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check;

ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
        'espera_proteccion'
    ));

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0081 agrega espera_proteccion: la fila de pausa por cero ventas '
    'espera el vencimiento del episodio de venta reciente sin cobrar cuota, '
    'sin entrar a applying y sin reiniciar el veto de 48 horas.';

DROP TABLE temporada_exclusion;
DROP TABLE temporada_fin_reduccion;
DROP TABLE temporada_efecto;
DROP TABLE temporada_episodio;
DROP FUNCTION temporada_efecto_0093_solo_cierre();
DROP FUNCTION temporada_episodio_0093_solo_cierre();

DROP VIEW v_cambio_bid;
CREATE VIEW v_cambio_bid AS
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
  JOIN v_hoja_activa h ON h.campana_id = a.campana_id AND h.current_bid IS NOT NULL
 WHERE a.confirmado_el IS NOT NULL AND a.clase = 'ajuste_ubicacion';

GRANT SELECT ON v_cambio_bid TO app_decide, app_read, app_admin;

COMMENT ON VIEW v_cambio_bid IS
  'BIDS 02 0.b (0060) + V.3 (0067): historial UNION de bids por hoja. Rama '
  'motor, rama regreso_del_dueno (0060) y rama ajuste_de_campana (0067: '
  'ajustes confirmados de clase ajuste_ubicacion, una fila por hoja '
  'activa con el bid vigente en bid_antes y bid_despues).';
