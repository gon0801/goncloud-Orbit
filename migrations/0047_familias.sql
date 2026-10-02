-- 0047: familias de producto en dos niveles (A2).
--
-- `familia` es el arbol (maximo dos niveles: familia -> subfamilia) y
-- `producto_familia` la etiqueta (un producto tiene UNA familia por
-- plataforma). La fabrica toma el tipo_producto del slug de la familia y
-- avisa cuando un grupo mezcla familias (regla operativa de la enmienda:
-- no mezclar familias de margen distinto en una misma campana).
--
-- El tope de dos niveles NO puede ser CHECK (un CHECK no ve otras filas):
-- lo impone el trigger `familia_dos_niveles` (misma razon que los
-- invariantes de tiempo: trigger, nunca CHECK).

BEGIN;

CREATE TABLE familia (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform    platform    NOT NULL,
    nombre      TEXT        NOT NULL,
    slug        TEXT        NOT NULL,
    padre_id    BIGINT      NULL REFERENCES familia(id),
    creada_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT familia_nombre_no_vacio CHECK (nombre <> '' AND slug <> ''),
    CONSTRAINT familia_slug_forma CHECK (slug ~ '^[a-z0-9_]+$'),
    CONSTRAINT familia_sin_ciclo CHECK (padre_id IS DISTINCT FROM id),
    CONSTRAINT familia_nombre_unica UNIQUE (platform, nombre),
    CONSTRAINT familia_slug_unica UNIQUE (platform, slug)
);
COMMENT ON TABLE familia IS
    'Arbol de familias por plataforma (A2): maximo dos niveles '
    '(familia -> subfamilia), impuesto por trigger. El slug es el '
    'tipo_producto de la fabrica (misma forma ^[a-z0-9_]+$ que 0018).';
COMMENT ON COLUMN familia.padre_id IS
    'NULL = familia de primer nivel; NOT NULL = subfamilia cuyo padre '
    'es de primer nivel y de la misma plataforma.';

CREATE TABLE producto_familia (
    product_id  BIGINT      NOT NULL REFERENCES product(id),
    platform    platform    NOT NULL,
    familia_id  BIGINT      NOT NULL REFERENCES familia(id),
    asignada_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    asignada_por TEXT       NOT NULL DEFAULT 'dueno',

    CONSTRAINT producto_familia_una_por_producto UNIQUE (product_id, platform)
);
COMMENT ON TABLE producto_familia IS
    'Etiqueta de familia (A2): un producto tiene UNA familia por '
    'plataforma (puede ser de primer nivel o subfamilia). Escritor '
    'unico: app/familias.py (UI /familias).';
COMMENT ON COLUMN producto_familia.asignada_por IS
    'Actor de la asignacion (auditoria futura A4/A5: bulk UI, fabrica, '
    'motor). Hoy siempre dueno: es el unico escritor via /familias.';

CREATE INDEX ON producto_familia (familia_id);
-- F5 AI-review PR #382: toda FK tiene indice de apoyo (DATABASE.md);
-- el trigger busca hijas por padre_id en cada escritura.
CREATE INDEX ON familia (padre_id);

-- Dos niveles: el padre de una subfamilia es de primer nivel, de la misma
-- plataforma, y nadie convierte en subfamilia a quien ya tiene hijas
-- (eso colgaria un tercer nivel por debajo).
CREATE OR REPLACE FUNCTION familia_dos_niveles() RETURNS trigger AS $$
DECLARE
    padre_padre BIGINT;
    padre_plataforma platform;
