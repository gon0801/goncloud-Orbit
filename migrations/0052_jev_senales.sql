-- ---------------------------------------------------------------------------
-- 0052 — JEV ADS 02 S.3: tablas de senales, login y cierre del perimetro.
-- PostgreSQL 16.
--
-- Diseno que manda: docs/superpowers/specs/2026-10-04-jev-ads-02-design.md
-- ("Persistencia"). DDL del diseno mas lo que el diseno da por entendido:
-- triggers append-only por tabla (modelo 0040), indice por cada FK que la
-- PK no encabece, REVOKE explicito tras cada CREATE y bloque DO final.
--
-- Una corrida del job es una jev_revision con sujeto_tipo = 'lote'. Las
-- cinco tablas nuevas son append-only y no tienen FK hacia tablas del
-- motor: guardan el numero que les pasa el lector, sin candados sobre la
-- cola. La unica FK de Jev al motor sigue siendo
-- jev_revision.decision_id (0049), que una fila lote deja en NULL.
--
-- Perimetro: 0001 concede SELECT por omision sobre toda tabla nueva, asi
-- que hoy app_decide y app_ingest leen las cuatro tablas de 0049. Esta
-- migracion revoca ese acceso (tablas nuevas, vista y las cuatro de 0049)
-- y el bloque final falla si el ciclo lee cualquier jev_* o si app_jev
-- lee decision, apply_queue o search_term_observation.
--
-- Expansiva, NO re-runnable. Reversa: 0052_reversa_jev_senales.sql (solo
-- antes de la primera fila lote; despues se revierte el interruptor).
-- ---------------------------------------------------------------------------

BEGIN;

-- ===========================================================================
-- 1. SUJETO LOTE EN jev_revision
-- ===========================================================================

ALTER TABLE jev_revision
  DROP CONSTRAINT jev_revision_sujeto_tipo_check,
  ADD  CONSTRAINT jev_revision_sujeto_tipo_check
       CHECK (sujeto_tipo IN ('decision', 'semillas', 'lote')),
  DROP CONSTRAINT jev_revision_sujeto_coherente,
  ADD  CONSTRAINT jev_revision_sujeto_coherente CHECK (
       (sujeto_tipo = 'decision' AND decision_id IS NOT NULL AND plan_canonico IS NULL
            AND plan_sha256 IS NULL AND fuentes_semillas IS NULL)
    OR (sujeto_tipo = 'semillas' AND decision_id IS NULL AND plan_canonico IS NOT NULL
            AND plan_sha256 IS NOT NULL AND fuentes_semillas IS NOT NULL)
    OR (sujeto_tipo = 'lote' AND decision_id IS NULL AND plan_canonico IS NULL
            AND plan_sha256 IS NULL AND fuentes_semillas IS NULL));

-- Al soltar el CHECK se pierde su COMMENT ON CONSTRAINT: se reescribe.
COMMENT ON CONSTRAINT jev_revision_sujeto_coherente ON jev_revision IS
  'Sujeto discriminado: revision de una decision guardada (decision_id), de '
  'un plan canonico de fabrica (plan + hash + fuentes de semillas) o de una '
  'corrida del job jev-senales (lote, con todo sujeto ajeno en NULL). Una '
  'fila lote llena censos (resumen del plan de la corrida) y contrato, que '
  'son NOT NULL. Jamas dos sujetos a la vez.';

-- ===========================================================================
-- 2. TABLAS NUEVAS (DDL del diseno)
-- ===========================================================================

CREATE TABLE jev_roster (
    sha256 TEXT PRIMARY KEY, plataforma platform NOT NULL,
    ad_group_id BIGINT NOT NULL REFERENCES ad_entity(id),
    miembros JSONB NOT NULL, ficha_version_ids UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());

REVOKE ALL ON TABLE jev_roster FROM app_decide, app_ingest;

COMMENT ON TABLE jev_roster IS
  'JEV ADS 02 S.3: hecho 1 por contenido (sin synced_at y sin la prueba). '
  'El sha256 identifica el contenido del roster; la prueba de S.4 decide '
  'si vale.';

