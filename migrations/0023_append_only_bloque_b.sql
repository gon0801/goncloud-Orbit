-- ---------------------------------------------------------------------------
-- 0023 — ORBIT 19 B: candados append-only de motor para las tablas nuevas
-- del bloque B (hallazgo bloqueante 3 del cross-review codex 2026-09-07).
--
-- 0020 (ads_product_metric_observation) y 0022 (disponibilidad_observation)
-- declararon el append-only SOLO con GRANTs (sin UPDATE/DELETE a ningun
-- rol). El esquema sellado de 0001 §16 exige ademas el trigger
-- prohibir_mutacion: el candado no debe depender de la AUSENCIA de un GRANT
-- — un GRANT futuro o el dueno del esquema podria reescribir historia en
-- silencio. Esta migracion repara esa omision sin tocar datos.
--
-- La funcion prohibir_mutacion() ya existe desde 0001 y no se redeclara.
-- v_economia_producto (0021) es una VISTA: no acumula hechos, no aplica.
--
-- Expansiva, NO re-runnable (los CREATE TRIGGER revientan a la segunda).
-- ---------------------------------------------------------------------------

CREATE TRIGGER apm_metrica_append_only
    BEFORE UPDATE OR DELETE ON ads_product_metric_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER apm_metrica_append_only_truncate
    BEFORE TRUNCATE ON ads_product_metric_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER disponibilidad_append_only
    BEFORE UPDATE OR DELETE ON disponibilidad_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER disponibilidad_append_only_truncate
    BEFORE TRUNCATE ON disponibilidad_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TRIGGER apm_metrica_append_only ON ads_product_metric_observation IS
    'Candado 0001 §16 aplicado tarde (0023): append-only por motor, no por GRANTs.';
COMMENT ON TRIGGER disponibilidad_append_only ON disponibilidad_observation IS
    'Candado 0001 §16 aplicado tarde (0023): append-only por motor, no por GRANTs.';
