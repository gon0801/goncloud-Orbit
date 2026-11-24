-- =============================================================================
--  Reversa de 0098_arreglos05_indice_shadow.sql
--
--  Restaura el indice mixto de 0002 (`apply_queue_clave_efecto_en_vuelo`) y
--  retira el indice live y el trigger de 0098. Un down NUNCA borra filas ni
--  promociona practicas (regla 5): si una practica vencida convivio con un
--  efecto live de la misma clave, restaurar el indice mixto chocaria. En ese
--  caso aborta ANTES de cualquier DROP/CREATE y deja el esquema de 0098 como
--  esta. Las claves afectadas se resuelven operando (descartando la practica
--  como administrador), no borrando historia.
--
--  apply_queue existe desde 0002, que siempre precede a 0098.
-- =============================================================================

DO $$
DECLARE
    v_colisiones INTEGER;
BEGIN
    SELECT count(*) INTO v_colisiones
      FROM (
            SELECT 1
              FROM apply_queue
             WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded')
             GROUP BY platform, ad_entity_id, familia, search_term
            HAVING count(*) > 1
           ) x;

    IF v_colisiones > 0 THEN
        RAISE EXCEPTION
            '0098 reversa: % clave(s) de efecto con mas de una fila no '
            'terminal; restaurar el indice mixto de 0002 chocaria. No se '
            'borran filas: se conserva el esquema de 0098.', v_colisiones
            USING ERRCODE = 'restrict_violation';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS apply_queue_clave_vigente ON apply_queue;
DROP FUNCTION IF EXISTS apply_queue_clave_vigente();
DROP INDEX IF EXISTS apply_queue_live_efecto_unico;

CREATE UNIQUE INDEX apply_queue_clave_efecto_en_vuelo
    ON apply_queue (platform, ad_entity_id, familia, search_term)
    NULLS NOT DISTINCT
    WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded');