CREATE TABLE jev_senal (
    id UUID PRIMARY KEY,
    lote_id UUID NOT NULL REFERENCES jev_revision(solicitud),
    plataforma platform NOT NULL, ad_group_id BIGINT NOT NULL REFERENCES ad_entity(id),
    termino TEXT NOT NULL, termino_sha256 TEXT NOT NULL, insumos_sha256 TEXT NOT NULL,
    regla_version INTEGER NOT NULL,
    roster_sha256 TEXT NOT NULL REFERENCES jev_roster(sha256),
    roster_probado BOOLEAN NOT NULL, roster_prueba JSONB NOT NULL,
    contrato_sha256 TEXT NOT NULL,
    relevancia TEXT NOT NULL CHECK (relevancia IN ('corresponde','ajena','sin_veredicto','no_evaluada')),
    motivos_jev TEXT[] NOT NULL DEFAULT '{}',
    productos_ok BIGINT[] NOT NULL DEFAULT '{}',
    evaluados INTEGER NOT NULL, miembros INTEGER NOT NULL, juicio_ids UUID[] NOT NULL DEFAULT '{}',
    ventana_inicio DATE, ventana_fin DATE, datos_hasta TIMESTAMPTZ,
    moneda currency NOT NULL,
    clics BIGINT, gasto money_amount, ordenes INTEGER,
    otros_que_venden JSONB NOT NULL DEFAULT '[]', ordenes_otros INTEGER, otros_sin_dato INTEGER NOT NULL,
    historial JSONB, ordenes_historial INTEGER,
    lectura TEXT NOT NULL CHECK (lectura IN
        ('vendio_aqui','vende_en_otro','relevante_sin_venta','ajena','sin_lectura')),
    motivos_lectura TEXT[] NOT NULL DEFAULT '{}',
    valida_hasta TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ad_group_id, termino_sha256, insumos_sha256),
    CONSTRAINT jev_senal_moneda_de_plataforma CHECK (
        (plataforma = 'amazon_mx' AND moneda = 'MXN') OR (plataforma = 'amazon_us' AND moneda = 'USD')),
    CONSTRAINT jev_senal_ajena_exige_roster CHECK (relevancia <> 'ajena'
        OR (roster_probado AND miembros > 0 AND evaluados = miembros
            AND cardinality(motivos_jev) = 0 AND cardinality(productos_ok) = 0)),
    -- IS NOT DISTINCT FROM, no "=": con NULL, "ordenes_otros = 0" da NULL y el CHECK pasaria.
    CONSTRAINT jev_senal_lectura_ajena CHECK (lectura <> 'ajena'
        OR (relevancia = 'ajena' AND otros_sin_dato = 0
            AND ordenes IS NOT DISTINCT FROM 0 AND ordenes_otros IS NOT DISTINCT FROM 0
            AND ordenes_historial IS NOT DISTINCT FROM 0)));

REVOKE ALL ON TABLE jev_senal FROM app_decide, app_ingest;

COMMENT ON TABLE jev_senal IS
  'JEV ADS 02 S.3: senal por busqueda-en-grupo, inmutable. Guarda los ids '
  'de los eventos que uso; nada se mejora despues. Todo importe de la '
  'fila, tambien en JSONB, va en moneda (regla 4 de CONTEXTO). NULL = '
  'dato faltante, nunca cero.';
COMMENT ON COLUMN jev_senal.insumos_sha256 IS
  'Hash canonico de todo lo que entra a leer mas el dia en que se sello. '
  'Dos corridas del mismo dia con los mismos datos no duplican la fila.';
COMMENT ON COLUMN jev_senal.valida_hasta IS
  'Lo que ocurra primero: 36 horas desde que se sello, o el vencimiento '
  'mas proximo de las fichas de su roster.';

CREATE TABLE jev_aviso (
    id UUID PRIMARY KEY, lote_id UUID NOT NULL REFERENCES jev_revision(solicitud),
    cola_id BIGINT NOT NULL, decision_id BIGINT NOT NULL,
    senal_origen_id UUID NOT NULL REFERENCES jev_senal(id),
    senal_destino_id UUID REFERENCES jev_senal(id),
    lectura TEXT NOT NULL,
    texto TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (cola_id, lectura));

REVOKE ALL ON TABLE jev_aviso FROM app_decide, app_ingest;

