-- sonda-aplicada: SELECT to_regclass('public.v_ad_group_impulso_abierto') IS NOT NULL AND to_regclass('public.v_anuncio_regresado') IS NOT NULL AND to_regprocedure('public.cierra_retiro_reactivado(bigint)') IS NOT NULL
-- 0079 (AUTO-03 H.1 y H.7): lo que el ciclo necesita leer para proponer la
-- pausa por producto, sin abrirle impulso ni el ledger de retiro.
--
-- H.1: v_ad_group_impulso_abierto, los ad groups de impulsos sin lectura
-- final (sin go_literal). Es el unico lugar de "impulso abierto": la usan
-- retiro_anuncios._SQL_ANUNCIOS y app/productos_io.py. app_decide sigue sin
-- SELECT en impulso e impulso_lectura (la vista corre con los permisos de su
-- dueno). El COMMENT de apply_queue registra el perimetro ampliado (S9).
--
-- H.7 (S17): v_anuncio_regresado, los product ads que regresaste (su ultimo
-- retiro tiene reposicion) y los que reactivaste a mano en Amazon (su retiro
-- de modo pausa sigue abierto, ad_entity_state los lee ENABLED con un sync
-- posterior al retiro y su ad group no esta en un impulso abierto;
-- regresado_el NULL). cierra_retiro_reactivado cierra el retiro de uno de
-- esos con actor reactivacion_manual_amazon, bajo la misma llave
-- retiro-ad:<id> del retiro y la reposicion; el ciclo la llama en TX3 como
-- app_decide sin abrirle anuncio_retiro* ni anuncio_reposicion.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0079_reversa_auto03_vista_impulso.sql.

CREATE VIEW v_ad_group_impulso_abierto AS
SELECT i.platform, r.ad_group_ad_entity_id, i.product_id
  FROM impulso i
  JOIN campana_grupo_rol r ON r.grupo_id = i.grupo_id
 WHERE NOT EXISTS (
       SELECT 1 FROM impulso_lectura l WHERE l.impulso_id = i.id AND l.es_final);

COMMENT ON VIEW v_ad_group_impulso_abierto IS
    'AUTO-03 H.1: ad groups de impulsos sin lectura final y el producto del '
    'impulso. Unico lugar de "impulso abierto" (retiro_anuncios y la pausa por '
    'producto). Sin go_literal: el ciclo la lee sin SELECT en impulso.';

CREATE VIEW v_anuncio_regresado AS
WITH ultimo AS (
    SELECT DISTINCT ON (r.ad_entity_id) r.id, r.platform, r.ad_entity_id, r.modo, r.retirado_el
      FROM anuncio_retiro r
     ORDER BY r.ad_entity_id, r.retirado_el DESC, r.id DESC
)
SELECT u.platform, u.ad_entity_id, u.id AS retiro_id, p.repuesto_el AS regresado_el
  FROM ultimo u
  JOIN anuncio_reposicion p ON p.retiro_id = u.id
UNION ALL
SELECT u.platform, u.ad_entity_id, u.id AS retiro_id, NULL::timestamptz AS regresado_el
  FROM ultimo u
  JOIN ad_entity pa ON pa.id = u.ad_entity_id
  JOIN ad_entity_state s ON s.ad_entity_id = u.ad_entity_id
 WHERE u.modo = 'pausa'
   AND s.status = 'ENABLED'
   AND s.synced_at > u.retirado_el
   AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = u.id)
   AND NOT EXISTS (
       SELECT 1 FROM v_ad_group_impulso_abierto v WHERE v.ad_group_ad_entity_id = pa.parent_id);

COMMENT ON VIEW v_anuncio_regresado IS
    'AUTO-03 H.7 (S17): product ads regresados (regresado_el = repuesto_el de '
    'la reposicion de su ultimo retiro) o reactivados a mano en Amazon '
    '(regresado_el NULL: retiro de pausa abierto, ENABLED con sync posterior '
    'al retiro, fuera de impulso abierto). Un estado anterior al retiro o un '
    'retiro de archivo no cuentan. La pausa por producto no los propone en 7 '
    'dias desde regresado_el.';

CREATE FUNCTION public.cierra_retiro_reactivado(p_retiro_id bigint) RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_ad_entity_id bigint;
    v_ad_id_externo text;
    v_repuesto timestamptz;
