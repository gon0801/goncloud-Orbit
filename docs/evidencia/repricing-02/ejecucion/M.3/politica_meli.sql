-- Politica `meli/meli` (REPRICING 02 M.3; la corre D.4).
--
-- Fuente: fila `estimacion_politica_version` copiada de la FBA vigente
-- (los demas campos de `settings` la copian, segun el plan), con los
-- valores de la tabla "Politicas por universo": iva 1.16, precio con IVA,
-- envio de la muestra (`logistica` 0 + semantica) e `isr_tasa` 0.025
-- (medido M.2 por orden sobre ingreso neto, regimen 2026; ver
-- `ejecucion/M.2/medicion.md`). Idempotente por label: no repite una
-- vigente (misma regla que las FBM de D.3).
INSERT INTO estimacion_politica_version
    (label, universo, formula_version, settings, valid_from)
SELECT 'repricing02_meli', 'meli/meli', 'S3',
       settings
       || '{"iva_divisor": "1.16"}'::jsonb
       || '{"precio_incluye_iva": true}'::jsonb
       || '{"isr_tasa": "0.025"}'::jsonb
       || '{"logistica": "0"}'::jsonb
       || '{"logistica_semantica": "L sale de precio_envio_muestra, no de esta constante"}'::jsonb,
       CURRENT_DATE
  FROM estimacion_politica_version
 WHERE universo = 'amazon_mx/fba' AND valid_to IS NULL
   AND NOT EXISTS (
           SELECT 1 FROM estimacion_politica_version WHERE label = 'repricing02_meli'
       );
