-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0080 (AUTO-03 I.1).
--
-- Devuelve anuncio_retiro_sin_abierto() a la definicion de 0066 (sin
-- search_path fijo, relaciones sin cualificar, sin COMMENT) y revoca de
-- app_decide lo que la 0080 le dio sobre el ledger de retiro. No borra datos:
-- los retiros y las intenciones que el ciclo ya sello son historia valida y
-- se quedan; el codigo anterior no ejecuta la pausa por producto.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). No re-runnable.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.anuncio_retiro_sin_abierto() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM anuncio_retiro r
     WHERE r.ad_entity_id = NEW.ad_entity_id
       AND NOT EXISTS (SELECT 1 FROM anuncio_reposicion p WHERE p.retiro_id = r.id);
    IF FOUND THEN
        RAISE EXCEPTION 'anuncio % ya tiene un retiro abierto', NEW.ad_entity_id;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.anuncio_retiro_sin_abierto() IS NULL;

REVOKE SELECT (retiro_id) ON public.anuncio_reposicion FROM app_decide;
REVOKE USAGE ON SEQUENCE public.anuncio_retiro_intencion_id_seq FROM app_decide;
REVOKE USAGE ON SEQUENCE public.anuncio_retiro_id_seq FROM app_decide;
REVOKE UPDATE (cerrada_el, detalle, retiro_id) ON public.anuncio_retiro_intencion FROM app_decide;
REVOKE SELECT, INSERT ON public.anuncio_retiro_intencion FROM app_decide;
REVOKE SELECT, INSERT ON public.anuncio_retiro FROM app_decide;

DO $$
BEGIN
    IF has_table_privilege('app_decide', 'public.anuncio_retiro', 'SELECT')
       OR has_table_privilege('app_decide', 'public.anuncio_retiro', 'INSERT')
       OR has_table_privilege('app_decide', 'public.anuncio_retiro_intencion', 'SELECT')
       OR has_table_privilege('app_decide', 'public.anuncio_retiro_intencion', 'INSERT')
       OR has_column_privilege('app_decide', 'public.anuncio_retiro_intencion', 'cerrada_el', 'UPDATE')
       OR has_column_privilege('app_decide', 'public.anuncio_reposicion', 'retiro_id', 'SELECT')
       OR has_sequence_privilege('app_decide', 'public.anuncio_retiro_id_seq', 'USAGE')
       OR has_sequence_privilege('app_decide', 'public.anuncio_retiro_intencion_id_seq', 'USAGE')
    THEN
        RAISE EXCEPTION '0080 reversa: app_decide conserva privilegios sobre el ledger de retiro';
    END IF;
END;
$$;
