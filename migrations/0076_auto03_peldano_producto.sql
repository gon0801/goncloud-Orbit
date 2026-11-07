-- sonda-aplicada: SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'target_acos_ciclo_procedencia_check' AND pg_get_constraintdef(oid) LIKE '%margen_producto%')
-- 0076 (AUTO-03 C.1): el CHECK de procedencia de target_acos_ciclo admite
-- margen_producto, la meta por producto, tercera en la cascada.
--
-- La lista y el orden ESPEJAN app.optimizer.goals.PELDANOS_CASCADA (el test
-- estatico y el del CHECK vivo de tests/test_apply_schema.py lo sellan).
-- Solo amplia: toda fila vigente cumple el CHECK nuevo, asi que no hay
-- SELECT previo que correr (regla 8).
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0076_reversa_auto03_peldano_producto.sql (aborta si ya hay filas
-- margen_producto).

ALTER TABLE target_acos_ciclo DROP CONSTRAINT target_acos_ciclo_procedencia_check;
ALTER TABLE target_acos_ciclo ADD CONSTRAINT target_acos_ciclo_procedencia_check
    CHECK (procedencia IN (
        'goal_campana', 'goal_plataforma', 'margen_producto', 'margen_familia',
        'margen_plataforma', 'setting_plataforma'
    ));
