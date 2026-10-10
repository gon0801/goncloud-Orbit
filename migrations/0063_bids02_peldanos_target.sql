-- 0063 (BIDS 02 T.1, seccion 2): retirar los peldanos cache_estado y
-- default del CHECK de target_acos_ciclo.
--
-- El target publicado en Amazon (ad_entity_state.acos_target) llega vacio
-- en todas las corridas, y un target inventado de 55 % contradice la
-- regla 3: sin peldano que resuelva, la hoja se salta con motivo
-- sin_target. (Bloque 0063 de docs/evidencia/bids-02/diseno/datos.sql.)
--
-- Regla 8 (SELECT previo en produccion, 2026-10-10): procedencias vivas
-- margen_plataforma (3380) y goal_campana (230); NINGUNA fila con
-- cache_estado ni default, asi que el CHECK los suelta sin conservar
-- historicos (cambio 8 no dispara). Evidencia:
-- docs/evidencia/bids-02/ejecucion/T.1/prod-procedencia/salidas/.
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, con su
-- reversa en 0063_reversa_bids02_peldanos_target.sql (restaura el CHECK
-- de 0048). La lista y el orden ESPEJAN
-- app.optimizer.goals.PELDANOS_CASCADA (el test estatico lo sella).

BEGIN;

ALTER TABLE target_acos_ciclo DROP CONSTRAINT target_acos_ciclo_procedencia_check;
ALTER TABLE target_acos_ciclo ADD CONSTRAINT target_acos_ciclo_procedencia_check
    CHECK (procedencia IN (
        'goal_campana', 'goal_plataforma', 'margen_familia', 'margen_plataforma',
        'setting_plataforma'
    ));

COMMIT;
