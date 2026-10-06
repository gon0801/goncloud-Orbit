-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0052 (patron 0011/0031/0049_reversa_*).
--
-- Deshace `0052_jev_senales.sql`: borra la vista, las cinco tablas, el
-- indice parcial sobre jev_par_evento y los dos CHECK nuevos de
-- jev_revision, que recrea con sus nombres y su COMMENT de 0049. Devuelve
-- SELECT a app_decide y app_ingest sobre las cuatro tablas de 0049.
--
-- Solo aplica ANTES de la primera fila lote: con una corrida real, el
-- DROP destruiria senales, avisos y auditoria irrecuperables; la guarda
-- de abajo aborta sin escribir nada en ese caso. Despues del primer dato
-- real la salida es actualizar el diseno, no reversar.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM jev_revision WHERE sujeto_tipo = 'lote') THEN
        RAISE EXCEPTION
            'reversa 0052: hay filas lote en jev_revision; '
            'la reversa ya no aplica sin decision consciente de perdida';
    END IF;
END
$$;

DROP VIEW jev_senal_vigente;
DROP TABLE jev_aviso_entrega;
DROP TABLE jev_aviso;
DROP TABLE jev_senal;
DROP TABLE jev_roster;
DROP TABLE jev_corrida;
DROP INDEX jev_par_evento_intencion_fecha_idx;

ALTER TABLE jev_revision
  DROP CONSTRAINT jev_revision_sujeto_tipo_check,
  ADD  CONSTRAINT jev_revision_sujeto_tipo_check
       CHECK (sujeto_tipo IN ('decision', 'semillas')),
  DROP CONSTRAINT jev_revision_sujeto_coherente,
  ADD  CONSTRAINT jev_revision_sujeto_coherente CHECK (
       (sujeto_tipo = 'decision'
            AND decision_id IS NOT NULL
            AND plan_canonico IS NULL AND plan_sha256 IS NULL
            AND fuentes_semillas IS NULL)
    OR (sujeto_tipo = 'semillas'
            AND decision_id IS NULL
            AND plan_canonico IS NOT NULL AND plan_sha256 IS NOT NULL
            AND fuentes_semillas IS NOT NULL)
  );

COMMENT ON CONSTRAINT jev_revision_sujeto_coherente ON jev_revision IS
  'Sujeto discriminado: revision de una decision guardada (decision_id) o '
  'de un plan canonico de fabrica (plan + hash + fuentes de semillas), '
  'jamas ambas ni ninguna.';

GRANT SELECT ON jev_ficha_version, jev_ficha_revocacion, jev_revision, jev_par_evento
    TO app_decide, app_ingest;

COMMIT;