BEGIN
    SELECT r.ad_entity_id, r.ad_id_externo INTO v_ad_entity_id, v_ad_id_externo
      FROM public.anuncio_retiro r WHERE r.id = p_retiro_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    IF NOT pg_catalog.pg_try_advisory_xact_lock(
           pg_catalog.hashtext('retiro-ad:' || v_ad_entity_id::text)) THEN
        RETURN NULL;
    END IF;
    SELECT p.repuesto_el INTO v_repuesto
      FROM public.anuncio_reposicion p WHERE p.retiro_id = p_retiro_id;
    IF FOUND THEN
        RETURN v_repuesto;
    END IF;
    IF NOT EXISTS (
           SELECT 1 FROM public.v_anuncio_regresado v
            WHERE v.retiro_id = p_retiro_id AND v.regresado_el IS NULL) THEN
        RETURN NULL;
    END IF;
    INSERT INTO public.anuncio_reposicion (retiro_id, ad_id_nuevo, estado_nuevo, repuesto_el, actor)
    VALUES (p_retiro_id, v_ad_id_externo, 'ENABLED', now(), 'reactivacion_manual_amazon')
    RETURNING repuesto_el INTO v_repuesto;
    RETURN v_repuesto;
END;
$$;

ALTER FUNCTION public.cierra_retiro_reactivado(bigint) OWNER TO app_admin;

COMMENT ON FUNCTION public.cierra_retiro_reactivado(bigint) IS
    'AUTO-03 H.7: cierra el retiro abierto de un product ad reactivado a mano en '
    'Amazon (anuncio_reposicion con actor reactivacion_manual_amazon) si sigue '
    'cumpliendo los predicados de v_anuncio_regresado. Toma sin esperar la llave '
    'retiro-ad:<id> del retiro y la reposicion: ocupada devuelve NULL sin '
    'escribir. Un retiro ya cerrado devuelve su repuesto_el. Nunca lanza por '
    'estado, asi que no aborta la TX3 del ciclo. SECURITY DEFINER de app_admin: '
    'app_decide la ejecuta sin privilegios sobre el ledger de retiro.';

REVOKE EXECUTE ON FUNCTION public.cierra_retiro_reactivado(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.cierra_retiro_reactivado(bigint) TO app_decide;

GRANT SELECT ON v_ad_group_impulso_abierto TO app_decide, app_read, app_admin;
GRANT SELECT ON v_anuncio_regresado TO app_decide, app_read, app_admin;

COMMENT ON TABLE apply_queue IS
  'Los CORTES (pause/negative/harvest) esperan aquí su ventana de veto de 48h '
  '(docs/APPLY.md §1): default al vencer = APLICAR; el silencio del dueño no '
  'bloquea, el veto explícito sí (30d durables por clave de efecto). La fila '
  'nace SIEMPRE pending_veto por trigger (un INSERT en otro estado saltaría '
  'la ventana) y las transiciones viven en la tabla EXACTA del brief §1.2 '
  'sellada por trigger de UPDATE: los terminales son inmutables y NO existe '
  'applying -> discarded (la quota no se quema en descartes; r3 qwen). La '
  'cola la ENCOLA LA APP (no un trigger sobre decision: el encolado necesita '
  'el modo efectivo por decisión), con modo shadow marcado para que el dueño '
  'practique el veto con candidatos reales. kind es dato auditable, NO parte '
  'de la clave de efecto: la clave es (platform, ad_entity_id, familia, '
  'search_term) con familia derivada del kind — negative y harvest del mismo '
  'término chocan (decision ya los trata como excluyentes por término). '
  'Perímetro ampliado por el dueño el 2026-11-05 (AUTO-03, S9): pause sobre '
  'product_ad, la pausa por producto sin ventas; su fila es shadow mientras '
  'ads_producto_pausa_<plataforma> no esté en live.';

DO $$
BEGIN
    IF has_table_privilege('app_decide', 'impulso', 'SELECT')
       OR has_table_privilege('app_decide', 'impulso_lectura', 'SELECT')
       OR has_table_privilege('app_decide', 'anuncio_retiro', 'SELECT')
       OR has_table_privilege('app_decide', 'anuncio_retiro_intencion', 'SELECT')
       OR has_table_privilege('app_decide', 'anuncio_reposicion', 'SELECT')
       OR has_table_privilege('app_decide', 'anuncio_reposicion', 'INSERT')
       OR NOT has_table_privilege('app_decide', 'v_ad_group_impulso_abierto', 'SELECT')
       OR NOT has_table_privilege('app_read', 'v_ad_group_impulso_abierto', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'v_ad_group_impulso_abierto', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'v_anuncio_regresado', 'SELECT')
       OR NOT has_table_privilege('app_read', 'v_anuncio_regresado', 'SELECT')
       OR NOT has_function_privilege('app_decide', 'public.cierra_retiro_reactivado(bigint)', 'EXECUTE')
       OR has_function_privilege('app_read', 'public.cierra_retiro_reactivado(bigint)', 'EXECUTE')
       OR has_function_privilege('app_ingest', 'public.cierra_retiro_reactivado(bigint)', 'EXECUTE')
       OR (SELECT pg_get_userbyid(proowner) FROM pg_proc
            WHERE oid = 'public.cierra_retiro_reactivado(bigint)'::regprocedure) <> 'app_admin'
    THEN
        RAISE EXCEPTION '0079: privilegios de las vistas, la funcion o el ledger de retiro distintos de los declarados';
    END IF;
END $$;