BEGIN
    -- F6 AI-review PR #382: mover la plataforma de una familia no puede
    -- dejar etiquetas en otra plataforma (raiz sin hijas incluida).
    IF EXISTS (
        SELECT 1 FROM producto_familia
         WHERE familia_id = NEW.id AND platform IS DISTINCT FROM NEW.platform
    ) THEN
        RAISE EXCEPTION '0047: la familia % tiene etiquetas en otra plataforma',
            NEW.id;
    END IF;
    IF NEW.padre_id IS NULL THEN
        -- F3 AI-review PR #382: un UPDATE de platform en una raiz con
        -- hijas no puede dejarlas en otra plataforma (el COMMENT declara
        -- misma plataforma padre/hijas).
        IF EXISTS (
            SELECT 1 FROM familia
             WHERE padre_id = NEW.id AND platform IS DISTINCT FROM NEW.platform
        ) THEN
            RAISE EXCEPTION '0047: la familia % tiene hijas en otra plataforma',
                NEW.id;
        END IF;
        RETURN NEW;
    END IF;
    -- FOR UPDATE: sin el bloqueo, dos transacciones concurrentes (una que
    -- cuelga una hija de B y otra que cuelga B de A) pasarian la validacion
    -- y formarian un tercer nivel. El UPDATE ya trae el lock de NEW.id.
    SELECT padre_id, platform INTO padre_padre, padre_plataforma
      FROM familia WHERE id = NEW.padre_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0047: la familia padre % no existe', NEW.padre_id;
    END IF;
    IF padre_padre IS NOT NULL THEN
        RAISE EXCEPTION '0047: tercer nivel rechazado (el padre % ya es subfamilia)',
            NEW.padre_id;
    END IF;
    IF padre_plataforma IS DISTINCT FROM NEW.platform THEN
        RAISE EXCEPTION '0047: la subfamilia debe ser de la plataforma del padre';
    END IF;
    IF EXISTS (SELECT 1 FROM familia WHERE padre_id = NEW.id) THEN
        RAISE EXCEPTION '0047: la familia % ya tiene hijas, no puede ser subfamilia',
            NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER familia_dos_niveles
    BEFORE INSERT OR UPDATE OF padre_id, platform ON familia
    FOR EACH ROW EXECUTE FUNCTION familia_dos_niveles();
COMMENT ON TRIGGER familia_dos_niveles ON familia IS
    'Tope de dos niveles + misma plataforma (A2). Un CHECK no ve otras '
    'filas: el invariante vive aqui, con su prueba de regresion.';

-- La etiqueta y la familia son de la misma plataforma.
CREATE OR REPLACE FUNCTION producto_familia_misma_plataforma() RETURNS trigger AS $$
DECLARE
    plataforma_familia platform;
BEGIN
    SELECT platform INTO plataforma_familia FROM familia WHERE id = NEW.familia_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0047: la familia % no existe', NEW.familia_id;
    END IF;
    IF plataforma_familia IS DISTINCT FROM NEW.platform THEN
        RAISE EXCEPTION '0047: la familia % no es de %', NEW.familia_id, NEW.platform;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER producto_familia_misma_plataforma
    BEFORE INSERT OR UPDATE OF familia_id, platform ON producto_familia
    FOR EACH ROW EXECUTE FUNCTION producto_familia_misma_plataforma();

-- Escritura humana (UI /familias via ORBIT_DSN_ADMIN): inserta, reasigna
-- (upsert) y crea familias. Lectura: dashboard (app_read), el motor
-- (app_decide la lee desde A4) y el dueno (app_admin).
GRANT SELECT, INSERT, UPDATE ON familia, producto_familia TO app_admin;
GRANT SELECT ON familia, producto_familia TO app_read, app_decide;
GRANT USAGE, SELECT ON SEQUENCE familia_id_seq TO app_admin;

-- Candado de privilegios (patron 0044/0046, solo has_* para no exigir
-- membresia de rol al migrar).
DO $$
BEGIN
    IF NOT has_table_privilege('app_admin', 'familia', 'INSERT, UPDATE') THEN
        RAISE EXCEPTION '0047: app_admin debe crear familias y reasignar';
    END IF;
    IF NOT has_table_privilege('app_admin', 'producto_familia', 'INSERT, UPDATE') THEN
        RAISE EXCEPTION '0047: app_admin debe etiquetar productos';
    END IF;
    IF NOT has_table_privilege('app_read', 'familia', 'SELECT') THEN
        RAISE EXCEPTION '0047: app_read debe leer el arbol';
    END IF;
    IF NOT has_table_privilege('app_decide', 'familia', 'SELECT') THEN
        RAISE EXCEPTION '0047: app_decide debe leer el arbol (A4)';
    END IF;
    IF has_table_privilege('app_ingest', 'familia', 'INSERT') THEN
        RAISE EXCEPTION '0047: app_ingest no debe crear familias';
    END IF;
END $$;

COMMIT;
