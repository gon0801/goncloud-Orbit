-- jev-ads-03 D.3: reporte a 30 dias del bloqueo que el dueno aprueba. SOLO LECTURA.
-- Encendido: config_version 42 (2026-10-18). Programado con `at` en goncloud.
-- Uso (desde el repo):
--   ssh gonserver 'docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1' \
--     < docs/evidencia/jev-ads-03/ejecucion/D.3/reporte-30d.sql
-- Criterio del plan: si MX ahorra menos de 167 MXN al mes, el lead propone
-- apagar jev.bloqueo al dueno.
BEGIN READ ONLY;

\echo '(1) veredictos por plataforma y tipo'
SELECT plataforma, tipo, count(*)
  FROM bloqueo_veredicto
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '(2) porcentaje aprobado de lo decidido (aprobado / (aprobado + rechazado))'
SELECT plataforma,
       count(*) FILTER (WHERE tipo = 'aprobado') AS aprobados,
       count(*) FILTER (WHERE tipo = 'rechazado') AS rechazados,
       round(100.0 * count(*) FILTER (WHERE tipo = 'aprobado')
             / nullif(count(*) FILTER (WHERE tipo IN ('aprobado', 'rechazado')), 0), 1) AS pct_aprobado
  FROM bloqueo_veredicto
 GROUP BY 1
 ORDER BY 1;

\echo '(3) aprobados por ultimo paso de efecto'
SELECT v.plataforma, coalesce(u.paso, 'sin_paso') AS ultimo_paso, count(*)
  FROM bloqueo_veredicto v
  LEFT JOIN LATERAL (
        SELECT paso FROM bloqueo_efecto f
         WHERE f.veredicto_id = v.id
         ORDER BY f.created_at DESC, f.id DESC LIMIT 1) u ON true
 WHERE v.tipo = 'aprobado'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '(4) gasto de la ventana de las busquedas bloqueadas (aprobados aplicados, sin quitar)'
WITH bloqueado AS (
    SELECT v.id, v.plataforma, v.ad_group_id, v.termino, s.gasto, s.moneda,
           s.ventana_inicio, s.ventana_fin, a.aplicado_en
      FROM bloqueo_veredicto v
      JOIN jev_senal s ON s.id = v.senal_id
      JOIN LATERAL (
            SELECT min(created_at) AS aplicado_en FROM bloqueo_efecto f
             WHERE f.veredicto_id = v.id AND f.paso = 'aplicado') a ON a.aplicado_en IS NOT NULL
     WHERE v.tipo = 'aprobado'
       AND NOT EXISTS (SELECT 1 FROM bloqueo_veredicto q
                        WHERE q.tipo = 'quitado' AND q.origen_id = v.id))
SELECT plataforma, moneda, count(*) AS bloqueados,
       count(*) FILTER (WHERE gasto IS NULL) AS sin_gasto,
       sum(gasto) AS gasto_ventana,
       min(ventana_inicio) AS desde, max(ventana_fin) AS hasta,
       round(sum(gasto * 30.0 / nullif(ventana_fin - ventana_inicio + 1, 0)), 2) AS gasto_30d_equivalente
  FROM bloqueado
 GROUP BY 1, 2
 ORDER BY 1;

\echo '(5) despues de aplicar (UTC): la busqueda bloqueada | el resto del grupo; filas_sin_dato = metrica NULL'
WITH bloqueado AS (
    SELECT v.id, v.plataforma, v.ad_group_id, lower(v.termino) AS termino,
           (a.aplicado_en AT TIME ZONE 'UTC')::date AS dia
      FROM bloqueo_veredicto v
      JOIN LATERAL (
            SELECT min(created_at) AS aplicado_en FROM bloqueo_efecto f
             WHERE f.veredicto_id = v.id AND f.paso = 'aplicado') a ON a.aplicado_en IS NOT NULL
     WHERE v.tipo = 'aprobado'
       AND NOT EXISTS (SELECT 1 FROM bloqueo_veredicto q
                        WHERE q.tipo = 'quitado' AND q.origen_id = v.id)),
grupo AS (
    SELECT plataforma, ad_group_id, min(dia) AS desde
      FROM bloqueado GROUP BY 1, 2),
ultima AS (
    SELECT DISTINCT ON (o.platform, o.ad_entity_id, o.search_term, o.metric_date)
           o.ad_entity_id, lower(o.search_term) AS termino, o.metric_date,
           o.cost, o.orders, o.ad_revenue
      FROM search_term_observation o
     WHERE o.ad_entity_id IN (SELECT ad_group_id FROM grupo)
     ORDER BY o.platform, o.ad_entity_id, o.search_term, o.metric_date,
              o.observed_at DESC, o.source_report_id DESC NULLS LAST),
fila AS (
    SELECT g.plataforma, g.ad_group_id, u.*,
           EXISTS (SELECT 1 FROM bloqueado b
                    WHERE b.ad_group_id = u.ad_entity_id AND b.termino = u.termino
                      AND u.metric_date > b.dia) AS bloqueada
      FROM grupo g
      JOIN ultima u ON u.ad_entity_id = g.ad_group_id AND u.metric_date > g.desde)
SELECT g.plataforma,
       count(DISTINCT g.ad_group_id) AS grupos,
       sum(f.cost) FILTER (WHERE f.bloqueada) AS gasto_busqueda_bloqueada,
       sum(f.orders) FILTER (WHERE NOT f.bloqueada) AS ordenes_grupo,
       sum(f.ad_revenue) FILTER (WHERE NOT f.bloqueada) AS ventas_grupo,
       count(f.ad_entity_id) FILTER (WHERE f.cost IS NULL OR f.orders IS NULL OR f.ad_revenue IS NULL) AS filas_sin_dato,
       max(f.metric_date) AS datos_hasta
  FROM grupo g
  LEFT JOIN fila f ON f.ad_group_id = g.ad_group_id
 GROUP BY 1
 ORDER BY 1;

ROLLBACK;
