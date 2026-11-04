-- B1-r1 0.2: SELECTs de solo lectura por mercado (MX/US).
-- Agregados unicamente: sin terminos, SKUs, ASINs, nombres ni textos.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL ROLE app_read;

\echo == 0. Identidad de la sesion
SELECT current_user AS rol, current_database() AS base, pg_is_in_recovery() AS en_recovery;

\echo == 1. Roles vigentes (LOGIN orbit_* y grupos app_*)
SELECT r.rolname AS login, r.rolcanlogin AS puede_login, g.rolname AS grupo
FROM pg_auth_members m
JOIN pg_roles r ON r.oid = m.member
JOIN pg_roles g ON g.oid = m.roleid
WHERE r.rolname LIKE 'orbit%' OR g.rolname LIKE 'app_%'
ORDER BY 1, 3;
SELECT rolname, rolcanlogin FROM pg_roles
WHERE rolname LIKE 'app_%' OR rolname LIKE 'orbit%'
ORDER BY 1;

\echo == 2. Fichas Jev: tablas esperadas (NULL = no existen aun)
SELECT to_regclass('public.jev_ficha_version') AS jev_ficha_version,
       to_regclass('public.jev_ficha_revocacion') AS jev_ficha_revocacion,
       to_regclass('public.jev_revision') AS jev_revision,
       to_regclass('public.jev_par_evento') AS jev_par_evento;

\echo == 3. Roster de grupos Amazon: exhaustividad declarada
SELECT platform, 'desconocido' AS roster_exhaustivo
FROM (SELECT DISTINCT platform FROM ad_entity WHERE kind = 'ad_group') p
WHERE platform IN ('amazon_mx', 'amazon_us');

\echo == 4. Censo de grupos por mercado (agregados; estados ausentes preservados)
WITH por_grupo AS (
  SELECT g.platform, g.id,
    count(a.id) AS anuncios_conocidos,
    count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS anuncios_ep,
    count(a.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED') AND l.id IS NULL) AS ep_sin_listing,
    count(DISTINCT l.id) FILTER (WHERE s.status IN ('ENABLED','PAUSED')) AS publicaciones_ep,
    count(a.id) FILTER (WHERE s.ad_entity_id IS NULL OR s.status IS NULL) AS sin_estado
  FROM ad_entity g
  LEFT JOIN ad_entity a ON a.parent_id = g.id AND a.platform = g.platform AND a.kind = 'product_ad'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = a.id
  LEFT JOIN listing l ON l.id = a.listing_id AND l.platform = a.platform
  WHERE g.kind = 'ad_group'
  GROUP BY g.platform, g.id
)
SELECT platform, count(*) AS grupos_conocidos,
  count(*) FILTER (WHERE anuncios_conocidos = 0) AS grupos_sin_anuncios_observados,
  count(*) FILTER (WHERE anuncios_ep > 0) AS grupos_con_ep,
  count(*) FILTER (WHERE anuncios_ep > 0 AND publicaciones_ep = 0) AS grupos_ep_sin_catalogo,
  count(*) FILTER (WHERE anuncios_ep > 0 AND ep_sin_listing > 0) AS grupos_ep_catalogo_incompleto,
  count(*) FILTER (WHERE sin_estado > 0) AS grupos_con_estado_ausente,
  sum(anuncios_ep) AS anuncios_ep, sum(ep_sin_listing) AS ep_sin_listing,
  sum(sin_estado) AS anuncios_sin_estado
FROM por_grupo WHERE platform IN ('amazon_mx', 'amazon_us')
GROUP BY platform ORDER BY platform;

\echo == 5. Casos etiquetables: decisiones negative/harvest por mercado (ultimos 30 dias)
SELECT e.platform, d.kind, count(*) AS decisiones,
  count(DISTINCT d.search_term) AS terminos_distintos,
  count(*) FILTER (WHERE d.window_end <= (d.decided_at - interval '10 days')::date) AS maduras_10d,
  min(d.decided_at)::date AS desde, max(d.decided_at)::date AS hasta
FROM decision d
JOIN ad_entity e ON e.id = d.ad_entity_id
WHERE d.kind IN ('negative', 'harvest')
  AND e.platform IN ('amazon_mx', 'amazon_us')
  AND d.decided_at >= now() - interval '30 days'
GROUP BY e.platform, d.kind ORDER BY e.platform, d.kind;

\echo == 6. Casos etiquetables: historico completo negative/harvest por mercado
SELECT e.platform, d.kind, count(*) AS decisiones,
  count(DISTINCT d.search_term) AS terminos_distintos,
  min(d.decided_at)::date AS desde, max(d.decided_at)::date AS hasta
FROM decision d
JOIN ad_entity e ON e.id = d.ad_entity_id
WHERE d.kind IN ('negative', 'harvest')
  AND e.platform IN ('amazon_mx', 'amazon_us')
GROUP BY e.platform, d.kind ORDER BY e.platform, d.kind;

\echo == 7. Semillas: bibliotecas por mercado (solo conteos)
SELECT 'keyword' AS biblioteca, platform, count(*) AS filas,
  count(DISTINCT tipo_producto) AS tipos_producto,
  min(first_seen_at)::date AS desde, max(first_seen_at)::date AS alta_mas_reciente
FROM keyword_biblioteca WHERE platform IN ('amazon_mx', 'amazon_us')
GROUP BY platform
UNION ALL
SELECT 'negative', platform, count(*),
  count(DISTINCT tipo_producto),
  min(first_seen_at)::date, max(first_seen_at)::date
FROM negative_biblioteca WHERE platform IN ('amazon_mx', 'amazon_us')
GROUP BY platform
ORDER BY 1, 2;

ROLLBACK;
