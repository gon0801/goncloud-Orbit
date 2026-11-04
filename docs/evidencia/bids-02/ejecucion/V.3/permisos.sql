-- Permisos y esquema esperados tras 0067 (BIDS 02 V.3; mismo contrato que
-- su bloque DO final, mas los efectivos por omision). Solo lectura: corre
-- como orbit_read en prod y en la base del ensayo. No falla cuando 0067
-- aun no aplica: lo que falta cuenta como diferencia (to_regclass y
-- pg_roles), nunca como error. Si todo cuadra imprime "permisos OK".
-- 0067 ademas otorga a app_admin INSERT sobre
-- ads_campana_config_observation (el readback confirmado del ajuste se
-- inserta con la conexion de escritura): se verifica aqui.
WITH esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        -- app_admin escribe campana_ajuste (rol de apply); nadie mas escribe.
        -- UPDATE de tabla en false: el unico UPDATE otorgado es por columna
        -- (confirmado_el), que has_table_privilege no cuenta.
        ('app_admin', 'campana_ajuste', 'INSERT', true),
        ('app_admin', 'campana_ajuste', 'UPDATE', false),
        ('app_admin', 'campana_ajuste', 'DELETE', false),
        ('app_read', 'campana_ajuste', 'INSERT', false),
        ('app_read', 'campana_ajuste', 'UPDATE', false),
        ('app_read', 'campana_ajuste', 'DELETE', false),
        ('app_ingest', 'campana_ajuste', 'INSERT', false),
        ('app_ingest', 'campana_ajuste', 'UPDATE', false),
        ('app_ingest', 'campana_ajuste', 'DELETE', false),
        ('app_decide', 'campana_ajuste', 'INSERT', false),
        ('app_decide', 'campana_ajuste', 'UPDATE', false),
        ('app_decide', 'campana_ajuste', 'DELETE', false),
        -- Lectura: los cuatro la leen (el default del esquema da SELECT a
        -- ingest, igual que la observacion de V.1; la pantalla y el plan
        -- leen ajustes con la conexion de lectura).
        ('app_read', 'campana_ajuste', 'SELECT', true),
        ('app_admin', 'campana_ajuste', 'SELECT', true),
        ('app_decide', 'campana_ajuste', 'SELECT', true),
        ('app_ingest', 'campana_ajuste', 'SELECT', true),
        -- El readback confirmado inserta la config con rol admin.
        ('app_admin', 'ads_campana_config_observation', 'INSERT', true)
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
    SELECT 'tablas 0067: ' || count(*) || ' de 1'
      FROM pg_tables WHERE schemaname = 'public'
       AND tablename IN ('campana_ajuste')
    HAVING count(*) <> 1
    UNION ALL
    SELECT 'secuencia 0067: falta o app_admin sin USAGE/SELECT'
      WHERE to_regclass('public.campana_ajuste_id_seq') IS NULL
         OR NOT has_sequence_privilege(
          'app_admin', 'campana_ajuste_id_seq', 'USAGE')
         OR NOT has_sequence_privilege(
          'app_admin', 'campana_ajuste_id_seq', 'SELECT')
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
