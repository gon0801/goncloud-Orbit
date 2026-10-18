-- Permisos esperados tras 0074 (mismo contrato que su bloque DO final).
-- Solo lectura; corre como orbit_read. Lo que falta cuenta como diferencia,
-- nunca como error. Si todo cuadra imprime "permisos OK".
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        ('app_admin', 'bloqueo_veredicto', 'SELECT', true),
        ('app_admin', 'bloqueo_veredicto', 'INSERT', true),
        ('app_admin', 'bloqueo_veredicto', 'UPDATE', false),
        ('app_admin', 'bloqueo_veredicto', 'DELETE', false),
        ('app_admin', 'bloqueo_efecto', 'SELECT', true),
        ('app_admin', 'bloqueo_efecto', 'INSERT', true),
        ('app_admin', 'bloqueo_efecto', 'UPDATE', false),
        ('app_admin', 'bloqueo_efecto', 'DELETE', false),
        ('app_read', 'bloqueo_veredicto', 'SELECT', true),
        ('app_read', 'bloqueo_veredicto', 'INSERT', false),
        ('app_read', 'bloqueo_efecto', 'SELECT', true),
        ('app_read', 'bloqueo_efecto', 'INSERT', false),
        ('app_decide', 'bloqueo_veredicto', 'SELECT', false),
        ('app_decide', 'bloqueo_efecto', 'SELECT', false),
        ('app_ingest', 'bloqueo_veredicto', 'SELECT', false),
        ('app_ingest', 'bloqueo_efecto', 'SELECT', false),
        ('app_jev', 'bloqueo_veredicto', 'SELECT', false),
        ('app_jev', 'bloqueo_efecto', 'SELECT', false)
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
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
