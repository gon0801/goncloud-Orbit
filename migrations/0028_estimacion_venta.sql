-- ---------------------------------------------------------------------------
-- MARGEN ESTIMADO 01 A.1 — persistencia append-only de oferta, cotizacion,
-- politica versionada y escenario reproducible (acta 0.3 / spec S2-S5).
--
-- Universo inicial: FBA Amazon MX. FBM/US quedan expresables como incompletos
-- sin convertir ausencias en cero. Correccion = nueva observacion.
--
-- Es expansiva: CREATE TYPE/TABLE/TRIGGER/FUNCTION/GRANT. No re-runnable.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TYPE estimacion_canal AS ENUM ('fba', 'fbm');

CREATE TYPE estimacion_estado AS ENUM (
    'disponible', 'incompleta', 'desactualizada', 'identidad_ambigua'
);

CREATE TYPE estimacion_fee_estado AS ENUM ('success', 'error');

-- Politica fiscal/normalizacion versionada (humana, app_admin). Sin filas
-- sembradas: la politica FBA MX sellada se expresa via settings JSONB.
CREATE TABLE estimacion_politica_version (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    label           TEXT NOT NULL,
    universo        TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    settings        JSONB NOT NULL,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    CONSTRAINT estimacion_politica_settings_objeto CHECK (
        jsonb_typeof(settings) = 'object'
    ),
    CONSTRAINT estimacion_politica_vigencia_coherente CHECK (
        valid_to IS NULL OR valid_to > valid_from
    )
);

COMMENT ON TABLE estimacion_politica_version IS
    'MARGEN ESTIMADO A.1: politica versionada append-only. Sin defaults de '
    'negocio en el esquema; settings documenta iva_divisor, isr_tasa, etc. '
    'valid_from/valid_to acotan vigencia calendario. La vigente en lectura '
    'as-of es la fila aplicable por universo (A.4).';

COMMENT ON COLUMN estimacion_politica_version.valid_from IS
    'MARGEN ESTIMADO A.1: inicio inclusive de vigencia (DATE calendario UTC).';
COMMENT ON COLUMN estimacion_politica_version.valid_to IS
    'Fin exclusive de vigencia; NULL = politica abierta.';

-- Snapshot de oferta/precio fechado desde bridge (acta 0.3).
CREATE TABLE estimacion_oferta_observation (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id          BIGINT NOT NULL REFERENCES listing (id),
    platform            platform NOT NULL,
    seller_sku          TEXT NOT NULL,
    asin                TEXT NOT NULL,
    canal               estimacion_canal NOT NULL,
    price_amount        money_amount NOT NULL,
    price_currency      currency NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    observed_at         TIMESTAMPTZ NOT NULL,
    source_event_id     TEXT NOT NULL,
    canonical_input     JSONB NOT NULL,
    context_fingerprint TEXT NOT NULL,
    ingest_run_id       BIGINT REFERENCES ingest_run (id),
    CONSTRAINT estimacion_oferta_precio_positivo CHECK (price_amount > 0),
    CONSTRAINT estimacion_oferta_listing_coherente FOREIGN KEY (platform, asin)
        REFERENCES listing (platform, external_id),
    CONSTRAINT estimacion_oferta_evento_unico UNIQUE (source_event_id),
    CONSTRAINT estimacion_oferta_contexto_unico UNIQUE (
        id, listing_id, platform, seller_sku, asin, canal,
        price_amount, price_currency, context_fingerprint
    ),
    CONSTRAINT estimacion_oferta_anti_duplicado UNIQUE (
        listing_id, canal, fetched_at, price_amount, price_currency, observed_at
    )
);

CREATE INDEX estimacion_oferta_por_listing
    ON estimacion_oferta_observation (listing_id, canal, fetched_at DESC, observed_at DESC);

COMMENT ON TABLE estimacion_oferta_observation IS
    'MARGEN ESTIMADO A.1 (Regla 5): oferta fechada append-only. Clave de '
    'negocio (marketplace, seller_sku, asin, canal, precio, fetched_at). '
    'Dedupe por source_event_id obligatorio: repetir el mismo evento fuente no '
    'rejuvenece; un cambio real es otra fila con otro evento.';

COMMENT ON COLUMN estimacion_oferta_observation.fetched_at IS
    'UTC del bridge (fecha fuente del precio). observed_at es captura Orbit.';
COMMENT ON COLUMN estimacion_oferta_observation.context_fingerprint IS
    'Huella estable de precio/canal para ligar cotizacion y escenario.';
