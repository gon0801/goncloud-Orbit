-- ---------------------------------------------------------------------------
-- 0061 — BIDS 02 V.1 (seccion 3): configuracion de campana en el sync.
-- PostgreSQL 16.
--
-- Presupuesto, estrategia de puja y ajustes de placement de cada campana.
-- Append-only y solo cuando algo cambia: la tabla es la HISTORIA
-- (ad_entity_state se pisa en cada sync y por eso no sirve). El presupuesto
-- llega sin moneda en el payload; se guarda con la del perfil, igual que
-- los bids. (Bloque "0061 VER LO INVISIBLE" de
-- docs/evidencia/bids-02/diseno/datos.sql: la tabla y la vista van intactas;
-- fuera_de_amazon la agrega la guia, V.1 cambio 2.)
--
-- Regla 7: BEGIN/COMMIT dentro del archivo, NO idempotente, dos triggers
-- prohibir_mutacion(), GRANT declarados y bloque DO que falla si falta o
-- sobra un privilegio. El trigger de kind es funcion propia de esta
-- migracion: la unica reutilizable nombra otra tabla en su error.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE ads_campana_config_observation (
    id                  BIGSERIAL PRIMARY KEY,
    ad_entity_id        BIGINT      NOT NULL REFERENCES ad_entity (id),
    observed_at         TIMESTAMPTZ NOT NULL,
    presupuesto_diario  NUMERIC,
    presupuesto_moneda  TEXT,
    estrategia_puja     TEXT,            -- texto tal cual de Amazon; el vocabulario real se sella con la sonda
    ajuste_top_pct      INTEGER,
    ajuste_resto_pct    INTEGER,
    ajuste_producto_pct INTEGER,
    fuera_de_amazon     TEXT,            -- offAmazonSettings no vacio, como texto JSON (V.1; V.3 sella el vocabulario)
    CONSTRAINT config_presupuesto_con_moneda
        CHECK ((presupuesto_diario IS NULL) = (presupuesto_moneda IS NULL)),
    CONSTRAINT config_presupuesto_positivo
        CHECK (presupuesto_diario IS NULL OR presupuesto_diario > 0),
    CONSTRAINT config_ajustes_en_rango
        CHECK (COALESCE(ajuste_top_pct, 0) BETWEEN 0 AND 900
           AND COALESCE(ajuste_resto_pct, 0) BETWEEN 0 AND 900
           AND COALESCE(ajuste_producto_pct, 0) BETWEEN 0 AND 900),
    UNIQUE (ad_entity_id, observed_at)
);
-- Ojo (trampa "NULL en CHECK", de datos.sql): un CHECK con NULL pasa. Por eso
-- los ajustes van con COALESCE explicito y la moneda con igualdad de nulidad.

CREATE VIEW v_campana_config_vigente AS
SELECT DISTINCT ON (ad_entity_id) *
  FROM ads_campana_config_observation
 ORDER BY ad_entity_id, observed_at DESC;

-- Patron harvest_excepcion_kind (0018): la FK sola admite cualquier entidad.
CREATE FUNCTION ads_campana_config_0061_kind() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'ads_campana_config_observation: ad_entity_id % no existe o no es kind=campaign',
            NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ads_campana_config_observation_kind
    BEFORE INSERT OR UPDATE ON ads_campana_config_observation
    FOR EACH ROW EXECUTE FUNCTION ads_campana_config_0061_kind();

CREATE TRIGGER ads_campana_config_observation_append_only
    BEFORE UPDATE OR DELETE ON ads_campana_config_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ads_campana_config_observation_append_only_truncate
    BEFORE TRUNCATE ON ads_campana_config_observation
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE ads_campana_config_observation IS
  'BIDS 02 V.1 (0061): historia append-only de la configuracion de cada '
  'campana (presupuesto con moneda del perfil, estrategia tal cual de '
  'Amazon, tres ajustes de placement, fuera_de_amazon). Una fila por '
  'cambio; la vigente se lee en v_campana_config_vigente.';
