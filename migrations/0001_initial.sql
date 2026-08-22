-- =============================================================================
--  ORBIT · MIGRACIÓN 0001 · ESQUEMA INICIAL  ·  PostgreSQL 16
--
--  Sistema NUEVO desde cero (base nueva) que reemplaza al stack de motores de
--  Amazon Ads (goncloud-MCP-2, cancelado 2026-08-21). No se migra esquema
--  viejo: solo las lecciones, que aquí se convierten en restricciones.
--
--  PRINCIPIO RECTOR: la base RECHAZA, la aplicación no recuerda.
--  Cada decisión de diseño de aquí mata un error concreto que ya se pagó.
--  La justificación de cada una vive en los COMMENT ON, para que no se
--  desincronice de un documento aparte. El resumen tabla por tabla está en
--  docs/DATABASE.md.
--
--  Reglas de diseño innegociables (docs/CONTEXTO.md, "Reglas de diseño"):
--  numeradas 1-10 en los comentarios como "Regla N".
--
--  NOTA SOBRE CHECKS Y ZONA HORARIA: PostgreSQL ACEPTA expresiones no
--  inmutables en un CHECK sin quejarse, y luego las evalúa según la TimeZone
--  DE CADA SESIÓN: los casts timestamptz::date / date::timestamptz son STABLE,
--  así que el mismo CHECK podría pasar en una sesión y fallar en otra. Por eso
--  los invariantes que comparan fecha con timestamp NO viven en CHECKs sino en
--  TRIGGERS (decision_madurez_corte), con UTC fijado explícitamente en la
--  expresión (la app corre con TZ UTC como segunda capa). Ver sección 12.
-- =============================================================================

BEGIN;

-- btree_gist: lo necesita el EXCLUDE de sku_cost para comparar
-- product_id (=) contra un daterange (&&) en el mismo constraint.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- =============================================================================
--  1. TIPOS  —  que 'platform' y 'currency' sean TIPOS, no texto libre
-- =============================================================================

CREATE TYPE platform       AS ENUM ('amazon_mx', 'amazon_us', 'meli');
CREATE TYPE currency       AS ENUM ('MXN', 'USD');
CREATE TYPE ad_entity_kind AS ENUM ('campaign', 'ad_group', 'keyword', 'product_target', 'placement');
CREATE TYPE ledger_kind    AS ENUM ('sale', 'fee', 'refund', 'withholding');
CREATE TYPE decision_kind  AS ENUM ('bid', 'budget', 'pause', 'resume', 'negative', 'harvest');
CREATE TYPE fx_source      AS ENUM ('exact', 'nearest_prior');

-- Dinero: NUMERIC exacto. NUNCA float. SQLite no tenía este tipo y por eso
-- todo el dinero del sistema viejo vivía en punto flotante.
CREATE DOMAIN money_amount AS NUMERIC(14, 4);

COMMENT ON DOMAIN money_amount IS
  'NUMERIC exacto. El sistema viejo guardaba dinero en REAL (float) porque '
  'SQLite no tiene tipo decimal. Prohibido usar float para dinero.';

COMMENT ON TYPE platform IS
  'ENUM, no texto. En el sistema viejo un typo en el string de plataforma no '
  'fallaba: devolvía cero filas y el reporte salía en blanco pareciendo válido.';

COMMENT ON TYPE decision_kind IS
  'Incluye harvest: harvestear un search term (moverlo a campaña manual exacta) '
  'es una decisión del optimizador con la misma exigencia de auditoría que un bid.';


-- =============================================================================
--  2. PROCEDENCIA  —  toda fila sabe de qué corrida de ingesta salió
-- =============================================================================

CREATE TABLE ingest_run (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          TEXT        NOT NULL,   -- 'amazon_ads_reports_v3', 'sp_api_finances', ...
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    rows_written    INTEGER,
    rows_skipped    INTEGER     NOT NULL DEFAULT 0,
    skip_reason     TEXT,
    ok              BOOLEAN,
    CONSTRAINT ingest_run_skip_explicado
        CHECK (rows_skipped = 0 OR skip_reason IS NOT NULL)
);

COMMENT ON TABLE ingest_run IS
  'Regla 3 y 10: toda fila saltada tiene que dejar rastro contable — INCLUIDAS '
  'las absorbidas por ON CONFLICT DO NOTHING en los dedupe de métricas y '
  'ledger: cuentan como rows_skipped con su motivo. En el sistema viejo las '
  'filas que se saltaban por falta de tipo de cambio sólo dejaban un '
  'log.error que nadie leía, y al salir de la ventana de reproceso se '
  'perdían para siempre. La corrida se cierra con UPDATE acotado por columna '
  '(finished_at/rows_written/rows_skipped/skip_reason/ok): es la excepción '
  'mutable deliberada entre las tablas de ingesta, porque nace abierta.';

COMMENT ON CONSTRAINT ingest_run_skip_explicado ON ingest_run IS
  'No se puede saltar filas sin decir por qué.';


-- =============================================================================
--  3. CATÁLOGO
-- =============================================================================

CREATE TABLE product (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    odoo_sku    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT true
);

COMMENT ON COLUMN product.odoo_sku IS
  'La identidad canónica del producto es el SKU de Odoo, NO el seller_sku de '
  'la plataforma. Confundirlos costó un JOIN que duplicaba ventas 48%.';

CREATE TABLE listing (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      BIGINT   NOT NULL REFERENCES product(id),
    platform        platform NOT NULL,
    external_id     TEXT     NOT NULL,          -- ASIN o MLM id
    seller_sku      TEXT,
    listing_price   money_amount,
    price_currency  currency,
    UNIQUE (platform, external_id),
    -- Regla 4: no existe un precio sin su moneda.
    CONSTRAINT listing_precio_con_moneda
        CHECK ((listing_price IS NULL) = (price_currency IS NULL)),
    CONSTRAINT listing_precio_positivo
        CHECK (listing_price IS NULL OR listing_price > 0)
);

COMMENT ON CONSTRAINT listing_precio_con_moneda ON listing IS
  'Regla 4. Un precio sin moneda es la semilla del error de 18.66x.';

COMMENT ON TABLE listing IS
  'Un producto puede tener 2-4 listings (varios ASIN por SKU). Ignorar esa '
  'cardinalidad es lo que infló las ventas 48% en el panel de salud del '
  'sistema viejo.';


-- =============================================================================
--  4. COSTOS  —  con vigencia temporal y con la pregunta fiscal FORZADA
-- =============================================================================

CREATE TABLE sku_cost (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id    BIGINT       NOT NULL REFERENCES product(id),
    cost_amount   money_amount NOT NULL,
    cost_currency currency     NOT NULL,
    includes_tax  BOOLEAN      NOT NULL,        -- <<< SIN DEFAULT, a propósito
    valid_from    DATE         NOT NULL,
    valid_to      DATE,
    ingest_run_id BIGINT REFERENCES ingest_run(id),
    CONSTRAINT sku_cost_positivo CHECK (cost_amount > 0),
    CONSTRAINT sku_cost_rango     CHECK (valid_to IS NULL OR valid_to > valid_from),
    -- Un solo costo vigente por producto y fecha. Sin solapamientos.
    EXCLUDE USING gist (
        product_id WITH =,
        daterange(valid_from, valid_to, '[)') WITH &&
    )
);

COMMENT ON COLUMN sku_cost.includes_tax IS
  'NOT NULL y SIN DEFAULT a propósito: obliga a responder si el costo lleva '
  'IVA. Esa pregunta quedó sin respuesta un año y valía 8 puntos de margen '
  'en México.';

COMMENT ON CONSTRAINT sku_cost_positivo ON sku_cost IS
  'Regla 3: un costo de CERO no es un costo, es un dato faltante. En el '
  'sistema viejo el 49% del COGS de MeLi era 0 y ningún WHERE ... IS NOT NULL '
  'lo detectaba: pasó tres auditorías como "limpio". Aquí un costo faltante '
  'es la AUSENCIA de fila, no una fila en cero.';

COMMENT ON TABLE sku_cost IS
  'El costo de una venta es el vigente A SU FECHA, no el actual. El sistema '
  'viejo ignoraba la fecha y cada cambio de costo en Odoo corrompía el '
  'histórico hacia atrás. Corregir un costo = CERRAR la vigencia de la fila '
  'vieja (UPDATE valid_to, el único UPDATE que el permiso permite) e INSERTAR '
  'una fila nueva con vigencia nueva: el importe y valid_from de una fila ya '
  'publicada jamás se reescriben; el EXCLUDE garantiza que la nueva vigencia '
  'no se solape con la anterior.';


-- =============================================================================
--  5. TIPO DE CAMBIO  —  append-only, y la conversión NUNCA se guarda
-- =============================================================================

CREATE TABLE fx_rate (
    rate_date      DATE     NOT NULL,
    base_currency  currency NOT NULL,
    quote_currency currency NOT NULL,
    rate           NUMERIC(18, 8) NOT NULL,
    ingest_run_id  BIGINT REFERENCES ingest_run(id),
    PRIMARY KEY (rate_date, base_currency, quote_currency),
    CONSTRAINT fx_rate_positiva CHECK (rate > 0),
    CONSTRAINT fx_rate_pares_distintos CHECK (base_currency <> quote_currency)
);