COMMENT ON TABLE jev_aviso IS
  'JEV ADS 02 S.3: lo que se le quiso decir al dueno, tal cual. Sin FK al '
  'motor: guarda los numeros que le pasa el lector.';
COMMENT ON COLUMN jev_aviso.lectura IS
  'La anunciada: la del origen; en harvest, la del destino (sin_lectura '
  'si el destino no se puede leer).';

CREATE TABLE jev_aviso_entrega (
    aviso_id UUID PRIMARY KEY REFERENCES jev_aviso(id),
    entregado_at TIMESTAMPTZ NOT NULL DEFAULT now());

REVOKE ALL ON TABLE jev_aviso_entrega FROM app_decide, app_ingest;

COMMENT ON TABLE jev_aviso_entrega IS
  'JEV ADS 02 S.3: existe solo si Telegram acepto el mensaje.';

CREATE TABLE jev_corrida (
    lote_id UUID NOT NULL REFERENCES jev_revision(solicitud),
    evento TEXT NOT NULL CHECK (evento IN ('inicio','fin')),
    cierre TEXT, resumen JSONB, at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (lote_id, evento));

REVOKE ALL ON TABLE jev_corrida FROM app_decide, app_ingest;

COMMENT ON TABLE jev_corrida IS
  'JEV ADS 02 S.3: rastro para /salud, dos eventos por corrida.';

CREATE VIEW jev_senal_vigente AS
SELECT DISTINCT ON (s.ad_group_id, s.termino_sha256)
       s.id AS senal_id, s.plataforma, s.ad_group_id, s.termino, s.lectura, s.created_at,
       (s.valida_hasta > now() AND NOT EXISTS (
            SELECT 1 FROM jev_roster ro JOIN jev_ficha_revocacion rv
                ON rv.ficha_version_id = ANY (ro.ficha_version_ids)
             WHERE ro.sha256 = s.roster_sha256)) AS vigente
  FROM jev_senal s ORDER BY s.ad_group_id, s.termino_sha256, s.created_at DESC, s.id DESC;

REVOKE ALL ON TABLE jev_senal_vigente FROM app_decide, app_ingest;

COMMENT ON VIEW jev_senal_vigente IS
  'JEV ADS 02 S.3: LA vigencia, definida una sola vez. La senal mas '
  'reciente por grupo y busqueda, vigente si no vencio y su roster no '
  'cita una ficha revocada.';

-- El permiso por omision de 0001 tambien cubre las cuatro tablas de 0049.
REVOKE ALL ON TABLE jev_ficha_version, jev_ficha_revocacion, jev_revision, jev_par_evento
    FROM app_decide, app_ingest;

-- ===========================================================================
-- 3. APPEND-ONLY POR MOTOR (5 tablas x UPDATE/DELETE + TRUNCATE; modelo 0040)
-- ===========================================================================

CREATE TRIGGER jev_roster_append_only
    BEFORE UPDATE OR DELETE ON jev_roster
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_roster_append_only_truncate
    BEFORE TRUNCATE ON jev_roster
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_senal_append_only
    BEFORE UPDATE OR DELETE ON jev_senal
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_senal_append_only_truncate
    BEFORE TRUNCATE ON jev_senal
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_aviso_append_only
    BEFORE UPDATE OR DELETE ON jev_aviso
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_aviso_append_only_truncate
    BEFORE TRUNCATE ON jev_aviso
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_aviso_entrega_append_only
    BEFORE UPDATE OR DELETE ON jev_aviso_entrega
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_aviso_entrega_append_only_truncate
    BEFORE TRUNCATE ON jev_aviso_entrega
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

CREATE TRIGGER jev_corrida_append_only
    BEFORE UPDATE OR DELETE ON jev_corrida
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER jev_corrida_append_only_truncate
    BEFORE TRUNCATE ON jev_corrida
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

-- ===========================================================================
-- 4. INDICES (FKs no encabezadas por la PK + parcial de intenciones)
-- ===========================================================================

