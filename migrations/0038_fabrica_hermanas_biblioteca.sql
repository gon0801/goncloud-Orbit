-- ---------------------------------------------------------------------------
-- FABRICA 02 A.2 — fase `hermanas_negadas`, trigger bid-solo del goal y
-- GRANTs de biblioteca para el motor.
--
-- Por qué: los jobs de harvest de grupo necesitan una fase nueva entre el
-- readback de la keyword (`exact_created`) y el cierre (`done`) para negar
-- el término en las hermanas (diseño §Fase `hermanas_negadas`); los goals de
-- campañas en grupo necesitan el estado "bid-solo" (terna limpiada, D.2); y
-- el motor (A.4) necesita escribir las bibliotecas con el statement sellado
-- aquí. No re-runnable: recrea el índice parcial `harvest_job_en_vuelo`,
-- suelta el CHECK `goal_harvest_completo` y el bloque DO inserta filas
-- semilla (aunque las borra al salir, re-correrla no es idempotente).
--
-- 0001, 0002 y 0018 NO se editan: todo es ALTER / CREATE OR REPLACE aquí,
-- en una sola transacción (`psql -1`, patrón de la 0019 en docs/DEPLOY.md).
-- El nombre del CHECK inline de `harvest_job.fase` se leyó de
-- `pg_constraint` en la base de test (regla 8), no se supuso:
-- `harvest_job_fase_check`.
-- ---------------------------------------------------------------------------

BEGIN;

-- (a) `harvest_job.fase` admite `hermanas_negadas`. La progresión nueva es
-- las seis de hoy, intactas, más exact_created -> hermanas_negadas y
-- hermanas_negadas -> done|failed. `exact_created -> done` SE CONSERVA
-- (jobs viejos). La obligación "un harvest de grupo pasa por la fase" NO va
-- en plpgsql (un trigger que consulte la membresía viva del grupo dejaría
-- jobs sin salida legal): la cumple la app con su test, en A.3.
CREATE OR REPLACE FUNCTION harvest_job_sella_fases() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.fase IS DISTINCT FROM OLD.fase THEN
        IF (OLD.fase, NEW.fase) IN (
            ('pending', 'negative_created'),
            ('negative_created', 'exact_created'),
            ('exact_created', 'hermanas_negadas'),
            ('hermanas_negadas', 'done'),
            ('exact_created', 'done'),
            ('pending', 'failed'),
            ('negative_created', 'failed'),
            ('exact_created', 'failed'),
            ('hermanas_negadas', 'failed')
        ) THEN
            NULL;  -- avance permitido por la progresion sellada
        ELSE
            RAISE EXCEPTION
                'harvest_job %: fase % -> % fuera de la progresion sellada '
                '(pending -> negative_created -> exact_created -> '
                'hermanas_negadas -> done, con exact_created -> done '
                'conservada para jobs viejos y failed como cierre desde '
                'cualquier fase en vuelo; done/failed terminales, sin saltos '
                'ni retrocesos). docs/APPLY.md seccion 6.',
                OLD.id, OLD.fase, NEW.fase
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION harvest_job_sella_fases IS
  'F2 (0038): la progresión de 0002 más la fase `hermanas_negadas` entre el '
  'readback de la keyword y el cierre (el POST de creación NO es idempotente: '
  'sin saltos que dejen el external_ids a medias ni retrocesos que re-posteen '
  'lo ya creado). `exact_created -> done` se conserva para jobs viejos; la '
  'obligación "harvest de grupo pasa por la fase" la cumple la app (A.3), no '
  'este trigger.';

ALTER TABLE harvest_job DROP CONSTRAINT harvest_job_fase_check;
ALTER TABLE harvest_job ADD CONSTRAINT harvest_job_fase_check
    CHECK (fase IN ('pending', 'negative_created', 'exact_created',
                    'hermanas_negadas', 'done', 'failed'));

-- (b) Índice parcial: `hermanas_negadas` también es fase en vuelo. Sin
-- CONCURRENTLY (va en la transacción; la tabla es chica).
DROP INDEX harvest_job_en_vuelo;
CREATE UNIQUE INDEX harvest_job_en_vuelo
    ON harvest_job (platform, ad_entity_id, search_term)
    WHERE fase IN ('pending', 'negative_created', 'exact_created', 'hermanas_negadas');

