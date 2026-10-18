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
       sum(gasto) AS gasto_ventana,
       min(ventana_inicio) AS desde, max(ventana_fin) AS hasta,
       round(sum(gasto * 30.0 / nullif(ventana_fin - ventana_inicio + 1, 0)), 2) AS gasto_30d_equivalente
  FROM bloqueado
 GROUP BY 1, 2
 ORDER BY 1;

\echo '(5) despues de aplicar: la busqueda bloqueada | el resto del grupo bloqueado'
WITH bloqueado AS (
    SELECT v.id, v.plataforma, v.ad_group_id, v.termino, a.aplicado_en
      FROM bloqueo_veredicto v
      JOIN LATERAL (
            SELECT min(created_at) AS aplicado_en FROM bloqueo_efecto f
             WHERE f.veredicto_id = v.id AND f.paso = 'aplicado') a ON a.aplicado_en IS NOT NULL
     WHERE v.tipo = 'aprobado'
       AND NOT EXISTS (SELECT 1 FROM bloqueo_veredicto q
                        WHERE q.tipo = 'quitado' AND q.origen_id = v.id)),
ultima AS (
    SELECT DISTINCT ON (o.platform, o.ad_entity_id, o.search_term, o.metric_date)
           o.platform, o.ad_entity_id, o.search_term, o.metric_date,
           o.cost, o.orders, o.ad_revenue
      FROM search_term_observation o
     WHERE o.ad_entity_id IN (SELECT ad_group_id FROM bloqueado)
     ORDER BY o.platform, o.ad_entity_id, o.search_term, o.metric_date, o.observed_at DESC),
grupo AS (
    SELECT DISTINCT b.plataforma, b.ad_group_id, min(b.aplicado_en) AS desde
      FROM bloqueado b GROUP BY 1, 2)
SELECT g.plataforma,
       count(DISTINCT g.ad_group_id) AS grupos,
       coalesce(sum(u.cost) FILTER (WHERE EXISTS (
           SELECT 1 FROM bloqueado b WHERE b.ad_group_id = g.ad_group_id
              AND lower(b.termino) = lower(u.search_term)
              AND u.metric_date > b.aplicado_en::date)), 0) AS gasto_busqueda_bloqueada,
       coalesce(sum(u.orders) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM bloqueado b WHERE b.ad_group_id = g.ad_group_id
              AND lower(b.termino) = lower(u.search_term))), 0) AS ordenes_grupo,
       coalesce(sum(u.ad_revenue) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM bloqueado b WHERE b.ad_group_id = g.ad_group_id
              AND lower(b.termino) = lower(u.search_term))), 0) AS ventas_grupo,
       max(u.metric_date) AS datos_hasta
  FROM grupo g
  LEFT JOIN ultima u ON u.ad_entity_id = g.ad_group_id AND u.metric_date > g.desde::date
 GROUP BY 1
 ORDER BY 1;

ROLLBACK;