COMMENT ON VIEW v_campana_config_vigente IS
  'BIDS 02 V.1 (0061): ultima observacion de configuracion por campana '
  '(DISTINCT ON ad_entity_id, observed_at DESC).';
COMMENT ON FUNCTION ads_campana_config_0061_kind IS
  'BIDS 02 V.1 (0061): la observacion de configuracion es de kind=campaign '
  '(la FK sola admite cualquier entidad).';

-- Solo app_ingest escribe: es el rol con que corre sync_structure. SELECT
-- explicito a lectura, admin e ingest (guarda_config lee la vigente con la
-- conexion del sync; explicito como 0051, sin depender solo del privilegio
-- por omision de 0001).
GRANT SELECT ON ads_campana_config_observation TO app_read, app_admin;
GRANT SELECT ON ads_campana_config_observation TO app_ingest;
GRANT INSERT ON ads_campana_config_observation TO app_ingest;
GRANT SELECT ON v_campana_config_vigente TO app_read, app_admin;
GRANT USAGE, SELECT ON SEQUENCE ads_campana_config_observation_id_seq TO app_ingest;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'ads_campana_config_observation', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'ads_campana_config_observation', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_campana_config_observation', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_campana_config_observation', 'INSERT')
       OR has_table_privilege('app_ingest', 'ads_campana_config_observation', 'UPDATE')
       OR has_table_privilege('app_ingest', 'ads_campana_config_observation', 'DELETE')
       OR has_table_privilege('app_read', 'ads_campana_config_observation', 'INSERT')
       OR has_table_privilege('app_read', 'ads_campana_config_observation', 'UPDATE')
       OR has_table_privilege('app_read', 'ads_campana_config_observation', 'DELETE')
       OR has_table_privilege('app_admin', 'ads_campana_config_observation', 'INSERT')
       OR has_table_privilege('app_admin', 'ads_campana_config_observation', 'UPDATE')
       OR has_table_privilege('app_admin', 'ads_campana_config_observation', 'DELETE')
       OR has_table_privilege('app_decide', 'ads_campana_config_observation', 'INSERT')
       OR has_table_privilege('app_decide', 'ads_campana_config_observation', 'UPDATE')
       OR has_table_privilege('app_decide', 'ads_campana_config_observation', 'DELETE') THEN
        RAISE EXCEPTION '0061: privilegios de ads_campana_config_observation invalidos';
    END IF;
    IF NOT has_table_privilege('app_read', 'v_campana_config_vigente', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'v_campana_config_vigente', 'SELECT')
       OR has_table_privilege('app_ingest', 'v_campana_config_vigente', 'INSERT')
       OR has_table_privilege('app_ingest', 'v_campana_config_vigente', 'UPDATE')
       OR has_table_privilege('app_ingest', 'v_campana_config_vigente', 'DELETE')
       OR has_table_privilege('app_read', 'v_campana_config_vigente', 'INSERT')
       OR has_table_privilege('app_read', 'v_campana_config_vigente', 'UPDATE')
       OR has_table_privilege('app_read', 'v_campana_config_vigente', 'DELETE')
       OR has_table_privilege('app_admin', 'v_campana_config_vigente', 'INSERT')
       OR has_table_privilege('app_admin', 'v_campana_config_vigente', 'UPDATE')
       OR has_table_privilege('app_admin', 'v_campana_config_vigente', 'DELETE')
       OR has_table_privilege('app_decide', 'v_campana_config_vigente', 'INSERT')
       OR has_table_privilege('app_decide', 'v_campana_config_vigente', 'UPDATE')
       OR has_table_privilege('app_decide', 'v_campana_config_vigente', 'DELETE') THEN
        RAISE EXCEPTION '0061: privilegios de v_campana_config_vigente invalidos';
    END IF;
    IF NOT has_sequence_privilege('app_ingest', 'ads_campana_config_observation_id_seq', 'USAGE')
       OR NOT has_sequence_privilege(
           'app_ingest', 'ads_campana_config_observation_id_seq', 'SELECT') THEN
        RAISE EXCEPTION '0061: privilegios de secuencia invalidos';
    END IF;
END
$$;

COMMIT;
