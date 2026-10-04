-- ---------------------------------------------------------------------------
-- 0049 — JEV ADS 01 (1.2): catalogo de fichas aprobadas, revisiones y
-- eventos de pares. PostgreSQL 16.
--
-- Diseno que manda: docs/superpowers/specs/2026-10-03-jev-ads-design.md
-- ("Persistencia, concurrencia y permisos"). Numero 0049 confirmado libre
-- en E/0.2 (el plan decia 0047 porque se escribio antes; el propio plan
-- manda tomar el proximo libre en el momento).
--
-- Cuatro tablas, todas append-only por motor (leccion de 0023: trigger
-- prohibir_mutacion de 0001, no basta la ausencia de GRANT):
--
--   jev_ficha_version     version inmutable de ficha aprobada; cada
--                         correccion inserta version. UNIQUE(sha256) del
--                         contenido canonico hace el registro idempotente
--                         (mismo contenido, misma fila).
--   jev_ficha_revocacion  evento de revocacion; una sola por ficha.
--   jev_revision          snapshot de contexto insertado ANTES del primer
--                         HTTP. UNIQUE(solicitud) impide duplicar contexto
--                         y rechaza reutilizarla con otro payload. El
--                         sujeto es discriminado por CHECK (decision con
--                         decision_id; semillas con plan canonico + hash).
--   jev_par_evento        cada intento por par: intencion (request hash
--                         antes del HTTP), resultado (respuesta validada o
--                         error) y reutilizacion (enlace explicito a un
--                         exito anterior). UNIQUE
--                         revision/par/ordinal/tipo + FK resultado->intencion
--                         hacen auditable cada intento; el trigger
--                         jev_par_evento_encadenado impide resultado sin
--                         intencion de la misma revision y clave, y
--                         reutilizacion que apunte a un fallo o a otra
--                         revision.
--
-- Reglas temporales en TRIGGERS con TimeZone UTC fijado (convencion del
-- repo, nunca CHECK contra la hora): revisar_antes_de >= observado_at en
-- fichas; decided_at/captured_at <= created_at en revisiones.
--
-- Roles de minimo privilegio: el grupo NOLOGIN nuevo `app_jev` (el asesor)
-- lee las entradas de catalogo (ad_entity, ad_entity_state, listing,
-- product) e INSERTA revisiones/eventos; NO toca decisiones, ledger, cola,
-- goals ni bibliotecas. `app_admin` registra y revoca fichas (config
-- humana). `app_read` lee resultados. Ningun grant nuevo para
-- app_ingest/app_decide: la asesoria no entra a su camino.
--
-- Expansiva, NO re-runnable. Reversa: 0049_reversa_jev_ads.sql (solo antes
-- de la primera ficha real; el deploy no la aplica).
-- ---------------------------------------------------------------------------

BEGIN;

-- ===========================================================================
-- 1. FICHAS APROBADAS (versiones y revocaciones)
-- ===========================================================================

CREATE TABLE jev_ficha_version (
    id               UUID PRIMARY KEY,
    producto_id      BIGINT NOT NULL REFERENCES product(id),
    plataforma       platform NOT NULL,
    listings         BIGINT[] NOT NULL,
    hechos           JSONB NOT NULL,
    desconocidos     TEXT[] NOT NULL DEFAULT '{}',
    sha256           TEXT NOT NULL,
    aprobador        TEXT NOT NULL,
    observado_at     TIMESTAMPTZ NOT NULL,
    revisar_antes_de TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sha256),
    CONSTRAINT jev_ficha_plataforma_amazon
        CHECK (plataforma IN ('amazon_mx', 'amazon_us')),
    CONSTRAINT jev_ficha_sha256_formato CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    -- cardinality, NO array_length: con '{}' array_length devuelve NULL y
    -- NULL >= 1 no es FALSE: el CHECK viejo dejaba pasar una ficha que no
    -- cubre nada (revision automatica B2-r4 F1).
    CONSTRAINT jev_ficha_listings_no_vacios CHECK (cardinality(listings) >= 1),
    CONSTRAINT jev_ficha_hechos_en_arreglo CHECK (jsonb_typeof(hechos) = 'array'),
    CONSTRAINT jev_ficha_aprobador_presente CHECK (btrim(aprobador) <> '')
);

COMMENT ON TABLE jev_ficha_version IS
  'JEV ADS 01: ficha de producto aprobada POR VERSION. La unidad de juicio '
  'es termino literal + version de ficha: una correccion inserta version '
  'nueva y las revisiones guardan la version exacta que usaron. El hash '
  'canonico (sha256) del contenido hace el registro idempotente; una ficha '
  'de otra variante no acredita a este producto (el listing cubierto manda, '
  'jam el parecido de nombres).';