COMMENT ON TABLE fx_rate IS
  'APPEND-ONLY (trigger prohibir_mutacion): una tasa corregida se inserta de '
  'nuevo solo si la PK es distinta; si es la misma fecha/par, la fila ya '
  'publicada ES el dato y re-ingestarla es un no-op por la PK. La PK incluye '
  'AMBAS monedas: el sistema viejo consultaba filtrando sólo quote_currency y '
  'una fila invertida se tomaba al revés (devolvía 0.92 como si fuera la tasa '
  'dólar-peso).';

COMMENT ON CONSTRAINT fx_rate_positiva ON fx_rate IS
  'Nada impedía una tasa 0 o negativa en el sistema viejo.';

-- Resolución con tope de antigüedad. Devuelve CERO FILAS si no hay tasa
-- utilizable: NUNCA una constante. El fallback silencioso a 20.5 infló
-- 28,549 MXN de revenue.
CREATE FUNCTION fx_resolve(
    p_date  DATE,
    p_base  currency,
    p_quote currency,
    p_max_age_days INTEGER DEFAULT 7
) RETURNS TABLE (rate NUMERIC, rate_date DATE, source fx_source)
LANGUAGE sql STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT f.rate,
           f.rate_date,
           CASE WHEN f.rate_date = p_date THEN 'exact'::fx_source
                ELSE 'nearest_prior'::fx_source END
      FROM fx_rate f
     WHERE f.base_currency = p_base
       AND f.quote_currency = p_quote
       AND f.rate_date <= p_date
       AND f.rate_date >= p_date - p_max_age_days
     ORDER BY f.rate_date DESC
     LIMIT 1;
$$;

COMMENT ON FUNCTION fx_resolve IS
  'Tope de 7 días medido contra la cadencia real: diaria, con huecos de 3 días '
  'por fin de semana y un máximo observado de 5 en 10 meses. Sin resultado '
  'devuelve CERO filas, nunca una constante. Y el resultado dice si la tasa '
  'era exacta o anterior: el sistema viejo no podía distinguir una tasa de un '
  'día de una de ochenta. Devuelve rate_date + source: eso ES la fecha_fx que '
  'pide la regla 4, resuelta en lectura (nunca persistimos el convertido).';


-- =============================================================================
--  6. MÉTRICAS DE ADS  —  el corazón. APPEND-ONLY, bitemporal.
-- =============================================================================

CREATE TABLE ad_entity (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform     platform       NOT NULL,
    kind         ad_entity_kind NOT NULL,
    external_id  TEXT           NOT NULL,
    parent_id    BIGINT REFERENCES ad_entity(id),
    name         TEXT,
    listing_id   BIGINT REFERENCES listing(id),
    -- Convención para keywords: match_type y keyword_text viven en columnas
    -- propias (no en el JSON de la API). El duplicate-check del harvest contra
    -- la campaña manual destino las necesita sin parsear payloads.
    match_type   TEXT,          -- EXACT / PHRASE / BROAD (solo kind='keyword')
    keyword_text TEXT,          -- el texto de la keyword (solo kind='keyword')
    UNIQUE (platform, kind, external_id),
    CONSTRAINT ad_entity_keyword_coherente CHECK (
        (kind = 'keyword' AND match_type IS NOT NULL AND keyword_text IS NOT NULL)
        OR (kind <> 'keyword' AND match_type IS NULL AND keyword_text IS NULL)
    )
);

CREATE INDEX ON ad_entity (parent_id);

COMMENT ON TABLE ad_entity IS
  'Cualquier cosa con external_id en la API de ads: campaña, ad group, '
  'keyword, product target, placement. Los search terms NO son entidades '
  '(no tienen id propio): viven en search_term_observation. '
  'platform, kind, external_id, parent_id, match_type y keyword_text son '
  'INMUTABLES por permisos (app_ingest solo puede UPDATE name y listing_id): '
  'mutarlos tras insertar hechos rompería el sello de moneda a posteriori y '
  'dejaría goals apuntando a kinds que ya no son campaign. Corregir una '
  'entidad = crear una nueva y desactivar la vieja.';

COMMENT ON CONSTRAINT ad_entity_keyword_coherente ON ad_entity IS
  'Keyword sin texto es entidad fantasma: el harvest no podría verificar '
  'duplicados contra ella (bid_cache del viejo no tenía keyword_text y el '
  'diseño v2 tuvo que declararlo explícitamente).';

CREATE TABLE ads_metric_observation (
    ad_entity_id     BIGINT      NOT NULL REFERENCES ad_entity(id),
    metric_date      DATE        NOT NULL,   -- el día del HECHO
    observed_at      TIMESTAMPTZ NOT NULL,   -- el día de la OBSERVACIÓN
    metric_currency  currency    NOT NULL,
    cost             money_amount,
    ad_revenue       money_amount,
    revenue_same_sku money_amount,
    impressions      BIGINT,
    clicks           BIGINT,
    orders           INTEGER,
    source_report_id TEXT,                  -- id del reporte de la API (idempotencia)
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run(id),

    PRIMARY KEY (ad_entity_id, metric_date, observed_at),

    CONSTRAINT metric_no_negativos CHECK (
        (cost IS NULL OR cost >= 0) AND
        (ad_revenue IS NULL OR ad_revenue >= 0) AND
        (revenue_same_sku IS NULL OR revenue_same_sku >= 0) AND
        (impressions IS NULL OR impressions >= 0) AND
        (clicks IS NULL OR clicks >= 0) AND
        (orders IS NULL OR orders >= 0)
    ),
    -- Regla 4: la venta del mismo SKU nunca puede exceder el total.
    CONSTRAINT metric_same_sku_cabe
        CHECK (revenue_same_sku IS NULL OR ad_revenue IS NULL
               OR revenue_same_sku <= ad_revenue)
);

