-- =============================================================================
--  ORBIT · MIGRACIÓN 0002 · APPLY  ·  PostgreSQL 16
--
--  El motor pasa a ESCRIBIR en Amazon (PR2 del diseño v2, plan ORBIT 04).
--  Esta migración aterriza los candados sellados de docs/APPLY.md (el brief
--  fino de la task 1.1) y del header de plans/orbit-04.md:
--
--  - apply_queue: la cola de CORTES con ventana de veto de 48h (sellados
--    1-4): máquina de estados por trigger, clave de efecto por FAMILIA
--    (entity_cut/term_cut, no por kind), único parcial en-vuelo NULLS NOT
--    DISTINCT y el veto exigido a admin POR SCHEMA;
--  - apply_attempt: el LEDGER de toda mutación (bid, corte, reversa, probe),
--    nacido PRE-HTTP, con el resultado sellado UNA vez por trigger acotado
--    por columnas (sellado 10; excepción deliberada al append-only estricto);
--  - reactivacion_manual: la casa en schema de la gracia de 7d desde el
--    ENABLED detectado, escrita por el APLICADOR (sellado 17);
--  - sellos sobre apply_quota_state (que existe desde 0001): la fila del día
--    nace SOLO con el cap de la clave mapeada en la config VIGENTE
--    (fail-closed: sin clave no hay applies), used jamás decrece y
--    quota_date es el día UTC de la base (sellado 8);
--  - transiciones de harvest_job selladas por trigger de UPDATE (sellado 13);
--  - decision_application.applied_cycle_id: el cooldown pasa a mirar el
--    ciclo EJECUTOR (sellado 21).
--
--  NO es re-runnable (estilo 0001) y NO recrea roles: los cuatro roles de
--  servicio ya existen desde 0001 — aquí solo se OTORGAN permisos nuevos
--  (GRANTs positivos por columna, sellado 24).
--
--  La numeración de secciones CONTINÚA la de 0001 (que cerró en la 16).
--  Los invariantes de tiempo llevan UTC fijado EN LA EXPRESIÓN (AT TIME ZONE
--  'UTC'), jamás CURRENT_DATE: misma defensa que decision_madurez_corte.
-- =============================================================================

BEGIN;


-- =============================================================================
--  17. COLA DE CORTES  —  apply_queue: máquina de estados con ventana de veto
-- =============================================================================

-- Perímetro sellado (docs/APPLY.md §1.1): los BIDS aplican automático en su
-- ciclo y NO tocan la cola; SOLO cortes (pause/negative/harvest) viven aquí.
-- El CHECK de kind es el candado de perímetro en schema: "nada nuevo se
-- cuelga de la cola" — un kind extra en esta tabla exige decisión nueva del
-- dueño, no una extensión silenciosa del ENUM de decision.
CREATE TABLE apply_queue (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform        platform      NOT NULL,
    ad_entity_id    BIGINT        NOT NULL REFERENCES ad_entity(id),
    -- Clave de EFECTO (sellado 4): familia derivada del kind, NO elegible
    -- (regla 2: un número, una fuente). pause -> entity_cut; negative y
    -- harvest -> term_cut: con kind en la clave, un veto de negative se
    -- eludía proponiendo harvest del MISMO término (r2 grok); con familia de
    -- efecto, negative y harvest del mismo término CHOCAN.
    familia         TEXT GENERATED ALWAYS AS (
                        CASE WHEN kind IN ('negative', 'harvest') THEN 'term_cut'
                             WHEN kind = 'pause' THEN 'entity_cut'
                        END
                    ) STORED NOT NULL,
    -- NULL solo para entity_cut (pause); los term_cut lo exigen NOT NULL.
    search_term     TEXT,
    kind            decision_kind NOT NULL,
    decision_id     BIGINT        NOT NULL REFERENCES decision(id),
    modo            TEXT          NOT NULL
                    CONSTRAINT apply_queue_modo_valido
                    CHECK (modo IN ('shadow', 'live')),
    estado          TEXT          NOT NULL
                    CONSTRAINT apply_queue_estado_valido
                    CHECK (estado IN ('pending_veto', 'released', 'applying',
                                      'applied', 'failed', 'vetoed', 'discarded')),
    -- Ventana de 48h al encolar; al vetar se EDITA al vencimiento del veto
    -- (30d durables). La app la calcula: la base no inventa ventanas.
    vence_el        TIMESTAMPTZ   NOT NULL,
    encolado_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    applying_at     TIMESTAMPTZ,
    applied_at      TIMESTAMPTZ,
    failed_at       TIMESTAMPTZ,
    discarded_at    TIMESTAMPTZ,
    vetoed_at       TIMESTAMPTZ,
    vetoed_by       TEXT,
    discard_motivo  TEXT,
    request_payload JSONB         NOT NULL,

    CONSTRAINT apply_queue_familia_valida
        CHECK (familia IN ('entity_cut', 'term_cut')),
    CONSTRAINT apply_queue_solo_cortes
        CHECK (kind IN ('pause', 'negative', 'harvest')),
    CONSTRAINT apply_queue_clave_coherente CHECK (
        (familia = 'entity_cut' AND search_term IS NULL)
        OR (familia = 'term_cut' AND search_term IS NOT NULL)
    )
);

