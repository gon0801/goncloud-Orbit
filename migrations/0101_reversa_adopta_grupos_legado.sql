-- Reversa de 0101_adopta_grupos_legado.sql.
--
-- Retira los roles y grupos de tipo 'legado' y devuelve campana_grupo al esquema
-- previo (lote NOT NULL y tipo IN ('fabrica5', 'impulso')).
--
-- Sin BEGIN/COMMIT: tools/despliegue/rollback.sh corre en una transaccion (-1).

DELETE FROM campana_grupo_rol
 WHERE grupo_id IN (SELECT id FROM campana_grupo WHERE tipo = 'legado');

DELETE FROM campana_grupo
 WHERE tipo = 'legado';

ALTER TABLE campana_grupo
    DROP CONSTRAINT campana_grupo_lote_segun_tipo,
    ALTER COLUMN lote SET NOT NULL,
    DROP CONSTRAINT campana_grupo_tipo_check,
    ADD CONSTRAINT campana_grupo_tipo_check
        CHECK (tipo IN ('fabrica5', 'impulso'));

COMMENT ON COLUMN campana_grupo.tipo IS
    'BIDS 02 I.1: fabrica5 (grupo de cinco de la fabrica) o impulso (grupo de dos del dueno).';