-- Idempotencia de ingesta: re-ingestar el MISMO reporte no duplica
-- observaciones aunque cambie observed_at (la PK sola no lo impediría).
-- La ingesta hace INSERT ... ON CONFLICT DO NOTHING con este índice, y las
-- filas absorbidas cuentan como rows_skipped con motivo en ingest_run.
CREATE UNIQUE INDEX metric_dedupe_reporte
    ON ads_metric_observation (ad_entity_id, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL;

COMMENT ON TABLE ads_metric_observation IS
  'Regla 5, LA MÁS IMPORTANTE. La clave incluye observed_at: cada re-lectura '
  'de la misma fecha es una FILA NUEVA, no un UPDATE. '
  'El sistema viejo hacía UPSERT in-place sobre keyword_daily_metrics: la fila '
  'de hace 3 meses contenía HOY el número que el motor JAMÁS pudo ver el día '
  'que decidió (verificado en 2,297 de 2,297 filas; el motor veía el 68.9% del '
  'revenue que la tabla mostraba). Ningún backtest del año fue válido, y no '
  'producía síntoma: salía espectacular. '
  'Esto también resuelve el clawback: Amazon RETIRA costo hasta D+15 (día 1 = '
  '108.5% del final). Corregir es insertar una observación nueva, no pisar la '
  'anterior. La corrección queda, la historia también. '
  'NOTA: el invariante "observed_at >= metric_date" del borrador NO vive en un '
  'CHECK: PostgreSQL acepta casts STABLE (date::timestamptz) en CHECKs y los '
  'evalúa según la TimeZone de cada sesión — el mismo CHECK pasaría en una '
  'sesión y fallaría en otra. Lo valida la ingesta, con TZ fija UTC.';

COMMENT ON COLUMN ads_metric_observation.metric_currency IS
  'Regla 4. amazon_us reporta ads en USD y sus ventas llegan en MXN. Cruzar '
  'las dos sin mirar la moneda daba un error de 18.66x, SIEMPRE en la '
  'dirección "todo es rentabilísimo". El trigger metric_moneda_de_plataforma '
  'sella el par plataforma-moneda contra el mapa fijo.';

COMMENT ON COLUMN ads_metric_observation.source_report_id IS
  'Id del reporte asíncrono (reporting v3) del que salió la fila. Con el '
  'índice metric_dedupe_reporte, re-correr el mismo sync es seguro: duplicar '
  'es imposible por schema, no por disciplina.';

CREATE INDEX ON ads_metric_observation (metric_date DESC, ad_entity_id);
CREATE INDEX ON ads_metric_observation (ad_entity_id, metric_date DESC, observed_at DESC);


-- =============================================================================
--  7. SEARCH TERMS  —  append-only igual que las métricas, pero NO son
--     ad_entity: un search term no tiene external_id propio en la API.
-- =============================================================================

CREATE TABLE search_term_observation (
    platform         platform    NOT NULL,
    ad_entity_id     BIGINT      NOT NULL REFERENCES ad_entity(id),  -- campaña/ad_group origen
    search_term      TEXT        NOT NULL,
    metric_date      DATE        NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    metric_currency  currency    NOT NULL,
    cost             money_amount,
    clicks           BIGINT,
    orders           INTEGER,
    ad_revenue       money_amount,
    is_asin_like     BOOLEAN     NOT NULL,   -- SIN DEFAULT, a propósito
    source_report_id TEXT,
    ingest_run_id    BIGINT      NOT NULL REFERENCES ingest_run(id),

    PRIMARY KEY (platform, ad_entity_id, search_term, metric_date, observed_at),

    CONSTRAINT st_metric_no_negativos CHECK (
        (cost IS NULL OR cost >= 0) AND
        (ad_revenue IS NULL OR ad_revenue >= 0) AND
        (clicks IS NULL OR clicks >= 0) AND
        (orders IS NULL OR orders >= 0)
    )
);

CREATE UNIQUE INDEX st_metric_dedupe_reporte
    ON search_term_observation (platform, ad_entity_id, search_term, metric_date, source_report_id)
    WHERE source_report_id IS NOT NULL;

COMMENT ON TABLE search_term_observation IS
  'Misma disciplina append-only que ads_metric_observation (regla 5): la '
  'higiene (NEGATIVE_EXACT) y el HARVEST deciden sobre esta tabla y necesitan '
  'la misma garantía de backtest honesto. La moneda queda sellada contra la '
  'plataforma por el trigger metric_moneda_de_plataforma.';

COMMENT ON COLUMN search_term_observation.is_asin_like IS
  'NOT NULL SIN DEFAULT: la ingesta está OBLIGADA a clasificar cada término; '
  'un ASIN propio sin clasificar NO PUEDE ENTRAR. Los términos ASIN-like '
  '(B0..., o el ASIN propio) SIEMPRE se saltan en negativos: regla sealed '
  'own-ASIN — negativizar un ASIN propio es apagarte a ti mismo. Un default '
  'false convertiría "no lo revisé" en "no es ASIN", exactamente el dato '
  'faltante disfrazado de dato que prohíbe la regla 3.';


-- =============================================================================
--  7b. SELLO PLATAFORMA↔MONEDA  —  trigger compartido por las dos tablas de
--      métricas. Mapa fijo: amazon_mx→MXN, amazon_us→USD, meli→MXN.
-- =============================================================================

CREATE FUNCTION metric_moneda_de_plataforma() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_platform platform;
    v_esperada currency;
BEGIN
    -- En ads_metric_observation la plataforma se deriva de la entidad;
    -- en search_term_observation viene en la fila. La rama ELSE es
    -- fail-loud: una tercera tabla que montara este trigger sin su rama
    -- no puede caer silenciosamente en la lógica de search_terms (referenciaría
    -- NEW.platform, que podría no existir).
    IF TG_TABLE_NAME = 'ads_metric_observation' THEN
        SELECT e.platform INTO v_platform
          FROM ad_entity e
         WHERE e.id = NEW.ad_entity_id;
    ELSIF TG_TABLE_NAME = 'search_term_observation' THEN
        -- search_term_observation: platform viene DESNORMALIZADA en la fila
        -- (la PK la incluye). Se cruza contra la entidad: una fila que
        -- declara una plataforma distinta a la de su entidad es un bug de
        -- ingesta y se rechaza aquí — sin este cruce, el mismo hecho cabría
        -- dos veces con plataformas distintas y el sello de moneda sellaría
        -- la equivocada.
        SELECT e.platform INTO v_platform
          FROM ad_entity e
         WHERE e.id = NEW.ad_entity_id;
        IF v_platform IS DISTINCT FROM NEW.platform THEN
            RAISE EXCEPTION
                'search_term_observation: la fila declara plataforma % pero '
                'su entidad % es %.', NEW.platform, NEW.ad_entity_id, v_platform
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        RAISE EXCEPTION
            'metric_moneda_de_plataforma: tabla % no soportada por el sello',
            TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
    END IF;

    v_esperada := CASE v_platform
                      WHEN 'amazon_mx' THEN 'MXN'::currency
                      WHEN 'amazon_us' THEN 'USD'::currency
                      WHEN 'meli'      THEN 'MXN'::currency
                  END;

    IF NEW.metric_currency IS DISTINCT FROM v_esperada THEN
        RAISE EXCEPTION
            '%: la plataforma % reporta sus metricas de ads en %, no en %. '
            'Una moneda distinta aqui es el bug de 18.66x en estado larval.',
            TG_TABLE_NAME, v_platform, v_esperada, NEW.metric_currency
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION metric_moneda_de_plataforma IS
  'Mapa FIJO plataforma→moneda de reporte de ads (amazon_mx→MXN, '
  'amazon_us→USD, meli→MXN): si una cuenta cambiara de moneda, el cambio se '
  'hace editando esta función en una migración nueva, no insertando filas '
  'raros. ledger_event queda EXENTO A PROPÓSITO: guarda montos en moneda '
  'ORIGINAL, que puede no ser la de la plataforma (ej. un cargo en USD en la '
  'cuenta MX); el ledger no reporta-métricas-de-ads sino dinero real.';

CREATE TRIGGER metric_moneda_sellada
    BEFORE INSERT ON ads_metric_observation
    FOR EACH ROW EXECUTE FUNCTION metric_moneda_de_plataforma();

CREATE TRIGGER st_metric_moneda_sellada
    BEFORE INSERT ON search_term_observation
    FOR EACH ROW EXECUTE FUNCTION metric_moneda_de_plataforma();


-- =============================================================================
--  8. LEDGER  —  ventas, fees, retenciones. order_id NULLABLE a propósito.
--     CONVENCIÓN DE SIGNOS: ventas positivas; fee/refund/withholding <= 0.
--     Así SUM(amount) funciona sin FILTER por kind.
-- =============================================================================

CREATE TABLE ledger_event (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform        platform    NOT NULL,
    kind            ledger_kind NOT NULL,
    event_date      DATE        NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    order_id        TEXT,                       -- NULL legítimo: ver comentario
    product_id      BIGINT REFERENCES product(id),
    quantity        INTEGER,                    -- unidades vendidas (para COGS)

    -- Regla 4: SIEMPRE el monto en su moneda ORIGINAL. Nunca se guarda el
    -- convertido: la conversión es una vista. Así el original no se puede perder.
    amount          money_amount NOT NULL,
    amount_currency currency     NOT NULL,

    -- Desglose fiscal, cuando la plataforma lo entrega (misma moneda que amount).
    item_price      money_amount,
    item_tax        money_amount,
    shipping_price  money_amount,
    shipping_tax    money_amount,

    fee_type        TEXT,

    -- Idempotencia: id del evento en la fuente (settlement id, shipment id...).
    -- Cuando la fuente no da id (el ISR de Amazon NUNCA lo da), queda NULL y
    -- el dedupe cae al índice ledger_dedupe_sin_orden.
    source_event_id TEXT,

    ingest_run_id   BIGINT NOT NULL REFERENCES ingest_run(id),

    -- CONVENCIÓN DE SIGNOS, hecha cumplir por la base: toda salida de dinero
    -- se guarda NEGATIVA. Sin esto, SUM(amount) por kind mezcla signos y cada
    -- reporte inventa su propia convención (el sistema viejo tenía fees
    -- positivos en una tabla y negativos en otra).
    CONSTRAINT ledger_convencion_signos CHECK (
        (kind = 'sale' AND amount > 0) OR
        (kind IN ('fee', 'refund', 'withholding') AND amount <= 0)
    ),
    -- Una venta CON producto tiene que decir cuántas unidades: sin quantity
    -- no hay COGS y el margen de contribución queda hueco (ver
    -- v_margen_plataforma, cobertura_cogs_pct). OJO: quantity > 0 solo no
    -- basta — un CHECK con expresión NULL PASA (no es FALSE), así que hay
    -- que prohibir el NULL explícitamente.
    CONSTRAINT ledger_venta_con_cantidad
        CHECK (NOT (kind = 'sale' AND product_id IS NOT NULL)
               OR (quantity IS NOT NULL AND quantity > 0)),
    CONSTRAINT ledger_desglose_coherente
        CHECK (
            (item_price IS NULL OR item_price >= 0)
            AND (item_tax IS NULL OR item_tax >= 0)
            AND (shipping_price IS NULL OR shipping_price >= 0)
            AND (shipping_tax IS NULL OR shipping_tax >= 0)
        )
);

-- Re-correr un sync no duplica: misma fuente, mismo evento, misma fila.
CREATE UNIQUE INDEX ledger_dedupe_source
    ON ledger_event (platform, kind, source_event_id)
    WHERE source_event_id IS NOT NULL;

-- Cargos SIN id de fuente (el ISR llega en bultos quincenales sin order_id ni
-- event id): dedupe por su clave natural, MONEDA INCLUIDA — sin
-- amount_currency, 100 USD y 100 MXN el mismo día colisionarían y un cargo
-- se perdería. NULLS NOT DISTINCT (PG 15+): sin esto, dos cargos con
-- fee_type NULL nunca chocarían en el índice (NULL <> NULL para UNIQUE) y el
-- ISR se duplicaría en cada re-sync. Si Amazon manda dos cargos idénticos el
-- mismo día con el mismo fee_type, importe y moneda, son el mismo cargo
-- re-leído; un cargo genuinamente distinto difiere en al menos un campo.
CREATE UNIQUE INDEX ledger_dedupe_sin_orden
    ON ledger_event (platform, kind, fee_type, event_date, amount, amount_currency)
    NULLS NOT DISTINCT
    WHERE source_event_id IS NULL AND order_id IS NULL;

-- Cargos CON order_id pero sin id de fuente (un re-sync de fees con orden
-- pero sin event id): no caen en ninguno de los otros dos índices y se
-- duplicarían en silencio. Misma clave natural, MONEDA INCLUIDA, y NULLS NOT
-- DISTINCT por la misma razón que ledger_dedupe_sin_orden.
CREATE UNIQUE INDEX ledger_dedupe_con_orden
    ON ledger_event (platform, kind, order_id, fee_type, event_date, amount, amount_currency)
    NULLS NOT DISTINCT
    WHERE source_event_id IS NULL AND order_id IS NOT NULL;

COMMENT ON INDEX ledger_dedupe_sin_orden IS
  'Los conflictos absorbidos por ON CONFLICT DO NOTHING NO son gratis: la '
  'ingesta los cuenta en ingest_run.rows_skipped con motivo (regla 3). Un '
  'dedupe que no deja rastro es un dato perdido disfrazado de eficiencia. '
  'Entre las tres claves de dedupe (ledger_dedupe_source por source_event_id, '
  'ledger_dedupe_con_orden con order_id sin source id, y esta) queda cubierto '
  'TODO el espacio: source_event_id / order_id / ninguno.';

COMMENT ON COLUMN ledger_event.order_id IS
  'NULLABLE A PROPÓSITO y es la asimetría central del dominio: el ISR de '
  'Amazon NUNCA trae order_id (13/13 filas en MX, 7/7 en US) y llega en bultos '
  'QUINCENALES, no por venta. En MeLi es al revés: 950/950 CON order_id. '
  'Por eso el ISR se caía de todo cálculo por-orden y no entraba al margen. '
  'Aquí no se pierde: ver la vista v_margen_plataforma, que expone el margen '
  'CON y SIN los cargos no atribuibles. Nunca se descartan en silencio.';

COMMENT ON COLUMN ledger_event.amount IS
  'En la moneda ORIGINAL, siempre, y con el signo de la convención. El sistema '
  'viejo guardaba todo convertido a MXN y de 477 ventas de amazon_us, CERO '
  'conservaban el importe en USD: la conversión era IRREVERSIBLE. Cuando 77 se '
  'convirtieron con una tasa inventada (20.5), no hubo forma de recuperar el '
  'valor real desde la fila.';

COMMENT ON CONSTRAINT ledger_convencion_signos ON ledger_event IS
  'sale > 0; fee/refund/withholding <= 0. Un fee de exactamente 0 no es un '
  'dato (regla 3) pero se admite porque las plataformas a veces asientan '
  'cargos en cero como placeholder de liquidación; lo que NO se admite es un '
  'fee positivo: eso rompe todos los SUM.';

CREATE INDEX ON ledger_event (platform, event_date DESC);
CREATE INDEX ON ledger_event (order_id) WHERE order_id IS NOT NULL;
-- Índice parcial: los cargos sin orden son los que hay que vigilar.
CREATE INDEX ON ledger_event (platform, event_date) WHERE order_id IS NULL;


-- =============================================================================
--  9. CONFIGURACIÓN VERSIONADA  —  qué config regía cuando se decidió
-- =============================================================================

CREATE TABLE config_version (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    label      TEXT,
    settings   JSONB NOT NULL
);

COMMENT ON TABLE config_version IS
  'Snapshot inmutable (append-only por trigger) del set completo de '
  'configuración; la config VIVA vigente es la fila más reciente. Una decisión '
  'apunta a la versión que regía cuando se tomó; sin esto no se puede '
  'reconstruir por qué el sistema hizo lo que hizo. La inserta app_admin '
  '(config humana, escalera off->shadow->live), no los motores. '
  'settings JAMÁS contiene credenciales: app_read tiene SELECT aquí.';


-- =============================================================================
--  10. ESTADO ACTUAL DE ENTIDADES  —  MUTABLE, cache. Excepción deliberada.
-- =============================================================================

CREATE TABLE ad_entity_state (
    ad_entity_id   BIGINT PRIMARY KEY REFERENCES ad_entity(id),
    current_bid    money_amount,
    bid_currency   currency,
    status         TEXT,          -- ENABLED / PAUSED / ARCHIVED (tal cual la API)
    targeting_type TEXT,          -- AUTO / MANUAL
    acos_target    NUMERIC(6, 2), -- cache del target publicado en la campaña
    synced_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT estado_bid_con_moneda
        CHECK ((current_bid IS NULL) = (bid_currency IS NULL))
);

COMMENT ON TABLE ad_entity_state IS
  'EXCEPCIÓN DELIBERADA a la regla append-only: es un CACHE del estado actual '
  'en Amazon (reemplazo de bid_cache + ad_campaigns del sistema viejo, sync '
  'diario), no historia. La historia de lo que el sistema CREYÓ que era el '
  'estado vive en decision.inputs (JSONB) de cada decisión. synced_at alimenta '
  'la guarda de frescura: plataforma saltada si synced_at > 48h.';

COMMENT ON COLUMN ad_entity_state.acos_target IS
  'Cache del acos_target que Amazon tiene publicado en la campaña. NO es la '
  'fuente del target para decidir: la cascada es goal -> setting -> este cache '
  '-> default 55 (ver ADS_OPTIMIZER_V2_DESIGN.md).';


-- =============================================================================
--  11. OPTIMIZADOR  —  goals, lock de ciclo, envelope, quotas.
--      Reglas y umbrales: docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md (manda).
--      (harvest_job vive en la sección 12, junto a decision, por su FK.)
-- =============================================================================

-- Goal por campaña o por plataforma. Es CONFIGURACIÓN viva: mutable a
-- propósito (como ad_entity_state). Lo que una decisión usó queda congelado
-- en decision.inputs + config_version, así que mutar un goal no reescribe
-- la historia. La escribe app_admin (endpoint /goals), no los motores.
CREATE TABLE ads_optimizer_goal (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope               TEXT     NOT NULL CHECK (scope IN ('campaign', 'platform')),
    ad_entity_id        BIGINT   REFERENCES ad_entity(id),  -- scope='campaign'
    platform            platform,                           -- scope='platform'
    target_acos_pct     NUMERIC(6, 2),
    bid_floor           money_amount NOT NULL DEFAULT 0.10,
    bid_ceiling         money_amount NOT NULL DEFAULT 2.50,
    bid_currency        currency     NOT NULL,
    -- Config de harvest: la campaña MANUAL destino. Nullable a propósito:
    -- falta config -> la decisión HARVEST se salta con motivo registrado.
    -- NUNCA un placeholder (regla 3 aplicada a config).
    harvest_campaign_id TEXT,
    harvest_ad_group_id TEXT,
    harvest_default_bid money_amount,
    enabled             BOOLEAN NOT NULL DEFAULT false,
    mode                TEXT    NOT NULL DEFAULT 'off'
                        CHECK (mode IN ('off', 'shadow', 'live')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT goal_scope_coherente CHECK (
        (scope = 'campaign' AND ad_entity_id IS NOT NULL AND platform IS NULL) OR
        (scope = 'platform' AND platform IS NOT NULL AND ad_entity_id IS NULL)
    ),
    CONSTRAINT goal_piso_bajo_techo CHECK (bid_floor <= bid_ceiling),
    CONSTRAINT goal_harvest_completo CHECK (
        (harvest_campaign_id IS NULL AND harvest_ad_group_id IS NULL
            AND harvest_default_bid IS NULL)
        OR (harvest_campaign_id IS NOT NULL AND harvest_ad_group_id IS NOT NULL
            AND harvest_default_bid IS NOT NULL)
    )
);

-- Un solo goal por campaña y uno por plataforma: si hubiera dos, la
-- precedencia campaña > plataforma no alcanzaría para desempatar.
CREATE UNIQUE INDEX goal_unico_campana
    ON ads_optimizer_goal (ad_entity_id) WHERE scope = 'campaign';
CREATE UNIQUE INDEX goal_unico_plataforma
    ON ads_optimizer_goal (platform) WHERE scope = 'platform';

-- Un goal de campaña tiene que apuntar a una entidad que ES una campaña.
-- La FK sola no lo garantiza (ad_entity mezcla kinds); un goal sobre un
-- ad_group pasaría la FK y la precedencia campaña>plataforma resolvería
-- contra una entidad que no es campaña.
CREATE FUNCTION goal_scope_campana_real() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.scope = 'campaign' AND NEW.ad_entity_id IS NOT NULL THEN
        PERFORM 1 FROM ad_entity
         WHERE id = NEW.ad_entity_id AND kind = 'campaign';
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'goal scope=campaign: ad_entity_id % no existe o no es '
                'kind=campaign', NEW.ad_entity_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER goal_scope_campana_real
    BEFORE INSERT OR UPDATE ON ads_optimizer_goal
    FOR EACH ROW EXECUTE FUNCTION goal_scope_campana_real();

COMMENT ON TABLE ads_optimizer_goal IS
  'Elegibilidad dura del optimizador: SOLO campañas cubiertas por un goal '
  'habilitado (propio o de su plataforma) entran al ciclo; el live nunca '
  'alcanza campañas no configuradas. Precedencia campaña > plataforma se '
  'resuelve EN LA APP (no hay forma declarativa sencilla de expresarla sin '
  'esconder la lógica en SQL). La escalera off->shadow->live es por goal y la '
  'maneja app_admin vía el endpoint /goals: mode live sin módulo apply degrada '
  'el ciclo a shadow + status degraded en optimizer_cycle (fail-closed).';

COMMENT ON COLUMN ads_optimizer_goal.harvest_default_bid IS
  'Bid inicial de la keyword harvesteada en la campaña manual. Su moneda es '
  'bid_currency DEL MISMO GOAL (la campaña destino está en la misma '
  'plataforma): nunca un número suelto sin moneda.';

COMMENT ON CONSTRAINT goal_harvest_completo ON ads_optimizer_goal IS
  'Los tres campos de harvest van juntos o no van: config a medias = skip '
  'silencioso disfrazado de dato.';

-- Claim de ciclo: cron y /run comparten job_key; TTL con heartbeat.
CREATE TABLE ads_optimizer_lock (
    job_key     TEXT PRIMARY KEY,
    owner       TEXT        NOT NULL,
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_seconds INTEGER     NOT NULL DEFAULT 1800 CHECK (ttl_seconds > 0)
);

COMMENT ON TABLE ads_optimizer_lock IS
  'Un ciclo por plataforma a la vez, aunque cron y el endpoint /run coincidan. '
  'El claim se toma con INSERT ON CONFLICT ... WHERE heartbeat_at + ttl '
  'vencido (la lógica de expiración vive en la app, que es quien corre los '
  'ciclos). TTL default 30 min, medido contra la duración de un ciclo.';

-- Envelope de ciclo: una corrida = una fila. Toda decisión apunta a su ciclo.
CREATE TABLE optimizer_cycle (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    motor           TEXT     NOT NULL DEFAULT 'ads_optimizer',
    mode            TEXT     NOT NULL CHECK (mode IN ('off', 'shadow', 'live')),
    platform        platform,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    decisions_count INTEGER,
    applied_count   INTEGER,
    status          TEXT     NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'done', 'degraded', 'skipped', 'failed')),
    notes           TEXT       -- motivo del skip/degradado, p.ej. 'falta config harvest'
);

