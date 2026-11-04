-- Reversa de la 0054 (patron 0049_reversa_jev_ads).
--
-- Guarda primero: si alguna tabla nueva tiene filas o alguna columna nueva
-- fue escrita, la reversa ya no aplica sin decision consciente. El valor
-- 'meli' del enum lo deja la reversa de la 0053 (Postgres no lo quita).
-- Las tres funciones de trigger se restauran a su texto EXACTO de la 0039
-- para que el esquema quede identico.
BEGIN;

DO $$
DECLARE
    v_datos BIGINT;
BEGIN
    SELECT (SELECT count(*) FROM precio_corrida)
         + (SELECT count(*) FROM precio_retencion)
         + (SELECT count(*) FROM precio_compuerta_medicion)
         + (SELECT count(*) FROM precio_compuerta_liberacion)
         + (SELECT count(*) FROM listing_miembro)
         + (SELECT count(*) FROM ledger_event_atribucion)
         + (SELECT count(*) FROM meli_publicacion_observation)
         + (SELECT count(*) FROM precio_envio_muestra)
      INTO v_datos;
    IF v_datos > 0 THEN
        RAISE EXCEPTION
            'reversa 0054: hay % filas en las tablas nuevas; la reversa ya '
            'no aplica sin decision consciente de perdida', v_datos;
    END IF;
    SELECT (SELECT count(*) FROM estimacion_escenario
             WHERE logistica_origen IS NOT NULL OR envio_muestra_id IS NOT NULL
                OR miembro_product_id IS NOT NULL OR fee_variable IS NOT NULL
                OR fee_fijo IS NOT NULL OR fee_cotizable IS NOT NULL)
         + (SELECT count(*) FROM precio_decision
             WHERE corrida_id IS NOT NULL OR goal_id IS NOT NULL
                OR l_origen IS NOT NULL OR senal_evidencia IS NOT NULL
                OR verificacion IS NOT NULL)
         + (SELECT count(*) FROM precio_goal
             WHERE origen IS NOT NULL OR lote IS NOT NULL OR m_referencia IS NOT NULL)
         + (SELECT count(*) FROM precio_cambio
             WHERE corrida_id IS NOT NULL OR lote_reversa IS NOT NULL)
      INTO v_datos;
    IF v_datos > 0 THEN
        RAISE EXCEPTION
            'reversa 0054: hay % filas que ya usan las columnas nuevas; la '
            'reversa ya no aplica sin decision consciente de perdida', v_datos;
    END IF;
END
$$;

REVOKE SELECT ON listing_miembro, ledger_event_atribucion,
                meli_publicacion_observation, precio_corrida, precio_retencion,
                precio_compuerta_medicion, precio_compuerta_liberacion,
                v_precio_unidad_miembro, v_precio_venta_unidad,
                v_precio_goal_vigente, v_precio_decision_liberada
    FROM app_read, app_decide, app_admin, app_ingest;
REVOKE INSERT ON precio_corrida, precio_retencion, precio_compuerta_medicion
    FROM app_decide;
REVOKE UPDATE (estado, cerrada_at, decisiones, movidas, virtuales, retenidas,
               errores, saltadas_por_edicion, resumen_enviado_at)
    ON precio_corrida FROM app_decide;
REVOKE UPDATE (avisada_at) ON precio_compuerta_medicion FROM app_decide;
REVOKE INSERT ON precio_compuerta_liberacion FROM app_admin;
REVOKE INSERT, UPDATE ON listing_miembro FROM app_ingest;
REVOKE INSERT ON ledger_event_atribucion, meli_publicacion_observation
    FROM app_ingest;
REVOKE SELECT ON precio_envio_muestra FROM app_ingest;
REVOKE USAGE ON SEQUENCE precio_corrida_id_seq, precio_retencion_id_seq
    FROM app_decide;
REVOKE USAGE ON SEQUENCE meli_publicacion_observation_id_seq FROM app_ingest;

DROP VIEW v_precio_decision_liberada, v_precio_goal_vigente,
           v_precio_venta_unidad, v_precio_unidad_miembro;

DROP TRIGGER precio_liberacion_exige_retencion ON precio_compuerta_liberacion;
DROP FUNCTION precio_liberacion_exige_retencion();
DROP TABLE precio_compuerta_liberacion;

DROP TRIGGER precio_retencion_coherente ON precio_retencion;
DROP FUNCTION precio_retencion_coherente();
DROP TABLE precio_retencion;

