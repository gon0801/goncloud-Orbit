-- Permisos y esquema Jev esperados tras 0052 (migracion B de S.3; mismo
-- contrato que su bloque DO final y que
-- tests/test_jev_perimetro.py::test_perimetro_jev_con_login_real).
-- Solo lectura: corre como orbit_read en prod y en la base del ensayo.
-- No falla cuando B aun no aplica: lo que falta cuenta como diferencia
-- (to_regclass y pg_roles), nunca como error. Si todo cuadra imprime
-- "permisos OK".
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        -- app_jev sobre las cinco tablas nuevas: SELECT + INSERT, nada mas.
        ('app_jev', 'jev_roster', 'SELECT', true),
        ('app_jev', 'jev_roster', 'INSERT', true),
        ('app_jev', 'jev_roster', 'UPDATE', false),
        ('app_jev', 'jev_roster', 'DELETE', false),
        ('app_jev', 'jev_senal', 'SELECT', true),
        ('app_jev', 'jev_senal', 'INSERT', true),
        ('app_jev', 'jev_senal', 'UPDATE', false),
        ('app_jev', 'jev_senal', 'DELETE', false),
        ('app_jev', 'jev_aviso', 'SELECT', true),
        ('app_jev', 'jev_aviso', 'INSERT', true),
        ('app_jev', 'jev_aviso', 'UPDATE', false),
        ('app_jev', 'jev_aviso', 'DELETE', false),
        ('app_jev', 'jev_aviso_entrega', 'SELECT', true),
        ('app_jev', 'jev_aviso_entrega', 'INSERT', true),
        ('app_jev', 'jev_aviso_entrega', 'UPDATE', false),
        ('app_jev', 'jev_aviso_entrega', 'DELETE', false),
        ('app_jev', 'jev_corrida', 'SELECT', true),
        ('app_jev', 'jev_corrida', 'INSERT', true),
        ('app_jev', 'jev_corrida', 'UPDATE', false),
        ('app_jev', 'jev_corrida', 'DELETE', false),
        -- Lectura de las tablas nuevas: app_read y app_admin si, ciclo no.
        ('app_read', 'jev_roster', 'SELECT', true),
        ('app_read', 'jev_senal', 'SELECT', true),
        ('app_read', 'jev_aviso', 'SELECT', true),
        ('app_read', 'jev_aviso_entrega', 'SELECT', true),
        ('app_read', 'jev_corrida', 'SELECT', true),
        ('app_admin', 'jev_roster', 'SELECT', true),
        ('app_admin', 'jev_senal', 'SELECT', true),
        ('app_admin', 'jev_aviso', 'SELECT', true),
        ('app_admin', 'jev_aviso_entrega', 'SELECT', true),
        ('app_admin', 'jev_corrida', 'SELECT', true),
        ('app_decide', 'jev_roster', 'SELECT', false),
        ('app_decide', 'jev_senal', 'SELECT', false),
        ('app_decide', 'jev_aviso', 'SELECT', false),
        ('app_decide', 'jev_aviso_entrega', 'SELECT', false),
        ('app_decide', 'jev_corrida', 'SELECT', false),
        ('app_ingest', 'jev_roster', 'SELECT', false),
        ('app_ingest', 'jev_senal', 'SELECT', false),
        ('app_ingest', 'jev_aviso', 'SELECT', false),
        ('app_ingest', 'jev_aviso_entrega', 'SELECT', false),
        ('app_ingest', 'jev_corrida', 'SELECT', false),
        -- La vista: la leen Jev, lectura y admin; el ciclo no.
        ('app_jev', 'jev_senal_vigente', 'SELECT', true),
        ('app_read', 'jev_senal_vigente', 'SELECT', true),
        ('app_admin', 'jev_senal_vigente', 'SELECT', true),
        ('app_decide', 'jev_senal_vigente', 'SELECT', false),
        ('app_ingest', 'jev_senal_vigente', 'SELECT', false),
        -- Cierre del perimetro: el ciclo ya no lee las cuatro de 0049.
        ('app_decide', 'jev_ficha_version', 'SELECT', false),
        ('app_decide', 'jev_ficha_revocacion', 'SELECT', false),
        ('app_decide', 'jev_revision', 'SELECT', false),
        ('app_decide', 'jev_par_evento', 'SELECT', false),
        ('app_ingest', 'jev_ficha_version', 'SELECT', false),
        ('app_ingest', 'jev_ficha_revocacion', 'SELECT', false),
        ('app_ingest', 'jev_revision', 'SELECT', false),
        ('app_ingest', 'jev_par_evento', 'SELECT', false),
        -- Jev no lee el motor (0049 ya lo dejaba asi; B lo conserva).
        ('app_jev', 'decision', 'SELECT', false),
        ('app_jev', 'apply_queue', 'SELECT', false),
        ('app_jev', 'search_term_observation', 'SELECT', false)
),
real AS (
    SELECT rol, tabla, privilegio, debe,
           CASE WHEN to_regclass('public.' || tabla) IS NULL THEN false
                WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = esperado.rol) THEN false
                ELSE has_table_privilege(rol, 'public.' || tabla, privilegio) END AS tiene
      FROM esperado
),
diferencias AS (
    SELECT rol || ' ' || privilegio || ' ' || tabla || ': esperado ' || debe || ', real ' || tiene AS d
      FROM real WHERE tiene <> debe
    UNION ALL
    SELECT 'tablas Jev nuevas: ' || count(*) || ' de 5'
      FROM pg_tables WHERE schemaname = 'public'
       AND tablename IN ('jev_roster', 'jev_senal', 'jev_aviso', 'jev_aviso_entrega', 'jev_corrida')
    HAVING count(*) <> 5
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