COMMENT ON TABLE optimizer_cycle IS
  'Mutable solo para CERRAR la corrida (UPDATE acotado por columna: '
  'finished_at/contadores/status/notes), igual que ingest_run: nace abierta '
  'en running. Las guardas de ciclo (frescura, completitud, plataforma '
  'saltada) dejan aquí su motivo: un ciclo que no decidió nada también es un '
  'resultado auditable. Orbit NO tiene tabla system_alerts (era del repo '
  'viejo): el sustituto por ahora es status=degraded + notes, y el digest '
  'diario de la Fase 3.';

-- Caps diarios por motor. La RESERVA para PAUSE (que un cap lleno nunca deje
-- una hemorragia sin pausar) la maneja la app al consumir quota; aquí solo
-- vive el contador, para que el tope sobreviva a un redeploy.
CREATE TABLE apply_quota_state (
    motor      TEXT     NOT NULL,
    quota_date DATE     NOT NULL,
    used       INTEGER  NOT NULL DEFAULT 0 CHECK (used >= 0),
    cap        INTEGER  NOT NULL CHECK (cap > 0),
    CONSTRAINT quota_no_excedida CHECK (used <= cap),
    PRIMARY KEY (motor, quota_date)
);

COMMENT ON TABLE apply_quota_state IS
  'Live automático CON TOPES: el cap diario de applies por motor. El consumo '
  'es INSERT ON CONFLICT ... DO UPDATE ... WHERE used < cap (atómico, en la '
  'app) — el CHECK quota_no_excedida es un backstop: impone que el ORDEN '
  'importe en la app (consumir SIEMPRE con WHERE used < cap, nunca UPDATE '
  'ciego). El motor solo puede UPDATE (used): cap y quota_date se fijan en el '
  'INSERT — EL TOPE DE SEGURIDAD NO PUEDE ANULARSE A SÍ MISMO (si app_decide '
  'pudiera subir cap, el candado sería decorativo). "used nunca decrece" NO es '
  'enforceable por CHECK (compararía contra el valor viejo del UPDATE): queda '
  'cubierto por el patrón de consumo atómico + la auditoría de decision '
  '(cada apply tiene su fila append-only). Caps bajos el día 1 del cutover; '
  'subir el tope es un INSERT de admin, no una decisión del motor.';


