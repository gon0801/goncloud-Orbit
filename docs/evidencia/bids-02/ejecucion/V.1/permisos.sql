-- Permisos y esquema esperados tras 0061 (BIDS 02 V.1; mismo contrato que
-- su bloque DO final, mas los efectivos por omision). Solo lectura: corre
-- como orbit_read en prod y en la base del ensayo. No falla cuando 0061
-- aun no aplica: lo que falta cuenta como diferencia (to_regclass y
-- pg_roles), nunca como error. Si todo cuadra imprime "permisos OK".
-- El DO fija lo que consumen (SELECT explicitos + INSERT de ingest +
-- ninguna escritura ajena); aqui ademas se verifica que los efectivos por
-- omision (decide, SELECT de la vista) sigan en su lugar.
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        -- app_ingest escribe la tabla (rol del sync); nadie mas escribe.
        ('app_ingest', 'ads_campana_config_observation', 'INSERT', true),
        ('app_ingest', 'ads_campana_config_observation', 'UPDATE', false),
        ('app_ingest', 'ads_campana_config_observation', 'DELETE', false),
        ('app_read', 'ads_campana_config_observation', 'INSERT', false),
        ('app_read', 'ads_campana_config_observation', 'UPDATE', false),
        ('app_read', 'ads_campana_config_observation', 'DELETE', false),
        ('app_admin', 'ads_campana_config_observation', 'INSERT', false),
        ('app_admin', 'ads_campana_config_observation', 'UPDATE', false),
        ('app_admin', 'ads_campana_config_observation', 'DELETE', false),
        ('app_decide', 'ads_campana_config_observation', 'INSERT', false),
        ('app_decide', 'ads_campana_config_observation', 'UPDATE', false),
        ('app_decide', 'ads_campana_config_observation', 'DELETE', false),
        -- Lectura de la tabla: los cuatro la leen (ingest con GRANT
        -- explicito, decide por el privilegio por omision de 0001;
        -- guarda_config lee la vigente con la conexion del sync).
        ('app_read', 'ads_campana_config_observation', 'SELECT', true),
        ('app_admin', 'ads_campana_config_observation', 'SELECT', true),
        ('app_ingest', 'ads_campana_config_observation', 'SELECT', true),
        ('app_decide', 'ads_campana_config_observation', 'SELECT', true),
        -- La vista: la leen los cuatro; nadie la escribe.
        ('app_read', 'v_campana_config_vigente', 'SELECT', true),
        ('app_admin', 'v_campana_config_vigente', 'SELECT', true),
        ('app_ingest', 'v_campana_config_vigente', 'SELECT', true),
        ('app_decide', 'v_campana_config_vigente', 'SELECT', true),
        ('app_ingest', 'v_campana_config_vigente', 'INSERT', false),
        ('app_ingest', 'v_campana_config_vigente', 'UPDATE', false),
        ('app_ingest', 'v_campana_config_vigente', 'DELETE', false),
        ('app_read', 'v_campana_config_vigente', 'INSERT', false),
        ('app_read', 'v_campana_config_vigente', 'UPDATE', false),
        ('app_read', 'v_campana_config_vigente', 'DELETE', false),
        ('app_admin', 'v_campana_config_vigente', 'INSERT', false),
        ('app_admin', 'v_campana_config_vigente', 'UPDATE', false),
        ('app_admin', 'v_campana_config_vigente', 'DELETE', false),
        ('app_decide', 'v_campana_config_vigente', 'INSERT', false),
        ('app_decide', 'v_campana_config_vigente', 'UPDATE', false),
        ('app_decide', 'v_campana_config_vigente', 'DELETE', false)
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
    SELECT 'tablas 0061: ' || count(*) || ' de 1'
      FROM pg_tables WHERE schemaname = 'public'
       AND tablename IN ('ads_campana_config_observation')
    HAVING count(*) <> 1
    UNION ALL
    SELECT 'vistas 0061: ' || count(*) || ' de 1'
      FROM pg_views WHERE schemaname = 'public'
       AND viewname IN ('v_campana_config_vigente')
    HAVING count(*) <> 1
    UNION ALL
    SELECT 'secuencia 0061: falta o app_ingest sin USAGE/SELECT'
      WHERE to_regclass('public.ads_campana_config_observation_id_seq') IS NULL
         OR NOT has_sequence_privilege(
          'app_ingest', 'ads_campana_config_observation_id_seq', 'USAGE')
         OR NOT has_sequence_privilege(
          'app_ingest', 'ads_campana_config_observation_id_seq', 'SELECT')
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