-- (c) El ledger gana el tipo `hermana` (una fila por hermana, sin quota;
-- el tope de reintentos por decisión es de la app y NO cuenta hermanas:
-- eso es A.3, aquí solo el esquema). `attempt_probe_sin_decision` queda
-- como está: una `hermana` sin `decision_id` sigue siendo un efecto sin
-- causa.
ALTER TABLE apply_attempt DROP CONSTRAINT attempt_tipo_valido;
ALTER TABLE apply_attempt ADD CONSTRAINT attempt_tipo_valido
    CHECK (tipo IN ('normal', 'reversa', 'probe', 'hermana'));

COMMENT ON TABLE apply_attempt IS
  'El LEDGER de TODO lo que salio a Amazon: bid, corte, reversa y probe '
  '(sellado 10). TODA mutación nace como fila aquí ANTES del HTTP — la '
  'intención durable: un crash entre ledger y HTTP deja rastro y la '
  'reconciliación lo resuelve. EXCEPCIÓN DELIBERADA al append-only estricto '
  'de prohibir_mutacion (el sello del resultado lo bloquearía; r3 qwen): el '
  'candado propio apply_attempt_solo_sella_resultado admite ÚNICAMENTE que '
  'ack/resultado/finished_at pasen de NULL a valor, UNA vez (juntos o de a '
  'uno); cualquier otro cambio y el DELETE revientan. Mismo trato declarado '
  'que decision_application en 0001. "No existe 4º intento" es un COUNT '
  'verificable contra este ledger (tope de reintentos en la app). '
  'quota_cobrada es lo auditable del consumo: reversas exentas y harvest = '
  '2+N HTTPs por operación lógica: [(1,''normal'',cobrada), (2,''normal'',no), '
  '(3..N,''hermana'',no)] se prueban aquí.';

-- (d) El CHECK `goal_harvest_completo` se va; entra un trigger que admite
-- exactamente tres estados: (1) los tres NULL; (2) los tres NOT NULL;
-- (3) campaign/ad_group NULL con bid NOT NULL, ÚNICAMENTE si
-- scope = 'campaign' y la campaña está en `campana_grupo_rol`. Todo lo demás
-- (cualquier parcial, o el estado 3 sin grupo o con scope = platform)
-- revienta con errcode `check_violation`: `goals_write` ya traduce
-- CheckViolation a 422 y otro errcode cambiaría la API.
CREATE FUNCTION ads_optimizer_goal_harvest_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- Estado 1: sin config de harvest.
    IF NEW.harvest_campaign_id IS NULL AND NEW.harvest_ad_group_id IS NULL
        AND NEW.harvest_default_bid IS NULL THEN
        RETURN NEW;
    END IF;
    -- Estado 2: terna completa.
    IF NEW.harvest_campaign_id IS NOT NULL AND NEW.harvest_ad_group_id IS NOT NULL
        AND NEW.harvest_default_bid IS NOT NULL THEN
        RETURN NEW;
    END IF;
    -- Estado 3: bid-solo post-D.2, solo con grupo que le dé destino.
    IF NEW.harvest_campaign_id IS NULL AND NEW.harvest_ad_group_id IS NULL
        AND NEW.harvest_default_bid IS NOT NULL
        AND NEW.scope = 'campaign' AND NEW.ad_entity_id IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM campana_grupo_rol WHERE ad_entity_id = NEW.ad_entity_id
        ) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
        'ads_optimizer_goal %: config de harvest a medias (los tres campos '
        'juntos, los tres NULL, o bid-solo con la campaña en '
        'campana_grupo_rol)',
        NEW.id
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER ads_optimizer_goal_harvest_coherente
    BEFORE INSERT OR UPDATE ON ads_optimizer_goal
    FOR EACH ROW EXECUTE FUNCTION ads_optimizer_goal_harvest_coherente();

COMMENT ON FUNCTION ads_optimizer_goal_harvest_coherente IS
  'F2 (0038, reemplaza al CHECK goal_harvest_completo): los tres campos de '
  'harvest van juntos o no van — config a medias = skip silencioso '
  'disfrazado de dato — salvo el bid-solo post-D.2 (campaign/ad_group NULL '
  'con bid NOT NULL), admitido únicamente si scope = campaign y la campaña '
  'está en campana_grupo_rol (sin grupo no hay destino que lo reemplace).';

