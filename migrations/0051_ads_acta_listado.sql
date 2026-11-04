-- ---------------------------------------------------------------------------
-- 0051 — JEV ADS 02 S.1: acta de listado de la ingesta de estructura.
-- PostgreSQL 16.
--
-- Cada corrida ok de `ingest structure` deja escrito que listo Amazon: por
-- plataforma, cuantos ad groups y product ads llegaron y cuantos declaro
-- Amazon (totalResults, o NULL si no lo declaro); por ad group, cuantos
-- anuncios vivos se escribieron, la huella de sus ids y cuantos no
-- archivados se descartaron. Una corrida que falla no deja acta: los
-- INSERT corren en la misma transaccion que sella ingest_run. Nada lee
-- todavia estas filas; el consumidor llega en S.4 (probar_roster).
--
-- DDL del diseno (seccion "Persistencia") mas lo que el diseno no trae:
-- CHECK de conteos no negativos, CHECK de formato de huella_vivos e
-- indice sobre ads_listado_grupo (ad_group_id). La FK compuesta
-- (ingest_run_id, platform) queda encabezada por la PK en ingest_run_id;
-- la tabla referenciada es append-only, asi que ningun DELETE la recorre.
-- ---------------------------------------------------------------------------

BEGIN;

CREATE TABLE ads_listado_plataforma (
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_run(id),
    platform      platform NOT NULL,
    ad_groups_recibidos    INTEGER NOT NULL,
    ad_groups_declarados   INTEGER,
    product_ads_recibidos  INTEGER NOT NULL,
    product_ads_declarados INTEGER,
    product_ads_sin_grupo  INTEGER NOT NULL,
    PRIMARY KEY (ingest_run_id, platform),
    CONSTRAINT ads_listado_plataforma_conteos_no_negativos CHECK (
        ad_groups_recibidos >= 0
        AND product_ads_recibidos >= 0
        AND product_ads_sin_grupo >= 0
        AND (ad_groups_declarados IS NULL OR ad_groups_declarados >= 0)
        AND (product_ads_declarados IS NULL OR product_ads_declarados >= 0)
    )
);

CREATE TABLE ads_listado_grupo (
    ingest_run_id BIGINT NOT NULL,
    platform platform NOT NULL,
    ad_group_id   BIGINT NOT NULL REFERENCES ad_entity(id),
    anuncios_vivos INTEGER NOT NULL,
    huella_vivos   TEXT NOT NULL,
    descartados    INTEGER NOT NULL,
    PRIMARY KEY (ingest_run_id, ad_group_id),
    FOREIGN KEY (ingest_run_id, platform) REFERENCES ads_listado_plataforma,
    CONSTRAINT ads_listado_grupo_conteos_no_negativos CHECK (
        anuncios_vivos >= 0 AND descartados >= 0
    ),
    CONSTRAINT ads_listado_grupo_huella_formato CHECK (
        huella_vivos ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX ads_listado_grupo_ad_group_idx ON ads_listado_grupo (ad_group_id);

CREATE TRIGGER ads_listado_plataforma_append_only
    BEFORE UPDATE OR DELETE ON ads_listado_plataforma
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ads_listado_plataforma_append_only_truncate
    BEFORE TRUNCATE ON ads_listado_plataforma
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ads_listado_grupo_append_only
    BEFORE UPDATE OR DELETE ON ads_listado_grupo
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ads_listado_grupo_append_only_truncate
    BEFORE TRUNCATE ON ads_listado_grupo
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE ads_listado_plataforma IS
  'JEV ADS 02 S.1: acta por plataforma de cada corrida de ingesta de '
  'estructura. Recibidos = items que llegaron; declarados = totalResults de '
  'Amazon o NULL si no lo declaro (regla 3: NULL no es cero). Sin fila de '
  'una corrida ok, S.4 no puede probar el roster de ningun grupo.';
COMMENT ON TABLE ads_listado_grupo IS
  'JEV ADS 02 S.1: acta por ad group escrito de cada corrida de ingesta. '
  'Vivos = product ads ENABLED/PAUSED materializados; huella_vivos = sha256 '
  'hex de sus adId ordenados; descartados = no archivados del payload que '
  'ningun filtro dejo escribir. Un grupo sin fila no tiene roster probado.';

-- Solo app_ingest escribe: es el rol con que corre toda llamada a
-- sync_structure. SELECT explicito a los cuatro (0040), sin depender solo
-- del privilegio por omision de 0001.
GRANT SELECT ON ads_listado_plataforma, ads_listado_grupo
    TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT ON ads_listado_plataforma, ads_listado_grupo TO app_ingest;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'ads_listado_plataforma', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_listado_plataforma', 'INSERT')
       OR has_table_privilege('app_ingest', 'ads_listado_plataforma', 'UPDATE')
       OR has_table_privilege('app_ingest', 'ads_listado_plataforma', 'DELETE') THEN
        RAISE EXCEPTION '0051: privilegios de ads_listado_plataforma invalidos';
    END IF;
    IF NOT has_table_privilege('app_read', 'ads_listado_grupo', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ads_listado_grupo', 'INSERT')
       OR has_table_privilege('app_ingest', 'ads_listado_grupo', 'UPDATE')
       OR has_table_privilege('app_ingest', 'ads_listado_grupo', 'DELETE') THEN
        RAISE EXCEPTION '0051: privilegios de ads_listado_grupo invalidos';
    END IF;
END
$$;

COMMIT;
