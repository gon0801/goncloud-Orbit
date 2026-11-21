-- TEMPORADA 01 B.1: consulta previa de solo lectura (el SQL de la migracion
-- 0093, diseno «Migracion»). Debe dar 0 ANTES de desplegar: los motivos
-- nacen en este plan, asi que ninguna trayectoria historica cambia.
-- Corrida con: bash docs/evidencia/arreglos-01/lee_prod.sh < consulta-previa.sql
SELECT count(*)
  FROM decision
 WHERE inputs->>'motivo' IN ('entra_temporada_baja', 'ajusta_temporada_baja',
                             'sale_temporada_baja');
