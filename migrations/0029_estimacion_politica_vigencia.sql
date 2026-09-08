-- ---------------------------------------------------------------------------
-- MARGEN ESTIMADO 01 A.3 — politica FBA MX sellada (acta 0.3).
--
-- Es expansiva: INSERT. No re-runnable. Las columnas valid_from/valid_to viven
-- en 0028; aqui solo se siembra la politica aprobada.
-- ---------------------------------------------------------------------------

BEGIN;

INSERT INTO estimacion_politica_version (
    label,
    universo,
    formula_version,
    settings,
    valid_from,
    valid_to
) VALUES (
    'amazon_mx_pf_rfc_valid_2026_01',
    'amazon_mx/fba',
    'S3',
    '{"iva_divisor": "1.16", "isr_tasa": "0.025", "logistica": "0",'
    ' "logistica_semantica": "L=0 solo porque logistica FBA esta incluida en fees Amazon",'
    ' "retencion_iva_reconciliacion": "0.08", "precio_incluye_iva": true,'
    ' "fee_tax_amount_requiere_politica": true}'::jsonb,
    '2026-01-01',
    NULL
);

COMMIT;