COMMENT ON COLUMN estimacion_oferta_observation.source_event_id IS
    'NOT NULL: dedupe fuerte; NULL permitiria rejuvenecer repitiendo filas.';

-- Cotizacion Product Fees ligada al snapshot exacto de oferta.
CREATE TABLE estimacion_fee_observation (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    oferta_observation_id BIGINT NOT NULL,
    listing_id            BIGINT NOT NULL REFERENCES listing (id),
    platform              platform NOT NULL,
    seller_sku            TEXT NOT NULL,
    asin                  TEXT NOT NULL,
    canal                 estimacion_canal NOT NULL,
    quoted_price_amount   money_amount NOT NULL,
    quoted_price_currency currency NOT NULL,
    total_fees            money_amount,
    fee_details           JSONB NOT NULL DEFAULT '[]',
    fees_estimated_at     TIMESTAMPTZ,
    fetched_at            TIMESTAMPTZ NOT NULL,
    observed_at           TIMESTAMPTZ NOT NULL,
    estado                estimacion_fee_estado NOT NULL,
    error_code            TEXT,
    source_event_id       TEXT NOT NULL,
    canonical_input       JSONB NOT NULL,
    context_fingerprint   TEXT NOT NULL,
    ingest_run_id         BIGINT REFERENCES ingest_run (id),
    CONSTRAINT estimacion_fee_precio_positivo CHECK (quoted_price_amount > 0),
    CONSTRAINT estimacion_fee_success_exige_total CHECK (
        (estado = 'success' AND total_fees IS NOT NULL)
        OR (estado = 'error' AND total_fees IS NULL)
    ),
    CONSTRAINT estimacion_fee_error_exige_codigo CHECK (
        estado <> 'error' OR error_code IS NOT NULL
    ),
    CONSTRAINT estimacion_fee_evento_unico UNIQUE (source_event_id),
    CONSTRAINT estimacion_fee_contexto_unico UNIQUE (
        id, listing_id, platform, seller_sku, asin, canal,
        quoted_price_amount, quoted_price_currency, context_fingerprint
    ),
    CONSTRAINT estimacion_fee_casa_oferta FOREIGN KEY (
        oferta_observation_id, listing_id, platform, seller_sku, asin, canal,
        quoted_price_amount, quoted_price_currency, context_fingerprint
    ) REFERENCES estimacion_oferta_observation (
        id, listing_id, platform, seller_sku, asin, canal,
        price_amount, price_currency, context_fingerprint
    ),
    CONSTRAINT estimacion_fee_anti_duplicado UNIQUE (
        oferta_observation_id, observed_at, source_event_id
    )
);

CREATE INDEX estimacion_fee_por_oferta
    ON estimacion_fee_observation (oferta_observation_id, observed_at DESC);

COMMENT ON TABLE estimacion_fee_observation IS
    'MARGEN ESTIMADO A.1: cotizacion append-only por contexto de oferta. '
    'estado=error registra intentos fallidos aparte del ultimo success (A.3). '
    'total_fees NULL en error — regla 3, no cero inventado. FK compuesta '
    'obliga a casar listing, canal, precio y huella con la oferta exacta.';

-- Escenario calculado con referencias congeladas para reproducir as-of.
CREATE TABLE estimacion_escenario (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id            BIGINT NOT NULL REFERENCES listing (id),
    platform              platform NOT NULL,
    seller_sku            TEXT NOT NULL,
    asin                  TEXT NOT NULL,
    canal                 estimacion_canal NOT NULL,
    valoracion_date       DATE NOT NULL,
    observed_at           TIMESTAMPTZ NOT NULL,
    politica_version_id   BIGINT NOT NULL REFERENCES estimacion_politica_version (id),
    formula_version       TEXT NOT NULL,
    oferta_observation_id BIGINT REFERENCES estimacion_oferta_observation (id),
    fee_observation_id    BIGINT REFERENCES estimacion_fee_observation (id),
    sku_cost_id           BIGINT REFERENCES sku_cost (id),
    fx_rate_date          DATE,
    fx_base               currency,
    fx_quote              currency,
    fx_rate               NUMERIC(18, 8),
    fx_source             TEXT,
    moneda                currency,
    contribucion          money_amount,
    contribucion_pct      NUMERIC(8, 4),
    estado                estimacion_estado NOT NULL,
    motivos               JSONB NOT NULL DEFAULT '[]',
    componentes           JSONB NOT NULL DEFAULT '[]',
    exclusiones           JSONB NOT NULL DEFAULT '[]',
    canonical_input       JSONB NOT NULL,
    context_fingerprint   TEXT NOT NULL,
    CONSTRAINT estimacion_escenario_disponible_exige_total CHECK (
        estado <> 'disponible'
        OR (
            contribucion IS NOT NULL
            AND contribucion_pct IS NOT NULL
            AND moneda IS NOT NULL
        )
    ),
    CONSTRAINT estimacion_escenario_no_disponible_sin_total CHECK (
        estado = 'disponible'
        OR (
            contribucion IS NULL
            AND contribucion_pct IS NULL
            AND moneda IS NULL
        )
    ),
    CONSTRAINT estimacion_escenario_fx_coherente CHECK (
        (
            fx_rate IS NULL
            AND fx_rate_date IS NULL
            AND fx_base IS NULL
            AND fx_quote IS NULL
            AND fx_source IS NULL
        )
        OR (
            fx_rate IS NOT NULL
            AND fx_rate_date IS NOT NULL
            AND fx_base IS NOT NULL
            AND fx_quote IS NOT NULL
            AND fx_source IS NOT NULL
        )
    ),
    CONSTRAINT estimacion_escenario_anti_duplicado UNIQUE (
        listing_id,
        canal,
        valoracion_date,
        context_fingerprint,
        politica_version_id,
        observed_at
    )
);

