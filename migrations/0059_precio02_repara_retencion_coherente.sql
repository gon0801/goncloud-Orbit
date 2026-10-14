-- 0059 (R03k): repara precio_retencion_coherente(); la 0054 no se toca (sellada).
-- Postgres rechaza el subselect multicolumna tras IS DISTINCT FROM: por eso
-- las columnas bajan a escalares con SELECT ... INTO.
BEGIN;
CREATE OR REPLACE FUNCTION precio_retencion_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_resultado TEXT;
    v_canal estimacion_canal;
    v_platform platform;
    v_decision_date DATE;
    v_corr_platform platform;
    v_corr_fecha DATE;
BEGIN
    SELECT d.resultado, d.canal, d.platform, d.decision_date
      INTO v_resultado, v_canal, v_platform, v_decision_date
      FROM precio_decision d WHERE d.id = NEW.decision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % no existe', NEW.decision_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_resultado NOT IN ('subir', 'bajar') THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % es % y solo una subir/bajar se '
            'retiene (retener no reescribe la decision).', NEW.decision_id,
            v_resultado USING ERRCODE = 'check_violation';
    END IF;
    SELECT c.platform, c.fecha INTO v_corr_platform, v_corr_fecha
      FROM precio_corrida c WHERE c.id = NEW.corrida_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_retencion: la corrida % no existe', NEW.corrida_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF (v_platform, v_decision_date)
       IS DISTINCT FROM (v_corr_platform, v_corr_fecha) THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % y la corrida % no son de la '
            'misma plataforma y el mismo dia.', NEW.decision_id, NEW.corrida_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.causa IN ('movimiento_masivo', 'insumo_sistemico') THEN
        IF NOT EXISTS (
            SELECT 1 FROM precio_compuerta_medicion m
             WHERE m.corrida_id = NEW.corrida_id
               AND m.canal = v_canal
               AND m.causa = NEW.causa
        ) THEN
            RAISE EXCEPTION
                'precio_retencion: la causa % exige la medicion de esa corrida '
                'y ese canal.', NEW.causa USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE
    v_prod BIGINT;
    v_lid BIGINT;
    v_lid2 BIGINT;
    v_cfg BIGINT;
    v_gid BIGINT;
    v_did BIGINT;
    v_did_mantener BIGINT;
    v_corrida BIGINT;
    v_corrida_otro BIGINT;
    v_rid BIGINT;
    v_ok BOOLEAN := false;
    v_n BIGINT;
BEGIN
    BEGIN
        INSERT INTO config_version (label, settings)
        VALUES ('zz-candado-0059', '{}'::jsonb) RETURNING id INTO v_cfg;
        INSERT INTO product (odoo_sku, name)
        VALUES ('zz-candado-0059', 'Candado 0059') RETURNING id INTO v_prod;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0059', 'zz-SKU-0059')
        RETURNING id INTO v_lid;
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por, go_literal)
        VALUES (v_lid, 'amazon_mx', 0.30, 'live', '2026-09-01', 'candado',
            'go candado 0059') RETURNING id INTO v_gid;
        INSERT INTO precio_decision (listing_id, platform, resultado, motivo,
            mode, goal, m_actual, p_actual, p_actual_currency, p_objetivo,
            p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor,
            i_currency, c_valor, c_currency, f_valor, f_currency, l_valor,
            l_currency, r_valor, r_currency)
        VALUES (v_lid, 'amazon_mx', 'subir', NULL, 'live', 0.30, 0.25, 100.00,
            'MXN', 110.00, 'MXN', 110.00, 'MXN', 10.00, 'MXN', 20.00, 'MXN',
            12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN') RETURNING id INTO v_did;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0059-2', 'zz-SKU-0059-2')
        RETURNING id INTO v_lid2;
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por, go_literal)
        VALUES (v_lid2, 'amazon_mx', 0.30, 'live', '2026-09-01', 'candado',
            'go candado 0059');
        INSERT INTO precio_decision (listing_id, platform, resultado, motivo,
            mode, goal, m_actual, p_actual, p_actual_currency, p_objetivo,
            p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor,
            i_currency, c_valor, c_currency, f_valor, f_currency, l_valor,
            l_currency, r_valor, r_currency)
        VALUES (v_lid2, 'amazon_mx', 'mantener', 'en_tolerancia', 'live', 0.30,
            0.30, 100.00, 'MXN', 100.00, 'MXN', 100.00, 'MXN', 10.00, 'MXN',
            20.00, 'MXN', 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN')
        RETURNING id INTO v_did_mantener;
        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida;

        INSERT INTO precio_retencion (decision_id, corrida_id, causa)
        VALUES (v_did, v_corrida, 'apagador') RETURNING id INTO v_rid;
        IF v_rid IS NULL THEN
            RAISE EXCEPTION '0059: el INSERT positivo de retencion no devolvio id';
        END IF;

        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_us', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida_otro;
        BEGIN
            INSERT INTO precio_retencion (decision_id, corrida_id, causa)
            VALUES (v_did, v_corrida_otro, 'apagador');
            RAISE EXCEPTION '0059: retencion cruzada amazon_mx/amazon_us NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_retencion (decision_id, corrida_id, causa)
            VALUES (v_did, v_corrida, 'movimiento_masivo');
            RAISE EXCEPTION '0059: retencion sin medicion NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_retencion (decision_id, corrida_id, causa)
            VALUES (-v_did, v_corrida, 'apagador');
            RAISE EXCEPTION '0059: retencion de decision inexistente NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN foreign_key_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_retencion (decision_id, corrida_id, causa)
            VALUES (v_did_mantener, v_corrida, 'apagador');
            RAISE EXCEPTION '0059: retencion de decision mantener NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        v_ok := true;
        RAISE EXCEPTION 'candado_0059_revertir' USING ERRCODE = 'P0001';
    EXCEPTION WHEN raise_exception THEN
        IF NOT v_ok OR SQLERRM <> 'candado_0059_revertir' THEN
            RAISE;
        END IF;
    END;

    SELECT count(*) INTO v_n FROM precio_retencion
     WHERE corrida_id IN (v_corrida, v_corrida_otro);
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0059: el candado dejo % filas en precio_retencion', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_corrida
     WHERE id IN (v_corrida, v_corrida_otro);
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0059: el candado dejo % filas en precio_corrida', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_decision
     WHERE listing_id IN (v_lid, v_lid2);
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0059: el candado dejo % decisiones sembradas', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_goal WHERE creado_por = 'candado'
        AND listing_id IN (v_lid, v_lid2);
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0059: el candado dejo % goals sembrados', v_n;
    END IF;
END;
$$;
COMMIT;
