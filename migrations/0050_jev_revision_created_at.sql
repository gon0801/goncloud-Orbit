-- ---------------------------------------------------------------------------
-- 0050 — JEV ADS 01 (fila R1 del plan): created_at de jev_revision es la
-- insercion real. PostgreSQL 16.
--
-- 0049 dejo `created_at DEFAULT now()`, que es el inicio de la transaccion.
-- El flujo del diseno captura el censo DENTRO de esa transaccion, asi que
-- una revision valida guardaba captured_at > created_at y quien leyera las
-- columnas creeria que la captura fue posterior al registro. Con
-- clock_timestamp() el par queda ordenado y el trigger puede exigir la
-- promesa del encabezado de 0049: decided_at/captured_at <= created_at.
--
-- Tambien corrige el typo "jam el parecido" del COMMENT de
-- jev_ficha_version. 0049 no se edita: ya esta mergeada.
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE jev_revision ALTER COLUMN created_at SET DEFAULT clock_timestamp();

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
    IF NEW.captured_at > NEW.created_at OR NEW.decided_at > NEW.created_at THEN
        RAISE EXCEPTION
            'jev_revision: decided_at/captured_at posteriores a created_at'
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

COMMENT ON COLUMN jev_revision.created_at IS
  'Insercion real de la revision (clock_timestamp(), 0050). decided_at y '
  'captured_at nunca son posteriores: el trigger jev_revision_tiempos lo '
  'exige.';

COMMENT ON TABLE jev_ficha_version IS
  'JEV ADS 01: ficha de producto aprobada POR VERSION. La unidad de juicio '
  'es termino literal + version de ficha: una correccion inserta version '
  'nueva y las revisiones guardan la version exacta que usaron. El hash '
  'canonico (sha256) del contenido hace el registro idempotente; una ficha '
  'de otra variante no acredita a este producto (el listing cubierto manda, '
  'jamas el parecido de nombres).';

COMMIT;