-- =============================================================================
--  12. DECISIONES  —  y la separación entre DECIDIR y APLICAR
-- =============================================================================

CREATE TABLE decision (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle_id          BIGINT        NOT NULL REFERENCES optimizer_cycle(id),
    ad_entity_id      BIGINT        NOT NULL REFERENCES ad_entity(id),
    kind              decision_kind NOT NULL,
    decided_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    config_version_id BIGINT        NOT NULL REFERENCES config_version(id),

    -- Regla 5 y 6: la EDAD del dato con que se decidió, explícita.
    data_observed_at  TIMESTAMPTZ   NOT NULL,

    -- Ventana de métricas con que se decidió (días calendario, inclusive).
    -- Sin estas columnas la regla de madurez no se puede cumplir NI auditar:
    -- el CHECK viejo comparaba decided_at contra UN timestamp y no decía
    -- nada sobre la ventana real de 30d que usó el motor.
    window_start      DATE          NOT NULL,
    window_end        DATE          NOT NULL,

    -- Identidad del término para decisiones sobre search terms: negative y
    -- harvest deciden sobre (entidad, término), no solo sobre la entidad.
    search_term       TEXT,

    old_value         NUMERIC,
    new_value         NUMERIC,
    value_currency    currency,
    inputs            JSONB         NOT NULL,   -- los insumos exactos, para reproducir

    CONSTRAINT decision_ventana_coherente CHECK (window_start <= window_end),
    CONSTRAINT decision_termino_coherente CHECK (
        (kind IN ('negative', 'harvest') AND search_term IS NOT NULL)
        OR (kind IN ('bid', 'budget', 'pause', 'resume') AND search_term IS NULL)
    ),
    CONSTRAINT decision_valor_con_moneda
        CHECK ((kind NOT IN ('bid','budget')) OR value_currency IS NOT NULL),
    -- timestamptz contra timestamptz: comparación INMUTABLE, CHECK válido.
    CONSTRAINT decision_dato_no_del_futuro
        CHECK (data_observed_at <= decided_at)
);

-- "Una decisión por entidad por ciclo", hecho cumplir por la base: los kinds
-- de entidad chocan por (ciclo, entidad); los de término por (ciclo, entidad,
-- término) — la misma entidad puede generar varios negative/harvest en un
-- ciclo, uno por término, pero nunca dos sobre el mismo término.
CREATE UNIQUE INDEX decision_unica_entidad_ciclo
    ON decision (cycle_id, ad_entity_id)
    WHERE kind IN ('bid', 'budget', 'pause', 'resume');
CREATE UNIQUE INDEX decision_unica_termino_ciclo
    ON decision (cycle_id, ad_entity_id, search_term)
    WHERE kind IN ('negative', 'harvest');

CREATE INDEX ON decision (ad_entity_id, decided_at DESC);
CREATE INDEX ON decision (cycle_id);

COMMENT ON TABLE decision IS
  'Append-only por trigger. Una decisión por entidad por ciclo (únicos '
  'parciales arriba; el envelope optimizer_cycle cuenta decisions_count). '
  'Reemplaza a ads_optimizer_audit del diseño v2: aquí auditar no es una '
  'tabla aparte del motor, ES la tabla del motor.';

COMMENT ON COLUMN decision.window_end IS
  'Regla 6: cortar (pause/negative/harvest) exige window_end <= '
  'decided_at - 10 días. NO vive en un CHECK: PostgreSQL acepta casts STABLE '
  '(timestamptz::date) en CHECKs y los evalúa según la TimeZone de cada '
  'sesión — el mismo CHECK pasaría en una y fallaría en otra. Lo hace cumplir '
  'el trigger decision_madurez_corte con UTC fijado en la expresión.';

COMMENT ON CONSTRAINT decision_dato_no_del_futuro ON decision IS
  'Decidir con dato observado DESPUÉS de la decisión es lookahead: el mismo '
  'vicio que invalidó los backtests del sistema viejo, aquí imposible por '
  'schema.';

-- Regla 6 hecha cumplir por la base: cortar exige >= 10 días de maduración.
-- Es TRIGGER y no CHECK porque decided_at::date es STABLE y un CHECK lo
-- evaluaría según la TimeZone de cada sesión (PostgreSQL acepta expresiones
-- no inmutables en CHECKs sin avisar). La defensa real es fijar UTC EN LA
-- EXPRESIÓN (AT TIME ZONE 'UTC'): el trigger queda inmune al TimeZone de la
-- sesión; que la app corra con TZ UTC es una segunda capa, no la defensa.
CREATE FUNCTION decision_madurez_corte() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_fecha_decision DATE;
BEGIN
    v_fecha_decision := (NEW.decided_at AT TIME ZONE 'UTC')::date;
    IF NEW.kind IN ('pause', 'negative', 'harvest')
       AND NEW.window_end > (v_fecha_decision - 10) THEN
        RAISE EXCEPTION
            'decision %: cortar exige datos con 10+ dias de maduracion '
            '(window_end % debe ser <= %). Curva medida: dia 0 = 8.7%% falsos '
            'cortes, dia 1 = 2.4%%, dia 5 = 0.53%%, dia 10 = 0.00%%.',
            NEW.kind, NEW.window_end, (v_fecha_decision - 10)
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION decision_madurez_corte IS
  'Trigger y no CHECK porque decided_at::date depende de la TimeZone de '
  'sesión y PostgreSQL evalúa los CHECKs no inmutables por sesión (los acepta '
  'en silencio): el invariante sería impredecible. UTC va fijado en la '
  'expresión (AT TIME ZONE ''UTC''), así que ni siquiera una sesión con otra '
  'TZ lo cambia. Aplica a pause, negative Y harvest: la primera fase del '
  'harvest es crear el negativo exacto en el origen — un corte (regla 6 dice '
  '"cortar/pausar"). Pausar es irreversible en la práctica: una entidad '
  'pausada ya no genera la señal que la revertiría.';