-- FKs con índice de apoyo (regla del repo): el único parcial de abajo no
-- sirve a la verificación de integridad (es parcial).
CREATE INDEX ON apply_queue (ad_entity_id);
CREATE INDEX ON apply_queue (decision_id);

-- A lo sumo UN en-vuelo por CLAVE DE EFECTO (sellado 4 / brief §1.5). Es
-- PARCIAL sobre NO terminales: applied/failed/vetoed/discarded liberan la
-- clave (un veto VENCIDO no bloquea: el motor re-propone con fila nueva).
-- NULLS NOT DISTINCT es OBLIGATORIO: search_term es NULL en los pause y sin
-- él dos pauses de la misma entidad no chocarían (NULL <> NULL para UNIQUE).
-- El mismo patrón ya cargó el ISR en ledger_dedupe_sin_orden (0001).
CREATE UNIQUE INDEX apply_queue_clave_efecto_en_vuelo
    ON apply_queue (platform, ad_entity_id, familia, search_term)
    NULLS NOT DISTINCT
    WHERE estado NOT IN ('applied', 'failed', 'vetoed', 'discarded');

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

COMMENT ON COLUMN apply_queue.familia IS
  'GENERATED del kind (regla 2: una sola fuente): pause -> entity_cut, '
  'negative/harvest -> term_cut. Con kind en la clave de efecto, un veto de '
  'negative se eludía proponiendo harvest del MISMO término (r2 grok); la '
  'familia de efecto es lo que choca en apply_queue_clave_efecto_en_vuelo.';

COMMENT ON COLUMN apply_queue.kind IS
  'Dato auditable (decision_kind), NO clave de efecto. El CHECK '
  'apply_queue_solo_cortes es el perímetro sellado: los bids aplican en su '
  'ciclo, nada más se cuelga de la cola.';

COMMENT ON COLUMN apply_queue.vence_el IS
  'Ventana de 48h al encolar (el reloj NO se detiene por infra caída; '
  'sellado 2). Al VETAR, el admin la EDITA al vencimiento del bloqueo (30d '
  'durables, sellado 3): por eso el GRANT UPDATE de vence_el es SOLO de '
  'app_admin — el rol del motor (app_decide) no puede tocarla (r2 grok 7). '
  'Al vencer el veto el motor RE-PROPONE con fila nueva: esta fila es '
  'terminal y no se reanima.';

COMMENT ON COLUMN apply_queue.request_payload IS
  'La intención EXACTA que se aplicará (mutación idempotente por fila): lo '
  'que el ledger apply_attempt congela pre-HTTP sale de aquí. NOT NULL '
  'porque toda fila en vuelo tiene efecto definido (regla 3: sin payload no '
  'hay corte).';

COMMENT ON INDEX apply_queue_clave_efecto_en_vuelo IS
  'Sellado 4/5: el ciclo no re-decide una clave de efecto con fila en vuelo '
  'o bloqueo vigente. NULLS NOT DISTINCT (PG 15+) porque los pause llevan '
  'search_term NULL: sin él, dos pauses de la misma entidad no chocarían y '
  'la reserva de la clave sería decorativa.';

