-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0081 (ORBIT 07 M1).
--
-- Quita la tabla de episodios de venta reciente y restaura el CHECK de
-- decision_sin_aplicar de la 0045 (sin espera_proteccion). Aborta si ya hay
-- episodios: borrarlos reescribiria historia (regla 5). El codigo anterior
-- corre con la 0081 aplicada (solo agrega), asi que el rollback del
-- despliegue sigue aunque esta reversa aborte.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM proteccion_venta_reciente) THEN
        RAISE EXCEPTION 'reversa 0081: proteccion_venta_reciente tiene episodios';
    END IF;
END
$$;

ALTER TABLE decision_sin_aplicar DROP CONSTRAINT decision_sin_aplicar_motivo_check;

ALTER TABLE decision_sin_aplicar ADD CONSTRAINT decision_sin_aplicar_motivo_check
    CHECK (motivo IN (
        'modo_no_live', 'ya_aplicada', 'bid_incompleto', 'entidad_no_decisora',
        'tope_intentos', 'fuera_de_cap', 'fallo_http', 'sin_quota',
        'espera_target', 'sin_respuesta', 'perdida', 'choque_clave'
    ));

DROP TABLE proteccion_venta_reciente;
DROP FUNCTION proteccion_venta_vence_fijo();
