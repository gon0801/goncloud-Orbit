-- Permisos esperados tras 0060 (mismo contrato que su bloque DO final).
-- Solo lectura: corre en la base del ensayo (y como orbit_read en prod tras
-- el deploy). No falla cuando 0060 aun no aplica: lo que falta cuenta como
-- diferencia (to_regclass y pg_roles), nunca como error. Si todo cuadra
-- imprime "permisos OK".
-- Nota: app_ingest tambien tiene SELECT en las dos vistas, por el ALTER
-- DEFAULT PRIVILEGES de 0001 (verificado). La 0060 no lo revoca (modelo
-- 0048): no se pinea aqui, igual que el DO no lo nombra.
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        -- Las dos vistas: las leen decide, read y admin; nadie las escribe.
        ('app_decide', 'v_hoja_activa', 'SELECT', true),
        ('app_decide', 'v_hoja_activa', 'INSERT', false),
        ('app_decide', 'v_hoja_activa', 'UPDATE', false),
        ('app_decide', 'v_hoja_activa', 'DELETE', false),
        ('app_read', 'v_hoja_activa', 'SELECT', true),
        ('app_read', 'v_hoja_activa', 'INSERT', false),
        ('app_read', 'v_hoja_activa', 'UPDATE', false),
        ('app_read', 'v_hoja_activa', 'DELETE', false),
        ('app_admin', 'v_hoja_activa', 'SELECT', true),
        ('app_admin', 'v_hoja_activa', 'INSERT', false),
        ('app_admin', 'v_hoja_activa', 'UPDATE', false),
        ('app_admin', 'v_hoja_activa', 'DELETE', false),
        ('app_decide', 'v_cambio_bid', 'SELECT', true),
        ('app_decide', 'v_cambio_bid', 'INSERT', false),
        ('app_decide', 'v_cambio_bid', 'UPDATE', false),
        ('app_decide', 'v_cambio_bid', 'DELETE', false),
        ('app_read', 'v_cambio_bid', 'SELECT', true),
        ('app_read', 'v_cambio_bid', 'INSERT', false),
        ('app_read', 'v_cambio_bid', 'UPDATE', false),
        ('app_read', 'v_cambio_bid', 'DELETE', false),
        ('app_admin', 'v_cambio_bid', 'SELECT', true),
        ('app_admin', 'v_cambio_bid', 'INSERT', false),
        ('app_admin', 'v_cambio_bid', 'UPDATE', false),
        ('app_admin', 'v_cambio_bid', 'DELETE', false)
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
    SELECT 'vistas 0060: ' || count(*) || ' de 2'
      FROM pg_views WHERE schemaname = 'public'
       AND viewname IN ('v_hoja_activa', 'v_cambio_bid')
    HAVING count(*) <> 2
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
