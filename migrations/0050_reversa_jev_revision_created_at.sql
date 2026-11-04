-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0050 (patron 0011/0031/0049_reversa_*).
--
-- Devuelve jev_revision.created_at a DEFAULT now() y el trigger
-- jev_revision_tiempos y el COMMENT de jev_ficha_version a su estado de
-- 0049. No borra datos: las revisiones ya insertadas conservan sus tiempos. Se corre ANTES de la reversa de 0049
-- si se deshacen ambas.
--
-- NO se aplica en el despliegue normal.
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE jev_revision ALTER COLUMN created_at SET DEFAULT now();

CREATE OR REPLACE FUNCTION jev_revision_tiempos() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
SET TimeZone = 'UTC'
AS $$
BEGIN
    IF NEW.created_at > clock_timestamp()
       OR NEW.captured_at > clock_timestamp()
       OR NEW.decided_at > clock_timestamp() THEN
        RAISE EXCEPTION
            'jev_revision: decided_at/captured_at/created_at posteriores a la insercion'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(NEW.ficha_version_ids) AS x WHERE x IS NULL)
       OR (SELECT count(DISTINCT y) FROM unnest(NEW.ficha_version_ids) AS y)
           <> COALESCE(array_length(NEW.ficha_version_ids, 1), 0) THEN
        RAISE EXCEPTION 'jev_revision: ficha_version_ids con NULL o duplicados'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN jev_revision.created_at IS NULL;

COMMENT ON TABLE jev_ficha_version IS
  'JEV ADS 01: ficha de producto aprobada POR VERSION. La unidad de juicio '
  'es termino literal + version de ficha: una correccion inserta version '
  'nueva y las revisiones guardan la version exacta que usaron. El hash '
  'canonico (sha256) del contenido hace el registro idempotente; una ficha '
  'de otra variante no acredita a este producto (el listing cubierto manda, '
  'jam el parecido de nombres).';

COMMIT;
