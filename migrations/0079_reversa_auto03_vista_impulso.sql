-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0079 (AUTO-03 H.1 y H.7).
--
-- Quita cierra_retiro_reactivado, v_anuncio_regresado y
-- v_ad_group_impulso_abierto, y devuelve el COMMENT de apply_queue al de la
-- 0002. No borra datos: las reposiciones con actor reactivacion_manual_amazon
-- que la funcion ya escribio son retiros cerrados validos y se quedan. El
-- codigo anterior no lee las vistas.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

DROP FUNCTION public.cierra_retiro_reactivado(bigint);
DROP VIEW v_anuncio_regresado;
DROP VIEW v_ad_group_impulso_abierto;

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
  'término chocan (decision ya los trata como excluyentes por término).';