-- La fila nace SIEMPRE pending_veto (patrón harvest_job de 0001): un INSERT
-- directo en released saltaría la ventana de veto — el dueño perdería el
-- derecho a vetar sin que nada lo registre.
CREATE FUNCTION apply_queue_nace_pending_veto() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.estado <> 'pending_veto' THEN
        RAISE EXCEPTION
            'apply_queue: la fila nace SIEMPRE pending_veto (ventana de veto '
            'de 48h; docs/APPLY.md seccion 1.5); las fases posteriores solo '
            'se alcanzan por UPDATE. Se recibio estado %', NEW.estado
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER apply_queue_nace_pending_veto
    BEFORE INSERT ON apply_queue
    FOR EACH ROW EXECUTE FUNCTION apply_queue_nace_pending_veto();

-- Máquina de estados EXACTA del brief §1.2 (sellado 4). Todo UPDATE de esta
-- tabla ES una transición: no existen updates in-place (reescribir payload,
-- mover vence_el fuera del veto). El veto exige admin POR SCHEMA (sellado
-- 4/18): el rol del motor NO veta ni siquiera teniendo el UPDATE del claim.
CREATE FUNCTION apply_queue_sella_transiciones() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.estado = OLD.estado THEN
        RAISE EXCEPTION
            'apply_queue %: un UPDATE que no cambia estado no es nada: todo '
            'UPDATE de la cola ES una transicion.', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    -- Sellado 6 (hallazgo reviewer r1): una fila shadow JAMAS se libera (ni
    -- applying/applied/failed: de una fila shadow no sale HTTP). Candado de
    -- schema, no disciplina de la app: su perimetro es veto (practica del
    -- dueño) o discard (flip de ORBIT 05). Va ANTES del check de la tabla
    -- para que TODO escape de una fila shadow reporte el perimetro (hallazgo
    -- reviewer r2: despues de la tabla, un pending_veto->applying de fila
    -- shadow reventaria con el mensaje de la maquina, no el del perimetro).
    IF OLD.modo = 'shadow' AND NEW.estado NOT IN ('vetoed', 'discarded') THEN
        RAISE EXCEPTION
            'apply_queue %: fila shadow (modo=%) JAMAS transiciona a % — su '
            'perimetro es vetoed|discarded (sellado 6: en shadow el dueño '
            'practica el veto; el flip descarta en bloque; cero HTTP).',
            OLD.id, OLD.modo, NEW.estado
            USING ERRCODE = 'check_violation';
    END IF;

    IF (OLD.estado, NEW.estado) IN (
        ('pending_veto', 'vetoed'),
        ('pending_veto', 'released'),
        ('pending_veto', 'discarded'),
        ('released', 'vetoed'),
        ('released', 'applying'),
        ('released', 'discarded'),
        ('applying', 'applied'),
        ('applying', 'failed')
    ) THEN
        NULL;  -- transicion permitida por la tabla EXACTA del brief §1.2
    ELSE
        RAISE EXCEPTION
            'apply_queue %: transicion % -> % fuera de la maquina de estados '
            '(docs/APPLY.md seccion 1.2). NO existe applying -> discarded (la '
            'quota no se quema en descartes); released SIGUE vetable mientras '
            'espera quota; los terminales (vetoed/applied/failed/discarded) '
            'son inmutables; y el veto contra una fila en vuelo (applying) se '
            'rechaza: applying es punto de no retorno.',
            OLD.id, OLD.estado, NEW.estado
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.estado = 'vetoed'
       AND NOT pg_has_role(current_user, 'app_admin', 'MEMBER') THEN
        RAISE EXCEPTION
            'apply_queue %: el veto es decision humana: exige admin '
            '(current_user = % no es miembro de app_admin). El rol del motor '
            'NO veta, ni siquiera con el UPDATE del claim (r2 grok 7).',
            OLD.id, current_user
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER apply_queue_sella_transiciones
    BEFORE UPDATE ON apply_queue
    FOR EACH ROW EXECUTE FUNCTION apply_queue_sella_transiciones();

COMMENT ON FUNCTION apply_queue_nace_pending_veto IS
  'Patron harvest_job: la fila nace SIEMPRE pending_veto ANTES de que corra '
  'ningun reloj de liberacion. Un INSERT en released saltaria la ventana de '
  'veto completa.';