CREATE TRIGGER decision_madurez_corte
    BEFORE INSERT ON decision
    FOR EACH ROW EXECUTE FUNCTION decision_madurez_corte();

-- Tracking de harvest con fases. La creación de keyword en la campaña manual
-- NO es idempotente en la API de Amazon: la fila se registra en 'pending'
-- ANTES del primer POST (así un crash no deja ventana de duplicación: el
-- único en-vuelo ya la bloquea) y avanza negative_created -> exact_created ->
-- done / failed. Si un POST queda en vuelo, la reconciliación AL INICIO DEL
-- SIGUIENTE CICLO (la hace la app contra /sp/keywords/list) decide si
-- reintentar o cerrar como failed.
CREATE TABLE harvest_job (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id  BIGINT   NOT NULL REFERENCES decision(id),  -- rastro ciclo->harvest
    search_term  TEXT     NOT NULL,
    platform     platform NOT NULL,
    ad_entity_id BIGINT   NOT NULL REFERENCES ad_entity(id),  -- origen del término
    fase         TEXT     NOT NULL
                 CHECK (fase IN ('pending', 'negative_created', 'exact_created',
                                 'done', 'failed')),
    external_ids JSONB,   -- ids que la API fue devolviendo (negative id, keyword id)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Un solo job EN VUELO por (plataforma, entidad, término): protege el POST
-- no idempotente — sin esto, un ciclo que reintenta a ciegas crearía el
-- negativo o la keyword dos veces. 'pending' cuenta como en-vuelo desde antes
-- del primer POST. Los jobs cerrados (done/failed) no bloquean: un harvest
-- nuevo sobre el mismo término es otra decisión.
CREATE UNIQUE INDEX harvest_job_en_vuelo
    ON harvest_job (platform, ad_entity_id, search_term)
    WHERE fase IN ('pending', 'negative_created', 'exact_created');

-- El job tiene que corresponder a una decisión de HARVEST sobre el mismo
-- (entidad, término, plataforma): sin este trigger, un typo en la app crea un
-- job huerfano que la reconciliación perseguiría contra Amazon en vano.
CREATE FUNCTION harvest_job_decision_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_kind     decision_kind;
    v_entity   BIGINT;
    v_term     TEXT;
    v_platform platform;
BEGIN
    -- La fila SIEMPRE nace en 'pending', ANTES del primer POST a Amazon
    -- (fail-closed ante crash: un job que no nace en pending es un bug de
    -- app, no un caso legítimo). Las fases posteriores solo se alcanzan por
    -- UPDATE, que este trigger no interfiere.
    IF NEW.fase <> 'pending' THEN
        RAISE EXCEPTION
            'harvest_job: la fila debe nacer en fase pending (se registra '
            'ANTES del primer POST a Amazon); las fases posteriores solo se '
            'alcanzan por UPDATE. Se recibio fase %', NEW.fase
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT d.kind, d.ad_entity_id, d.search_term, e.platform
      INTO v_kind, v_entity, v_term, v_platform
      FROM decision d
      JOIN ad_entity e ON e.id = d.ad_entity_id
     WHERE d.id = NEW.decision_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'harvest_job: decision_id % no existe', NEW.decision_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF v_kind <> 'harvest'
       OR v_entity <> NEW.ad_entity_id
       OR v_term IS DISTINCT FROM NEW.search_term
       OR v_platform <> NEW.platform THEN
        RAISE EXCEPTION
            'harvest_job: no corresponde a la decision % (kind=%, entidad=%, '
            'termino=%, plataforma=%)',
            NEW.decision_id, v_kind, v_entity, v_term, v_platform
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER harvest_job_decision_coherente
    BEFORE INSERT ON harvest_job
    FOR EACH ROW EXECUTE FUNCTION harvest_job_decision_coherente();

COMMENT ON TABLE harvest_job IS
  'POSTs de creación no-idempotentes fail-closed: nunca se reintenta a ciegas. '
  'La fila nace SIEMPRE en pending ANTES de tocar Amazon — la base lo exige '
  '(trigger harvest_job_decision_coherente: un INSERT en otra fase es un bug '
  'de app; las fases posteriores solo se alcanzan por UPDATE); external_ids '
  'acumula lo confirmado por la API; la reconciliación la hace la app al '
  'inicio de ciclo (regla: conciliar contra la fuente externa, no contra la '
  'propia consistencia interna). decision_id liga el job a la decisión de '
  'harvest que lo originó (validado por trigger): el rastro '
  'ciclo->decisión->job queda completo.';

CREATE TABLE decision_application (
    decision_id    BIGINT PRIMARY KEY REFERENCES decision(id),
    attempted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at   TIMESTAMPTZ,
    verify_ok      BOOLEAN,        -- NULL = en vuelo; se sella en el readback
    platform_ack   JSONB,
    error          TEXT,
    -- Un intento cerrado tiene que decir si funcionó o falló. Mientras
    -- confirmed_at y error sean ambos NULL, el intento sigue "en vuelo";
    -- el chequeo de intentos viejos sin resolver es un invariante diario,
    -- no un CHECK (no se puede usar now() en un CHECK de forma confiable:
    -- es STABLE y la evaluación dependería de cuándo corre la sesión).
    CONSTRAINT application_no_ambas
        CHECK (NOT (confirmed_at IS NOT NULL AND error IS NOT NULL)),
    -- Confirmado sin ack es "confíen en mí": la confirmación ES el readback
    -- de la plataforma, y el readback se guarda.
    CONSTRAINT application_confirmacion_con_ack
        CHECK (confirmed_at IS NULL OR platform_ack IS NOT NULL),
    -- Confirmado implica veredicto sellado: la terna
    -- confirmed_at + platform_ack + verify_ok se escribe JUNTA en el readback.
    CONSTRAINT application_confirmacion_con_veredicto
        CHECK (confirmed_at IS NULL OR verify_ok IS NOT NULL),
    -- Y la inversa: veredicto sin confirmación es un estado espurio que
    -- enfriaría una entidad sin readback (el cooldown consulta verify_ok
    -- IS TRUE directamente). La terna se sella JUNTA o no se sella.
    CONSTRAINT application_veredicto_requiere_confirmacion
        CHECK (verify_ok IS NULL OR confirmed_at IS NOT NULL)
);

COMMENT ON TABLE decision_application IS
  'REGLA SELLADA MÁS CARA DEL SISTEMA VIEJO: applied=1 en el audit NO '
  'significaba que la plataforma lo hubiera aceptado. Aquí decidir y aplicar '
  'son tablas distintas, y aplicar sólo cuenta con confirmación de la '
  'plataforma (confirmed_at + platform_ack + verify_ok, escritas juntas en el '
  'readback). Patrón: INSERT+COMMIT -> HTTP -> readback -> UPDATE. Por eso '
  'esta tabla ES mutable (excepción deliberada): el readback pisa el intento, '
  'nunca la decisión.';

COMMENT ON COLUMN decision_application.verify_ok IS
  'Resultado sellado del readback: TRUE = la plataforma tiene lo que pedimos, '
  'FALSE = divergencia (se reintenta), NULL = en vuelo. El cooldown de 7d '
  'consulta verify_ok directamente (solo enfrían applies live con '
  'verify_ok IS TRUE): NUNCA se parsea platform_ack para decidir cooldown — '
  'el ack es evidencia, no señal de control.';


-- =============================================================================
--  13. CONCILIACIÓN EXTERNA  —  Regla 10, la que termina la regresión
-- =============================================================================

CREATE TABLE external_reconciliation (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform        platform     NOT NULL,
    period_start    DATE         NOT NULL,
    period_end      DATE         NOT NULL,
    source          TEXT         NOT NULL,  -- 'amazon_settlement_report'
    external_total  money_amount NOT NULL,
    internal_total  money_amount NOT NULL,
    total_currency  currency     NOT NULL,
    difference      money_amount GENERATED ALWAYS AS (external_total - internal_total) STORED,
    checked_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- La vigente por período/fuente es la de mayor checked_at: re-conciliar un
-- período corregido es una FILA NUEVA, no un UPDATE (append-only con
-- historia, como todo lo que registra hechos en este esquema).
CREATE INDEX ON external_reconciliation
    (platform, period_start, period_end, source, checked_at DESC);

COMMENT ON TABLE external_reconciliation IS
  'Regla 10. Los 12 invariantes del sistema viejo verificaban que el sistema no '
  'se contradijera A SÍ MISMO; nunca se conciliaron los números contra los '
  'reportes de liquidación de Amazon. Esta tabla es el único chequeo que '
  'termina la regresión infinita de revisiones, porque no depende del criterio '
  'de nadie: cuadra o no cuadra. Es APPEND-ONLY (trigger prohibir_mutacion) y '
  'sin UNIQUE por período a propósito: si el ledger se corrige y el período se '
  're-concilia, la verificación anterior es HISTORIA que no se pisa — la '
  'diferencia que desaparece entre dos corridas es la prueba de que la '
  'corrección funcionó.';


-- =============================================================================
--  14. VISTAS  —  lo que la aplicación consume. Nadie lee las tablas crudas.
-- =============================================================================

-- La última observación de cada (entidad, fecha). Para operar HOY.
CREATE VIEW v_metric_latest AS
SELECT DISTINCT ON (ad_entity_id, metric_date) *
  FROM ads_metric_observation
 ORDER BY ad_entity_id, metric_date, observed_at DESC;

-- Sólo lo maduro. El costo cierra a D+15 en ambas plataformas. El corte usa
-- UTC fijado en la expresión: CURRENT_DATE dependería de la TimeZone de cada
-- sesión (misma clase de problema que llevó la madurez a un trigger con UTC).
CREATE VIEW v_metric_mature AS
SELECT * FROM v_metric_latest
 WHERE metric_date <= (now() AT TIME ZONE 'UTC')::date - 15;

COMMENT ON VIEW v_metric_mature IS
  'Para ANÁLISIS ECONÓMICO (márgenes, TACoS), no para el ciclo de decisión: '
  'los bids leen v_metric_latest con guarda de frescura (ventana termina en '
  'max(metric_date) - 3d, spec v2) y los cortes exigen madurez D-10 por '
  'trigger. El día en curso muestra 19.9% del costo y 11.5% del revenue '
  'finales: no se pondera, se descarta. Y el costo madura HACIA ABAJO '
  '(clawback de clicks inválidos), así que con datos frescos el numerador '
  'está inflado ~7% y el denominador desinflado ~30% AL MISMO TIEMPO: un ACoS '
  'de día 1 sale ~1.5x peor que el real. '
  'El corte D-15 usa (now() AT TIME ZONE ''UTC'')::date, no CURRENT_DATE: la '
  'defensa real es UTC fijado en la expresión (coherente con el trigger '
  'decision_madurez_corte), no la TimeZone de la sesión.';

-- Backtest honesto: qué se veía en un momento dado.
CREATE FUNCTION metrics_as_of(p_as_of TIMESTAMPTZ)
RETURNS SETOF ads_metric_observation
LANGUAGE sql STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT DISTINCT ON (ad_entity_id, metric_date) *
      FROM ads_metric_observation
     WHERE observed_at <= p_as_of
     ORDER BY ad_entity_id, metric_date, observed_at DESC;
$$;

COMMENT ON FUNCTION metrics_as_of IS
  'La función que hace posible un backtest honesto: devuelve lo que el sistema '
  'PODÍA VER en ese instante, no lo que sabemos hoy. Sin esto, todo backtest '
  'se auto-engaña y sale espectacular.';

-- Margen de contribución por plataforma Y MONEDA (un SUM mezclando monedas
-- debe ser imposible por schema: regla 4). Sin COGS no hay margen: y el COGS
-- solo cuenta cuando está CUBIERTO — nunca un margen que esconde huecos.
CREATE VIEW v_margen_plataforma AS
WITH ventas AS (
    -- COGS por línea de venta: costo vigente A LA FECHA de la venta
    -- (el EXCLUDE de sku_cost garantiza a lo más uno), convertido a la moneda
    -- de la venta con la tasa de ESE día. Sin tasa utilizable, la línea
    -- queda con cogs NULL: NO entra al cogs_conocido y baja la cobertura.
    SELECT l.platform,
           l.amount_currency,
           CASE
               WHEN c.id IS NULL THEN NULL
               WHEN c.cost_currency = l.amount_currency
                   THEN c.cost_amount * l.quantity
               ELSE c.cost_amount * l.quantity * fx.rate
           END AS cogs
      FROM ledger_event l
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(l.event_date, c.cost_currency, l.amount_currency) r
      ) fx ON c.id IS NOT NULL AND c.cost_currency <> l.amount_currency
     WHERE l.kind = 'sale'
), cogs AS (
    SELECT platform,
           amount_currency,
           COUNT(*)                              AS ventas_totales,
           COUNT(cogs)                           AS ventas_con_costo,
           SUM(cogs)                             AS cogs_conocido
      FROM ventas
     GROUP BY 1, 2
), venta AS (
    -- VENTA TOTAL: TODAS las ventas, con o sin order_id. La versión anterior
    -- solo sumaba las de order_id IS NOT NULL y, con cobertura 100%, el
    -- margen restaba el COGS de ventas que no estaban en `venta`. La venta
    -- total no necesita supuesto de atribución; la separación
    -- atribuible/no atribuible se aplica a los CARGOS, no a la venta.
    SELECT platform,
           amount_currency,
           SUM(amount) AS venta
      FROM ledger_event
     WHERE kind = 'sale'
     GROUP BY platform, amount_currency
), por_orden AS (
    SELECT platform,
           amount_currency,
           SUM(amount) FILTER (WHERE kind IN ('fee','refund','withholding')) AS cargos_con_orden
      FROM ledger_event
     WHERE order_id IS NOT NULL
     GROUP BY platform, amount_currency
), sin_orden AS (
    SELECT platform, amount_currency, SUM(amount) AS cargos_sin_orden
      FROM ledger_event
     WHERE order_id IS NULL
       -- Solo CARGOS sin orden: una venta sin order_id (el dominio dice que
       -- no existe) inflaría esta columna con signo positivo; si algún día
       -- aparece, no se mezcla con los cargos — queda fuera de ambas
       -- columnas y se detecta en la conciliación externa.
       AND kind IN ('fee', 'refund', 'withholding')
     GROUP BY platform, amount_currency
)
SELECT COALESCE(v.platform, o.platform, s.platform, g.platform)   AS platform,
       COALESCE(v.amount_currency, o.amount_currency,
                s.amount_currency, g.amount_currency)             AS amount_currency,
       v.venta,
       o.cargos_con_orden,
       COALESCE(s.cargos_sin_orden, 0) AS cargos_sin_orden,
       g.cogs_conocido,
       ROUND(100.0 * g.ventas_con_costo / NULLIF(g.ventas_totales, 0), 2)
           AS cobertura_cogs_pct,
       -- El margen de contribución SOLO existe con cobertura 100%: con un
       -- solo hueco de costo, el número sería mentira con formato.
       CASE
           WHEN g.ventas_totales > 0
                AND g.ventas_con_costo = g.ventas_totales
               THEN COALESCE(v.venta, 0) + COALESCE(o.cargos_con_orden, 0)
                    + COALESCE(s.cargos_sin_orden, 0)
                    - COALESCE(g.cogs_conocido, 0)
           ELSE NULL
       END AS margen_contribucion
  FROM venta v
  FULL OUTER JOIN por_orden o
    ON o.platform = v.platform AND o.amount_currency = v.amount_currency
  FULL OUTER JOIN sin_orden s
    ON s.platform = COALESCE(v.platform, o.platform)
   AND s.amount_currency = COALESCE(v.amount_currency, o.amount_currency)
  FULL OUTER JOIN cogs g
    ON g.platform = COALESCE(v.platform, o.platform, s.platform)
   AND g.amount_currency = COALESCE(v.amount_currency, o.amount_currency,
                                    s.amount_currency);

COMMENT ON VIEW v_margen_plataforma IS
  'Agrupa por (platform, amount_currency): la primera versión de esta vista '
  'agrupaba solo por plataforma y SUMaba MXN con USD — el bug de 18.66x '
  'reimplementado. '
  'venta es la VENTA TOTAL: todas las ventas, con o sin order_id, sin '
  'supuesto de atribución — la separación atribuible/no atribuible se aplica '
  'a los cargos (cargos_con_orden/cargos_sin_orden), no a la venta. '
  'INCLUYE COGS (costo vigente a event_date * quantity): sin costo no es '
  'margen de contribución, es facturación menos cargos. Pero el COGS solo '
  'entra con COBERTURA visible: cobertura_cogs_pct dice qué fracción de las '
  'ventas tiene costo conocido, y margen_contribucion es NULL salvo cobertura '
  '100% — el error histórico es el 49% del COGS de MeLi en CERO disfrazado de '
  'dato (pasó tres auditorías como "limpio"): aquí el hueco no se disfraza de '
  'margen bueno, se declara. '
  'SIN dimensión temporal A PROPÓSITO: es historia completa; el análisis por '
  'período filtra event_date en la query contra ledger_event, no contra esta '
  'vista. '
  'Expone cargos_sin_orden siempre: el ISR (que llega sin order_id) nunca se '
  'descarta en silencio. '
  'OJO: cargos_sin_orden = 0 puede significar "no llegó" (el ISR no se '
  'ingirió), no "no hubo" — sin fuente externa la ausencia es indistinguible '
  'del cero. Lo atrapa external_reconciliation contra los settlement reports, '
  'no esta vista.';

-- TACoS por PLATAFORMA: la única medida de si el gasto de ads gana, SIN
-- suposiciones de atribución. No se puede agrupar por moneda solamente:
-- amazon_us gasta en USD pero sus ventas llegan en MXN, y amazon_mx + meli
-- comparten MXN — por moneda los mezclaba.
CREATE VIEW v_tacos AS
WITH gasto AS (
    -- Gasto maduro (corte D-15 de v_metric_mature, UTC fijado), convertido
    -- POR FILA a la moneda canónica (MXN). El gasto ya está sellado por
    -- trigger (amazon_us -> USD, amazon_mx/meli -> MXN), pero convertir por
    -- fila elimina cualquier supuesto de moneda única por mes.
    SELECT e.platform                              AS platform,
           date_trunc('month', m.metric_date)::date AS mes,
           SUM(CASE
                   WHEN m.metric_currency = 'MXN'::currency THEN m.cost
                   ELSE m.cost * fx.rate
               END)                                AS gasto_ads
      FROM v_metric_mature m
      JOIN ad_entity e ON e.id = m.ad_entity_id
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(m.metric_date, m.metric_currency, 'MXN'::currency) r
      ) fx ON m.metric_currency <> 'MXN'::currency
     GROUP BY 1, 2
), venta AS (
    -- VENTANA SIMÉTRICA con el gasto: corte D-15 UTC con la MISMA expresión
    -- que v_metric_mature (el día en curso no cuenta: sin esto, el mes en
    -- curso salía con TACoS sistemáticamente bajo). TODAS las ventas, con o
    -- sin order_id, convertidas por fila a MXN.
    SELECT l.platform,
           date_trunc('month', l.event_date)::date AS mes,
           SUM(CASE
                   WHEN l.amount_currency = 'MXN'::currency THEN l.amount
                   ELSE l.amount * fx.rate
               END)                                AS venta_total
      FROM ledger_event l
      LEFT JOIN LATERAL (
           SELECT r.rate
             FROM fx_resolve(l.event_date, l.amount_currency, 'MXN'::currency) r
      ) fx ON l.amount_currency <> 'MXN'::currency
     WHERE l.kind = 'sale'
       AND l.event_date <= (now() AT TIME ZONE 'UTC')::date - 15
     GROUP BY 1, 2
)
SELECT COALESCE(g.platform, v.platform) AS platform,
       COALESCE(g.mes,    v.mes)        AS mes,
       g.gasto_ads,
       v.venta_total,
       -- Ambos lados ya están en MXN: si un mes tuviera dos monedas de venta,
       -- ambas se convirtieron y se SUMARON — el gasto no se repite por fila.
       -- Sin tasa utilizable, ese monto queda FUERA del agregado (SUM ignora
       -- NULL); si un lado entero queda sin datos, tacos_pct es NULL:
       -- fail-loud, NUNCA un número inventado (regla 3).
       CASE
           WHEN g.gasto_ads IS NULL OR NULLIF(v.venta_total, 0) IS NULL THEN NULL
           ELSE ROUND(100 * g.gasto_ads / v.venta_total, 2)
       END AS tacos_pct
  FROM gasto g
  FULL OUTER JOIN venta v
    ON v.platform = g.platform AND v.mes = g.mes;

