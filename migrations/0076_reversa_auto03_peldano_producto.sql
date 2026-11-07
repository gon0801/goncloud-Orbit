-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0076 (AUTO-03 C.1).
--
-- Restaura el CHECK de procedencia de target_acos_ciclo de 0063 (cinco
-- peldanos). Aborta si ya hay filas margen_producto: target_acos_ciclo es
-- append-only y esas filas no cumplirian el CHECK restaurado.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM target_acos_ciclo WHERE procedencia = 'margen_producto') THEN
        RAISE EXCEPTION 'reversa 0076: hay filas margen_producto en target_acos_ciclo';
    END IF;
END
$$;

ALTER TABLE target_acos_ciclo DROP CONSTRAINT target_acos_ciclo_procedencia_check;
ALTER TABLE target_acos_ciclo ADD CONSTRAINT target_acos_ciclo_procedencia_check
    CHECK (procedencia IN (
        'goal_campana', 'goal_plataforma', 'margen_familia', 'margen_plataforma',
        'setting_plataforma'
    ));
