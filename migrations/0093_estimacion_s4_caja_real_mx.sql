-- sonda-aplicada: SELECT (SELECT count(*) FROM estimacion_politica_version WHERE formula_version = 'S4' AND valid_to IS NULL AND universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')) = 3
-- 0093 (C2 / Bloque K): formula S4 de caja real para Mexico (Amazon MX y MeLi).
--
-- El dueno confirmo el 2026-11-13 que no paga impuestos adicionales a los que
-- retienen las plataformas. Hasta hoy S3 restaba el 16% completo de IVA sobre
-- la venta (en lugar del 8% retenido) y en Amazon MX usaba ReferralFee sin IVA
-- (12.93% vs 15.00% real en 493/500 pedidos conciliados en ledger_event).
--
-- S4 alinea estimacion y motor de precios con v_margen_producto y caja real:
--   - Ingreso base = P (venta bruta con IVA), igual que v_margen_producto.
--   - Descuenta solo lo retenido: ISR (2.5% de P/1.16) + IVA retenido (8% de P/1.16).
--   - En Amazon MX aplica referral_fee_iva_multiplicador = 1.16 solo a ReferralFee
--     (FBAFees ya llega con IVA, conciliado contra ledger_event).
--   - En MeLi referral_fee_iva_multiplicador = 1 (199/199 pedidos exacto 1.0000).
--   - Conserva fee_tax_amount_requiere_politica = true (si SP-API empieza a enviar
--     TaxAmount != 0, se abstiene para no contar IVA dos veces).
--
-- Se preserva todo el historial S3: estimacion_politica_version adopta el mismo
-- patron de cierre unico de vigencia que sku_cost y precio_goal (solo permite
-- pasar valid_to de NULL a DATE una vez; DELETE y TRUNCATE siguen prohibidos).
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh corre en una transaccion (-1).
-- Reversa: 0093_reversa_estimacion_s4_caja_real_mx.sql.

CREATE OR REPLACE FUNCTION estimacion_politica_solo_cierra_vigencia() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
       OR OLD.label IS DISTINCT FROM NEW.label
       OR OLD.universo IS DISTINCT FROM NEW.universo
       OR OLD.formula_version IS DISTINCT FROM NEW.formula_version
       OR OLD.settings IS DISTINCT FROM NEW.settings
       OR OLD.valid_from IS DISTINCT FROM NEW.valid_from THEN
        RAISE EXCEPTION
            'estimacion_politica_version %: solo se puede cerrar valid_to; crear nueva fila para cambiar politica',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL THEN
        RAISE EXCEPTION
            'estimacion_politica_version %: valid_to solo puede pasar de NULL a una fecha, una vez',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION estimacion_politica_solo_cierra_vigencia IS
    '0093: patron sku_cost/precio_goal para estimacion_politica_version. '
    'Permite cerrar valid_to una sola vez (NULL -> DATE) sin alterar ningun '
    'otro campo; DELETE y TRUNCATE siguen prohibidos por prohibir_mutacion.';

DROP TRIGGER estimacion_politica_append_only ON estimacion_politica_version;

CREATE TRIGGER estimacion_politica_solo_cierra_vigencia
    BEFORE UPDATE ON estimacion_politica_version
    FOR EACH ROW EXECUTE FUNCTION estimacion_politica_solo_cierra_vigencia();

CREATE TRIGGER estimacion_politica_sin_borrado
    BEFORE DELETE ON estimacion_politica_version
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

-- Cierra las politicas S3 abiertas de Mexico a partir de hoy (UTC).
UPDATE estimacion_politica_version
   SET valid_to = (now() AT TIME ZONE 'UTC')::date
 WHERE universo IN ('amazon_mx/fba', 'amazon_mx/fbm', 'meli/meli')
   AND formula_version = 'S3'
   AND valid_to IS NULL
   AND valid_from < (now() AT TIME ZONE 'UTC')::date;

-- Siembra las tres politicas S4 vigentes desde hoy (UTC).
INSERT INTO estimacion_politica_version (
    label,
    universo,
    formula_version,
    settings,
    valid_from,
    valid_to
)
SELECT v.label, v.universo, 'S4', v.settings, (now() AT TIME ZONE 'UTC')::date, NULL
  FROM (
      VALUES
          (
              'amazon_mx_fba_s4_caja_real_2026_11',
              'amazon_mx/fba',
              '{"iva_divisor": "1.16", "isr_tasa": "0.025", "retencion_iva_tasa": "0.08",'
              ' "referral_fee_iva_multiplicador": "1.16", "logistica": "0",'
              ' "logistica_semantica": "L=0 solo porque logistica FBA esta incluida en fees Amazon",'
              ' "precio_incluye_iva": true, "fee_tax_amount_requiere_politica": true}'::jsonb
          ),
          (
              'amazon_mx_fbm_s4_caja_real_2026_11',
              'amazon_mx/fbm',
              '{"iva_divisor": "1.16", "isr_tasa": "0.025", "retencion_iva_tasa": "0.08",'
              ' "referral_fee_iva_multiplicador": "1.16", "logistica": "0",'
              ' "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",'
              ' "precio_incluye_iva": true, "fee_tax_amount_requiere_politica": true}'::jsonb
          ),
          (
              'meli_s4_caja_real_2026_11',
              'meli/meli',
              '{"iva_divisor": "1.16", "isr_tasa": "0.025", "retencion_iva_tasa": "0.08",'
              ' "referral_fee_iva_multiplicador": "1", "logistica": "0",'
              ' "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",'
              ' "precio_incluye_iva": true, "fee_tax_amount_requiere_politica": true}'::jsonb
          )
  ) AS v(label, universo, settings)
 WHERE NOT EXISTS (
     SELECT 1
       FROM estimacion_politica_version p
      WHERE p.universo = v.universo
        AND p.formula_version = 'S4'
        AND p.valid_to IS NULL
 );