COMMENT ON VIEW v_tacos IS
  'TACoS POR PLATAFORMA (no por moneda): la medición que estuvo disponible '
  'todo el año y nunca se hizo. No necesita atribución, ni halo, ni margen '
  'per-SKU: gasto de ads sobre venta total de la plataforma. '
  'VENTANAS SIMÉTRICAS: ambos lados cortan en D-15 UTC (el gasto vía '
  'v_metric_mature; la venta con la misma expresión) — antes la venta tomaba '
  'TODAS las ventas del mes y el mes en curso salía con TACoS '
  'sistemáticamente bajo (optimista: todo se ve rentable). '
  'SIN SUPUESTO DE MONEDA ÚNICA: cada fila se convierte a la canónica MXN con '
  'fx_resolve (exacta o nearest_prior <= 7d) y el JOIN es por (platform, mes) '
  'sobre montos ya en MXN — si un mes tuviera dos monedas de venta, ambas se '
  'convierten y se SUMAN en vez de duplicar el gasto por fila. Sin tasa, ese '
  'monto queda fuera del agregado y tacos_pct es NULL si falta cualquier '
  'lado: fail-loud, nunca un número inventado (regla 3). Meta declarada del '
  'dueño: 8-12%.';


-- =============================================================================
--  15. ROLES  —  que el que lee no pueda escribir, y que el que escribe
--      no pueda pisar la historia
-- =============================================================================