DROP TRIGGER precio_medicion_solo_sella_aviso ON precio_compuerta_medicion;
DROP TRIGGER precio_medicion_copia_corrida ON precio_compuerta_medicion;
DROP FUNCTION precio_medicion_solo_sella_aviso();
DROP FUNCTION precio_medicion_copia_corrida();
DROP TABLE precio_compuerta_medicion;

ALTER TABLE precio_decision
    DROP CONSTRAINT precio_decision_l_origen_valido,
    DROP CONSTRAINT precio_decision_evidencia_valida,
    DROP CONSTRAINT precio_decision_verificacion_valida,
    DROP CONSTRAINT precio_decision_muestra_segun_origen,
    DROP COLUMN corrida_id,
    DROP COLUMN goal_id,
    DROP COLUMN l_origen,
    DROP COLUMN senal_evidencia,
    DROP COLUMN verificacion;

ALTER TABLE precio_cambio
    DROP CONSTRAINT precio_cambio_lote_solo_reversa,
    DROP COLUMN corrida_id,
    DROP COLUMN lote_reversa;
ALTER TABLE precio_cambio DROP CONSTRAINT precio_cambio_confirmado_por_valido;
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_confirmado_por_valido
    CHECK (confirmado_por IS NULL
           OR confirmado_por IN ('observacion', 'virtual'));

DROP TRIGGER precio_corrida_solo_avanza ON precio_corrida;
DROP TRIGGER precio_corrida_fecha_utc ON precio_corrida;
DROP FUNCTION precio_corrida_solo_avanza();
DROP FUNCTION precio_corrida_fecha_utc();
DROP TABLE precio_corrida;

DROP TABLE meli_publicacion_observation;
DROP TABLE ledger_event_atribucion;
DROP TABLE listing_miembro;

ALTER TABLE estimacion_escenario
    DROP CONSTRAINT estimacion_escenario_logistica_origen_valido,
    DROP CONSTRAINT estimacion_escenario_muestra_segun_origen,
    DROP COLUMN envio_muestra_id,
    DROP COLUMN logistica_origen,
    DROP COLUMN miembro_product_id,
    DROP COLUMN fee_variable,
    DROP COLUMN fee_fijo,
    DROP COLUMN fee_cotizable;

ALTER TABLE precio_envio_muestra
    DROP CONSTRAINT precio_muestra_origen_valido,
    DROP CONSTRAINT precio_muestra_origen_coherente,
    DROP CONSTRAINT precio_muestra_cobrado_con_moneda,
    DROP COLUMN canal,
    DROP COLUMN origen,
    DROP COLUMN familia_id,
    DROP COLUMN donantes,
    DROP COLUMN regla_version,
    DROP COLUMN excl_multiproducto,
    DROP COLUMN excl_sin_producto,
    DROP COLUMN cargos_otros,
    DROP COLUMN cobrado_n,
    DROP COLUMN cobrado_mediana,
    DROP COLUMN cobrado_mediana_currency;

DROP INDEX precio_goal_unico_por_fecha;
DROP INDEX IF EXISTS precio_decision_por_dia;
ALTER TABLE precio_goal
    ADD CONSTRAINT precio_goal_unico_por_fecha
        UNIQUE (listing_id, platform, valid_from);
ALTER TABLE precio_goal
    DROP CONSTRAINT precio_goal_origen_valido,
    DROP COLUMN origen,
    DROP COLUMN lote,
    DROP COLUMN m_referencia;

-- Las tres funciones de trigger vuelven a su texto EXACTO de la 0039.
CREATE OR REPLACE FUNCTION precio_decision_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_lid BIGINT;
    v_plat platform;
    v_prod BIGINT;
    v_mues_prod BIGINT;
    v_mues_plat platform;
    v_goal NUMERIC(6, 4);
