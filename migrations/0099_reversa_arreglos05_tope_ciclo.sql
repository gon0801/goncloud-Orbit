-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0099 (ARREGLOS 05 H.2).
--
-- Devuelve el vocabulario de `decision_sin_aplicar` al estado de 0094: quita
-- el motivo `tope_ciclo` del CHECK nombrado.
--
-- Que conserva y que no:
--   1) ABORTA si ya hay desenlaces `tope_ciclo`: restaurar el CHECK sin ese
--      motivo reescribiria historia (regla 5). El mensaje dice como salir.
--   2) NO toca `apply_attempt` ni `optimizer_cycle`: los intentos del ledger y
--      los ciclos quedan como estaban.
--   3) No hay tabla, columna, indice ni grant que deshacer: 0099 solo amplio
--      el vocabulario del CHECK.
--
-- Binario compatible: el codigo anterior a 0099 no conoce `tope_ciclo` y no lo
-- escribe; contra este esquema sigue funcionando mientras la reversa no se
-- aplica. Los desenlaces `tope_ciclo` ya escritos impiden la reversa.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM decision_sin_aplicar WHERE motivo = 'tope_ciclo') THEN
        RAISE EXCEPTION
            'reversa 0099: hay desenlaces tope_ciclo; conservalos (borrarlos '
            'reescribiria historia): revierte el codigo y deja el esquema, o '
            'resuelvelos a mano con el dueno antes de revertir'
            USING ERRCODE = 'restrict_violation';
    END IF;
END $$;

ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check;
ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave',
        'espera_proteccion', 'bid_cambiado_fuera', 'vigencia_vencida'
    ));

COMMENT ON CONSTRAINT decision_sin_aplicar_motivo_check ON decision_sin_aplicar IS
    'Vocabulario cerrado, espejo de app.apply_cuota.MOTIVOS_SIN_APLICAR (mismo '
    'orden). 0094 agrega vigencia_vencida: la decision ordinaria que nunca se '
    'intento deja de ser elegible al cambiar el dia en que se decidio. No se '
    'borra la decision ni se toca la que ya tiene un intento: esa solo la '
    'resuelve la conciliacion, leyendo.';
