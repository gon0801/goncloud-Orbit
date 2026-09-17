-- ---------------------------------------------------------------------------
-- REPRICING 01 A.0 — motor de precios por goal de margen: esquema.
--
-- Por qué: cada producto con goal llega a su margen por precio (decisiones
-- 1–12 del dueño, S1 del spec). Esta migración crea las cinco tablas del
-- motor — `precio_goal` (goal vigente por publicación, S3), `precio_decision`
-- (una fila por día, S5), `precio_cotizacion` (fees a precio candidato,
-- separada de `estimacion_fee_observation` para no apagar la estimación del
-- día siguiente, hecho 11), `precio_envio_muestra` (evidencia de `L`, forma
-- literal de S5) y `precio_cambio` (escritura asíncrona cerrada por
-- observación, S6) — más la cuota propia (`precio:<platform>`, decisión 12)
-- ampliando `apply_cap_de_config`. No re-runnable: CREATE TYPE/TABLE/INDEX y
-- el bloque DO inserta filas semilla (aunque las revierte al salir,
-- re-correrla no es idempotente).
--
-- 0001…0038 NO se editan: todo es CREATE / ALTER / CREATE OR REPLACE aquí,
-- en una sola transacción (`psql -1`, patrón de la 0019 en docs/DEPLOY.md).
-- Lo ÚNICO que 0039 toca de una tabla existente es
-- `ALTER TABLE listing ADD CONSTRAINT listing_id_platform_key UNIQUE (id,
-- platform)`: `listing` hoy solo tiene `UNIQUE (platform, external_id)` y la
-- FK compuesta de `precio_goal` (S3) lo exige.
--
-- Residuales declarados (decisión del lead, van al PR):
-- - El nacimiento virtual (`aplicado = false` nace `confirmado`/`virtual`,
--   S4 #13) y la reversa sin decisión (`decision_id` NULL solo si
--   `es_reversa`, S6) los impone el trigger de INSERT de `precio_cambio`:
--   S5 no los define y aquí quedan escritos en el COMMENT de la tabla.
-- - `precio_envio_muestra` nace con la forma literal de S5 (spec v1.3): las
--   órdenes, fuentes usadas y duplicados de S10 v1.3 dependen de E.0/E.2 y
--   los ajusta la fila E.3 con su propia migración.
-- - No se toca `apply_quota_fila_desde_config`: su RAISE nombra solo el
--   vocabulario de Ads cuando falta una clave `precio_cap_*` (declarado).
-- - Desviación de S5 (r3-1): S5 pedía `precio_decision.cotizacion_id` Y
--   `precio_cotizacion.decision_id`; sobrevive solo el primero (la cotización
--   nace ANTES, con identidad propia listing+intento+fecha).
-- ---------------------------------------------------------------------------

BEGIN;

-- (a) Modo del goal por producto, con la ceremonia de los goals de Ads: subir
-- a `live` exige go literal (el CHECK lo impone también contra `psql`), bajar
-- a `shadow` no. Sin default (regla 3: sin fila vigente no hay decisión).
CREATE TYPE precio_mode AS ENUM ('shadow', 'live');

COMMENT ON TYPE precio_mode IS
  'REPRICING 01 A.0 (S3): modo del goal por producto. Sin default: sin fila '
  'vigente no hay decisión (decisión 3 del dueño, regla 3 de Orbit).';

-- (b) La FK compuesta de S3 necesita `UNIQUE (id, platform)` en `listing`
-- (hecho medido por el lead 2026-09-17 UTC: hoy solo existe
-- `UNIQUE (platform, external_id)`).
ALTER TABLE listing ADD CONSTRAINT listing_id_platform_key UNIQUE (id, platform);

-- (c) Goal de precio por producto (S3): append-only con vigencia, patrón
-- `sku_cost_solo_cierra_vigencia` — de una fila publicada solo se cierra
-- `valid_to`, una vez. `margen_goal_pct` es porcentaje en fracción (0.30 =
-- 30%): la herramienta lo recibe como porcentaje con dos decimales
-- (`--goal-pct 30.00`, A.1) y la banda 0.10–0.60 vive en CHECK literal y en
-- `goals_write` (claves `precio_goal_min_pct`/`precio_goal_max_pct`).
CREATE TABLE precio_goal (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id      BIGINT NOT NULL,
    platform        platform NOT NULL,
    margen_goal_pct NUMERIC(6, 4) NOT NULL,
    mode            precio_mode NOT NULL,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    creado_por      TEXT NOT NULL,
    go_literal      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_goal_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_goal_banda
        CHECK (margen_goal_pct BETWEEN 0.10 AND 0.60),
    CONSTRAINT precio_goal_live_exige_go
        CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> '')),
    -- La vigencia es [valid_from, valid_to): valid_to = valid_from es un
    -- intervalo vacío —el goal queda ANULADO, nunca estuvo vigente— y así se
    -- apaga un error de dedo el mismo día (si no, la corrida de las 13:10 UTC
    -- actuaría sobre él). Aquí 0039 se aparta del patrón de `sku_cost`
    -- (`valid_to > valid_from`) a propósito: un costo rige pasado; un goal
    -- con error debe poder no regir nunca (regla 7: la reversa existe antes).
    CONSTRAINT precio_goal_vigencia_coherente
        CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT precio_goal_unico_por_fecha UNIQUE (listing_id, platform, valid_from)
);

-- Un solo vigente por (listing, platform): el índice parcial es el que
-- rechaza el segundo vigente (el UNIQUE por fecha admite dos filas con
-- `valid_from` distinto y `valid_to` NULL).
CREATE UNIQUE INDEX precio_goal_un_vigente
    ON precio_goal (listing_id, platform)
    WHERE valid_to IS NULL;

-- Pero ni el parcial (solo filas abiertas) ni el UNIQUE (solo `valid_from`)
-- impiden dos goals vigentes el mismo día con una vigencia ya cerrada
-- (A cerrada `[09-01, 09-20)` + B desde `09-10`: del 10 al 19 hay dos goals y
-- «el goal vigente del día» devuelve dos filas). El invariante lo garantiza
-- la base con EXCLUDE, igual que `sku_cost` en 0001 (ronda 2 de revisión):
-- la vigencia es `[valid_from, valid_to)` semiabierta y un rango vacío
-- (`valid_to = valid_from`, goal anulado) no se solapa con nada, así que la
-- decisión 8 de r1 sigue pasando. El parcial y el UNIQUE se quedan (S3).
ALTER TABLE precio_goal ADD CONSTRAINT precio_goal_sin_solape
    EXCLUDE USING gist (
        listing_id WITH =,
        platform WITH =,
        daterange(valid_from, valid_to, '[)') WITH &&
    );

