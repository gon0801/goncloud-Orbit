-- sonda-aplicada: SELECT pg_get_constraintdef(oid) LIKE '%confirmado%' FROM pg_constraint WHERE conrelid = 'public.precio_cambio'::regclass AND conname = 'precio_cambio_error_code_solo_error'
-- 0090 (ARREGLOS 03 P-C, precio.md F1/E1): un cambio de precio en `error`
-- se cierra con la observacion del dia siguiente, igual que un `enviado`.
--
-- Hasta hoy un `error` (la red se corto despues del PATCH) quedaba asi para
-- siempre: el cierre solo miraba `enviado` y la relectura inmediata, que ve
-- el precio viejo porque la plataforma tarda en mostrar el nuevo, lo daba
-- por «sin efecto». Ahora cerrar_por_observacion (app/precio/cambios.py) lo
-- cierra `confirmado` si el dia siguiente se ve el precio nuevo; si se ve el
-- anterior se queda en `error` con confirmado_por = 'observacion' (sin
-- efecto probado). El `error` cerrado conserva su error_code como historia.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). Re-runnable. Reversa:
-- 0090_reversa_arreglos03_error_cierra_por_observacion.sql.

-- El codigo de error ya no exige `error`: tambien lo conserva el `error` que
-- la observacion cerro `confirmado`. Se valida contra todas las filas.
ALTER TABLE precio_cambio DROP CONSTRAINT precio_cambio_error_code_solo_error;
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_error_code_solo_error
    CHECK (error_code IS NULL OR estado IN ('error', 'confirmado'));

-- precio_cambio_sella_transicion(): el texto de la 0054 con
-- ('error', 'confirmado') en la lista de transiciones y en el mensaje.
CREATE OR REPLACE FUNCTION precio_cambio_sella_transicion() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_cambio es el ledger de TODO lo que salió (o habría salido) '
            'a la plataforma: una fila no se borra.'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NOT OLD.aplicado THEN
        RAISE EXCEPTION
            'precio_cambio %: virtual (aplicado = false) inmutable tras nacer: '
            'nació cerrado y consume cooldown y freno tal cual nació.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.estado IS DISTINCT FROM OLD.estado THEN
        IF (OLD.estado, NEW.estado) IN (
            ('pendiente', 'enviado'),
            ('pendiente', 'error'),
            ('enviado', 'confirmado'),
            ('enviado', 'no_confirmado'),
            ('error', 'confirmado')
        ) THEN
            NULL;
        ELSE
            RAISE EXCEPTION
                'precio_cambio %: estado % -> % fuera de la progresión sellada '
                '(pendiente -> enviado | error, enviado -> confirmado | '
                'no_confirmado, error -> confirmado; el cierre es por '
                'observación del día siguiente, S6).', OLD.id, OLD.estado,
                NEW.estado
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.enviado_at IS DISTINCT FROM OLD.enviado_at
       AND (OLD.enviado_at IS NOT NULL OR NEW.enviado_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: enviado_at ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.ack IS DISTINCT FROM OLD.ack
       AND (OLD.ack IS NOT NULL OR NEW.ack IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: ack ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_precio IS DISTINCT FROM OLD.readback_precio
       AND (OLD.readback_precio IS NOT NULL OR NEW.readback_precio IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_precio ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_precio_currency IS DISTINCT FROM OLD.readback_precio_currency
       AND (OLD.readback_precio_currency IS NOT NULL
            OR NEW.readback_precio_currency IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_precio_currency ya sellado o se intenta '
            'des-sellar.', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_estado IS DISTINCT FROM OLD.readback_estado
       AND (OLD.readback_estado IS NOT NULL OR NEW.readback_estado IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_estado ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_at IS DISTINCT FROM OLD.readback_at
       AND (OLD.readback_at IS NOT NULL OR NEW.readback_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_at ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.confirmado_por IS DISTINCT FROM OLD.confirmado_por
       AND (OLD.confirmado_por IS NOT NULL OR NEW.confirmado_por IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: confirmado_por ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.error_code IS DISTINCT FROM OLD.error_code
       AND (OLD.error_code IS NOT NULL OR NEW.error_code IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: error_code ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;

    IF ROW(NEW.id, NEW.decision_id, NEW.listing_id, NEW.platform,
           NEW.precio_antes, NEW.precio_antes_currency,
           NEW.precio_observado_antes, NEW.precio_observado_antes_currency,
           NEW.precio_despues, NEW.precio_despues_currency,
           NEW.aplicado, NEW.es_reversa, NEW.reversa_de, NEW.corrida_id,
           NEW.lote_reversa, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.decision_id, OLD.listing_id, OLD.platform,
           OLD.precio_antes, OLD.precio_antes_currency,
           OLD.precio_observado_antes, OLD.precio_observado_antes_currency,
           OLD.precio_despues, OLD.precio_despues_currency,
           OLD.aplicado, OLD.es_reversa, OLD.reversa_de, OLD.corrida_id,
           OLD.lote_reversa, OLD.created_at) THEN
        RAISE EXCEPTION
            'precio_cambio %: de una fila del cambio SOLO se sellan estado '
            '(por la progresión) y columnas de sello (NULL -> valor, una vez). '
            'El cambio nace ANTES de la escritura y no se reescribe.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
COMMENT ON FUNCTION precio_cambio_sella_transicion IS
  'ARREGLOS 03 P-C 0090: la 0054 con `error -> confirmado` (el error que la '
  'observación del día siguiente encontró en el precio nuevo). Los sellos '
  'NULL -> valor una vez no cambian.';