BEGIN
    IF NEW.escenario_id IS NOT NULL THEN
        SELECT e.listing_id, e.platform INTO v_lid, v_plat
          FROM estimacion_escenario e
         WHERE e.id = NEW.escenario_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: escenario_id % inexistente', NEW.escenario_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: escenario % es del listing %/% y no de %/%',
                NEW.escenario_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.fee_observation_id IS NOT NULL THEN
        SELECT f.listing_id, f.platform INTO v_lid, v_plat
          FROM estimacion_fee_observation f
         WHERE f.id = NEW.fee_observation_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: fee_observation_id % inexistente',
                NEW.fee_observation_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: fee % es del listing %/% y no de %/%',
                NEW.fee_observation_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.cotizacion_id IS NOT NULL THEN
        SELECT c.listing_id, c.platform INTO v_lid, v_plat
          FROM precio_cotizacion c
         WHERE c.id = NEW.cotizacion_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: cotizacion_id % inexistente', NEW.cotizacion_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: cotizacion % es del listing %/% y no de %/%',
                NEW.cotizacion_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.envio_muestra_id IS NOT NULL THEN
        SELECT m.product_id, m.platform INTO v_mues_prod, v_mues_plat
          FROM precio_envio_muestra m
         WHERE m.id = NEW.envio_muestra_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: envio_muestra_id % inexistente',
                NEW.envio_muestra_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT l.product_id INTO v_prod
          FROM listing l
         WHERE l.id = NEW.listing_id AND l.platform = NEW.platform;
        IF (v_mues_prod, v_mues_plat) IS DISTINCT FROM (v_prod, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: muestra % es del product %/% y no de %/%',
                NEW.envio_muestra_id, v_mues_prod, v_mues_plat, v_prod, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.product_id IS NOT NULL THEN
        SELECT l.product_id INTO v_prod
          FROM listing l
         WHERE l.id = NEW.listing_id AND l.platform = NEW.platform;
        IF NEW.product_id IS DISTINCT FROM v_prod THEN
            RAISE EXCEPTION
                'precio_decision: product_id % no es el producto % del listing %',
                NEW.product_id, v_prod, NEW.listing_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- «Sin fila vigente no hay decisión» (S3) en la base y no solo en el
    -- motor (ronda 4 de revisión, punto 3): `app_decide` tiene INSERT
    -- directo. Vigencia [valid_from, valid_to) con el MISMO mode: un goal
    -- `shadow` no ampara una decisión `live`. La fecha contra la que se
    -- compara es `(now() AT TIME ZONE 'UTC')::date` —la misma expresión con
    -- la que `precio_decision_fecha_utc` fija `decision_date` después—: este
    -- trigger no lee `NEW.decision_date` (los BEFORE INSERT del mismo evento
    -- disparan en orden alfabético y `coherente` va antes que `fecha_utc`).
    -- Y regla 2 (ronda 5, punto 3): `goal` es UN número con UNA fuente —el
    -- trigger lo asigna desde la fila vigente y el valor del cliente se
    -- ignora, mismo trato que `decision_date`—.
    SELECT g.margen_goal_pct INTO v_goal
      FROM precio_goal g
     WHERE g.listing_id = NEW.listing_id
       AND g.platform = NEW.platform
       AND g.mode = NEW.mode
       AND g.valid_from <= (now() AT TIME ZONE 'UTC')::date
       AND (g.valid_to IS NULL
            OR (now() AT TIME ZONE 'UTC')::date < g.valid_to);
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_decision: sin goal vigente en % del mismo mode % para %/%',
            (now() AT TIME ZONE 'UTC')::date, NEW.mode,
            NEW.listing_id, NEW.platform
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.goal := v_goal;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION precio_decision_coherente IS
  'REPRICING 01 A.0 (r3-3, patrón estimacion_oferta_listing_coherente; r4-3): '
  'escenario, fee y cotización del mismo (listing_id, platform); la muestra, '
  'del mismo (product_id, platform); product_id, el del listing; y «sin fila '
  'vigente no hay decisión» (S3): goal del mismo par y mode vigente en la '
  'fecha UTC del servidor. Solo INSERT: la decisión es append-only y sus '
  'columnas nunca cambian.';

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

    -- El virtual nace cerrado y así se queda (r3-6): cualquier UPDATE sobre
    -- él se rechaza, aunque no cambie el estado (si no, un UPDATE sin cambio
    -- de estado le pondría ack/readback por el sello NULL -> valor).
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
            ('enviado', 'no_confirmado')
        ) THEN
            NULL;  -- avance permitido por la progresión sellada (S6)
        ELSE
            RAISE EXCEPTION
                'precio_cambio %: estado % -> % fuera de la progresión sellada '
                '(pendiente -> enviado | error, enviado -> confirmado | '
                'no_confirmado; el cierre es por observación del día '
                'siguiente, S6).', OLD.id, OLD.estado, NEW.estado
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
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_precio_currency IS DISTINCT FROM OLD.readback_precio_currency
       AND (OLD.readback_precio_currency IS NOT NULL
            OR NEW.readback_precio_currency IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_precio_currency ya sellado o se intenta '
            'des-sellar.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_estado IS DISTINCT FROM OLD.readback_estado
       AND (OLD.readback_estado IS NOT NULL OR NEW.readback_estado IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_estado ya sellado o se intenta des-sellar.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_at IS DISTINCT FROM OLD.readback_at
       AND (OLD.readback_at IS NOT NULL OR NEW.readback_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_at ya sellado o se intenta des-sellar.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.confirmado_por IS DISTINCT FROM OLD.confirmado_por
       AND (OLD.confirmado_por IS NOT NULL OR NEW.confirmado_por IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: confirmado_por ya sellado o se intenta des-sellar.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.error_code IS DISTINCT FROM OLD.error_code
       AND (OLD.error_code IS NOT NULL OR NEW.error_code IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: error_code ya sellado o se intenta des-sellar.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- `created_at` también es inmutable (r3-5, como `started_at` en
    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
    IF ROW(NEW.id, NEW.decision_id, NEW.listing_id, NEW.platform,
           NEW.precio_antes, NEW.precio_antes_currency,
           NEW.precio_observado_antes, NEW.precio_observado_antes_currency,
           NEW.precio_despues, NEW.precio_despues_currency,
           NEW.aplicado, NEW.es_reversa, NEW.reversa_de, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.decision_id, OLD.listing_id, OLD.platform,
           OLD.precio_antes, OLD.precio_antes_currency,
           OLD.precio_observado_antes, OLD.precio_observado_antes_currency,
           OLD.precio_despues, OLD.precio_despues_currency,
           OLD.aplicado, OLD.es_reversa, OLD.reversa_de, OLD.created_at) THEN
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
  'REPRICING 01 A.0 (S6, patrón apply_attempt_solo_sella_resultado de 0002): '
  'la progresión pendiente -> enviado | error, enviado -> confirmado | '
  'no_confirmado, sin saltos (el cierre es por observación D+1) ni '
  'retrocesos; sello NULL -> valor UNA vez por columna de sello; el virtual '
  '(aplicado = false) inmutable tras nacer (r3-6); el DELETE jamás; TRUNCATE '
  'por la capa de sentencia.';

CREATE OR REPLACE FUNCTION precio_goal_solo_cierra_vigencia() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_goal es histórico: una vigencia publicada no se BORRA. Se '
            'CIERRA (UPDATE valid_to) y el goal nuevo entra como fila nueva.'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.valid_to IS DISTINCT FROM OLD.valid_to
       AND (OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL) THEN
        RAISE EXCEPTION
            'precio_goal %: valid_to sólo puede pasar de NULL a una fecha, una vez. '
            'Mover el corte de una vigencia cerrada o reabrirla reescribe el '
            'período en que ese goal aplicó.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- `created_at` también es inmutable (r3-5, como `started_at` en
    -- `apply_attempt_solo_sella_resultado`): ni con GRANT amplio se reescribe.
    IF ROW(NEW.id, NEW.listing_id, NEW.platform, NEW.margen_goal_pct,
           NEW.mode, NEW.valid_from, NEW.creado_por, NEW.go_literal,
           NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.listing_id, OLD.platform, OLD.margen_goal_pct,
           OLD.mode, OLD.valid_from, OLD.creado_por, OLD.go_literal,
           OLD.created_at) THEN
        RAISE EXCEPTION
            'precio_goal %: de una fila publicada sólo se puede cerrar la vigencia '
            '(valid_to). Corregir el goal es INSERTAR una fila nueva con '
            'vigencia nueva.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION precio_goal_solo_cierra_vigencia IS
  'REPRICING 01 A.0 (S3, patrón sku_cost_solo_cierra_vigencia): la única '
  'mutación legítima es cerrar la vigencia —valid_to y nada más, transición '
  'NULL -> fecha una vez—; el DELETE jamás, sin depender de qué GRANT tenga '
  'el rol. El segundo vigente lo rechaza el índice parcial '
  'precio_goal_un_vigente, no este trigger.';

-- La config sembrada por la 0054 es append-only como toda config_version:
-- solo se quita si sigue siendo la ultima fila y nadie la edito encima. Si
-- hay versiones posteriores, las claves nuevas quedan inertes (nadie las
-- lee sin el codigo nuevo) y se reporta.
DO $$
DECLARE
    v_id BIGINT;
    v_label TEXT;
BEGIN
    SELECT id, label INTO v_id, v_label
      FROM config_version ORDER BY id DESC LIMIT 1;
    IF v_label = 'REPRICING 02 0054: apagador, fusible y senal' THEN
        SET LOCAL session_replication_role = replica;
        DELETE FROM config_version WHERE id = v_id;
        RAISE NOTICE 'reversa 0054: config % sembrada por la 0054 retirada', v_id;
    ELSE
        RAISE NOTICE
            'reversa 0054: la config vigente ya no es la de la 0054 (%); las '
            'claves precio_modo_* quedan inertes', v_label;
    END IF;
END
$$;

COMMIT;