CREATE INDEX estimacion_escenario_as_of
    ON estimacion_escenario (listing_id, canal, valoracion_date, observed_at DESC);

COMMENT ON TABLE estimacion_escenario IS
    'MARGEN ESTIMADO A.1: escenario append-only con insumos congelados. '
    'Consulta as-of: observed_at <= corte ORDER BY observed_at DESC. '
    'Recalcular no reutiliza costo/FX vigente hoy — guarda ids y valores usados. '
    'S5: solo disponible lleva contribucion/moneda; otros estados exigen NULL.';

-- Coherencia identidad: oferta debe casar listing_id con platform/asin/seller_sku.
CREATE FUNCTION estimacion_oferta_listing_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1
      FROM listing l
     WHERE l.id = NEW.listing_id
       AND l.platform = NEW.platform
       AND l.external_id = NEW.asin
       AND l.seller_sku IS NOT DISTINCT FROM NEW.seller_sku;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'estimacion_oferta: listing % no casa platform/asin/seller_sku',
            NEW.listing_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER estimacion_oferta_listing_coherente
    BEFORE INSERT ON estimacion_oferta_observation
    FOR EACH ROW EXECUTE FUNCTION estimacion_oferta_listing_coherente();

-- Fecha fuente futura: trigger UTC (no CHECK dependiente de sesion).
CREATE FUNCTION estimacion_fuente_no_futura() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.fetched_at > NEW.observed_at THEN
        RAISE EXCEPTION
            'estimacion: fetched_at (%) posterior a observed_at (%)',
            NEW.fetched_at, NEW.observed_at
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER estimacion_oferta_fuente_no_futura
    BEFORE INSERT ON estimacion_oferta_observation
    FOR EACH ROW EXECUTE FUNCTION estimacion_fuente_no_futura();

CREATE TRIGGER estimacion_fee_fuente_no_futura
    BEFORE INSERT ON estimacion_fee_observation
    FOR EACH ROW EXECUTE FUNCTION estimacion_fuente_no_futura();

-- Temporalidad fee: success exige fees_estimated_at acotado; oferta no posterior al fee.
CREATE FUNCTION estimacion_fee_temporal_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_oferta_fetched_at TIMESTAMPTZ;
    v_oferta_observed_at TIMESTAMPTZ;
BEGIN
    SELECT o.fetched_at, o.observed_at
      INTO v_oferta_fetched_at, v_oferta_observed_at
      FROM estimacion_oferta_observation o
     WHERE o.id = NEW.oferta_observation_id;

    IF v_oferta_observed_at > NEW.observed_at THEN
        RAISE EXCEPTION
            'estimacion_fee: oferta % observada despues del fee',
            NEW.oferta_observation_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.estado = 'success' THEN
        IF NEW.fees_estimated_at IS NULL THEN
            RAISE EXCEPTION
                'estimacion_fee: success exige fees_estimated_at'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.fees_estimated_at < v_oferta_fetched_at THEN
            RAISE EXCEPTION
                'estimacion_fee: fees_estimated_at (%) anterior a fetched_at oferta (%)',
                NEW.fees_estimated_at, v_oferta_fetched_at
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.fees_estimated_at > NEW.observed_at THEN
            RAISE EXCEPTION
                'estimacion_fee: fees_estimated_at (%) posterior a observed_at (%)',
                NEW.fees_estimated_at, NEW.observed_at
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER estimacion_fee_temporal_coherente
    BEFORE INSERT ON estimacion_fee_observation
    FOR EACH ROW EXECUTE FUNCTION estimacion_fee_temporal_coherente();

