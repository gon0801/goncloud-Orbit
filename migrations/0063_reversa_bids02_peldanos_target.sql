-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0063 (patron 0011/0031/0049_reversa_*/0052_reversa_*).
--
-- Deshace `0063_bids02_peldanos_target.sql`: restaura el CHECK de
-- procedencia de target_acos_ciclo a su estado de 0048 (los siete
-- peldanos). No hay datos que perder (un CHECK no guarda filas).
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE target_acos_ciclo DROP CONSTRAINT target_acos_ciclo_procedencia_check;
ALTER TABLE target_acos_ciclo ADD CONSTRAINT target_acos_ciclo_procedencia_check
    CHECK (procedencia IN (
        'goal_campana', 'goal_plataforma', 'margen_familia', 'margen_plataforma',
        'setting_plataforma', 'cache_estado', 'default'
    ));

COMMIT;