COMMENT ON FUNCTION apply_queue_sella_transiciones IS
  'La maquina de estados del brief §1.2 hecha cumplir por la base, no por la '
  'disciplina de la app (las transiciones atómicas UPDATE ... WHERE estado '
  'siguen siendo obligatorias en la app para las carreras; esto es el '
  'backstop de schema). Cuatro candados en uno: tabla EXACTA de transiciones '
  '(sin applying -> discarded, terminales inmutables), todo UPDATE es '
  'transicion (nada de reescribir la fila in-place), una fila shadow jamas '
  'sale del perimetro vetoed|discarded (sellado 6) y el veto exige '
  'pg_has_role(current_user, app_admin) — el endpoint de veto corre como '
  'admin (sellado 18); el motor jamas.';


-- =============================================================================
--  18. LEDGER DE MUTACIONES  —  apply_attempt: nace PRE-HTTP, se sella UNA vez
-- =============================================================================

CREATE TABLE apply_attempt (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- NULL SOLO para probes: toda mutación real (normal/reversa) cuelga de
    -- su decisión (sellado 10 / brief §4.1).
    decision_id     BIGINT      REFERENCES decision(id),
    seq             INTEGER     NOT NULL,
    tipo            TEXT        NOT NULL
                    CONSTRAINT attempt_tipo_valido
                    CHECK (tipo IN ('normal', 'reversa', 'probe')),
    request_payload JSONB       NOT NULL,
    quota_cobrada   BOOLEAN     NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ack             JSONB,
    resultado       TEXT,
    finished_at     TIMESTAMPTZ,

    CONSTRAINT attempt_probe_sin_decision
        CHECK (tipo = 'probe' OR decision_id IS NOT NULL)
);

CREATE INDEX ON apply_attempt (decision_id);

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
  'quota_cobrada es lo auditable del consumo: reversas exentas y harvest = 1 '
  'operación lógica (aunque sean 2 HTTPs) se prueban aquí.';

COMMENT ON COLUMN apply_attempt.decision_id IS
  'NULL SOLO si tipo = probe (el smoke de 2.5 corre con identidad del motor y '
  'sus filas nacen tipo probe): una mutación normal o reversa SIN decisión es '
  'un efecto sin causa.';

COMMENT ON COLUMN apply_attempt.request_payload IS
  'El payload EXACTO enviado (para harvest: el bid efectivo a escribir, '
  'sellado 14 — el sugerido consultado al aplicar, clampeado y persistido '
  'pre-POST). Inmutable desde el nacimiento: es la prueba de qué se pidió.';

-- Trigger ACOTADO por columnas, patrón sku_cost_solo_cierra_vigencia: el
-- append-only estricto bloquearía el sello del resultado al volver del HTTP.
-- SOLO ack/resultado/finished_at admiten NULL -> valor, UNA vez; todo lo
-- demás y el DELETE revientan — sin depender de qué GRANT tenga el rol.
CREATE FUNCTION apply_attempt_solo_sella_resultado() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'apply_attempt es el ledger de TODO lo que salio a Amazon: una '
            'fila no se borra. El intento fallido ES la evidencia de la '
            'secuencia sellada (regla 5).'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.ack IS DISTINCT FROM OLD.ack
       AND (OLD.ack IS NOT NULL OR NEW.ack IS NULL) THEN
        RAISE EXCEPTION
            'apply_attempt %: ack ya sellado o se intenta des-sellar: el '
            'sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.resultado IS DISTINCT FROM OLD.resultado
       AND (OLD.resultado IS NOT NULL OR NEW.resultado IS NULL) THEN
        RAISE EXCEPTION
            'apply_attempt %: resultado ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.finished_at IS DISTINCT FROM OLD.finished_at
       AND (OLD.finished_at IS NOT NULL OR NEW.finished_at IS NULL) THEN
        RAISE EXCEPTION
            'apply_attempt %: finished_at ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF ROW(NEW.id, NEW.decision_id, NEW.seq, NEW.tipo, NEW.request_payload,
           NEW.quota_cobrada, NEW.started_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.decision_id, OLD.seq, OLD.tipo, OLD.request_payload,
           OLD.quota_cobrada, OLD.started_at) THEN
        RAISE EXCEPTION
            'apply_attempt %: de una fila del ledger SOLO se sellan '
            'ack/resultado/finished_at (NULL -> valor, una vez). El intento '
            'nace ANTES del HTTP y no se reescribe: corregir es insertar la '
            'fila del reintento (seq siguiente).', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER apply_attempt_solo_sella_resultado
    BEFORE UPDATE OR DELETE ON apply_attempt
    FOR EACH ROW EXECUTE FUNCTION apply_attempt_solo_sella_resultado();

