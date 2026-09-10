-- ---------------------------------------------------------------------------
-- SP-API 01 A.5 — plataforma en ingest_run (JUSTIFICADA EN EL PR #NNN).
--
-- EL PROBLEMA: A.5 exige salud y alertas POR FUENTE+PLATAFORMA leidas SOLO
-- de ingest_run (regla 2, sin tabla nueva de estado). Pero ingest_run no
-- tiene plataforma y SOURCE es global ("spapi_orders" cubre MX+US): con el
-- cron diario las corridas de una fuente alternan plataforma, asi que el
-- flanco "dos fallidas seguidas" NUNCA se cumpliria (MX-falla, US-ok,
-- MX-falla...) y /salud mostraria corridas de US bajo MX. Derivarlo de las
-- tablas de observaciones es PEOR: una corrida totalmente fallida (LWA
-- caido: 0 filas, justo el caso que debe alertar) no deja rastro ahi.
--
-- LA SALIDA MINIMA: una columna anulable (filas pre-A.5 quedan NULL = sin
-- atribuir; las consultas A.5 filtran platform = %s y las ignoran). Sin
-- UPDATE deliberado: la plataforma se fija en el INSERT y es INMUTABLE por
-- permisos (patron ad_entity en 0001). SELECT por columna explicito para
-- los 4 roles (leccion 0033->0034) + verificacion con asserts.
--
-- Expansiva, no toca datos. Las 4 ingestas escriben platform al abrir el
-- run en el mismo PR. No re-runnable (sin IF NOT EXISTS, patron 0033/0035).
-- ---------------------------------------------------------------------------

BEGIN;

ALTER TABLE ingest_run ADD COLUMN platform platform;

COMMENT ON COLUMN ingest_run.platform IS
    'SP-API 01 A.5: plataforma de la corrida (las 4 ingestas SP-API la '
    'escriben al abrir el run). NULL = corrida pre-A.5 sin atribuir: salud '
    'y alertas solo ven filas con plataforma. INMUTABLE por permisos (sin '
    'GRANT de UPDATE a proposito).';

-- Explicito aunque exista SELECT a nivel tabla (leccion 0033->0034: lo
-- nuevo se declara). UPDATE a proposito AUSENTE (inmutabilidad).
GRANT SELECT (platform) ON ingest_run TO app_read, app_ingest, app_decide, app_admin;

-- Verificacion: lectura para los 4 roles, escritura cerrada.
DO $$
BEGIN
    IF NOT has_column_privilege('app_read', 'ingest_run', 'platform', 'SELECT') THEN
        RAISE EXCEPTION '0036: app_read sin SELECT en ingest_run.platform';
    END IF;
    IF NOT has_column_privilege('app_ingest', 'ingest_run', 'platform', 'SELECT') THEN
        RAISE EXCEPTION '0036: app_ingest sin SELECT en ingest_run.platform';
    END IF;
    -- El candado del INSERT (el privilegio que las 4 ingestas REALMENTE
    -- ejercen al abrir el run) NO va aqui: 0036 ya esta mergeada y no es
    -- re-runnable, asi que un entorno que ya la aplico nunca volveria a
    -- correr este bloque. Vive en 0037, que aun no se aplico en ningun
    -- lado (hallazgo CodeRabbit PR #247).
    IF has_column_privilege('app_ingest', 'ingest_run', 'platform', 'UPDATE') THEN
        RAISE EXCEPTION '0036: app_ingest NO debe poder reatribuir plataforma';
    END IF;
END $$;

COMMIT;
