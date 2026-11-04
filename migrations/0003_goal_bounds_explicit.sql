-- =============================================================================
--  ORBIT · MIGRACIÓN 0003 · GOAL BOUNDS EXPLÍCITOS ·  PostgreSQL 16
--
--  Quita el `DEFAULT 0.10/2.50` de `bid_floor`/`bid_ceiling` en
--  `ads_optimizer_goal` (ORBIT 05 preflight 1.2, sellado 2 del plan
--  plans/orbit-05-preflight.md; spot-check 4.4: esos números estaban
--  pensados en USD y el goal 4 MXN nació con ellos — 144/233 keywords y
--  44/51 targets MX tenían bid > 2.50 MXN, que el techo habría APLASTADO
--  en vivo; decisión del dueño 2026-08-28):
--
--  - NOT NULL se queda: un INSERT que omita piso/techo REVIENTA
--    (NotNullViolation) en vez de nacer en USD sin moneda;
--  - los defaults viven UNA sola fuente: `DEFAULTS_POR_MONEDA` en
--    app/optimizer/goals.py (USD 0.10/2.50 con max real observado 2.00;
--    MXN 1.00/45.00; OTRA moneda = error explícito — no se inventan
--    números, regla 3);
--  - `edita_goal` (app/goals_write.py, dueño único de la escritura) resuelve
--    por moneda cualquier piso/techo efectivo ausente antes de persistir.
--
--  NO es re-runnable (estilo 0001/0002: el segundo `DROP DEFAULT` sobre una
--  columna sin default es un no-op, pero la migración se aplica UNA vez por
--  base). NO toca datos: las 4 filas existentes ya llevan bounds explícitos
--  (goal 4 corregido a mano por el dueño a 1.00/45.00 MXN; goals 5/6/7 USD
--  0.10/2.50). NO toca GRANTs ni triggers ni CHECKs.
-- =============================================================================

BEGIN;

ALTER TABLE ads_optimizer_goal ALTER COLUMN bid_floor DROP DEFAULT;

ALTER TABLE ads_optimizer_goal ALTER COLUMN bid_ceiling DROP DEFAULT;

COMMIT;
