-- Permisos y esquema del acta de listado S.1 (0051: ads_listado_plataforma
-- y ads_listado_grupo). Solo lectura: corre como orbit_read en prod y en la
-- base del ensayo. Falla con la lista de diferencias; si todo cuadra imprime
-- "permisos OK". Antes del despliegue las tablas no existen y reporta
-- "tablas del acta ausentes" en vez de reventar con UndefinedTable.
WITH ok AS (
    SELECT to_regclass('public.ads_listado_plataforma') IS NOT NULL
       AND to_regclass('public.ads_listado_grupo') IS NOT NULL AS tablas
),
esperado(rol, tabla, privilegio, debe) AS (
    VALUES
        ('app_read', 'ads_listado_plataforma', 'SELECT', true),
        ('app_ingest', 'ads_listado_plataforma', 'SELECT', true),
        ('app_decide', 'ads_listado_plataforma', 'SELECT', true),
        ('app_admin', 'ads_listado_plataforma', 'SELECT', true),
        ('app_ingest', 'ads_listado_plataforma', 'INSERT', true),
        ('app_read', 'ads_listado_plataforma', 'INSERT', false),
        ('app_decide', 'ads_listado_plataforma', 'INSERT', false),
        ('app_admin', 'ads_listado_plataforma', 'INSERT', false),
        ('app_ingest', 'ads_listado_plataforma', 'UPDATE', false),
        ('app_ingest', 'ads_listado_plataforma', 'DELETE', false),
        ('app_read', 'ads_listado_grupo', 'SELECT', true),
        ('app_ingest', 'ads_listado_grupo', 'SELECT', true),
        ('app_decide', 'ads_listado_grupo', 'SELECT', true),
        ('app_admin', 'ads_listado_grupo', 'SELECT', true),
        ('app_ingest', 'ads_listado_grupo', 'INSERT', true),
        ('app_read', 'ads_listado_grupo', 'INSERT', false),
        ('app_decide', 'ads_listado_grupo', 'INSERT', false),
        ('app_admin', 'ads_listado_grupo', 'INSERT', false),
        ('app_ingest', 'ads_listado_grupo', 'UPDATE', false),
        ('app_ingest', 'ads_listado_grupo', 'DELETE', false)
),
real AS (
    SELECT rol, tabla, privilegio, debe,
           CASE WHEN (SELECT tablas FROM ok)
                THEN has_table_privilege(rol, 'public.' || tabla, privilegio)
                ELSE debe END AS tiene
      FROM esperado
),
diferencias AS (
    SELECT rol || ' ' || privilegio || ' ' || tabla || ': esperado ' || debe || ', real ' || tiene AS d
      FROM real WHERE tiene <> debe
    UNION ALL
    SELECT 'tablas del acta ausentes (pre-0051)' WHERE NOT (SELECT tablas FROM ok)
    UNION ALL
    SELECT 'triggers append-only del acta: ' || t.n || ' de 4'
      FROM (SELECT count(*) AS n FROM pg_trigger
             WHERE tgrelid IN (to_regclass('public.ads_listado_plataforma'),
                               to_regclass('public.ads_listado_grupo'))
               AND NOT tgisinternal) AS t
     WHERE (SELECT tablas FROM ok) AND t.n <> 4
    UNION ALL
    SELECT 'CHECK ausente: ' || c.falta
      FROM (VALUES ('ads_listado_plataforma_conteos_no_negativos'),
                   ('ads_listado_grupo_conteos_no_negativos'),
                   ('ads_listado_grupo_huella_formato')) AS c(falta)
     WHERE (SELECT tablas FROM ok)
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = c.falta)
)
SELECT CASE WHEN count(*) = 0 THEN 'permisos OK'
            ELSE 'PERMISOS FALLAN: ' || string_agg(d, '; ') END
  FROM diferencias;