ALTER TABLE ads_optimizer_goal DROP CONSTRAINT goal_harvest_completo;

-- Trigger simétrico en `campana_grupo_rol`: si la campaña de la fila tiene
-- un goal en estado 3, rechaza SACARLA (DELETE) o RE-APUNTARLA (UPDATE que
-- cambie ad_entity_id o grupo_id). Sin esto, `app_admin` deja un goal sin
-- destino en silencio (`sin_destino_de_harvest` sin que nadie lo pida). Con
-- goal en estado 1 o 2, el DELETE sigue siendo legal.
CREATE FUNCTION campana_grupo_rol_destino_protegido() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM ads_optimizer_goal g
         WHERE g.scope = 'campaign' AND g.ad_entity_id = OLD.ad_entity_id
           AND g.harvest_campaign_id IS NULL AND g.harvest_ad_group_id IS NULL
           AND g.harvest_default_bid IS NOT NULL
    ) THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION
                'campana_grupo_rol: la campaña % tiene goal en bid-solo '
                '(sin grupo quedaría en sin_destino_de_harvest)',
                OLD.ad_entity_id
                USING ERRCODE = 'check_violation';
        ELSIF NEW.ad_entity_id IS DISTINCT FROM OLD.ad_entity_id
            OR NEW.grupo_id IS DISTINCT FROM OLD.grupo_id THEN
            RAISE EXCEPTION
                'campana_grupo_rol: la campaña % tiene goal en bid-solo; '
                're-apuntarla la dejaría sin destino',
                OLD.ad_entity_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER campana_grupo_rol_destino_protegido
    BEFORE UPDATE OR DELETE ON campana_grupo_rol
    FOR EACH ROW EXECUTE FUNCTION campana_grupo_rol_destino_protegido();

COMMENT ON FUNCTION campana_grupo_rol_destino_protegido IS
  'F2 (0038, simétrico del trigger del goal): una campaña con goal en '
  'bid-solo no se saca ni se re-apunta del grupo (el trigger del goal ya '
  'impide crear ese estado sin grupo; este impide deshacerlo por el otro '
  'lado). Con goal en estado 1 o 2, el DELETE sigue legal.';

-- (e) GRANTs a `app_decide`: INSERT en las dos bibliotecas + USAGE de sus
-- secuencias (son BIGSERIAL: sin USAGE el INSERT truena aunque tenga el
-- GRANT de tabla — la clase de bug 0033→0034) + UPDATE por columna y solo
-- en `keyword_biblioteca` (`negative_biblioteca` no tiene esa columna).
-- Decisión del dueño 2026-09-10: sin dinero — el statement del motor no
-- toca otra columna. `harvest_excepcion` sigue solo `app_admin`. Nada para
-- `app_read` ni `app_ingest`.
GRANT INSERT ON keyword_biblioteca, negative_biblioteca TO app_decide;
GRANT USAGE ON SEQUENCE keyword_biblioteca_id_seq, negative_biblioteca_id_seq
    TO app_decide;
GRANT UPDATE (updated_at) ON keyword_biblioteca TO app_decide;

-- (f) El DO es el candado, no un adorno: ejecuta EL statement literal del
-- motor bajo SET ROLE app_decide y lee la fila de vuelta. El motor que
-- escribe la biblioteca es A.4 y todavía no existe: 0038 lo SELLA y A.4 lo
-- tendrá que usar tal cual (si A.4 escribe otro statement, este candado ya
-- no lo cubre y hay que decirlo en su PR).
--
-- Ojo: un INSERT con cost/moneda PASA por GRANT (las columnas de dinero no
-- están acotadas por permiso) — el "sin dinero" lo garantiza la app
-- (A.4 DoD (g): un mutante que escriba cost o moneda muere en sus tests),
-- no el esquema. No se ejecuta aquí: solo queda dicho.
DO $$
DECLARE
    v_semilla_id BIGINT;
    v_semilla_ts TIMESTAMPTZ;
    v_id_kw      BIGINT;
    v_ts_kw      TIMESTAMPTZ;
    v_id_neg     BIGINT;
