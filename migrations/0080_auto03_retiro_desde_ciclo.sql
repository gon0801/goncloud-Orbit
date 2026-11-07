-- sonda-aplicada: SELECT has_table_privilege('app_decide', 'public.anuncio_retiro', 'INSERT') AND has_column_privilege('app_decide', 'public.anuncio_reposicion', 'retiro_id', 'SELECT') AND (SELECT coalesce(array_to_string(proconfig, ','), '') LIKE '%search_path%' FROM pg_proc WHERE oid = 'public.anuncio_retiro_sin_abierto()'::regprocedure)
-- 0080 (AUTO-03 I.1, S10): el ciclo ejecuta la pausa por producto en TX4 como
-- app_decide (app/apply_producto.py -> retiro_anuncios.ejecuta_retiro), asi
-- que necesita el ledger de retiro con grants minimos:
--
-- - anuncio_retiro: SELECT e INSERT (sella el retiro tras el readback).
-- - anuncio_retiro_intencion: SELECT e INSERT (la intencion antes del PUT) y
--   UPDATE solo de cerrada_el, detalle y retiro_id (el cierre; el trigger
--   solo-cierre de 0069/0078 sigue mandando).
-- - USAGE de las dos secuencias.
-- - anuncio_reposicion: solo SELECT (retiro_id), lo que leen
--   retiro_anuncios._SQL_YA_RETIRADO y el trigger anuncio_retiro_sin_abierto
--   (sin SECURITY DEFINER: corre con el rol que inserta). Sin INSERT ni las
--   demas columnas: regresar un anuncio sigue siendo de un humano (app_admin)
--   o de cierra_retiro_reactivado (0079, SECURITY DEFINER).
-- - impulso e impulso_lectura siguen cerrados: el ciclo lee impulsos abiertos
--   solo por v_ad_group_impulso_abierto (0079).
--
-- Como app_decide gana INSERT en anuncio_retiro, anuncio_retiro_sin_abierto()
-- se redefine con search_path fijo y relaciones cualificadas: una tabla
-- temporal homonima vacia (pg_temp va primero en el search_path por omision)
-- ocultaba el retiro abierto y dejaba sellar un segundo. La reversa restaura
-- la definicion de 0066.
--
-- No se edita ninguna migracion aplicada: los DO-blocks de 0066 y 0069 solo
-- corrieron al aplicarlas.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). No re-runnable. Reversa:
-- 0080_reversa_auto03_retiro_desde_ciclo.sql.

GRANT SELECT, INSERT ON public.anuncio_retiro TO app_decide;
GRANT SELECT, INSERT ON public.anuncio_retiro_intencion TO app_decide;
GRANT UPDATE (cerrada_el, detalle, retiro_id) ON public.anuncio_retiro_intencion TO app_decide;
GRANT USAGE ON SEQUENCE public.anuncio_retiro_id_seq TO app_decide;
GRANT USAGE ON SEQUENCE public.anuncio_retiro_intencion_id_seq TO app_decide;
GRANT SELECT (retiro_id) ON public.anuncio_reposicion TO app_decide;

CREATE OR REPLACE FUNCTION public.anuncio_retiro_sin_abierto() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
    AS $$
BEGIN
    PERFORM 1 FROM public.anuncio_retiro r
     WHERE r.ad_entity_id = NEW.ad_entity_id
       AND NOT EXISTS (SELECT 1 FROM public.anuncio_reposicion p WHERE p.retiro_id = r.id);
    IF FOUND THEN
        RAISE EXCEPTION 'anuncio % ya tiene un retiro abierto', NEW.ad_entity_id;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.anuncio_retiro_sin_abierto() IS
    'BIDS 02 I.4 + AUTO-03 I.1: rechaza un retiro nuevo de un anuncio con un retiro '
    'sin reposicion. search_path fijo y relaciones cualificadas: app_decide inserta '
    '(0080) y una tabla temporal homonima no puede ocultar el retiro abierto.';

DO $$
BEGIN
    IF NOT has_table_privilege('app_decide', 'public.anuncio_retiro', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'public.anuncio_retiro', 'INSERT')
       OR has_table_privilege('app_decide', 'public.anuncio_retiro', 'UPDATE')
       OR has_table_privilege('app_decide', 'public.anuncio_retiro', 'DELETE')
       OR NOT has_table_privilege('app_decide', 'public.anuncio_retiro_intencion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'public.anuncio_retiro_intencion', 'INSERT')
       OR has_table_privilege('app_decide', 'public.anuncio_retiro_intencion', 'DELETE')
       OR NOT has_column_privilege('app_decide', 'public.anuncio_retiro_intencion', 'cerrada_el', 'UPDATE')
       OR NOT has_column_privilege('app_decide', 'public.anuncio_retiro_intencion', 'detalle', 'UPDATE')
       OR NOT has_column_privilege('app_decide', 'public.anuncio_retiro_intencion', 'retiro_id', 'UPDATE')
       OR has_column_privilege('app_decide', 'public.anuncio_retiro_intencion', 'decision_id', 'UPDATE')
       OR has_column_privilege('app_decide', 'public.anuncio_retiro_intencion', 'ad_entity_id', 'UPDATE')
       OR NOT has_sequence_privilege('app_decide', 'public.anuncio_retiro_id_seq', 'USAGE')
       OR NOT has_sequence_privilege('app_decide', 'public.anuncio_retiro_intencion_id_seq', 'USAGE')
       OR NOT has_column_privilege('app_decide', 'public.anuncio_reposicion', 'retiro_id', 'SELECT')
       OR has_table_privilege('app_decide', 'public.anuncio_reposicion', 'SELECT')
       OR has_table_privilege('app_decide', 'public.anuncio_reposicion', 'INSERT')
       OR has_column_privilege('app_decide', 'public.anuncio_reposicion', 'ad_id_nuevo', 'SELECT')
       OR has_column_privilege('app_decide', 'public.anuncio_reposicion', 'estado_nuevo', 'SELECT')
       OR has_column_privilege('app_decide', 'public.anuncio_reposicion', 'repuesto_el', 'SELECT')
       OR has_column_privilege('app_decide', 'public.anuncio_reposicion', 'actor', 'SELECT')
       OR has_table_privilege('app_decide', 'public.impulso', 'SELECT')
       OR has_table_privilege('app_decide', 'public.impulso_lectura', 'SELECT')
       OR has_table_privilege('app_decide', 'public.anuncio_recreacion_intento', 'SELECT')
    THEN
        RAISE EXCEPTION '0080: privilegios de app_decide sobre el ledger de retiro distintos de los declarados';
    END IF;
    IF NOT EXISTS (
           SELECT 1 FROM pg_proc
            WHERE oid = 'public.anuncio_retiro_sin_abierto()'::regprocedure
              AND proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']) THEN
        RAISE EXCEPTION '0080: anuncio_retiro_sin_abierto() sin search_path fijo';
    END IF;
END;
$$;
