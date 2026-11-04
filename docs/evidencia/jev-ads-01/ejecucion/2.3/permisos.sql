-- Permisos y esquema Jev esperados tras 0049 + 0050 (mismo contrato que
-- tests/test_jev_catalogo.py::test_roles_de_minimo_privilegio). Solo lectura:
-- corre como orbit_read en prod y en la base del ensayo. Falla con la lista
-- de diferencias; si todo cuadra imprime "permisos OK".
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        ('app_jev', 'product', 'SELECT', true),
        ('app_jev', 'listing', 'SELECT', true),
        ('app_jev', 'ad_entity', 'SELECT', true),
        ('app_jev', 'ad_entity_state', 'SELECT', true),
        ('app_jev', 'jev_revision', 'INSERT', true),
        ('app_jev', 'jev_par_evento', 'INSERT', true),
        ('app_jev', 'jev_ficha_version', 'SELECT', true),
        ('app_jev', 'decision', 'SELECT', false),
        ('app_jev', 'ledger_event', 'SELECT', false),
        ('app_jev', 'ads_optimizer_goal', 'SELECT', false),
        ('app_jev', 'search_term_observation', 'SELECT', false),
        ('app_jev', 'apply_queue', 'SELECT', false),
        ('app_jev', 'jev_ficha_version', 'INSERT', false),
        ('app_jev', 'jev_revision', 'UPDATE', false),
        ('app_admin', 'jev_ficha_version', 'INSERT', true),
        ('app_admin', 'jev_ficha_revocacion', 'INSERT', true),
        ('app_admin', 'jev_revision', 'INSERT', false),
        ('app_admin', 'jev_ficha_version', 'UPDATE', false),
        ('app_read', 'jev_ficha_version', 'SELECT', true),
        ('app_read', 'jev_revision', 'SELECT', true),
        ('app_read', 'jev_par_evento', 'SELECT', true),
        ('app_read', 'jev_revision', 'INSERT', false),
        ('app_decide', 'jev_revision', 'INSERT', false),
        ('app_ingest', 'jev_revision', 'INSERT', false)
),
real AS (
    SELECT rol, tabla, privilegio, debe,
           has_table_privilege(rol, 'public.' || tabla, privilegio) AS tiene
      FROM esperado
),
diferencias AS (
    SELECT rol || ' ' || privilegio || ' ' || tabla || ': esperado ' || debe || ', real ' || tiene AS d
      FROM real WHERE tiene <> debe
    UNION ALL
    SELECT 'jev_revision.created_at DEFAULT ' || coalesce(column_default, 'NULL')
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'jev_revision' AND column_name = 'created_at'
       AND column_default IS DISTINCT FROM 'clock_timestamp()'
    UNION ALL
    SELECT 'tablas Jev: ' || count(*) || ' de 4'
      FROM pg_tables WHERE schemaname = 'public'
       AND tablename IN ('jev_ficha_version', 'jev_ficha_revocacion', 'jev_revision', 'jev_par_evento')
    HAVING count(*) <> 4
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