BEGIN
    -- Semilla con updated_at viejo: now() es fijo en la transacción, así
    -- que "updated_at movido" se prueba contra este valor, no entre dos
    -- now() iguales.
    INSERT INTO keyword_biblioteca
        (tipo_producto, platform, texto, origen, updated_at)
    VALUES
        ('candado_0038', 'amazon_mx', 'texto candado',
         'grupo:0/campana:0/harvest:0', now() - interval '1 hour')
    RETURNING id, updated_at INTO v_semilla_id, v_semilla_ts;

    SET ROLE app_decide;

    INSERT INTO keyword_biblioteca (tipo_producto, platform, texto, origen)
    VALUES ('candado_0038', 'amazon_mx', 'texto candado', 'grupo:0/campana:0/harvest:0')
    ON CONFLICT (tipo_producto, platform, texto) DO UPDATE SET updated_at = now()
    RETURNING id, updated_at INTO v_id_kw, v_ts_kw;
    IF v_id_kw IS DISTINCT FROM v_semilla_id THEN
        RAISE EXCEPTION '0038: el upsert del motor no devolvió la fila existente';
    END IF;
    IF v_ts_kw IS NULL OR v_ts_kw <= v_semilla_ts THEN
        RAISE EXCEPTION '0038: el upsert del motor no movió updated_at';
    END IF;

    INSERT INTO negative_biblioteca (tipo_producto, platform, texto, origen)
    VALUES ('candado_0038', 'amazon_mx', 'texto candado', 'origen:negativo')
    ON CONFLICT (tipo_producto, platform, texto) DO NOTHING;
    SELECT id INTO v_id_neg FROM negative_biblioteca
     WHERE tipo_producto = 'candado_0038' AND platform = 'amazon_mx'
       AND texto = 'texto candado';
    IF v_id_neg IS NULL THEN
        RAISE EXCEPTION '0038: el insert del motor no dejó fila en negative_biblioteca';
    END IF;

    -- Negativos: DELETE en bibliotecas, UPDATE en harvest_excepcion y
    -- UPDATE de origen/texto/first_seen_at (y cualquier UPDATE en
    -- negative_biblioteca, que no tiene updated_at) truenan.
    BEGIN
        DELETE FROM keyword_biblioteca WHERE id = v_id_kw;
        RAISE EXCEPTION '0038: app_decide pudo DELETE en keyword_biblioteca';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE keyword_biblioteca SET origen = 'otro' WHERE id = v_id_kw;
        RAISE EXCEPTION '0038: app_decide pudo UPDATE(origen) en keyword_biblioteca';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE keyword_biblioteca SET texto = 'otro' WHERE id = v_id_kw;
        RAISE EXCEPTION '0038: app_decide pudo UPDATE(texto) en keyword_biblioteca';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE keyword_biblioteca SET first_seen_at = now() WHERE id = v_id_kw;
        RAISE EXCEPTION '0038: app_decide pudo UPDATE(first_seen_at) en keyword_biblioteca';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        DELETE FROM negative_biblioteca WHERE id = v_id_neg;
        RAISE EXCEPTION '0038: app_decide pudo DELETE en negative_biblioteca';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE negative_biblioteca SET origen = 'otro' WHERE id = v_id_neg;
        RAISE EXCEPTION '0038: app_decide pudo UPDATE en negative_biblioteca';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        UPDATE harvest_excepcion SET go_literal = 'otro' WHERE false;
        RAISE EXCEPTION '0038: app_decide pudo UPDATE en harvest_excepcion';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        INSERT INTO harvest_excepcion
            (ad_entity_id, destino_campaign_external, destino_ad_group_external, go_literal)
        VALUES (-1, 'x', 'y', 'go');
        RAISE EXCEPTION '0038: app_decide pudo INSERT en harvest_excepcion';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    RESET ROLE;

    -- Limpieza como dueño y assert de salida: el candado no deja rastro.
    DELETE FROM keyword_biblioteca WHERE tipo_producto = 'candado_0038';
    DELETE FROM negative_biblioteca WHERE tipo_producto = 'candado_0038';
    PERFORM 1 FROM keyword_biblioteca WHERE tipo_producto = 'candado_0038';
    IF FOUND THEN
        RAISE EXCEPTION '0038: la limpieza del candado dejó filas en keyword_biblioteca';
    END IF;
    PERFORM 1 FROM negative_biblioteca WHERE tipo_producto = 'candado_0038';
    IF FOUND THEN
        RAISE EXCEPTION '0038: la limpieza del candado dejó filas en negative_biblioteca';
    END IF;
END $$;

COMMIT;
