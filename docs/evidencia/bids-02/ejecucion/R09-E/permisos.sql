-- Permisos esperados de v_impulso_estado tras la 0071 (BIDS 02 R09-E).
-- Solo lectura. Si todo cuadra imprime "permisos OK". La 0071 declara
-- el SELECT de app_read y app_admin; los demas roles no se tocan (hoy
-- ingest y decide tambien leen la vista por default privileges y la
-- fila no lo cambia).
WITH esperado(rol, privilegio, debe) AS (
    VALUES
        ('app_read', 'SELECT', true),
        ('app_admin', 'SELECT', true),
        ('app_read', 'INSERT', false),
        ('app_read', 'UPDATE', false),
        ('app_read', 'DELETE', false),
        ('app_admin', 'INSERT', false),
        ('app_admin', 'UPDATE', false),
        ('app_admin', 'DELETE', false)
),
real AS (
    SELECT rol, privilegio, debe,
           CASE WHEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = esperado.rol)
                THEN false
                ELSE has_table_privilege(rol, 'public.v_impulso_estado', privilegio)
           END AS tiene
      FROM esperado
),
diferencias AS (
    SELECT rol || ' ' || privilegio || ' v_impulso_estado: esperado ' || debe
           || ', real ' || tiene AS d
      FROM real WHERE tiene <> debe
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
