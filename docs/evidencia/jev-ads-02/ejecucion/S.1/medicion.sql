-- Medicion S.1 ("Mide" de plans/jev-ads-02-bloque-s.md). SOLO LECTURA: corre
-- como orbit_read; las unicas escrituras son TEMPORALES de la sesion.
-- Por plataforma dice: si los totales declarados igualan a los recibidos,
-- cuantos anuncios no archivados se descartaron, y cuantos grupos con gasto
-- de busquedas cumplen las condiciones de roster del diseno (nombres de
-- MotivoSinProbar del bosquejo). El dia del diseno, en lo que se podia medir
-- sin el acta, cumplian 22 en MX y 11 en US.
-- Antes del despliegue no hay acta: reporta "tablas ausentes" y la linea base
-- medible sin acta (motivos de base + plataforma_no_listada).
-- Ventana de gasto: la de las consultas del diseno (D-39 a D-10, no ASIN).
-- Uso (desde la raiz del repo, sin secretos en la linea):
--   ssh goncloud 'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -q -v ON_ERROR_STOP=1' < docs/evidencia/jev-ads-02/ejecucion/S.1/medicion.sql

CREATE TEMP TABLE med_corrida AS
SELECT id, started_at, finished_at
  FROM ingest_run
 WHERE source = 'amazon_ads_structure_v2' AND ok
 ORDER BY id DESC LIMIT 1;

-- Acta de esa corrida, o cascarones vacios si 0051 aun no esta: asi el resto
-- del archivo corre igual pre y post despliegue.
DO $$
BEGIN
    IF to_regclass('public.ads_listado_plataforma') IS NOT NULL
       AND to_regclass('public.ads_listado_grupo') IS NOT NULL THEN
        EXECUTE 'CREATE TEMP TABLE med_plat AS SELECT * FROM ads_listado_plataforma WHERE ingest_run_id = (SELECT id FROM med_corrida)';
        EXECUTE 'CREATE TEMP TABLE med_grupo AS SELECT * FROM ads_listado_grupo WHERE ingest_run_id = (SELECT id FROM med_corrida)';
    ELSE
        CREATE TEMP TABLE med_plat (
            ingest_run_id BIGINT, platform platform,
            ad_groups_recibidos INTEGER, ad_groups_declarados INTEGER,
            product_ads_recibidos INTEGER, product_ads_declarados INTEGER,
            product_ads_sin_grupo INTEGER);
        CREATE TEMP TABLE med_grupo (
            ingest_run_id BIGINT, platform platform, ad_group_id BIGINT,
            anuncios_vivos INTEGER, huella_vivos TEXT, descartados INTEGER);
    END IF;
END
$$;

\echo == M1 ultima corrida ok de estructura y estado del acta
SELECT id AS corrida, started_at::timestamp(0) AS inicio,
       finished_at::timestamp(0) AS fin,
       round((extract(epoch FROM (now() - finished_at)) / 3600)::numeric, 1) AS edad_horas,
       (SELECT count(*) FROM med_plat) AS acta_plataforma,
       (SELECT count(*) FROM med_grupo) AS acta_grupos
  FROM med_corrida;
SELECT CASE WHEN to_regclass('public.ads_listado_plataforma') IS NULL
                 OR to_regclass('public.ads_listado_grupo') IS NULL
            THEN 'ACTA: tablas ausentes (pre-0051)'
            WHEN (SELECT count(*) FROM med_plat) = 0
            THEN 'ACTA: la ultima corrida ok no dejo filas (perfiles rechazados?)'
            ELSE 'ACTA: presente' END AS estado_acta;

\echo == M2 por plataforma: declarados contra recibidos, no archivados descartados
SELECT v.platform,
       p.ad_groups_declarados, p.ad_groups_recibidos,
       CASE WHEN p.ad_groups_recibidos IS NULL THEN NULL
            ELSE p.ad_groups_declarados IS NOT NULL AND p.ad_groups_declarados = p.ad_groups_recibidos END AS grupos_cuadran,
       p.product_ads_declarados, p.product_ads_recibidos,
       CASE WHEN p.product_ads_recibidos IS NULL THEN NULL
            ELSE p.product_ads_declarados IS NOT NULL AND p.product_ads_declarados = p.product_ads_recibidos END AS anuncios_cuadran,
       p.product_ads_sin_grupo AS sin_grupo,
       coalesce(d.descartados, 0) AS descartados_grupo,
       coalesce(p.product_ads_sin_grupo, 0) + coalesce(d.descartados, 0) AS no_archivados_descartados
  FROM (VALUES ('amazon_mx'::platform), ('amazon_us'::platform)) AS v(platform)
  LEFT JOIN med_plat p ON p.platform = v.platform
  LEFT JOIN (SELECT platform, sum(descartados) AS descartados FROM med_grupo GROUP BY 1) AS d
    ON d.platform = v.platform
 ORDER BY v.platform;

-- Grupos con gasto de busquedas (misma ventana y dedupe que
-- diseno/consultas/grupos-con-gasto.sql) y su estado en base.
CREATE TEMP TABLE med_gasto AS
WITH ult AS (
  SELECT DISTINCT ON (platform, ad_entity_id, search_term, metric_date)
         platform, ad_entity_id, cost
    FROM search_term_observation
   WHERE metric_date BETWEEN current_date - 39 AND current_date - 10 AND NOT is_asin_like
   ORDER BY platform, ad_entity_id, search_term, metric_date, observed_at DESC)
