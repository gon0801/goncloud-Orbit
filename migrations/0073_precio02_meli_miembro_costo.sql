-- REPRICING 02 carril M, paso M.3 (se despliega en D.4).
--
-- La cuenta de la fila `meli` es la del miembro que manda
-- (`miembro_product_id`), no la del ancla (`listing.product_id`): el costo
-- referenciado debe ser del miembro, y el miembro debe ser miembro de la unidad
-- (leido de `v_precio_unidad_miembro`, la misma vista que usa la siembra).
-- Amazon intacto: su rama no cambia. El trigger es BEFORE INSERT: solo
-- dispara en filas nuevas; la historia no se revalida.
BEGIN;
CREATE OR REPLACE FUNCTION estimacion_escenario_referencias_coherentes() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_product_id BIGINT;
    v_oferta_observed_at TIMESTAMPTZ;
    v_oferta_fetched_at TIMESTAMPTZ;
    v_fee_observed_at TIMESTAMPTZ;
    v_fee_oferta_id BIGINT;
    v_politica_created_at TIMESTAMPTZ;
    v_politica_universo TEXT;
    v_politica_formula TEXT;
    v_politica_valid_from DATE;
    v_politica_valid_to DATE;
    v_costo_product_id BIGINT;
    v_costo_started_at TIMESTAMPTZ;
    v_costo_validated_at TIMESTAMPTZ;
    v_costo_includes_tax BOOLEAN;
    v_costo_valid_from DATE;
    v_costo_valid_to DATE;
    v_run_source TEXT;
    v_run_ok BOOLEAN;
    v_run_rows_skipped INT;
    v_fee_estado estimacion_fee_estado;
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

    -- 0073: en `meli` disponible la cuenta es la del que manda: sin miembro
    -- no hay cuenta (defensa en profundidad; la siembra siempre lo pone).
    IF NEW.platform = 'meli' AND NEW.estado = 'disponible'
       AND NEW.miembro_product_id IS NULL THEN
        RAISE EXCEPTION
            'estimacion_escenario: disponible meli exige miembro (listing %)',
            NEW.listing_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT l.product_id INTO v_product_id
      FROM listing l
     WHERE l.id = NEW.listing_id;

    IF NEW.politica_version_id IS NOT NULL THEN
        SELECT p.created_at, p.universo, p.formula_version, p.valid_from, p.valid_to
          INTO v_politica_created_at, v_politica_universo, v_politica_formula,
               v_politica_valid_from, v_politica_valid_to
          FROM estimacion_politica_version p
         WHERE p.id = NEW.politica_version_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'estimacion_escenario: politica % inexistente',
                NEW.politica_version_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_politica_created_at > NEW.observed_at THEN
            RAISE EXCEPTION
                'estimacion_escenario: politica % creada despues del corte',
                NEW.politica_version_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF NEW.estado = 'disponible' AND NOT (
            v_politica_universo = NEW.platform::TEXT || '/' || NEW.canal::TEXT
            AND v_politica_formula = NEW.formula_version
            AND v_politica_valid_from <= NEW.valoracion_date
            AND (v_politica_valid_to IS NULL OR v_politica_valid_to > NEW.valoracion_date)
        ) THEN
            RAISE EXCEPTION
                'estimacion_escenario: politica % no aplica al escenario',
                NEW.politica_version_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.oferta_observation_id IS NOT NULL THEN
        SELECT o.observed_at, o.fetched_at
          INTO v_oferta_observed_at, v_oferta_fetched_at
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

        IF NEW.estado = 'disponible' AND (
            v_oferta_fetched_at > NEW.observed_at
            OR NEW.observed_at - v_oferta_fetched_at > INTERVAL '6 hours'
        ) THEN
            RAISE EXCEPTION
                'estimacion_escenario: oferta vencida o futura para estado disponible'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.fee_observation_id IS NOT NULL THEN
        SELECT f.observed_at, f.oferta_observation_id, f.estado
          INTO v_fee_observed_at, v_fee_oferta_id, v_fee_estado
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


        IF NEW.estado = 'disponible' AND v_fee_estado <> 'success' THEN
            RAISE EXCEPTION
                'estimacion_escenario: disponible exige fee success'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.sku_cost_id IS NOT NULL THEN
        SELECT c.product_id, c.includes_tax, c.valid_from, c.valid_to, ir.started_at
          INTO v_costo_product_id, v_costo_includes_tax, v_costo_valid_from,
               v_costo_valid_to, v_costo_started_at
          FROM sku_cost c
          LEFT JOIN ingest_run ir ON ir.id = c.ingest_run_id
         WHERE c.id = NEW.sku_cost_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'estimacion_escenario: sku_cost % inexistente',
                NEW.sku_cost_id
                USING ERRCODE = 'check_violation';
        END IF;

        -- 0073 (REPRICING 02 M.3): en `meli` la cuenta de la fila es la del
        -- miembro que manda, no la del ancla: el costo debe ser del miembro
        -- (`miembro_product_id`, que a su vez debe ser miembro de la unidad).
        IF NEW.platform = 'meli' AND NEW.miembro_product_id IS NOT NULL THEN
            PERFORM 1
              FROM v_precio_unidad_miembro m
             WHERE m.listing_id = NEW.listing_id
               AND m.product_id IS NOT DISTINCT FROM NEW.miembro_product_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'estimacion_escenario: miembro % no es de la unidad %',
                    NEW.miembro_product_id, NEW.listing_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF v_costo_product_id <> NEW.miembro_product_id THEN
                RAISE EXCEPTION
                    'estimacion_escenario: sku_cost % no es del miembro %',
                    NEW.sku_cost_id, NEW.miembro_product_id
                    USING ERRCODE = 'check_violation';
            END IF;
        ELSIF v_costo_product_id <> v_product_id THEN
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

        IF NEW.estado = 'disponible' THEN
            IF v_costo_includes_tax IS NOT FALSE THEN
                RAISE EXCEPTION
                    'estimacion_escenario: disponible exige includes_tax=false en sku_cost %',
                    NEW.sku_cost_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NOT (
                v_costo_valid_from <= NEW.valoracion_date
                AND (v_costo_valid_to IS NULL OR v_costo_valid_to > NEW.valoracion_date)
            ) THEN
                RAISE EXCEPTION
                    'estimacion_escenario: sku_cost % no vigente en valoracion_date',
                    NEW.sku_cost_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.costo_validation_run_id IS NULL OR NEW.costo_validated_at IS NULL THEN
                RAISE EXCEPTION
                    'estimacion_escenario: disponible exige validacion de costo'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;

    IF NEW.costo_validation_run_id IS NOT NULL THEN
        SELECT r.finished_at, r.source, r.ok, r.rows_skipped
          INTO v_costo_validated_at, v_run_source, v_run_ok, v_run_rows_skipped
          FROM ingest_run r
         WHERE r.id = NEW.costo_validation_run_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'estimacion_escenario: validacion de costo % inexistente',
                NEW.costo_validation_run_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_run_source <> 'accounting_sku_costs'
            OR v_run_ok IS NOT TRUE
            OR v_costo_validated_at IS NULL
            OR v_run_rows_skipped IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION
                'estimacion_escenario: validacion de costo % no es accounting_sku_costs ok',
                NEW.costo_validation_run_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_costo_validated_at IS DISTINCT FROM NEW.costo_validated_at THEN
            RAISE EXCEPTION
                'estimacion_escenario: validacion de costo % no coincide',
                NEW.costo_validation_run_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF (v_costo_validated_at AT TIME ZONE 'UTC')::date <> NEW.valoracion_date THEN
            RAISE EXCEPTION
                'estimacion_escenario: validacion de costo % no es del dia UTC de valoracion',
                NEW.costo_validation_run_id
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_costo_validated_at > NEW.observed_at THEN
            RAISE EXCEPTION
                'estimacion_escenario: costo validado despues del corte'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

COMMIT;