COMMENT ON COLUMN jev_ficha_version.listings IS
  'Listings (IDs de listing) que la ficha cubre. El trigger '
  'jev_ficha_version_cobertura exige que cada uno exista, pertenezca al '
  'producto y sea de la misma plataforma, sin NULLs ni duplicados.';

COMMENT ON COLUMN jev_ficha_version.hechos IS
  'Arreglo JSON de {texto, fuente}: atributos con procedencia revisados a '
  'mano. No se extrae compatibilidad de product.name (diseno).';

COMMENT ON COLUMN jev_ficha_version.desconocidos IS
  'Campos que la ficha NO afirma. La ausencia de evidencia jamas se '
  'convierte en incompatibilidad (diseno).';

COMMENT ON COLUMN jev_ficha_version.revisar_antes_de IS
  'Fecha de revision declarada por el responsable segun su fuente; no hay '
  'TTL inventado. Vencida: la ficha deja de acreditar (ficha_vigente).';

-- Cobertura y vigencia basica en TRIGGER con UTC fijado: listings reales
-- del producto y de la plataforma, sin NULLs ni duplicados; y
-- revisar_antes_de >= observado_at (regla temporal, nunca CHECK contra la
-- hora ni contra now()).
CREATE FUNCTION jev_ficha_version_cobertura() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
SET TimeZone = 'UTC'
AS $$
DECLARE
    v_listing BIGINT;
BEGIN
    IF NEW.revisar_antes_de < NEW.observado_at THEN
        RAISE EXCEPTION
            'jev_ficha_version: revisar_antes_de % anterior a observado_at %',
            NEW.revisar_antes_de, NEW.observado_at
            USING ERRCODE = 'check_violation';
    END IF;
    -- COALESCE, NO array_length crudo: con '{}' la comparacion vieja era
    -- NULL y el IF no mordia (mismo hueco del CHECK, B2-r4 F1).
    IF EXISTS (SELECT 1 FROM unnest(NEW.listings) AS x WHERE x IS NULL)
       OR (SELECT count(DISTINCT y) FROM unnest(NEW.listings) AS y)
           <> COALESCE(array_length(NEW.listings, 1), 0) THEN
        RAISE EXCEPTION 'jev_ficha_version: listings con NULL o duplicados'
            USING ERRCODE = 'check_violation';
    END IF;
    FOREACH v_listing IN ARRAY NEW.listings LOOP
        PERFORM 1
          FROM listing
         WHERE id = v_listing
           AND product_id = NEW.producto_id
           AND platform = NEW.plataforma;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'jev_ficha_version: listing % no es del producto % en %',
                v_listing, NEW.producto_id, NEW.plataforma
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER jev_ficha_version_cobertura
    BEFORE INSERT ON jev_ficha_version
    FOR EACH ROW EXECUTE FUNCTION jev_ficha_version_cobertura();

COMMENT ON FUNCTION jev_ficha_version_cobertura IS
  'JEV ADS 01: patron campana_grupo_producto_listing (0018). La FK sola '
  'admite un listing de otro producto o de otra plataforma; la cobertura '
  'de la ficha debe cuadrar. Regla temporal revisar_antes_de >= '
  'observado_at aqui y no en CHECK, con TimeZone UTC fijado.';

CREATE TABLE jev_ficha_revocacion (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ficha_version_id UUID NOT NULL REFERENCES jev_ficha_version(id),
    autor            TEXT NOT NULL CHECK (btrim(autor) <> ''),
    motivo           TEXT NOT NULL CHECK (btrim(motivo) <> ''),
    fecha            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ficha_version_id)
);

COMMENT ON TABLE jev_ficha_revocacion IS
  'JEV ADS 01: revocar una ficha inserta un evento append-only; la ficha '
  'y las revisiones que la usaron permanecen consultables y el lector '
  'historico las marca obsoletas sin reescribir resultados. UNIQUE por '
  'ficha: una segunda revocacion se rechaza (ficha ya revocada).';

-- ===========================================================================
-- 2. REVISIONES Y EVENTOS DE PARES (insertadas antes del primer HTTP)
-- ===========================================================================