COMMENT ON TABLE precio_goal IS
  'REPRICING 01 A.0 (S3): goal de margen por publicación, vigencia con el '
  'patrón de `sku_cost` (solo se cierra `valid_to`, una vez; corregir el goal '
  'es fila nueva) SALVO la coherencia: la vigencia es [valid_from, valid_to) '
  'y `valid_to = valid_from` es intervalo vacío —el goal queda ANULADO, nunca '
  'estuvo vigente— para apagar un error de dedo el mismo día (un costo rige '
  'pasado; un goal con error debe poder no regir nunca). Y como en `sku_cost`, '
  'el EXCLUDE `precio_goal_sin_solape` garantiza UN goal por día: ni el índice '
  'parcial (solo abiertas) ni el UNIQUE (solo `valid_from`) impiden dos goals '
  'vigentes el mismo día con una vigencia ya cerrada (ronda 2 de revisión). '
  'Sin fila vigente no hay decisión: sin defaults por plataforma ni herencia '
  '(decisión 3, regla 3). `platform` admite amazon_mx, amazon_us y meli; el '
  'motor solo evalúa las combinaciones habilitadas por la fase vigente y '
  'declara el resto `fuera_de_alcance(fase)`.';
COMMENT ON COLUMN precio_goal.margen_goal_pct IS
  'Fracción sobre el ingreso sin impuesto (I), igual que la contribución '
  'estimada. Banda 0.10–0.60 en CHECK literal (claves precio_goal_min_pct / '
  'precio_goal_max_pct, replicada en app/precio/goals_write.py, A.1).';
COMMENT ON COLUMN precio_goal.go_literal IS
  'Go literal del dueño para subir a `live` (ceremonia de los goals de Ads): '
  'el CHECK precio_goal_live_exige_go lo impone también contra `psql`.';

CREATE FUNCTION precio_goal_solo_cierra_vigencia() RETURNS trigger
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

CREATE TRIGGER precio_goal_solo_cierra_vigencia
    BEFORE UPDATE OR DELETE ON precio_goal
    FOR EACH ROW EXECUTE FUNCTION precio_goal_solo_cierra_vigencia();

-- Misma razón que en las append-only: los triggers de fila no se disparan
-- con TRUNCATE.
CREATE TRIGGER precio_goal_append_only_truncate
    BEFORE TRUNCATE ON precio_goal
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON FUNCTION precio_goal_solo_cierra_vigencia IS
  'REPRICING 01 A.0 (S3, patrón sku_cost_solo_cierra_vigencia): la única '
  'mutación legítima es cerrar la vigencia —valid_to y nada más, transición '
  'NULL -> fecha una vez—; el DELETE jamás, sin depender de qué GRANT tenga '
  'el rol. El segundo vigente lo rechaza el índice parcial '
  'precio_goal_un_vigente, no este trigger.';

