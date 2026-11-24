-- =============================================================================
--  Reversa de 0097_arreglos05_reposicion.sql
--
--  Un down JAMAS borra el historial de intentos (regla 5): si la tabla tiene
--  aunque sea una fila, aborta y deja el esquema como esta. Solo un esquema sin
--  intentos puede retirar la tabla y su disparador.
-- =============================================================================

DO $$
BEGIN
    IF to_regclass('public.keyword_reposicion_intento') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM keyword_reposicion_intento) THEN
        RAISE EXCEPTION
            '0097 reversa: keyword_reposicion_intento tiene intentos; se '
            'conserva el historial y la tabla no se retira'
            USING ERRCODE = 'restrict_violation';
    END IF;
END;
$$;

DROP TABLE IF EXISTS keyword_reposicion_intento;
DROP FUNCTION IF EXISTS keyword_reposicion_intento_invariantes();
