-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0094 (ARREGLOS 05 A.2).
--
-- Devuelve el esquema al estado de 0093: quita el disparador y su funcion, las
-- columnas decision_committed_at y resume_of de optimizer_cycle (con su clave
-- foranea) y el motivo vigencia_vencida del CHECK de decision_sin_aplicar.
--
-- Que conserva y que no:
--   1) ABORTA si ya hay desenlaces `vigencia_vencida`: restaurar el CHECK sin
--      ese motivo reescribiria historia (regla 5). El mensaje dice como salir.
--   2) NO toca apply_attempt: los intentos del ledger quedan como estaban,
--      abiertos o sellados.
--   3) NO borra ni reescribe optimizer_cycle: los envelopes siguen, tambien los
--      ejecutores. Su referencia al origen sobrevive en notes.resume_of, que el
--      ejecutor escribe siempre.
--
-- Binario compatible: el codigo anterior a 0094 no conoce las dos columnas y
-- nunca las escribe, asi que corre contra este esquema mientras la reversa no
-- se aplica. Por eso rollback.sh restaura el codigo ANTES de revertir las
-- migraciones. Limites conocidos con el binario anterior: no completa las
-- decisiones que un ejecutor dejo pendientes (quedan visibles en
-- v_decision_huerfana) y no expira lo no intentado al cambiar de dia.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM decision_sin_aplicar WHERE motivo = 'vigencia_vencida') THEN
        RAISE EXCEPTION
            'reversa 0094: hay desenlaces vigencia_vencida; conservalos (borrarlos '
            'reescribiria historia): revierte el codigo y deja el esquema, o '
            'resuelvelos a mano con el dueno antes de revertir';
    END IF;
END $$;

DROP TRIGGER optimizer_cycle_0094_progreso ON optimizer_cycle;
DROP FUNCTION optimizer_cycle_0094_progreso();

ALTER TABLE optimizer_cycle DROP COLUMN resume_of;
ALTER TABLE optimizer_cycle DROP COLUMN decision_committed_at;

ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check;
ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
        'espera_proteccion', 'bid_cambiado_fuera'
    ));

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0093 agrega bid_cambiado_fuera: la lectura fresca antes del PUT '
    '(bloque E) encontro en Amazon un bid distinto del que Orbit vio al '
    'decidir; no se envia nada y la hoja queda excluida del episodio.';