-- Escenario: insumos del mismo listing/producto y no observados despues del corte.
CREATE FUNCTION estimacion_escenario_referencias_coherentes() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_product_id BIGINT;
    v_oferta_observed_at TIMESTAMPTZ;
    v_fee_observed_at TIMESTAMPTZ;
    v_fee_oferta_id BIGINT;
    v_politica_created_at TIMESTAMPTZ;
    v_costo_product_id BIGINT;
    v_costo_started_at TIMESTAMPTZ;
BEGIN
    PERFORM 1
      FROM listing l
     WHERE l.id = NEW.listing_id
       AND l.platform = NEW.platform
       AND l.external_id = NEW.asin
       AND l.seller_sku IS NOT DISTINCT FROM NEW.seller_sku;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'estimacion_escenario: listing % no casa platform/asin/seller_sku',
            NEW.listing_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT l.product_id INTO v_product_id
      FROM listing l
     WHERE l.id = NEW.listing_id;

    SELECT p.created_at
      INTO v_politica_created_at
      FROM estimacion_politica_version p
     WHERE p.id = NEW.politica_version_id;

    IF v_politica_created_at > NEW.observed_at THEN
        RAISE EXCEPTION
            'estimacion_escenario: politica % creada despues del corte',
            NEW.politica_version_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.oferta_observation_id IS NOT NULL THEN
        SELECT o.observed_at
          INTO v_oferta_observed_at
          FROM estimacion_oferta_observation o
         WHERE o.id = NEW.oferta_observation_id
           AND o.listing_id = NEW.listing_id
           AND o.platform = NEW.platform
           AND o.seller_sku = NEW.seller_sku
           AND o.asin = NEW.asin
           AND o.canal = NEW.canal
           AND o.context_fingerprint = NEW.context_fingerprint;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'estimacion_escenario: oferta % no casa con el contexto',
                NEW.oferta_observation_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_oferta_observed_at > NEW.observed_at THEN
            RAISE EXCEPTION
                'estimacion_escenario: oferta % observada despues del corte',
                NEW.oferta_observation_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.fee_observation_id IS NOT NULL THEN
        SELECT f.observed_at, f.oferta_observation_id
          INTO v_fee_observed_at, v_fee_oferta_id
          FROM estimacion_fee_observation f
         WHERE f.id = NEW.fee_observation_id
           AND f.listing_id = NEW.listing_id
           AND f.platform = NEW.platform
           AND f.seller_sku = NEW.seller_sku
           AND f.asin = NEW.asin
           AND f.canal = NEW.canal
           AND f.context_fingerprint = NEW.context_fingerprint;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'estimacion_escenario: fee % no casa con el contexto',
                NEW.fee_observation_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_fee_observed_at > NEW.observed_at THEN
            RAISE EXCEPTION
                'estimacion_escenario: fee % observado despues del corte',
                NEW.fee_observation_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF NEW.oferta_observation_id IS NOT NULL
            AND v_fee_oferta_id <> NEW.oferta_observation_id THEN
            RAISE EXCEPTION
                'estimacion_escenario: fee % no pertenece a oferta %',
                NEW.fee_observation_id, NEW.oferta_observation_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.sku_cost_id IS NOT NULL THEN
        SELECT c.product_id, ir.started_at
          INTO v_costo_product_id, v_costo_started_at
          FROM sku_cost c
          LEFT JOIN ingest_run ir ON ir.id = c.ingest_run_id
         WHERE c.id = NEW.sku_cost_id;

        IF v_costo_product_id <> v_product_id THEN
            RAISE EXCEPTION
                'estimacion_escenario: sku_cost % no es del producto del listing',
                NEW.sku_cost_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_costo_started_at IS NOT NULL
            AND v_costo_started_at > NEW.observed_at THEN
            RAISE EXCEPTION
                'estimacion_escenario: sku_cost % registrado despues del corte',
                NEW.sku_cost_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER estimacion_escenario_referencias_coherentes
    BEFORE INSERT ON estimacion_escenario
    FOR EACH ROW EXECUTE FUNCTION estimacion_escenario_referencias_coherentes();

