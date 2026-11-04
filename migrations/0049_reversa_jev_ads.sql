-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0049 (patron 0011/0031_reversa_*).
--
-- Deshace `0049_jev_ads.sql`: borra las cuatro tablas Jev, sus funciones
-- de trigger y el grupo `app_jev` con sus grants sobre tablas ajenas.
--
-- Solo aplica ANTES de la primera ficha o revision real: con cualquiera
-- de esas tablas pobladas, el DROP destruiria fichas aprobadas, fichas
-- fuente y auditoria de intentos irrecuperables; la guarda de abajo
-- aborta sin escribir nada en ese caso. Despues del primer dato real la
-- salida es actualizar el diseno, no reversar.
--
-- NO se aplica en el despliegue normal. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

DO $$
DECLARE
    v_datos INTEGER;
BEGIN
    SELECT (SELECT count(*) FROM jev_par_evento)
         + (SELECT count(*) FROM jev_revision)
         + (SELECT count(*) FROM jev_ficha_revocacion)
         + (SELECT count(*) FROM jev_ficha_version)
      INTO v_datos;
    IF v_datos > 0 THEN
        RAISE EXCEPTION
            'reversa 0049: hay % filas Jev (fichas/revisiones/eventos); '
            'la reversa ya no aplica sin decision consciente de perdida',
            v_datos;
    END IF;
END
$$;

DROP TABLE jev_par_evento;
DROP TABLE jev_revision;
DROP TABLE jev_ficha_revocacion;
DROP TABLE jev_ficha_version;

DROP FUNCTION jev_par_evento_encadenado();
DROP FUNCTION jev_revision_tiempos();
DROP FUNCTION jev_ficha_version_cobertura();

-- Los grants sobre tablas ajenas impiden DROP ROLE; se revocan primero.
REVOKE SELECT ON ad_entity, ad_entity_state, listing, product FROM app_jev;

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_jev') THEN
        DROP ROLE app_jev;
    END IF;
END
$$;

COMMIT;