CREATE INDEX jev_roster_ad_group_idx ON jev_roster (ad_group_id);
CREATE INDEX jev_senal_lote_idx ON jev_senal (lote_id);
CREATE INDEX jev_senal_roster_idx ON jev_senal (roster_sha256);
-- ad_group_id de jev_senal va encabezado por
-- UNIQUE (ad_group_id, termino_sha256, insumos_sha256): sin indice propio.
CREATE INDEX jev_aviso_lote_idx ON jev_aviso (lote_id);
CREATE INDEX jev_aviso_origen_idx ON jev_aviso (senal_origen_id);
CREATE INDEX jev_aviso_destino_idx ON jev_aviso (senal_destino_id);
-- aviso_id de jev_aviso_entrega y lote_id de jev_corrida van encabezados
-- por su PK: sin indice propio.
CREATE INDEX jev_par_evento_intencion_fecha_idx
    ON jev_par_evento (created_at) WHERE tipo = 'intencion';

-- ===========================================================================
-- 5. GRANTS DE MINIMO PRIVILEGIO
-- ===========================================================================

GRANT SELECT, INSERT ON jev_roster, jev_senal, jev_aviso, jev_aviso_entrega, jev_corrida
    TO app_jev;
GRANT SELECT ON TABLE jev_senal_vigente TO app_jev;
GRANT SELECT ON jev_roster, jev_senal, jev_aviso, jev_aviso_entrega, jev_corrida
    TO app_read, app_admin;
GRANT SELECT ON TABLE jev_senal_vigente TO app_read, app_admin;

-- ===========================================================================
-- 6. VERIFICACION (falla si falta o sobra un privilegio; patron 0034/0049)
-- ===========================================================================

DO $$
DECLARE
    v_nuevas TEXT[] := ARRAY['jev_roster', 'jev_senal', 'jev_aviso',
                             'jev_aviso_entrega', 'jev_corrida'];
    v_0049 TEXT[] := ARRAY['jev_ficha_version', 'jev_ficha_revocacion',
                           'jev_revision', 'jev_par_evento'];
    v_motor TEXT[] := ARRAY['decision', 'apply_queue', 'search_term_observation'];
    v_todas TEXT[];
    v_t TEXT;
BEGIN
    v_todas := v_nuevas || v_0049;
    FOREACH v_t IN ARRAY v_nuevas LOOP
        IF NOT has_table_privilege('app_jev', v_t, 'SELECT')
           OR NOT has_table_privilege('app_jev', v_t, 'INSERT')
           OR has_table_privilege('app_jev', v_t, 'UPDATE')
           OR has_table_privilege('app_jev', v_t, 'DELETE') THEN
            RAISE EXCEPTION '0052: privilegios de app_jev sobre % invalidos', v_t;
        END IF;
        IF NOT has_table_privilege('app_read', v_t, 'SELECT')
           OR NOT has_table_privilege('app_admin', v_t, 'SELECT') THEN
            RAISE EXCEPTION '0052: app_read/app_admin deben leer %', v_t;
        END IF;
    END LOOP;
    IF NOT has_table_privilege('app_jev', 'jev_senal_vigente', 'SELECT')
       OR NOT has_table_privilege('app_read', 'jev_senal_vigente', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'jev_senal_vigente', 'SELECT') THEN
        RAISE EXCEPTION '0052: app_jev/app_read/app_admin deben leer jev_senal_vigente';
    END IF;
    FOREACH v_t IN ARRAY v_todas LOOP
        IF has_table_privilege('app_decide', v_t, 'SELECT')
           OR has_table_privilege('app_ingest', v_t, 'SELECT') THEN
            RAISE EXCEPTION '0052: app_decide/app_ingest no deben leer %', v_t;
        END IF;
    END LOOP;
    IF has_table_privilege('app_decide', 'jev_senal_vigente', 'SELECT')
       OR has_table_privilege('app_ingest', 'jev_senal_vigente', 'SELECT') THEN
        RAISE EXCEPTION '0052: app_decide/app_ingest no deben leer jev_senal_vigente';
    END IF;
    -- to_regclass: apply_queue vive en 0002 y una base de prueba puede no traerla.
    FOREACH v_t IN ARRAY v_motor LOOP
        IF to_regclass('public.' || v_t) IS NOT NULL
           AND has_table_privilege('app_jev', v_t, 'SELECT') THEN
            RAISE EXCEPTION '0052: app_jev no debe leer %', v_t;
        END IF;
    END LOOP;
END
$$;

COMMIT;
