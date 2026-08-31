-- =============================================================================
--  ORBIT · MIGRACION 0004 · ad_entity_kind += product_ad · PostgreSQL 16
--
--  ORBIT 06 tarea 0.4: materializar product ads como entidades para atribuir
--  margen 1:1 ASIN→listing. `ad_entity.listing_id` (UN listing por fila) no
--  alcanza en el ad group — en US 0/48 ad groups tienen un solo ASIN (medicion
--  lead 2026-08-31). Decision del dueño: grano product_ad.
--
--  Pre-aprobada en plans/orbit-06.md §Pre-aprobaciones. OJO PostgreSQL: el
--  valor nuevo del enum NO puede usarse en la misma transaccion que lo
--  agrega. Esta migracion SOLO hace ADD VALUE y COMMITEA; la ingesta que
--  INSERTA kind='product_ad' corre despues, en otro paso.
--
--  NO es re-runnable (segundo ADD VALUE del mismo literal falla). NO toca
--  datos, GRANTs, triggers ni CHECKs.
-- =============================================================================

BEGIN;

ALTER TYPE ad_entity_kind ADD VALUE 'product_ad';

COMMIT;