CREATE TRIGGER apply_attempt_append_only_truncate
    BEFORE TRUNCATE ON apply_attempt
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON FUNCTION apply_attempt_solo_sella_resultado IS
  'La tercera via del repo (entre prohibir_mutacion y el UPDATE libre): '
  'trigger acotado por columnas, patron sku_cost_solo_cierra_vigencia. El '
  'sello NULL -> valor UNA vez por columna; el DELETE jamas; TRUNCATE por la '
  'capa de sentencia (los triggers de fila no se disparan con TRUNCATE). La '
  'excepcion al append-only es DELIBERADA y esta declarada en el COMMENT de '
  'la tabla: sin ella, el readback no podria cerrar el intento.';


-- =============================================================================
--  19. REACTIVACIÓN MANUAL  —  la gracia de 7d con casa en el schema
-- =============================================================================

-- Sellado 17 (r3 qwen): synced_at se pisa en cada sync y ninguna tarea
-- tocaba al escritor — el instante del ENABLED detectado necesita casa
-- PROPIA e inmutable. La escribe el APLICADOR (no el sync): el apply ya hace
-- GET fresco en su re-check y ahí detecta "pause verificado propio + estado
-- vivo ENABLED". INSERT idempotente por PK; gracia = 7d DESDE detectada_en
-- (mover esa fecha movería la gracia: por eso append-only puro).
CREATE TABLE reactivacion_manual (
    ad_entity_id BIGINT      PRIMARY KEY REFERENCES ad_entity(id),
    detectada_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER reactivacion_append_only
    BEFORE UPDATE OR DELETE ON reactivacion_manual
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER reactivacion_append_only_truncate
    BEFORE TRUNCATE ON reactivacion_manual
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE reactivacion_manual IS
  'Hecho PURO (append-only por prohibir_mutacion): la primera detección del '
  'ENABLED manual es la que abre la gracia de 7d; una segunda detección no '
  'mueve detectada_en (la PK lo impide) y re-detectar tras la gracia no '
  'reescribe la historia. Solo el aplicador escribe (INSERT exclusivo de '
  'app_decide; el SELECT es como toda tabla del schema, patrón 0001 — la '
  'gracia es visible en Salud): el sync no toca esta tabla (structure.py no '
  'se toca; solo el caso detectable, residual declarado en el header).';


-- =============================================================================
--  20. SELLOS DE QUOTA  —  apply_quota_state: la fila del día nace SOLO de
--      la config vigente (fail-closed), used creciente, día UTC de la base
-- =============================================================================

-- Mapeo EXPLÍCITO config <-> quota (sellado 7 / brief §5.2; r2 grok 13: dos
-- vocabularios sin mapeo era el hueco). Vocabulario CERRADO: un motor fuera
-- de este mapa no resuelve clave y devuelve NULL — el INSERT revienta
-- (fail-closed). Kinds nuevos de quota = decisión nueva del dueño.
-- "Config vigente": el schema NO tiene un concepto de vigencia — se usa la
-- ÚLTIMA config_version por id (IDENTITY monótono, coherente con created_at),
-- que es lo que el COMMENT de config_version (0001) declara "la config VIVA
-- vigente": la fila más reciente.
CREATE FUNCTION apply_cap_de_config(p_motor TEXT) RETURNS INTEGER
LANGUAGE sql STABLE
SET search_path = pg_catalog, public
AS $$
    WITH v_clave AS (
        SELECT CASE p_motor
                   WHEN 'ads_optimizer:amazon_us:bid'      THEN 'ads_apply_cap_amazon_us_bid'
                   WHEN 'ads_optimizer:amazon_us:pause'    THEN 'ads_apply_cap_amazon_us_pause'
                   WHEN 'ads_optimizer:amazon_us:negative' THEN 'ads_apply_cap_amazon_us_negative'
                   WHEN 'ads_optimizer:amazon_us:harvest'  THEN 'ads_apply_cap_amazon_us_harvest'
                   WHEN 'ads_optimizer:amazon_mx:bid'      THEN 'ads_apply_cap_amazon_mx_bid'
                   WHEN 'ads_optimizer:amazon_mx:pause'    THEN 'ads_apply_cap_amazon_mx_pause'
                   WHEN 'ads_optimizer:amazon_mx:negative' THEN 'ads_apply_cap_amazon_mx_negative'
                   WHEN 'ads_optimizer:amazon_mx:harvest'  THEN 'ads_apply_cap_amazon_mx_harvest'
                   ELSE NULL
               END AS clave
    ),
    v_vigente AS (
        SELECT settings
          FROM config_version
         ORDER BY id DESC
         LIMIT 1
    )
    SELECT (v.settings ->> k.clave)::INTEGER
      FROM v_clave k CROSS JOIN v_vigente v
     WHERE k.clave IS NOT NULL;
$$;

COMMENT ON FUNCTION apply_cap_de_config IS
  'El mapeo sellado y TESTEADO config->quota (brief §5.2): clave '
  'ads_apply_cap_<platform>_<kind> para el motor ads_optimizer:<platform>:<kind>, '
  'con platform en {amazon_us, amazon_mx} y kind en {bid, pause, negative, '
  'harvest}. Devuelve NULL si el motor esta fuera del vocabulario, si no hay '
  'config_version, o si la clave no existe en la config vigente: en los tres '
  'casos el INSERT de quota revienta (fail-closed, sellado 8: sin clave NO '
  'nace fila del dia -> cero applies, y el estado fail-closed es VISIBLE en '
  'Salud, no disfraz de rampa sana).';

-- Sello del INSERT (brief §5.1): la fila del día (motor, quota_date) solo
-- nace con cap copiado de la config y quota_date = día UTC de la BASE.
-- El día se valida con (now() AT TIME ZONE 'UTC')::date, NUNCA CURRENT_DATE:
-- un DATE sin zona validado contra CURRENT_DATE depende de la TimeZone de la
-- sesión y dos sesiones en zonas distintas duplicaban el cap (r2 codex).
CREATE FUNCTION apply_quota_fila_desde_config() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_cap_config INTEGER;
BEGIN
    v_cap_config := apply_cap_de_config(NEW.motor);
    IF v_cap_config IS NULL THEN
        RAISE EXCEPTION
            'apply_quota_state: motor % fuera del vocabulario '
            'ads_optimizer:<platform>:<kind> (plataformas amazon_us/amazon_mx, '
            'kinds bid/pause/negative/harvest) o sin clave ads_apply_cap_* en '
            'la config vigente. Sin clave NO nace fila del dia: fail-closed '
            '(sellado 8), cero applies.', NEW.motor
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.cap <> v_cap_config THEN
        RAISE EXCEPTION
            'apply_quota_state: el cap de % el % no se inventa: vale % segun '
            'la config vigente y se recibio %. Subir el tope es config nueva '
            'de admin, nunca un numero del motor.', NEW.motor, NEW.quota_date,
            v_cap_config, NEW.cap
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.quota_date <> (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION
            'apply_quota_state: quota_date % no es el dia UTC de la base (%). '
            'El dia se fija con UTC en la expresion: CURRENT_DATE dependeria '
            'de la TimeZone de la sesion y duplicaria el cap (r2 codex).',
            NEW.quota_date, (now() AT TIME ZONE 'UTC')::date
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER apply_quota_fila_desde_config
    BEFORE INSERT ON apply_quota_state
    FOR EACH ROW EXECUTE FUNCTION apply_quota_fila_desde_config();

-- Sello del UPDATE (sellado 8): used jamás decrece (en 0001 esto no era
-- enforceable por CHECK — compararía contra el valor viejo) y la identidad
-- de la fila (motor, quota_date) y su cap son inmutables: el cap de una fila
-- del día ya nacida NO se sube ni por admin (brief §5.5); subir el tope es
-- config nueva que rige desde la primera fila que nazca después.
CREATE FUNCTION apply_quota_used_creciente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.used < OLD.used THEN
        RAISE EXCEPTION
            'apply_quota_state: used JAMAS decrece (% -> % para % el %). El '
            'cobro es monotono: descontar un consumo reescribiria la historia '
            'del dia y el cap dejaria de ser un tope.', OLD.used, NEW.used,
            OLD.motor, OLD.quota_date
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.cap <> OLD.cap OR NEW.quota_date <> OLD.quota_date
       OR NEW.motor <> OLD.motor THEN
        RAISE EXCEPTION
            'apply_quota_state: motor/quota_date/cap son inmutables por '
            'UPDATE (la fila del dia YA nacio con su cap desde config). '
            'Mover consumo entre motores o dias reescribe la rampa: corregir '
            'es una fila nueva el dia que corresponda.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER apply_quota_used_creciente
    BEFORE UPDATE ON apply_quota_state
    FOR EACH ROW EXECUTE FUNCTION apply_quota_used_creciente();

COMMENT ON FUNCTION apply_quota_fila_desde_config IS
  'La fila del dia nace SOLO desde config (fail-closed). Tres sellos en el '
  'INSERT: vocabulario cerrado de motor (mapeo apply_cap_de_config), cap '
  'copiado EXACTO de la clave vigente y quota_date = dia UTC de la base con '
  'UTC fijado en la expresion (misma defensa que decision_madurez_corte). '
  'El INSERT de la fila sigue siendo del motor (grant 0001): lo que no puede '
  'es inventarse el cap.';

COMMENT ON FUNCTION apply_quota_used_creciente IS
  'Sellado 8: used creciente y PK/cap inmutables por UPDATE. En 0001 el '
  '"used nunca decrece" descansaba en el patron de consumo atomico de la '
  'app; aqui lo hace cumplir la base contra el valor viejo del UPDATE. El '
  'CHECK quota_no_excedida de 0001 sigue de backstop del orden del consumo.';


-- =============================================================================
--  21. TRANSICIONES DE HARVEST_JOB  —  la progresión sellada por trigger
-- =============================================================================

-- Sellado 13: harvest_job nace AL LIBERAR (trigger INSERT de 0001 intacto) y
-- sus fases avanzan por la cadena pending -> negative_created ->
-- exact_created -> done, SIN saltos ni retrocesos. failed es el cierre por
-- fallo definitivo y es alcanzable desde CUALQUIER fase en vuelo (la matriz
-- §6.1 cierra failed desde negative_created — con reversa automática — y
-- desde exact_created; un pending cuyo POST del negativo muere sin reintento
-- también cierra); done solo se alcanza desde exact_created. done/failed son
-- terminales. El UPDATE que no cambia fase (acumular external_ids, updated_at)
-- sigue siendo legítimo: el job es ejecución viva, no hecho congelado.
CREATE FUNCTION harvest_job_sella_fases() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.fase IS DISTINCT FROM OLD.fase THEN
        IF (OLD.fase, NEW.fase) IN (
            ('pending', 'negative_created'),
            ('negative_created', 'exact_created'),
            ('exact_created', 'done'),
            ('pending', 'failed'),
            ('negative_created', 'failed'),
            ('exact_created', 'failed')
        ) THEN
            NULL;  -- avance permitido por la progresion sellada
        ELSE
            RAISE EXCEPTION
                'harvest_job %: fase % -> % fuera de la progresion sellada '
                '(pending -> negative_created -> exact_created -> done, con '
                'failed como cierre desde cualquier fase en vuelo; done/failed '
                'terminales, sin saltos ni retrocesos). docs/APPLY.md seccion 6.',
                OLD.id, OLD.fase, NEW.fase
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER harvest_job_sella_fases
    BEFORE UPDATE ON harvest_job
    FOR EACH ROW EXECUTE FUNCTION harvest_job_sella_fases();

COMMENT ON FUNCTION harvest_job_sella_fases IS
  'El POST de creacion de keyword NO es idempotente en Amazon: la progresion '
  'de fases protege el reintento. La reconciliacion viva (inicio de ciclo, '
  'identidad completa plataforma/profile + adGroupId + keyword_text + '
  'match_type) decide si reintentar o cerrar como failed: la base solo '
  'garantiza que el avance sea por la cadena, sin saltos que dejen el '
  'external_ids a medias ni retrocesos que re-posteen lo ya creado.';


-- =============================================================================
--  22. RESUMEN Y CACHE  —  decision_application.applied_cycle_id (ciclo
--      EJECUTOR) y el UPDATE acotado de ad_entity_state para el readback
-- =============================================================================

-- Sellado 21: el cooldown pasa a mirar el ciclo que EJECUTÓ, no el que
-- decidió (caso decisión-shadow-aplicada-en-live). NULL hasta confirmar:
-- el sellado AL CONFIRMAR (jamás pre-HTTP: un crash no cuenta como applied)
-- es disciplina de la APP, declarada aquí; la base garantiza la FK y que
-- solo app_decide pueda escribir esta columna (GRANT de la sección 23).
ALTER TABLE decision_application
    ADD COLUMN applied_cycle_id BIGINT REFERENCES optimizer_cycle(id);

CREATE INDEX ON decision_application (applied_cycle_id);

COMMENT ON COLUMN decision_application.applied_cycle_id IS
  'El ciclo que EJECUTÓ el apply (modo live del que corrió la mutación), '
  'sellado AL CONFIRMAR — jamás pre-HTTP: un crash no cuenta como applied y '
  'applied_count cuadra por ciclo ejecutor. La disciplina de sellarlo al '
  'confirmar (junto a la terna confirmed_at/platform_ack/verify_ok) es de la '
  'app; esta columna es su ancla auditable.';

-- (Sellado 16: el GRANT UPDATE acotado de ad_entity_state para que el apply
-- escriba el cache CON el readback está en la sección 23, junto al resto de
-- permisos positivos de esta migración.)


-- =============================================================================
--  23. PERMISOS  —  GRANTs positivos completos (sellado 24)
-- =============================================================================

-- SELECT explícitos sobre las tablas nuevas (patrón líneas 1459-1460 de
-- 0001). El ALTER DEFAULT PRIVILEGES de 0001 también daría SELECT a app_read
-- si el owner es el mismo, pero el candado no descansa en eso.
GRANT SELECT ON apply_queue, apply_attempt, reactivacion_manual TO app_read;
GRANT SELECT ON apply_queue, apply_attempt, reactivacion_manual
    TO app_ingest, app_decide, app_admin;

-- MOTOR (app_decide): encola cortes, escribe el ledger, avanza la máquina
-- con SUS timestamps, descarta con motivo (re-validación fallida), detecta
-- reactivaciones, consume quota y sella el resultado + el resumen. NO toca
-- vence_el/vetoed_at/vetoed_by: el veto y su bloqueo son del admin, y el
-- trigger apply_queue_sella_transiciones lo exige además POR SCHEMA.
GRANT INSERT ON apply_queue, apply_attempt TO app_decide;
GRANT INSERT ON reactivacion_manual TO app_decide;
GRANT UPDATE (estado, released_at, applying_at, applied_at, failed_at,
              discarded_at, discard_motivo)
    ON apply_queue TO app_decide;
GRANT UPDATE (ack, resultado, finished_at) ON apply_attempt TO app_decide;
-- GRANTs por columna CUMULATIVOS con los de 0001: decision_application gana
-- SOLO applied_cycle_id además de su sello del readback.
GRANT UPDATE (applied_cycle_id) ON decision_application TO app_decide;
-- Sellado 16: el cache se actualiza CON el readback (lo LEÍDO, no lo
-- pedido). Sin esto, el ciclo siguiente calcula +15% sobre el bid viejo
-- (regla 2: la fuente es Amazon y el readback ES de Amazon).
GRANT UPDATE (current_bid, status, synced_at) ON ad_entity_state TO app_decide;

-- ADMIN (app_admin): el veto (transición a vetoed con actor, rastro y
-- vence_el editable = bloqueo durable 30d) y el discard masivo de filas
-- shadow del cutover ORBIT 05 (brief §12). Corre con el DSN admin
-- (sellado 18); el trigger exige el rol además de este GRANT.
GRANT UPDATE (estado, vence_el, vetoed_at, vetoed_by, discarded_at,
              discard_motivo)
    ON apply_queue TO app_admin;

-- Secuencias IDENTITY nuevas (apply_queue_id_seq, apply_attempt_id_seq):
-- mismo patrón que la línea 1517 de 0001, explícito para que ningún refactor
-- a columnas serial rompa permisos en silencio.
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest, app_decide, app_admin;


COMMIT;