-- Append-only por motor desde el dia 1 (leccion 0023).
CREATE TRIGGER estimacion_politica_append_only
    BEFORE UPDATE OR DELETE ON estimacion_politica_version
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER estimacion_politica_append_only_truncate
    BEFORE TRUNCATE ON estimacion_politica_version
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER estimacion_oferta_append_only
    BEFORE UPDATE OR DELETE ON estimacion_oferta_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER estimacion_oferta_append_only_truncate
    BEFORE TRUNCATE ON estimacion_oferta_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER estimacion_fee_append_only
    BEFORE UPDATE OR DELETE ON estimacion_fee_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER estimacion_fee_append_only_truncate
    BEFORE TRUNCATE ON estimacion_fee_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER estimacion_escenario_append_only
    BEFORE UPDATE OR DELETE ON estimacion_escenario
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER estimacion_escenario_append_only_truncate
    BEFORE TRUNCATE ON estimacion_escenario
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- Reversa segura: revoca ingesta nueva, NO borra hechos observados.
CREATE TABLE estimacion_reversa_registro (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    aplicada_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    filas_politica BIGINT NOT NULL,
    filas_oferta   BIGINT NOT NULL,
    filas_fee      BIGINT NOT NULL,
    filas_escenario BIGINT NOT NULL
);

COMMENT ON TABLE estimacion_reversa_registro IS
    'MARGEN ESTIMADO A.1: auditoria de reversa. Solo INSERT al aplicar '
    'estimacion_venta_reversa(); los hechos append-only se conservan.';

CREATE FUNCTION estimacion_venta_reversa() RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_pol BIGINT;
    v_oferta BIGINT;
    v_fee BIGINT;
    v_esc BIGINT;
BEGIN
    SELECT count(*) INTO v_pol FROM estimacion_politica_version;
    SELECT count(*) INTO v_oferta FROM estimacion_oferta_observation;
    SELECT count(*) INTO v_fee FROM estimacion_fee_observation;
    SELECT count(*) INTO v_esc FROM estimacion_escenario;

    REVOKE INSERT ON estimacion_oferta_observation FROM app_ingest;
    REVOKE INSERT ON estimacion_fee_observation FROM app_ingest;
    REVOKE INSERT ON estimacion_escenario FROM app_ingest;
    REVOKE INSERT ON estimacion_politica_version FROM app_admin;
    REVOKE USAGE ON SEQUENCE estimacion_oferta_observation_id_seq FROM app_ingest;
    REVOKE USAGE ON SEQUENCE estimacion_fee_observation_id_seq FROM app_ingest;
    REVOKE USAGE ON SEQUENCE estimacion_escenario_id_seq FROM app_ingest;
    REVOKE USAGE ON SEQUENCE estimacion_politica_version_id_seq FROM app_admin;

    IF (SELECT count(*) FROM estimacion_politica_version) <> v_pol
        OR (SELECT count(*) FROM estimacion_oferta_observation) <> v_oferta
        OR (SELECT count(*) FROM estimacion_fee_observation) <> v_fee
        OR (SELECT count(*) FROM estimacion_escenario) <> v_esc THEN
        RAISE EXCEPTION
            'estimacion_venta_reversa: conteo de hechos cambio durante la reversa';
    END IF;

    INSERT INTO estimacion_reversa_registro (
        filas_politica, filas_oferta, filas_fee, filas_escenario
    ) VALUES (v_pol, v_oferta, v_fee, v_esc);
END;
$$;

COMMENT ON FUNCTION estimacion_venta_reversa IS
    'MARGEN ESTIMADO A.1 (Regla 7): desactiva ingesta/politica nueva sin '
    'DROP ni DELETE silencioso de observaciones. Proyeccion UI/API se retira '
    'en B; los hechos quedan para auditoria y as-of.';

-- Permisos minimos: ingesta escribe observaciones; lectura pura decide/read.
GRANT SELECT ON estimacion_politica_version TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON estimacion_politica_version TO app_admin;

GRANT SELECT ON estimacion_oferta_observation TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON estimacion_oferta_observation TO app_ingest;

GRANT SELECT ON estimacion_fee_observation TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON estimacion_fee_observation TO app_ingest;

GRANT SELECT ON estimacion_escenario TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON estimacion_escenario TO app_ingest;

GRANT USAGE ON SEQUENCE estimacion_politica_version_id_seq TO app_admin;
GRANT USAGE ON SEQUENCE estimacion_oferta_observation_id_seq TO app_ingest;
GRANT USAGE ON SEQUENCE estimacion_fee_observation_id_seq TO app_ingest;
GRANT USAGE ON SEQUENCE estimacion_escenario_id_seq TO app_ingest;

GRANT SELECT ON estimacion_reversa_registro TO app_read, app_ingest, app_decide, app_admin;

COMMIT;
