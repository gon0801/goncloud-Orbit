-- Senales esperadas del candado de la 0072 (BIDS 02 R09-J).
-- Solo lectura. Si todo cuadra imprime "permisos OK". Verifica que el
-- trigger y su funcion existan; la conducta (rechazar un impulso
-- sobre un grupo fabrica5) la fija tests/test_migracion_0072.py.
WITH esperado(senal, debe) AS (
    VALUES
        ('trigger impulso_grupo_tipo en impulso', true),
        ('funcion impulso_grupo_es_impulso', true)
),
real AS (
    SELECT senal, debe,
           CASE senal
               WHEN 'trigger impulso_grupo_tipo en impulso' THEN EXISTS (
                   SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                    WHERE t.tgname = 'impulso_grupo_tipo' AND NOT t.tgisinternal
                      AND c.relname = 'impulso')
               WHEN 'funcion impulso_grupo_es_impulso' THEN EXISTS (
                   SELECT 1 FROM pg_proc WHERE proname = 'impulso_grupo_es_impulso')
           END AS tiene
      FROM esperado
),
diferencias AS (
    SELECT senal || ': esperado ' || debe || ', real ' || tiene AS d
      FROM real WHERE tiene <> debe
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
