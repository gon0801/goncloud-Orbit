-- sonda-aplicada: SELECT (SELECT count(*) FROM estimacion_politica_version WHERE formula_version = 'S4' AND valid_to IS NULL AND universo = 'amazon_us/fbm') = 1
-- 0100 (ARREGLOS 05 Bloque K): formula S4 de caja real para Amazon US FBM.
--
-- Conciliacion directa contra Amazon SP-API Finances v2024 en USD nativo
-- (ATVPDKIKX0DER; 164/164 items en 2026-01, 2026-05 y 2026-10/11) y contra
-- 531/531 pedidos historicos de 2025-12 a 2026-11 con FX exacto:
--   - Product Fees cotiza ReferralFee al 15.00% sin TaxAmount.
--   - SP-API Finances cobra Commission = Base (15.00%) + Tax (16.00% sobre Base)
--     = 17.40% exacto (razon mediana 1.159887, 0 anomalias).
--   - MarketplaceFacilitatorTax es sales tax estatal de EE. UU. con efecto neto
--     $0.00 USD para el vendedor; Amazon US no retiene IVA mexicano
--     (retencion_iva_tasa = 0).
--   - ShippingHB ya entra con IVA incluido en precio_envio_muestra desde el ledger.
--   - Se conserva isr_tasa = 0.0207 y fee_tax_amount_requiere_politica = true
--     (si Product Fees envia TaxAmount != 0, abstiene con impuesto_fee_pendiente).
--
-- Depende de 0093 (trigger estimacion_politica_solo_cierra_vigencia y CHECK
-- valid_to >= valid_from). Conserva intacto todo el historial S3.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh corre en una transaccion (-1).
-- Reversa: 0100_reversa_arreglos05_k_us_s4.sql.

-- Cierra la politica S3 abierta de Amazon US FBM a partir de hoy (UTC), incluso
-- si una reversa previa del mismo dia abrio una continuacion S3 con valid_from = hoy.
UPDATE estimacion_politica_version
   SET valid_to = (now() AT TIME ZONE 'UTC')::date
 WHERE universo = 'amazon_us/fbm'
   AND formula_version = 'S3'
   AND valid_to IS NULL
   AND valid_from <= (now() AT TIME ZONE 'UTC')::date;

-- Siembra la politica S4 de Amazon US FBM vigente desde hoy (UTC).
INSERT INTO estimacion_politica_version (
    label,
    universo,
    formula_version,
    settings,
    valid_from,
    valid_to
)
SELECT
    'amazon_us_fbm_s4_caja_real_2026_11',
    'amazon_us/fbm',
    'S4',
    (
        '{"iva_divisor": "1", "isr_tasa": "0.0207", "retencion_iva_tasa": "0",'
        ' "referral_fee_iva_multiplicador": "1.16", "logistica": "0",'
        ' "logistica_semantica": "L sale de precio_envio_muestra, no de esta constante",'
        ' "precio_incluye_iva": false, "fee_tax_amount_requiere_politica": true}'
    )::jsonb,
    (now() AT TIME ZONE 'UTC')::date,
    NULL
 WHERE NOT EXISTS (
     SELECT 1
       FROM estimacion_politica_version p
      WHERE p.universo = 'amazon_us/fbm'
        AND p.formula_version = 'S4'
        AND p.valid_to IS NULL
 );