-- (d) Decisión diaria por publicación (S5): exactamente una fila por
-- (listing, platform, día). `decision_date` lo fija el trigger al día UTC de
-- la base —el valor que mande el cliente se ignora— porque un DATE validado
-- contra CURRENT_DATE dependería de la TimeZone de la sesión (misma defensa
-- que `apply_quota_fila_desde_config`, r2 codex). Dinero con moneda
-- SIEMPRE (regla 4): cada importe lleva su columna `currency NOT NULL`;
-- el importe es NULL cuando el día es `no_evaluado` (regla 3: nunca un
-- default ni un cero inventado), la moneda no. `canal` reutiliza
-- `estimacion_canal` y admite NULL porque un `no_evaluado(canal_sin_dato)`
-- no tiene canal (el valor de MeLi lo agrega la migración de su fase).
CREATE TABLE precio_decision (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id          BIGINT NOT NULL,
    platform            platform NOT NULL,
    decision_date       DATE NOT NULL,
    resultado           TEXT NOT NULL,
    motivo              TEXT,
    product_id          BIGINT REFERENCES product (id),
    canal               estimacion_canal,
    m_actual            NUMERIC(8, 4),
    goal                NUMERIC(6, 4),
    p_actual            money_amount,
    p_actual_currency   currency NOT NULL,
    p_objetivo          money_amount,
    p_objetivo_currency currency NOT NULL,
    p_aplicado          money_amount,
    p_aplicado_currency currency NOT NULL,
    i_valor             money_amount,
    i_currency          currency NOT NULL,
    c_valor             money_amount,
    c_currency          currency NOT NULL,
    f_valor             money_amount,
    f_currency          currency NOT NULL,
    l_valor             money_amount,
    l_currency          currency NOT NULL,
    r_valor             money_amount,
    r_currency          currency NOT NULL,
    escenario_id        BIGINT REFERENCES estimacion_escenario (id),
    fee_observation_id  BIGINT REFERENCES estimacion_fee_observation (id),
    -- `cotizacion_id` y `envio_muestra_id` nacen como BIGINT plano: sus FK se
    -- declaran por ALTER tras crear `precio_cotizacion`/`precio_envio_muestra`
    -- (r3-1: la cotización nace ANTES que la decisión, así que ya no hay
    -- circularidad, pero el ALTER tras el CREATE mantiene el orden de
    -- lectura del archivo).
    cotizacion_id       BIGINT,
    envio_muestra_id    BIGINT,
    u15                 INTEGER,
    u60                 INTEGER,
    n15                 INTEGER,
    n60                 INTEGER,
    racha_senal         INTEGER,
    perdiendo           BOOLEAN,
    buy_box_is_own      BOOLEAN,
    mode                precio_mode NOT NULL,
    prioridad           NUMERIC(18, 4),
    config_version_id   BIGINT REFERENCES config_version (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_decision_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_decision_unica_por_dia UNIQUE (listing_id, platform, decision_date),
    -- Vocabulario cerrado de S4 (seis resultados) y ningún silencio
    -- (decisión 11 del dueño): fuera de subir/bajar el motivo es obligatorio
    -- y no en blanco; en append-only no se arregla después.
    CONSTRAINT precio_decision_resultado_valido
        CHECK (resultado IN ('subir', 'bajar', 'mantener', 'no_evaluado',
                             'goal_inalcanzable', 'frenado')),
    CONSTRAINT precio_decision_motivo_no_silencio
        CHECK (resultado IN ('subir', 'bajar')
               OR (motivo IS NOT NULL AND btrim(motivo) <> ''))
);

COMMENT ON TABLE precio_decision IS
  'REPRICING 01 A.0 (S5): una fila por (listing, platform, día) —`subir`, '
  '`bajar`, `mantener(motivo)`, `no_evaluado(motivo)`, '
  '`goal_inalcanzable(motivo)` o `frenado(motivo)`— con la cuenta completa '
  '(escenario, fee del escenario, cotización propia y, en FBM, muestra de '
  'envío) para reproducirla sin releer insumos. Append-only por '
  'prohibir_mutacion: corregir es la fila del día siguiente.';
COMMENT ON COLUMN precio_decision.decision_date IS
  'Día UTC de la BASE fijado por trigger (se ignora el del cliente): la '
  'segunda corrida del día no decide (UNIQUE), no pisa.';
COMMENT ON COLUMN precio_decision.canal IS
  'Reutiliza estimacion_canal; NULL cuando no hay canal (canal_sin_dato, '
  'regla 3: nunca un default). El valor de MeLi lo agrega su fase.';
COMMENT ON COLUMN precio_decision.cotizacion_id IS
  'La cotización propia USADA (S4 #3: como máximo dos intentos por día; NULL '
  'cuando el día no cotizó). La cotización nace ANTES que la decisión '
  '(desviación r3-1 de S5, declarada en la cabecera) y este es el único '
  'puntero decisión→cotización que existe.';

-- Índices de apoyo de las FKs (PostgreSQL no los crea por el REFERENCES).
CREATE INDEX precio_decision_por_producto ON precio_decision (product_id);
CREATE INDEX precio_decision_por_escenario ON precio_decision (escenario_id);
CREATE INDEX precio_decision_por_fee ON precio_decision (fee_observation_id);
CREATE INDEX precio_decision_por_cotizacion ON precio_decision (cotizacion_id);
CREATE INDEX precio_decision_por_muestra ON precio_decision (envio_muestra_id);
CREATE INDEX precio_decision_por_config ON precio_decision (config_version_id);

CREATE FUNCTION precio_decision_fecha_utc() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.decision_date := (now() AT TIME ZONE 'UTC')::date;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_decision_fecha_utc
    BEFORE INSERT ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION precio_decision_fecha_utc();

COMMENT ON FUNCTION precio_decision_fecha_utc IS
  'REPRICING 01 A.0 (S5): decision_date es el día UTC de la base con UTC '
  'fijado en la expresión —el cliente no lo decide (regla 6: el día en curso '
  'se descarta y la idempotencia diaria descansa en este valor, no en el '
  'reloj de quien inserta).';

CREATE TRIGGER precio_decision_append_only
    BEFORE UPDATE OR DELETE ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER precio_decision_append_only_truncate
    BEFORE TRUNCATE ON precio_decision
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- (e) Cotización de fees a precio candidato (S5 con la desviación del punto
-- 1, ronda 3 de revisión —ver «Residuales declarados» en la cabecera—): S5
-- pedía `precio_decision.cotizacion_id` Y `precio_cotizacion.decision_id`, y
-- eso no se puede llenar nunca (la decisión es append-only sin UPDATE, las
-- dos PK son GENERATED ALWAYS y la FK no es diferible). Sobrevive el puntero
-- que SÍ se llena: la decisión apunta a la cotización que se usó, y la
-- cotización nace ANTES (es insumo de la decisión, S4 #3) con identidad
-- propia —`(listing_id, platform)` + `intento` (1 o 2, S4 #3: como máximo
-- dos) + `cotizacion_date` por trigger UTC (el valor del cliente se ignora,
-- mismo patrón que `decision_date`)— y `UNIQUE (listing_id, platform,
-- cotizacion_date, intento)`: la base garantiza «≤ 2 cotizaciones reales»
-- por publicación y día, y el intento no usado queda localizable. Vive en su
-- propia tabla, separada de `estimacion_fee_observation` (hecho 11: cotizar
-- ahí apagaría la estimación del día siguiente, porque su lector toma la más
-- reciente). `estado` reutiliza `estimacion_fee_estado`. Append-only.
CREATE TABLE precio_cotizacion (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id            BIGINT NOT NULL,
    platform              platform NOT NULL,
    intento               SMALLINT NOT NULL,
    cotizacion_date       DATE NOT NULL,
    oferta_observation_id BIGINT NOT NULL REFERENCES estimacion_oferta_observation (id),
    quoted_price          money_amount NOT NULL,
    quoted_price_currency currency NOT NULL,
    total_fees            money_amount,
    total_fees_currency   currency,
    fee_details           JSONB NOT NULL DEFAULT '[]',
    fees_estimated_at     TIMESTAMPTZ,
    estado                estimacion_fee_estado NOT NULL,
    error_code            TEXT,
    source_event_id       TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_cotizacion_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_cotizacion_intento_valido CHECK (intento IN (1, 2)),
    CONSTRAINT precio_cotizacion_unica_por_intento
        UNIQUE (listing_id, platform, cotizacion_date, intento),
    CONSTRAINT precio_cotizacion_precio_positivo CHECK (quoted_price > 0),
    CONSTRAINT precio_cotizacion_total_con_moneda
        CHECK ((total_fees IS NULL) = (total_fees_currency IS NULL)),
    -- Candado de 0028 copiado entero (ronda 3 de revisión, punto 2): success
    -- trae total y fecha estimada; error trae código y NADA de fees.
    CONSTRAINT precio_cotizacion_success_exige_total CHECK (
        (estado = 'success' AND total_fees IS NOT NULL)
        OR (estado = 'error' AND total_fees IS NULL)
    ),
    CONSTRAINT precio_cotizacion_error_exige_codigo
        CHECK (estado <> 'error' OR error_code IS NOT NULL),
    CONSTRAINT precio_cotizacion_success_exige_estimada
        CHECK (estado <> 'success' OR fees_estimated_at IS NOT NULL),
    CONSTRAINT precio_cotizacion_evento_unico UNIQUE (source_event_id)
);

COMMENT ON TABLE precio_cotizacion IS
  'REPRICING 01 A.0 (S5 con desviación r3-1, declarada en la cabecera): '
  'cotización Product Fees a un precio candidato (S4 #3: como máximo dos por '
  'publicación y día, intento 1 o 2), insumo de la decisión —nace ANTES que '
  'ella y la decisión la apunta con `cotizacion_id`—. Guardada aparte de '
  'estimacion_fee_observation para no invalidar el escenario del día '
  'siguiente (hecho 11). Append-only: corregir es cotizar de nuevo (intento '
  'siguiente, nunca el mismo).';
COMMENT ON COLUMN precio_cotizacion.total_fees IS
  'NOT NULL en success, NULL en error (regla 3, no cero inventado —candado de '
  '0028 copiado entero); con su moneda por paridad CHECK, como '
  '`listing_price`/`price_currency` en 0001.';
COMMENT ON COLUMN precio_cotizacion.cotizacion_date IS
  'Día UTC de la BASE fijado por trigger (se ignora el del cliente), mismo '
  'patrón que `decision_date`: la idempotencia por día descansa en este '
  'valor, no en el reloj de quien inserta.';

CREATE INDEX precio_cotizacion_por_oferta ON precio_cotizacion (oferta_observation_id);

CREATE FUNCTION precio_cotizacion_fecha_utc() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.cotizacion_date := (now() AT TIME ZONE 'UTC')::date;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_cotizacion_fecha_utc
    BEFORE INSERT ON precio_cotizacion
    FOR EACH ROW EXECUTE FUNCTION precio_cotizacion_fecha_utc();

COMMENT ON FUNCTION precio_cotizacion_fecha_utc IS
  'REPRICING 01 A.0 (r3-1): `cotizacion_date` es el día UTC de la base con '
  'UTC fijado en la expresión —el cliente no lo decide (misma defensa que '
  '`precio_decision_fecha_utc`).';

CREATE TRIGGER precio_cotizacion_append_only
    BEFORE UPDATE OR DELETE ON precio_cotizacion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER precio_cotizacion_append_only_truncate
    BEFORE TRUNCATE ON precio_cotizacion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- (f) Muestra de envíos que produjo `L` (S5, forma literal del spec v1.3):
-- con esta fila se reproduce el número sin volver a recorrer el ledger. La
-- tabla nace vacía y la ajusta la fila E.3 con su propia migración (residual
-- declarado en la cabecera). Append-only.
CREATE TABLE precio_envio_muestra (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES product (id),
    platform        platform NOT NULL,
    ventana_desde   DATE NOT NULL,
    ventana_hasta   DATE NOT NULL,
    envios          INTEGER NOT NULL,
    percentil       TEXT,
    valor           money_amount NOT NULL,
    valor_currency  currency NOT NULL,
    mediana         money_amount,
    mediana_currency currency,
    p90             money_amount,
    p90_currency    currency,
    maximo          money_amount,
    maximo_currency currency,
    calculada_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_muestra_ventana_coherente CHECK (ventana_hasta >= ventana_desde),
    CONSTRAINT precio_muestra_envios_positivo CHECK (envios > 0),
    CONSTRAINT precio_muestra_mediana_con_moneda
        CHECK ((mediana IS NULL) = (mediana_currency IS NULL)),
    CONSTRAINT precio_muestra_p90_con_moneda
        CHECK ((p90 IS NULL) = (p90_currency IS NULL)),
    CONSTRAINT precio_muestra_maximo_con_moneda
        CHECK ((maximo IS NULL) = (maximo_currency IS NULL))
);

COMMENT ON TABLE precio_envio_muestra IS
  'REPRICING 01 A.0 (S5, forma literal v1.3): evidencia de `L` (costo de envío '
  'medido en FBM) —ventana efectiva, conteo, valor sellado y dispersión—; '
  'toda decisión FBM la referencia (envio_muestra_id). Nace vacía: la ajusta '
  'E.3 con su migración (residual declarado). Append-only.';

CREATE INDEX precio_muestra_por_producto ON precio_envio_muestra (product_id);

CREATE TRIGGER precio_muestra_append_only
    BEFORE UPDATE OR DELETE ON precio_envio_muestra
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER precio_muestra_append_only_truncate
    BEFORE TRUNCATE ON precio_envio_muestra
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- (g) FKs diferidas de `precio_decision` (ver nota en el CREATE): ya existen
-- ambas tablas.
ALTER TABLE precio_decision ADD CONSTRAINT precio_decision_cotizacion_fk
    FOREIGN KEY (cotizacion_id) REFERENCES precio_cotizacion (id);
ALTER TABLE precio_decision ADD CONSTRAINT precio_decision_muestra_fk
    FOREIGN KEY (envio_muestra_id) REFERENCES precio_envio_muestra (id);

-- (h) Cambio de precio (S5/S6): la escritura en Amazon es asíncrona (PATCH
-- 202 + `submissionId`, hecho 9), así que la fila nace `pendiente` con el
-- precio leído por GET justo antes, se sella a `enviado`/`error` con el ack
-- literal, y la observación del día siguiente la cierra a
-- `confirmado`/`no_confirmado`. El readback es informativo: nunca decide el
-- estado (un readback fallido es `readback_estado = fallido`, jamás
-- `no_confirmado`). `es_reversa` marca la reversa del dueño (S6: existe y se
-- prueba con ids reales antes del primer cambio real; nunca automática).
CREATE TABLE precio_cambio (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id               BIGINT REFERENCES precio_decision (id),
    listing_id                BIGINT NOT NULL,
    platform                  platform NOT NULL,
    precio_antes              money_amount NOT NULL,
    precio_antes_currency     currency NOT NULL,
    precio_observado_antes    money_amount,
    precio_observado_antes_currency currency,
    precio_despues            money_amount NOT NULL,
    precio_despues_currency   currency NOT NULL,
    aplicado                  BOOLEAN NOT NULL,
    estado                    TEXT NOT NULL,
    enviado_at                TIMESTAMPTZ,
    ack                       JSONB,
    readback_precio           money_amount,
    readback_precio_currency  currency,
    readback_estado           TEXT,
    readback_at               TIMESTAMPTZ,
    confirmado_por            TEXT,
    error_code                TEXT,
    es_reversa                BOOLEAN NOT NULL DEFAULT false,
    reversa_de                BIGINT REFERENCES precio_cambio (id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_cambio_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT precio_cambio_estado_valido
        CHECK (estado IN ('pendiente', 'enviado', 'error', 'confirmado', 'no_confirmado')),
    CONSTRAINT precio_cambio_observado_con_moneda
        CHECK ((precio_observado_antes IS NULL) = (precio_observado_antes_currency IS NULL)),
    CONSTRAINT precio_cambio_readback_con_moneda
        CHECK ((readback_precio IS NULL) = (readback_precio_currency IS NULL)),
    CONSTRAINT precio_cambio_readback_estado_valido
        CHECK (readback_estado IS NULL OR readback_estado IN ('ok', 'fallido')),
    -- Precios de verdad (ronda 2 de revisión): como `listing_precio_positivo`
    -- en 0001 y `precio_cotizacion_precio_positivo` aquí, el ledger no admite
    -- 0 ni negativos; observado/readback solo cuando no son NULL.
    CONSTRAINT precio_cambio_precios_positivos
        CHECK (precio_antes > 0 AND precio_despues > 0
               AND (precio_observado_antes IS NULL OR precio_observado_antes > 0)
               AND (readback_precio IS NULL OR readback_precio > 0)),
    -- Un estado no avanza sin su sello: el error trae código, el cierre trae
    -- origen, y el vuelo (`enviado` en un cambio real) trae ack + enviado_at.
    -- El virtual (`aplicado = false`) queda fuera del tercero a propósito:
    -- nace `confirmado` sin ack.
    CONSTRAINT precio_cambio_error_exige_codigo
        CHECK (estado <> 'error' OR error_code IS NOT NULL),
    CONSTRAINT precio_cambio_cierre_exige_origen
        CHECK (estado NOT IN ('confirmado', 'no_confirmado')
               OR confirmado_por IS NOT NULL),
    CONSTRAINT precio_cambio_envio_exige_ack
        CHECK (NOT aplicado
               OR estado IN ('pendiente', 'error')
               OR (ack IS NOT NULL AND enviado_at IS NOT NULL)),
    -- Reversa sin decisión propia (S6, decisión del lead): la corre el dueño
    -- con la herramienta y no tiene `precio_decision`.
    CONSTRAINT precio_cambio_reversa_binaria
        CHECK ((NOT es_reversa AND reversa_de IS NULL)
               OR (es_reversa AND reversa_de IS NOT NULL)),
    CONSTRAINT precio_cambio_decision_salvo_reversa
        CHECK ((NOT es_reversa AND decision_id IS NOT NULL)
               OR (es_reversa AND decision_id IS NULL))
);

-- Un solo cambio abierto por (listing, platform): el predicado queda literal
-- como en S5 —`estado IN ('pendiente','enviado')`— así que el virtual, que
-- nace cerrado, no lo ocupa.
CREATE UNIQUE INDEX precio_cambio_abierto_unico
    ON precio_cambio (listing_id, platform)
    WHERE estado IN ('pendiente', 'enviado');

CREATE INDEX precio_cambio_por_decision ON precio_cambio (decision_id);
CREATE INDEX precio_cambio_por_listing ON precio_cambio (listing_id, platform);
CREATE INDEX precio_cambio_por_reversa ON precio_cambio (reversa_de);

COMMENT ON TABLE precio_cambio IS
  'REPRICING 01 A.0 (S5/S6): cada escritura de precio (o su sombra virtual) —'
  'fila `pendiente` + COMMIT, escritura externa, sello por ack, cierre por '
  'observación del día siguiente. Sombra fiel (S4 #13, decisión del lead): '
  'el virtual (`aplicado = false`) nace CERRADO (`confirmado`/`virtual`, '
  '`enviado_at` puesto, sin ack ni readback) y consume cooldown y freno igual '
  'que uno real sin ocupar el índice de abierto. Reversa (S6): `es_reversa` '
  'con `precio_despues = precio_antes` del revertido, por el mismo camino y '
  'con el mismo cierre; sin decisión propia; nunca automática (un '
  '`no_confirmado` frena y avisa).';
COMMENT ON COLUMN precio_cambio.precio_antes IS
  'Precio vivo leído por GET del item inmediatamente antes de escribir (S6).';
COMMENT ON COLUMN precio_cambio.readback_estado IS
  'Lectura informativa tras el ack: `ok` o `fallido` —nunca `no_confirmado` '
  '(S6): el readback no decide el estado.';

-- Nacimiento (decisión del lead sobre los huecos de S5, más r3-4): con
-- decisión, `aplicado = (decision.mode = 'live')` en las dos direcciones
-- (S4 #13: en sombra el cambio es virtual y sin PATCH; un virtual bajo `live`
-- también es incoherente); en reversa (sin decisión), `aplicado = true`.
-- Con `aplicado = true` SOLO se nace `pendiente`; con `aplicado = false`
-- (virtual) se nace cerrado —`confirmado`/`virtual` con `enviado_at` y sin
-- ack, readback ni error— para consumir cooldown y freno sin ocupar el
-- índice de abierto.
CREATE FUNCTION precio_cambio_nacimiento() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_mode precio_mode;
BEGIN
    IF NEW.decision_id IS NOT NULL THEN
        SELECT d.mode INTO v_mode
          FROM precio_decision d
         WHERE d.id = NEW.decision_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_cambio: decision_id % inexistente', NEW.decision_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.aplicado <> (v_mode = 'live') THEN
            RAISE EXCEPTION
                'precio_cambio: aplicado = % con decision en mode %: el cambio '
                'real cuelga de una decisión `live` y el virtual de una '
                '`shadow` (S4 #13: en sombra no hay PATCH).',
                NEW.aplicado, v_mode
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NOT NEW.aplicado THEN
        RAISE EXCEPTION
            'precio_cambio: sin decisión (reversa) solo aplicado = true: lo '
            'que no cuelga de ninguna decisión sí salió a la plataforma.'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.aplicado THEN
        IF NEW.estado <> 'pendiente' THEN
            RAISE EXCEPTION
                'precio_cambio: con aplicado = true solo se nace `pendiente` '
                '(el ack decide `enviado | error`, la observación del día '
                'siguiente decide `confirmado | no_confirmado`).'
                USING ERRCODE = 'check_violation';
        END IF;
        -- Nacer `pendiente` con sellos ya puestos bloquearía después el sello
        -- legítimo («sello una sola vez»): ack, readback, origen y código
        -- nacen NULL. `enviado_at` SÍ puede nacer puesto (la fila A.4 pide la
        -- fila `pendiente` con `enviado_at` antes del ack).
        IF NEW.ack IS NOT NULL
           OR NEW.readback_precio IS NOT NULL
           OR NEW.readback_estado IS NOT NULL
           OR NEW.readback_at IS NOT NULL
           OR NEW.confirmado_por IS NOT NULL
           OR NEW.error_code IS NOT NULL THEN
            RAISE EXCEPTION
                'precio_cambio: un cambio real nace sin sellos puestos '
                '(ack/readback/confirmado_por/error_code NULL; solo enviado_at '
                'puede nacer puesto): si no, el sello una sola vez bloquearía '
                'el sello legítimo.'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        IF NEW.estado <> 'confirmado'
           OR NEW.confirmado_por <> 'virtual'
           OR NEW.enviado_at IS NULL
           OR NEW.ack IS NOT NULL
           OR NEW.readback_precio IS NOT NULL
           OR NEW.readback_estado IS NOT NULL
           OR NEW.readback_at IS NOT NULL
           OR NEW.error_code IS NOT NULL THEN
            RAISE EXCEPTION
                'precio_cambio: el virtual (aplicado = false) nace cerrado —'
                'estado `confirmado`, confirmado_por `virtual`, enviado_at '
                'puesto, ack/readback/error nulos—: consume cooldown y freno '
                'igual que uno real sin ocupar el índice de abierto (S4 #13).'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_cambio_nacimiento
    BEFORE INSERT ON precio_cambio
    FOR EACH ROW EXECUTE FUNCTION precio_cambio_nacimiento();

COMMENT ON FUNCTION precio_cambio_nacimiento IS
  'REPRICING 01 A.0 (hueco de S5 cerrado por decisión del lead, más r3-4): '
  'con decisión, aplicado = (mode = live) en las dos direcciones (S4 #13); '
  'sin decisión (reversa), aplicado = true. Estado de nacimiento —real solo '
  '`pendiente`, virtual nace cerrado—. Es trigger de INSERT (no CHECK): con '
  '`aplicado = true` el estado LEGALMENTE deja de ser `pendiente` al '
  'sellarse, y un CHECK lo prohibiría.';

-- Transiciones (S6) con sello acotado por columnas, patrón
-- `apply_attempt_solo_sella_resultado` (0002): `pendiente → enviado | error`,
-- `enviado → confirmado | no_confirmado`, sin saltos ni retrocesos; las
-- columnas de sello admiten NULL -> valor UNA vez; todo lo demás (precios,
-- monedas, origen, flags) es inmutable y el DELETE jamás.
CREATE FUNCTION precio_cambio_sella_transicion() RETURNS trigger
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

CREATE TRIGGER precio_cambio_sella_transicion
    BEFORE UPDATE OR DELETE ON precio_cambio
    FOR EACH ROW EXECUTE FUNCTION precio_cambio_sella_transicion();

CREATE TRIGGER precio_cambio_append_only_truncate
    BEFORE TRUNCATE ON precio_cambio
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON FUNCTION precio_cambio_sella_transicion IS
  'REPRICING 01 A.0 (S6, patrón apply_attempt_solo_sella_resultado de 0002): '
  'la progresión pendiente -> enviado | error, enviado -> confirmado | '
  'no_confirmado, sin saltos (el cierre es por observación D+1) ni '
  'retrocesos; sello NULL -> valor UNA vez por columna de sello; el virtual '
  '(aplicado = false) inmutable tras nacer (r3-6); el DELETE jamás; TRUNCATE '
  'por la capa de sentencia.';

-- (h2) Coherencia de vecindad (ronda 3 de revisión, punto 3; patrón
-- `estimacion_oferta_listing_coherente`, 0028 l.251–274): ninguna FK impide
-- colgar un insumo de otro listing — la decisión de A con el escenario de B
-- pasaría todos los constraints—. Lo impiden estos triggers BEFORE INSERT,
-- con `check_violation` y mensaje que nombra los dos ids.
CREATE FUNCTION precio_cotizacion_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_lid BIGINT;
    v_plat platform;
BEGIN
    SELECT o.listing_id, o.platform INTO v_lid, v_plat
      FROM estimacion_oferta_observation o
     WHERE o.id = NEW.oferta_observation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_cotizacion: oferta_observation_id % inexistente',
            NEW.oferta_observation_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
        RAISE EXCEPTION
            'precio_cotizacion: oferta % es del listing %/% y no de %/%',
            NEW.oferta_observation_id, v_lid, v_plat, NEW.listing_id, NEW.platform
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_cotizacion_coherente
    BEFORE INSERT ON precio_cotizacion
    FOR EACH ROW EXECUTE FUNCTION precio_cotizacion_coherente();

COMMENT ON FUNCTION precio_cotizacion_coherente IS
  'REPRICING 01 A.0 (r3-3, patrón estimacion_oferta_listing_coherente): la '
  'oferta cotizada es del mismo (listing_id, platform) de la cotización.';

CREATE FUNCTION precio_decision_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_lid BIGINT;
    v_plat platform;
    v_prod BIGINT;
    v_mues_prod BIGINT;
    v_mues_plat platform;
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

    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_decision_coherente
    BEFORE INSERT ON precio_decision
    FOR EACH ROW EXECUTE FUNCTION precio_decision_coherente();

COMMENT ON FUNCTION precio_decision_coherente IS
  'REPRICING 01 A.0 (r3-3, patrón estimacion_oferta_listing_coherente): '
  'escenario, fee y cotización del mismo (listing_id, platform); la muestra, '
  'del mismo (product_id, platform); y product_id, el del listing. Solo '
  'INSERT: la decisión es append-only y sus columnas nunca cambian.';

CREATE FUNCTION precio_cambio_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_lid BIGINT;
    v_plat platform;
    v_aplicado BOOLEAN;
    v_es_reversa BOOLEAN;
BEGIN
    IF NEW.decision_id IS NOT NULL THEN
        SELECT d.listing_id, d.platform INTO v_lid, v_plat
          FROM precio_decision d
         WHERE d.id = NEW.decision_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_cambio: decision_id % inexistente', NEW.decision_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_cambio: decision % es del listing %/% y no de %/%',
                NEW.decision_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.reversa_de IS NOT NULL THEN
        SELECT c.listing_id, c.platform, c.aplicado, c.es_reversa
          INTO v_lid, v_plat, v_aplicado, v_es_reversa
          FROM precio_cambio c
         WHERE c.id = NEW.reversa_de;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_cambio: reversa_de % inexistente', NEW.reversa_de
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT v_aplicado OR v_es_reversa THEN
            RAISE EXCEPTION
                'precio_cambio: reversa_de % no es un cambio real no-reversa',
                NEW.reversa_de
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_cambio: reversa % es del listing %/% y no de %/%',
                NEW.reversa_de, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER precio_cambio_coherente
    BEFORE INSERT ON precio_cambio
    FOR EACH ROW EXECUTE FUNCTION precio_cambio_coherente();

COMMENT ON FUNCTION precio_cambio_coherente IS
  'REPRICING 01 A.0 (r3-3, patrón estimacion_oferta_listing_coherente): la '
  'decisión (si hay) es del mismo (listing_id, platform); la reversa apunta '
  'a un cambio REAL no-reversa del mismo par (un virtual no se revierte: '
  'nunca salió a la plataforma). Solo INSERT: el cambio no se reescribe.';

-- (i) GRANTs por columna (S5 y hecho 2 del brief): el motor (`app_decide`)
-- inserta decisión/cotización/muestra/cambio y sella el cambio por columnas;
-- los goals los escribe `app_admin` (INSERT + cierre de `valid_to`);
-- `app_read` solo lee (SELECT explícito en las cinco, como en 0028 —además
-- del default privilege de solo lectura de 0001—). A `app_ingest` no le da
-- escritura (el motor no ingiere hechos); el SELECT explícito sigue el
-- patrón de 0028.
GRANT SELECT ON precio_goal, precio_decision, precio_cotizacion,
    precio_envio_muestra, precio_cambio
    TO app_read, app_ingest, app_decide, app_admin;

GRANT INSERT ON precio_decision, precio_cotizacion, precio_envio_muestra
    TO app_decide;
GRANT INSERT ON precio_cambio TO app_decide;
GRANT UPDATE (estado, enviado_at, ack, readback_precio, readback_precio_currency,
    readback_estado, readback_at, confirmado_por, error_code)
    ON precio_cambio TO app_decide;

GRANT INSERT ON precio_goal TO app_admin;
GRANT UPDATE (valid_to) ON precio_goal TO app_admin;

GRANT USAGE ON SEQUENCE precio_goal_id_seq TO app_admin;
GRANT USAGE ON SEQUENCE precio_decision_id_seq, precio_cotizacion_id_seq,
    precio_envio_muestra_id_seq, precio_cambio_id_seq
    TO app_decide;

-- (j) Cuota propia del motor (decisión 12): `apply_cap_de_config` gana los
-- tres motores `precio:*` mapeados a `precio_cap_*`. Los ocho mapeos de Ads
-- quedan idénticos (los tests de 0002 siguen verdes). Sin clave no nace fila
-- del día (fail-closed del trigger de 0002, intacto —su RAISE nombra el
-- vocabulario de Ads: residual declarado en la cabecera—).
CREATE OR REPLACE FUNCTION apply_cap_de_config(p_motor TEXT) RETURNS INTEGER
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
                   WHEN 'precio:amazon_mx'                 THEN 'precio_cap_amazon_mx'
                   WHEN 'precio:amazon_us'                 THEN 'precio_cap_amazon_us'
                   WHEN 'precio:meli'                      THEN 'precio_cap_meli'
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
  'REPRICING 01 A.0: el mapeo sellado de 0002 (ocho motores '
  'ads_optimizer:<platform>:<kind>, intactos) más los tres del motor de '
  'precios —precio:amazon_mx, precio:amazon_us y precio:meli hacia '
  'precio_cap_amazon_mx/_us/_meli—. Vocabulario cerrado: fuera del mapa, '
  'NULL y el INSERT de quota revienta (fail-closed).';

-- (k) El DO es el candado, no un adorno (patrón 0038): ejercita los INSERTs
-- que cada rol REALMENTE va a ejecutar —el motor cotiza ANTES de decidir
-- (r3-1), decide en `live` con el puntero lleno y sella el cambio real a
-- `enviado`; el admin inserta el goal— y el rechazo de `app_read`, todo bajo
-- `SET ROLE`. Al salir no queda NI UNA fila: TODO (incluida la semilla de
-- catálogo y oferta) vive dentro del bloque interior, que termina con una
-- excepción marcada que el bloque exterior traga, y tragarla revierte el
-- subtransaction completo. Sin DELETEs de limpieza a propósito: la oferta
-- semilla es append-only por 0028 y `prohibir_mutacion` bloquearía su borrado
-- hasta para el dueño. Cualquier fallo REAL (un positivo que no inserta, un
-- negativo que no es rechazado) trae otro mensaje y se re-lanza: la
-- migración falla. Las filas semilla usan valores imposibles en producción
-- (prefijo zz_).
DO $$
DECLARE
    v_prod    BIGINT;
    v_lid     BIGINT;
    v_ofid    BIGINT;
    v_cot     BIGINT;
    v_did     BIGINT;
    v_gid     BIGINT;
    v_cid     BIGINT;
    v_n       BIGINT;
    v_ok      BOOLEAN := false;
BEGIN
    BEGIN
        -- Semilla como dueño DENTRO del bloque (se revierte al salir).
        INSERT INTO product (odoo_sku, name)
        VALUES ('zz-candado-0039', 'Candado 0039') RETURNING id INTO v_prod;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0039', 'zz-SKU-0039') RETURNING id INTO v_lid;
        INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku, asin,
            canal, price_amount, price_currency, fetched_at, observed_at,
            source_event_id, canonical_input, context_fingerprint)
        VALUES (v_lid, 'amazon_mx', 'zz-SKU-0039', 'zz-ASIN-0039', 'fba', 100.00, 'MXN',
            now() - interval '2 hours', now(), 'zz-candado-0039-oferta',
            '{}'::jsonb, 'zz-huella') RETURNING id INTO v_ofid;

        -- El motor, camino nuevo (r3-1): cotiza ANTES de decidir, decide en
        -- `live` con el puntero lleno, cuelga el cambio real y lo sella.
        SET ROLE app_decide;
        INSERT INTO precio_cotizacion (listing_id, platform, intento, oferta_observation_id,
            quoted_price, quoted_price_currency, total_fees, total_fees_currency,
            fees_estimated_at, estado, source_event_id)
        VALUES (v_lid, 'amazon_mx', 1, v_ofid, 110.00, 'MXN', 12.00, 'MXN',
            now(), 'success', 'zz-candado-0039-cotiz')
        RETURNING id INTO v_cot;
        INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,
            cotizacion_id,
            p_actual_currency, p_objetivo_currency, p_aplicado_currency,
            i_currency, c_currency, f_currency, l_currency, r_currency)
        VALUES (v_lid, 'amazon_mx', 'subir', 'candado', 'live', v_cot,
            'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN')
        RETURNING id INTO v_did;
        IF (SELECT cotizacion_id FROM precio_decision WHERE id = v_did) <> v_cot THEN
            RAISE EXCEPTION '0039: el puntero cotizacion_id no quedó lleno';
        END IF;
        INSERT INTO precio_cambio (decision_id, listing_id, platform, precio_antes,
            precio_antes_currency, precio_despues, precio_despues_currency, aplicado, estado)
        VALUES (v_did, v_lid, 'amazon_mx', 100.00, 'MXN', 110.00, 'MXN', true, 'pendiente')
        RETURNING id INTO v_cid;
        UPDATE precio_cambio SET estado = 'enviado', enviado_at = now(),
            ack = '{"submissionId": "zz"}'::jsonb WHERE id = v_cid;
        IF (SELECT estado FROM precio_cambio WHERE id = v_cid) <> 'enviado' THEN
            RAISE EXCEPTION '0039: el sello del motor no avanzó a enviado';
        END IF;

        -- Negativo del motor: en `precio_goal` no inserta.
        BEGIN
            INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
                valid_from, creado_por)
            VALUES (v_lid, 'amazon_mx', 0.30, 'shadow', '2026-09-01', 'candado');
            RAISE EXCEPTION '0039: INSERT de app_decide en precio_goal NO fue rechazado';
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;  -- SQLSTATE 42501: es lo que se espera
        END;
        RESET ROLE;

        -- El admin: goal + cierre de vigencia; en decisión no inserta.
        SET ROLE app_admin;
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por)
        VALUES (v_lid, 'amazon_mx', 0.30, 'shadow', '2026-09-01', 'candado')
        RETURNING id INTO v_gid;
        UPDATE precio_goal SET valid_to = '2026-09-02' WHERE id = v_gid;
        BEGIN
            -- Con motivo y en shadow sin cambio: lo que se prueba aquí es el
            -- privilegio, no los CHECKs de motivo ni modo (r1-5, r3-4).
            INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,
                p_actual_currency, p_objetivo_currency, p_aplicado_currency,
                i_currency, c_currency, f_currency, l_currency, r_currency)
            VALUES (v_lid, 'amazon_mx', 'mantener', 'candado', 'shadow',
                'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN');
            RAISE EXCEPTION '0039: INSERT de app_admin en precio_decision NO fue rechazado';
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        RESET ROLE;

        -- Negativo de lectura: `app_read` no inserta en ninguna de las cinco.
        SET ROLE app_read;
        BEGIN
            INSERT INTO precio_envio_muestra (product_id, platform, ventana_desde,
                ventana_hasta, envios, valor, valor_currency)
            VALUES (v_prod, 'amazon_mx', '2026-06-01', '2026-08-30', 8, 95.00, 'MXN');
            RAISE EXCEPTION '0039: INSERT de app_read en precio_envio_muestra NO fue rechazado';
        EXCEPTION WHEN insufficient_privilege THEN
            NULL;
        END;
        RESET ROLE;

        -- Todo el candado pasó: marcar y revertir el subtransaction.
        v_ok := true;
        RAISE EXCEPTION 'candado_0039_revertir' USING ERRCODE = 'P0001';
    EXCEPTION WHEN raise_exception THEN
        IF NOT v_ok OR SQLERRM <> 'candado_0039_revertir' THEN
            RAISE;  -- fallo real: la migración falla
        END IF;
        -- Tragar la marcada revierte el subtransaction: ni una fila sobrevive.
    END;

    -- Asserts de salida: el candado no deja rastro (las cinco, ronda 2; más
    -- la semilla, que también se revirtió: ronda 3).
    SELECT count(*) INTO v_n FROM precio_decision;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0039: el candado dejó % filas en precio_decision', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_cambio;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0039: el candado dejó % filas en precio_cambio', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_goal;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0039: el candado dejó % filas en precio_goal', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_cotizacion;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0039: el candado dejó % filas en precio_cotizacion', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_envio_muestra;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0039: el candado dejó % filas en precio_envio_muestra', v_n;
    END IF;
    PERFORM 1 FROM product WHERE odoo_sku = 'zz-candado-0039';
    IF FOUND THEN
        RAISE EXCEPTION '0039: el candado dejó la semilla de catálogo';
    END IF;
    PERFORM 1 FROM estimacion_oferta_observation
     WHERE source_event_id = 'zz-candado-0039-oferta';
    IF FOUND THEN
        RAISE EXCEPTION '0039: el candado dejó la oferta semilla';
    END IF;
END $$;

COMMIT;
