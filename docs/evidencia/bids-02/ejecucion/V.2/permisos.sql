-- Permisos y esquema esperados tras 0062 (BIDS 02 V.2; mismo contrato que
-- su bloque DO final, mas los efectivos por omision). Solo lectura: corre
-- como orbit_read en prod y en la base del ensayo. No falla cuando 0062
-- aun no aplica: lo que falta cuenta como diferencia (to_regclass y
-- pg_roles), nunca como error. Si todo cuadra imprime "permisos OK".
-- El DO fija lo que consumen (SELECT explicitos + INSERT de ingest +
-- ninguna escritura ajena); aqui ademas se verifica que el efectivo por
-- omision (decide) siga en su lugar. V.2 no crea vistas ni secuencias.
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        -- app_ingest escribe la tabla (rol del reporte de placements);
        -- nadie mas escribe.
        ('app_ingest', 'ads_placement_observation', 'INSERT', true),
        ('app_ingest', 'ads_placement_observation', 'UPDATE', false),
        ('app_ingest', 'ads_placement_observation', 'DELETE', false),
        ('app_read', 'ads_placement_observation', 'INSERT', false),
        ('app_read', 'ads_placement_observation', 'UPDATE', false),
        ('app_read', 'ads_placement_observation', 'DELETE', false),
        ('app_admin', 'ads_placement_observation', 'INSERT', false),
        ('app_admin', 'ads_placement_observation', 'UPDATE', false),
        ('app_admin', 'ads_placement_observation', 'DELETE', false),
        ('app_decide', 'ads_placement_observation', 'INSERT', false),
        ('app_decide', 'ads_placement_observation', 'UPDATE', false),
        ('app_decide', 'ads_placement_observation', 'DELETE', false),
        -- Lectura de la tabla: los cuatro la leen (ingest con GRANT
        -- explicito, decide por el privilegio por omision de 0001).
        ('app_read', 'ads_placement_observation', 'SELECT', true),
        ('app_admin', 'ads_placement_observation', 'SELECT', true),
        ('app_ingest', 'ads_placement_observation', 'SELECT', true),
        ('app_decide', 'ads_placement_observation', 'SELECT', true)
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
    SELECT 'tablas 0062: ' || count(*) || ' de 1'
      FROM pg_tables WHERE schemaname = 'public'
       AND tablename IN ('ads_placement_observation')
    HAVING count(*) <> 1
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