CREATE TABLE jev_revision (
    solicitud        UUID NOT NULL UNIQUE,
    sujeto_tipo      TEXT NOT NULL CHECK (sujeto_tipo IN ('decision', 'semillas')),
    decision_id      BIGINT REFERENCES decision(id),
    plan_canonico    JSONB,
    plan_sha256      TEXT,
    fuentes_semillas JSONB,
    censos           JSONB NOT NULL,
    ficha_version_ids UUID[] NOT NULL DEFAULT '{}',
    contrato         JSONB NOT NULL,
    decided_at       TIMESTAMPTZ,
    captured_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jev_revision_sujeto_coherente CHECK (
        (sujeto_tipo = 'decision'
            AND decision_id IS NOT NULL
            AND plan_canonico IS NULL AND plan_sha256 IS NULL
            AND fuentes_semillas IS NULL)
     OR (sujeto_tipo = 'semillas'
            AND decision_id IS NULL
            AND plan_canonico IS NOT NULL AND plan_sha256 IS NOT NULL
            AND fuentes_semillas IS NOT NULL)
    ),
    CONSTRAINT jev_revision_plan_sha_formato
        CHECK (plan_sha256 IS NULL OR plan_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE jev_revision IS
  'JEV ADS 01: snapshot del contexto de una ejecucion solicitada, '
  'insertado ANTES del primer HTTP. UNIQUE(solicitud) impide duplicar '
  'contexto y reutilizarla con otro payload (1.4). Congela censos, '
  'versiones de fichas usadas y contrato (modelo, instrucciones y '
  'serializacion versionadas, orden de opciones, limites y version de '
  'composicion). decided_at (cuando se tomo la decision) y captured_at '
  '(catalogo revisado) se conservan por separado: un catalogo actual no '
  'demuestra que habia cuando se decidio.';

COMMENT ON CONSTRAINT jev_revision_sujeto_coherente ON jev_revision IS
  'Sujeto discriminado: revision de una decision guardada (decision_id) o '
  'de un plan canonico de fabrica (plan + hash + fuentes de semillas), '
  'jamas ambas ni ninguna.';

COMMENT ON COLUMN jev_revision.ficha_version_ids IS
  'Versiones exactas de ficha usadas por la revision. Nunca se buscan '
  'exitos posteriores para mejorar una revision antigua.';

-- Reglas temporales de la revision en TRIGGER con UTC fijado: nada puede
-- ser posterior a la INSERCION REAL (clock_timestamp(), no now(): el
-- DEFAULT now() de created_at fija el inicio de la transaccion y una
-- captura de censo dentro de la misma tx — flujo del diseno: BEGIN, leer
-- censo, captured_at, INSERT antes del primer HTTP — es posterior a ese
-- inicio y anterior al INSERT; comparar contra now() rechazaria revisiones
-- validas). created_at en el futuro se rechaza igual.
CREATE FUNCTION jev_revision_tiempos() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
SET TimeZone = 'UTC'
AS $$
BEGIN
    IF NEW.created_at > clock_timestamp()
       OR NEW.captured_at > clock_timestamp()
       OR NEW.decided_at > clock_timestamp() THEN
        RAISE EXCEPTION
            'jev_revision: decided_at/captured_at/created_at posteriores a la insercion'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(NEW.ficha_version_ids) AS x WHERE x IS NULL)
       OR (SELECT count(DISTINCT y) FROM unnest(NEW.ficha_version_ids) AS y)
           <> COALESCE(array_length(NEW.ficha_version_ids, 1), 0) THEN
        RAISE EXCEPTION 'jev_revision: ficha_version_ids con NULL o duplicados'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER jev_revision_tiempos
    BEFORE INSERT ON jev_revision
    FOR EACH ROW EXECUTE FUNCTION jev_revision_tiempos();

COMMENT ON FUNCTION jev_revision_tiempos IS
  'JEV ADS 01: los dos tiempos de la revision (diseno "Tiempo y '
  'presentacion") no pueden ser posteriores a la insercion; regla en '
  'trigger UTC, no en CHECK contra la hora.';

CREATE TABLE jev_par_evento (
    id               UUID PRIMARY KEY,
    revision_id      UUID NOT NULL REFERENCES jev_revision(solicitud),
    termino_sha256   TEXT NOT NULL CHECK (termino_sha256 ~ '^[0-9a-f]{64}$'),
    ficha_version_id UUID NOT NULL REFERENCES jev_ficha_version(id),
    contrato_sha256  TEXT NOT NULL CHECK (contrato_sha256 ~ '^[0-9a-f]{64}$'),
    ordinal          INT NOT NULL CHECK (ordinal >= 1),
    tipo             TEXT NOT NULL
                     CHECK (tipo IN ('intencion', 'resultado', 'reutilizacion')),
    request_sha256   TEXT,
    respuesta        JSONB,
    error            TEXT,
    intencion_id     UUID REFERENCES jev_par_evento(id),
    reutiliza_id     UUID REFERENCES jev_par_evento(id),
    duracion_ms      INT,
    usage            JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (revision_id, termino_sha256, ficha_version_id,
            contrato_sha256, ordinal, tipo),
    CONSTRAINT jev_par_request_hash_formato
        CHECK (request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT jev_par_duracion_positiva
        CHECK (duracion_ms IS NULL OR duracion_ms >= 0),
    CONSTRAINT jev_par_discriminador CHECK (
        (tipo = 'intencion'
            AND request_sha256 IS NOT NULL
            AND respuesta IS NULL AND error IS NULL
            AND intencion_id IS NULL AND reutiliza_id IS NULL)
     OR (tipo = 'resultado'
            AND intencion_id IS NOT NULL AND reutiliza_id IS NULL
            AND (respuesta IS NULL) <> (error IS NULL))
     OR (tipo = 'reutilizacion'
            AND reutiliza_id IS NOT NULL AND intencion_id IS NULL
            AND request_sha256 IS NULL
            AND respuesta IS NULL AND error IS NULL)
    )
);

COMMENT ON TABLE jev_par_evento IS
  'JEV ADS 01: un intento por par (termino literal hasheado + version de '
  'ficha + hash de contrato). Cada crash/reanudacion registra otro ordinal '
  'sin borrar el anterior. La falta de usage deja costo desconocido; '
  'NINGUN fallo se convierte en incompatibilidad.';

COMMENT ON COLUMN jev_par_evento.termino_sha256 IS
  'Hash del TEXTO LITERAL UTF-8 del termino. No fusiona acentos, numeros '
  'ni terminos parecidos: la clave es exacta.';

COMMENT ON COLUMN jev_par_evento.intencion_id IS
  'FK resultado->intencion de la misma revision y clave: auditable cada '
  'intento. El trigger jev_par_evento_encadenado la exige.';

COMMENT ON COLUMN jev_par_evento.reutiliza_id IS
  'FK de reutilizacion a un RESULTADO con respuesta (exito) de la misma '
  'revision y clave. Reutilizar un fallo, o un exito de otra revision, se '
  'rechaza: nunca se mejoran en silencio revisiones antiguas.';

-- Encadenado auditable: resultado exige intencion de la misma revision y
-- clave; reutilizacion exige exito de la misma revision y clave.
CREATE FUNCTION jev_par_evento_encadenado() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
SET TimeZone = 'UTC'
AS $$
DECLARE
    v_padre jev_par_evento%ROWTYPE;
BEGIN
    IF NEW.tipo = 'resultado' THEN
        SELECT * INTO v_padre FROM jev_par_evento WHERE id = NEW.intencion_id;
        IF NOT FOUND
           OR v_padre.tipo <> 'intencion'
           OR v_padre.revision_id <> NEW.revision_id
           OR v_padre.termino_sha256 <> NEW.termino_sha256
           OR v_padre.ficha_version_id <> NEW.ficha_version_id
           OR v_padre.contrato_sha256 <> NEW.contrato_sha256 THEN
            RAISE EXCEPTION
                'jev_par_evento: resultado sin intencion de la misma revision y clave'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.tipo = 'reutilizacion' THEN
        SELECT * INTO v_padre FROM jev_par_evento WHERE id = NEW.reutiliza_id;
        IF NOT FOUND
           OR v_padre.tipo <> 'resultado'
           OR v_padre.respuesta IS NULL
           OR v_padre.revision_id <> NEW.revision_id
           OR v_padre.termino_sha256 <> NEW.termino_sha256
           OR v_padre.ficha_version_id <> NEW.ficha_version_id
           OR v_padre.contrato_sha256 <> NEW.contrato_sha256 THEN
            RAISE EXCEPTION
                'jev_par_evento: reutilizacion debe apuntar a un exito de la misma revision y clave'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER jev_par_evento_encadenado
    BEFORE INSERT ON jev_par_evento
    FOR EACH ROW EXECUTE FUNCTION jev_par_evento_encadenado();

COMMENT ON FUNCTION jev_par_evento_encadenado IS
  'JEV ADS 01: impide resultado sin intencion de la misma revision y '
  'clave, y reutilizacion que apunte a un fallo o a otra revision '
  '(diseno "Persistencia, concurrencia y permisos").';

-- ===========================================================================
-- 3. APPEND-ONLY POR MOTOR (4 tablas x UPDATE/DELETE + TRUNCATE; leccion 0023)
-- ===========================================================================

CREATE TRIGGER jev_ficha_version_append_only
    BEFORE UPDATE OR DELETE ON jev_ficha_version
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_ficha_version_append_only_truncate
    BEFORE TRUNCATE ON jev_ficha_version
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_ficha_revocacion_append_only
    BEFORE UPDATE OR DELETE ON jev_ficha_revocacion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_ficha_revocacion_append_only_truncate
    BEFORE TRUNCATE ON jev_ficha_revocacion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_revision_append_only
    BEFORE UPDATE OR DELETE ON jev_revision
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_revision_append_only_truncate
    BEFORE TRUNCATE ON jev_revision
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_par_evento_append_only
    BEFORE UPDATE OR DELETE ON jev_par_evento
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_par_evento_append_only_truncate
    BEFORE TRUNCATE ON jev_par_evento
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- ===========================================================================
-- 4. INDICES (decision/fecha, hash de plan/fecha, clave de par/fecha, FKs)
-- ===========================================================================

CREATE INDEX jev_revision_decision_idx ON jev_revision (decision_id, created_at);
CREATE INDEX jev_revision_plan_idx ON jev_revision (plan_sha256, created_at);
CREATE INDEX jev_ficha_version_producto_idx
    ON jev_ficha_version (producto_id, plataforma, created_at);
CREATE INDEX jev_par_evento_revision_idx ON jev_par_evento (revision_id);
CREATE INDEX jev_par_evento_clave_idx
    ON jev_par_evento (termino_sha256, ficha_version_id, contrato_sha256, created_at);
CREATE INDEX jev_par_evento_intencion_idx ON jev_par_evento (intencion_id);
CREATE INDEX jev_par_evento_reutiliza_idx ON jev_par_evento (reutiliza_id);

-- ===========================================================================
-- 5. ROLES Y GRANTS DE MINIMO PRIVILEGIO
-- ===========================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_jev') THEN
        CREATE ROLE app_jev NOLOGIN;   -- el asesor: catalogo + sus tablas
    END IF;
END
$$;

-- Entradas del censo (lectura) y sus propias tablas (revision/eventos).
GRANT SELECT ON ad_entity, ad_entity_state, listing, product TO app_jev;
GRANT SELECT, INSERT ON jev_revision, jev_par_evento TO app_jev;
GRANT SELECT ON jev_ficha_version, jev_ficha_revocacion TO app_jev;

-- Administracion de fichas aprobadas: registrar y revocar (config humana).
GRANT SELECT, INSERT ON jev_ficha_version, jev_ficha_revocacion TO app_admin;
GRANT SELECT ON jev_revision, jev_par_evento TO app_admin;

-- Lecturas: dashboard y analisis ven resultados; no escriben.
GRANT SELECT ON jev_ficha_version, jev_ficha_revocacion, jev_revision, jev_par_evento
    TO app_read;

COMMENT ON ROLE app_jev IS
  'JEV ADS 01: grupo NOLOGIN del asesor. Lee entradas de catalogo e '
  'INSERTA revisiones/eventos; SIN permisos sobre decisiones, cola, '
  'ledger, goals ni bibliotecas (diseno). La asesoria no recibe '
  'credenciales Ads y no se llama desde el ciclo ni desde apply.';

-- ===========================================================================
-- 6. VERIFICACION (falla la migracion si un grant falta; patron 0034/0048)
-- ===========================================================================

DO $$
BEGIN
    IF NOT has_table_privilege('app_jev', 'jev_revision', 'INSERT') THEN
        RAISE EXCEPTION '0049: app_jev debe insertar jev_revision';
    END IF;
    IF NOT has_table_privilege('app_jev', 'jev_par_evento', 'INSERT') THEN
        RAISE EXCEPTION '0049: app_jev debe insertar jev_par_evento';
    END IF;
    IF NOT has_table_privilege('app_jev', 'ad_entity', 'SELECT') THEN
        RAISE EXCEPTION '0049: app_jev debe leer ad_entity (censo)';
    END IF;
    IF NOT has_table_privilege('app_admin', 'jev_ficha_version', 'INSERT') THEN
        RAISE EXCEPTION '0049: app_admin debe registrar fichas';
    END IF;
    IF NOT has_table_privilege('app_read', 'jev_revision', 'SELECT') THEN
        RAISE EXCEPTION '0049: app_read debe leer revisiones';
    END IF;
END
$$;

COMMIT;