SELECT platform, ad_entity_id AS grupo, sum(cost) AS c
  FROM ult GROUP BY 1, 2 HAVING sum(cost) > 0;

CREATE TEMP TABLE med_base AS
SELECT pa.platform, pa.parent_id AS grupo,
       count(*) FILTER (WHERE s.status IN ('ENABLED', 'PAUSED')) AS vivos,
       count(*) FILTER (WHERE s.status IS NULL) AS sin_estado,
       count(*) FILTER (WHERE s.status IN ('ENABLED', 'PAUSED') AND l.product_id IS NULL) AS vivos_sin_producto
  FROM ad_entity pa
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = pa.id
  LEFT JOIN listing l ON l.id = pa.listing_id AND l.platform = pa.platform
 WHERE pa.kind = 'product_ad'
 GROUP BY 1, 2;

-- Huella de los vivos en base, con la misma receta que huella_anuncios:
-- sha256 hex de los external_id ordenados por byte (COLLATE "C") unidos con
-- salto de linea. builtin sha256(bytea), sin pgcrypto.
CREATE TEMP TABLE med_huella AS
SELECT pa.platform, pa.parent_id AS grupo,
       encode(sha256(convert_to(string_agg(pa.external_id, chr(10) ORDER BY pa.external_id COLLATE "C"), 'UTF8')), 'hex') AS h
  FROM ad_entity pa JOIN ad_entity_state s ON s.ad_entity_id = pa.id
 WHERE pa.kind = 'product_ad' AND s.status IN ('ENABLED', 'PAUSED')
 GROUP BY 1, 2;

CREATE TEMP TABLE med_eval AS
SELECT z.*, array_remove(ARRAY[
    CASE WHEN z.acta_fin IS NULL OR now() - z.acta_fin > interval '48 hours' THEN 'sin_corrida_reciente' END,
    CASE WHEN z.ad_groups_recibidos IS NULL THEN 'plataforma_no_listada' END,
    CASE WHEN z.ad_groups_recibidos IS NOT NULL AND (z.ad_groups_declarados IS NULL OR z.product_ads_declarados IS NULL) THEN 'listado_sin_total' END,
    CASE WHEN z.ad_groups_declarados IS NOT NULL AND z.product_ads_declarados IS NOT NULL
              AND (z.ad_groups_declarados <> z.ad_groups_recibidos OR z.product_ads_declarados <> z.product_ads_recibidos) THEN 'listado_no_cuadra' END,
    CASE WHEN coalesce(z.product_ads_sin_grupo, 0) > 0 THEN 'anuncios_sin_grupo' END,
    CASE WHEN z.ad_groups_recibidos IS NOT NULL AND z.anuncios_vivos IS NULL THEN 'grupo_no_listado' END,
    CASE WHEN coalesce(z.descartados, 0) > 0 THEN 'anuncios_descartados' END,
    CASE WHEN z.huella_vivos IS NOT NULL AND z.huella_vivos <> z.huella_base THEN 'huella_distinta' END,
    CASE WHEN z.sin_estado > 0 THEN 'estado_ausente' END,
    CASE WHEN z.vivos_sin_producto > 0 THEN 'anuncio_sin_producto' END
  ], NULL) AS motivos
  FROM (SELECT g.platform, g.grupo, g.c AS gasto,
               p.ad_groups_recibidos, p.ad_groups_declarados,
               p.product_ads_recibidos, p.product_ads_declarados,
               p.product_ads_sin_grupo,
               gr.anuncios_vivos, gr.huella_vivos, gr.descartados,
               coalesce(b.vivos, 0) AS vivos_base,
               coalesce(b.sin_estado, 0) AS sin_estado,
               coalesce(b.vivos_sin_producto, 0) AS vivos_sin_producto,
               coalesce(h.h, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855') AS huella_base,
               (SELECT finished_at FROM med_corrida) AS acta_fin
          FROM med_gasto g
          LEFT JOIN med_plat p ON p.platform = g.platform
          LEFT JOIN med_grupo gr ON gr.platform = g.platform AND gr.ad_group_id = g.grupo
          LEFT JOIN med_base b ON b.platform = g.platform AND b.grupo = g.grupo
          LEFT JOIN med_huella h ON h.platform = g.platform AND h.grupo = g.grupo) AS z;

\echo == M3 detalle por grupo con gasto: acta, base y motivos
SELECT platform, grupo, round(gasto, 0) AS gasto,
       anuncios_vivos AS acta_vivos, vivos_base,
       descartados AS acta_descartados,
       CASE WHEN huella_vivos IS NULL THEN NULL
            ELSE huella_vivos IS NOT DISTINCT FROM huella_base END AS huella_igual,
       motivos, (motivos = '{}') AS roster_probado
  FROM med_eval
 ORDER BY platform, gasto DESC;

\echo == M4 resumen por plataforma: grupos con gasto que cumplen el roster
SELECT platform,
       count(*) AS grupos_con_gasto,
       round(sum(gasto), 0) AS gasto_total,
       count(*) FILTER (WHERE motivos = '{}') AS roster_probado,
       count(*) FILTER (WHERE motivos = ARRAY['plataforma_no_listada']) AS limpios_salvo_acta
  FROM med_eval GROUP BY platform ORDER BY platform;

\echo == M4b motivos por plataforma (un grupo cuenta en cada motivo que tiene)
SELECT platform, motivo, count(*) AS grupos
  FROM med_eval, unnest(motivos) AS motivo
 GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2;

SELECT 'MEDICION OK' AS medicion;