CREATE ROLE app_ingest;          -- los sincronizadores
CREATE ROLE app_decide;          -- los motores
CREATE ROLE app_read;            -- dashboard, análisis, agentes externos
CREATE ROLE app_admin NOLOGIN;   -- config humana: /goals, config_version

GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_ingest, app_decide, app_admin;

-- INGESTA: inserta en tablas de hechos; UPDATE genérico solo en cache
-- (ad_entity_state) o catálogo (product/listing/ad_entity corrigen
-- nombre/estado). Los cierres son por COLUMNA: ingest_run solo se sella;
-- sku_cost solo cierra vigencias (valid_to) — reescribir cost_amount o
-- valid_from de una fila publicada, jamás (la corrección es una fila nueva).
GRANT INSERT ON ingest_run, product, listing, sku_cost, ad_entity,
    ads_metric_observation, search_term_observation, ledger_event, fx_rate,
    external_reconciliation
    TO app_ingest;
GRANT UPDATE (finished_at, rows_written, rows_skipped, skip_reason, ok)
    ON ingest_run TO app_ingest;
GRANT UPDATE (valid_to) ON sku_cost TO app_ingest;
GRANT UPDATE ON product, listing TO app_ingest;
-- ad_entity SOLO por columnas: platform, kind, external_id, parent_id,
-- match_type y keyword_text se fijan en el INSERT y son INMUTABLES por
-- permisos — mutarlos tras insertar hechos rompería el sello de moneda a
-- posteriori y dejaría goals apuntando a kinds que ya no son campaign. Si la
-- ingesta necesita corregir una entidad, crea una nueva y desactiva la vieja.
GRANT UPDATE (name, listing_id) ON ad_entity TO app_ingest;
GRANT INSERT, UPDATE ON ad_entity_state TO app_ingest;

-- MOTORES: escriben decisiones y su ciclo de vida de apply. El readback de
-- decision_application solo sella su resultado por COLUMNA (confirmed_at,
-- verify_ok, platform_ack, error): attempted_at y decision_id son inmutables
-- por permisos. Igual harvest_job: solo avanza de fase. optimizer_cycle solo
-- se sella; apply_quota_state solo consume (used).
-- OJO con el cap diario: la fila del día la INSERTA el motor copiando el cap
-- desde config_version (que escribe app_admin). UPDATE solo toca `used`, así
-- que el cap no se puede subir DESPUÉS de creada la fila — pero su valor
-- inicial descansa en la config, no en un permiso. Declarado, no escondido.
-- Los motores NO escriben goals ni config_version: la escalera off->shadow->live
-- es decisión humana (app_admin).
GRANT INSERT ON decision, decision_application,
    optimizer_cycle, harvest_job, apply_quota_state
    TO app_decide;
GRANT UPDATE (confirmed_at, verify_ok, platform_ack, error)
    ON decision_application TO app_decide;
GRANT UPDATE (fase, external_ids, updated_at) ON harvest_job TO app_decide;
GRANT UPDATE (used) ON apply_quota_state TO app_decide;
GRANT UPDATE (finished_at, decisions_count, applied_count, status, notes)
    ON optimizer_cycle TO app_decide;
GRANT INSERT, UPDATE, DELETE ON ads_optimizer_lock TO app_decide;

-- ADMIN (NOLOGIN, lo usa el servicio de endpoints de administración):
-- goals y snapshots de config. /goals corre como app_admin.
GRANT INSERT, UPDATE ON ads_optimizer_goal TO app_admin;
GRANT INSERT ON config_version TO app_admin;
-- Fijar caps manualmente (y la fila del día, en general) es decisión de
-- admin, no del motor: el INSERT es compartido con app_decide, que solo
-- puede tocar `used` después (UPDATE por columna).
GRANT INSERT ON apply_quota_state TO app_admin;

-- Las identity columns no necesitan grant de secuencia por separado, pero lo
-- dejamos explícito para que ningún refactor a columnas serial rompa permisos
-- en silencio.
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_ingest, app_decide, app_admin;

-- Que ningún rol futuro herede permisos por accidente.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO app_read, app_ingest, app_decide, app_admin;


-- =============================================================================
--  16. EL CANDADO DE VERDAD  —  append-only a nivel de motor
-- =============================================================================

-- Los GRANT de arriba por sí solos son insuficientes: el candado descansaría
-- en la AUSENCIA de un GRANT y un `GRANT ALL` futuro lo derrotaría en
-- silencio. Este trigger no depende de permisos: bloquea el UPDATE y el
-- DELETE aunque el rol los tenga.
CREATE FUNCTION prohibir_mutacion() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION
        'La tabla % es APPEND-ONLY: corregir es INSERTAR una observación '
        'nueva, no pisar la anterior. Ese UPSERT in-place es el bug que '
        'invalidó todos los backtests del sistema anterior.', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER metric_append_only
    BEFORE UPDATE OR DELETE ON ads_metric_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER st_metric_append_only
    BEFORE UPDATE OR DELETE ON search_term_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER ledger_append_only
    BEFORE UPDATE OR DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER config_append_only
    BEFORE UPDATE OR DELETE ON config_version
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER decision_append_only
    BEFORE UPDATE OR DELETE ON decision
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER fx_append_only
    BEFORE UPDATE OR DELETE ON fx_rate
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER reconciliacion_append_only
    BEFORE UPDATE OR DELETE ON external_reconciliation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON FUNCTION prohibir_mutacion IS
  'Regla 5, hecha cumplir a nivel de motor. La diferencia entre una regla '
  'escrita en un documento —que el repo viejo YA TENÍA y no sirvió de nada— y '
  'una que la base hace cumplir aunque el que escriba tenga todos los '
  'permisos. Excepciones deliberadas (documentadas en su tabla): ingest_run, '
  'optimizer_cycle, decision_application, ad_entity_state, ads_optimizer_goal, '
  'ads_optimizer_lock, harvest_job, apply_quota_state, catálogo.';

COMMIT;
