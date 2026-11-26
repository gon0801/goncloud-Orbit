-- sonda-aplicada: SELECT to_regclass('public.envio_observation') IS NOT NULL AND to_regclass('public.envio_paquete_observation') IS NOT NULL AND to_regclass('public.v_envio_actual') IS NOT NULL AND to_regclass('public.envio_observation_actual_idx') IS NOT NULL AND to_regprocedure('public.envio_tiempo_coherente()') IS NOT NULL
-- =============================================================================
--  Reversa de 0102_envios01_seguimiento.sql
--
--  Retira el modulo completo, tambien con observaciones (spec ENVIOS 01):
--  nada fuera de Envios las referencia. Se pierde la historia observada.
-- =============================================================================

DROP VIEW IF EXISTS v_envio_actual;
DROP TABLE IF EXISTS envio_paquete_observation;
DROP TABLE IF EXISTS envio_observation;
DROP FUNCTION IF EXISTS envio_tiempo_coherente();
