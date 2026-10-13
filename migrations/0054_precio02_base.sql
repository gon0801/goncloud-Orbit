-- REPRICING 02 corte 0.a: base de datos del motor de precios en todas las
-- plataformas. Modelo: docs/evidencia/repricing-02/diseno/datos.sql, con los
-- triggers y el bloque DO que ahi solo se describen.
--
-- ADITIVA y CONVIVE con el codigo que ya corre: ninguna CHECK exige una
-- columna nueva (los CHECK exigentes llegan con la 0055 y la 0058, junto al
-- codigo que escribe esas columnas). Dos ensanches declarados del diseno:
-- (x1) `precio_goal_unico_por_fecha` pasa de UNIQUE a indice unico parcial
--      (ignora las anuladas del mismo dia: reeditar es cerrar y sembrar);
-- (x2) `precio_cambio_confirmado_por_valido` admite 'lectura_viva'.
BEGIN;

-- -----------------------------------------------------------------------------
-- 1. IDENTIDAD: la publicacion es la unidad de precio; sus miembros, aparte
-- -----------------------------------------------------------------------------
CREATE TABLE listing_miembro (
    listing_id   BIGINT   NOT NULL,
    platform     platform NOT NULL,
    variante_ext TEXT     NOT NULL DEFAULT '',
    seller_sku   TEXT     NOT NULL,
    product_id   BIGINT   REFERENCES product (id),
    activo       BOOLEAN  NOT NULL DEFAULT true,
    visto_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (listing_id, variante_ext),
    CONSTRAINT listing_miembro_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT listing_miembro_solo_meli CHECK (platform = 'meli')
);
CREATE INDEX listing_miembro_por_producto ON listing_miembro (product_id);
COMMENT ON TABLE listing_miembro IS
  'REPRICING 02: productos que se venden por una publicacion de MeLi (N:M). '
  'Mutable por la ingesta de catalogo, como `listing` (es identidad, no '
  'historia). `product_id` NULL = SKU de la variante sin producto: la unidad '
  'sale `no_evaluado(variante_sin_mapear)` con el SKU a la vista. El producto '
  'ancla de `listing.product_id` es identidad; el costo sale de aqui.';

CREATE VIEW v_precio_unidad_miembro AS
    SELECT l.id AS listing_id, l.platform, ''::text AS variante_ext,
           l.seller_sku, l.product_id
      FROM listing l
     WHERE l.platform <> 'meli'
    UNION ALL
    SELECT m.listing_id, m.platform, m.variante_ext, m.seller_sku, m.product_id
      FROM listing_miembro m
     WHERE m.activo;

COMMENT ON VIEW v_precio_unidad_miembro IS
  'REPRICING 02: UNA respuesta a que productos vende cada unidad, para Amazon '
  '(la fila de listing) y MeLi (sus miembros activos). El trigger de coherencia '
  'de la decision y la cobertura la leen; nadie mas arma esta union.';

-- -----------------------------------------------------------------------------
-- 2. LEDGER: a que publicacion pertenece cada evento
-- -----------------------------------------------------------------------------
CREATE TABLE ledger_event_atribucion (
    ledger_event_id BIGINT PRIMARY KEY REFERENCES ledger_event (id),
    platform        platform NOT NULL,
    listing_id      BIGINT NOT NULL,
    variante_ext    TEXT,
    resuelto_por    TEXT NOT NULL,
    ingest_run_id   BIGINT NOT NULL REFERENCES ingest_run (id),
    CONSTRAINT ledger_atribucion_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT ledger_atribucion_resuelto_valido
        CHECK (resuelto_por IN ('sku', 'externo_unico', 'producto_unico',
                                'canal_orden'))
);
CREATE INDEX ledger_atribucion_por_listing ON ledger_event_atribucion (listing_id);
CREATE TRIGGER ledger_atribucion_append_only
    BEFORE UPDATE OR DELETE ON ledger_event_atribucion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
COMMENT ON TABLE ledger_event_atribucion IS
  'REPRICING 02: a que publicacion pertenece cada evento del ledger. '
  'Append-only junto a ledger_event; la ingesta inserta con ON CONFLICT DO '
  'NOTHING: un evento atribuido no se re-atribuye. Lo que hoy no se puede '
  'decidir NO se escribe (una fila indeterminado quedaria fija para siempre): '
  'la siguiente ingesta lo reintenta cuando llegue el dato que lo resuelve.';

CREATE VIEW v_precio_venta_unidad AS
    SELECT e.platform, a.listing_id, a.variante_ext, e.product_id,
           e.event_date, e.quantity, e.amount, e.amount_currency, e.order_id,
           a.resuelto_por
      FROM ledger_event e
      LEFT JOIN ledger_event_atribucion a ON a.ledger_event_id = e.id
     WHERE e.kind = 'sale';
COMMENT ON VIEW v_precio_venta_unidad IS
  'REPRICING 02: ventas por UNIDAD de precio. Las ventas del producto sin '
  'publicacion decidible salen con listing_id NULL: el adaptador las cuenta '
  'en HistoriaUnidad.sin_atribuir.';

-- -----------------------------------------------------------------------------
-- 3. MELI: observacion diaria de la publicacion
-- -----------------------------------------------------------------------------
CREATE TABLE meli_publicacion_observation (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id       TEXT NOT NULL,
    metric_date   DATE NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL,
    estado        TEXT NOT NULL,
    precio        money_amount,
    moneda        currency,
    stock         INTEGER,
    tipo_publicacion TEXT,
    categoria_id  TEXT,
    logistica     TEXT,
    visitas       INTEGER,
    variantes     JSONB NOT NULL DEFAULT '[]',
    ingest_run_id BIGINT REFERENCES ingest_run (id),
    CONSTRAINT meli_pub_obs_unica UNIQUE (item_id, observed_at),
    CONSTRAINT meli_pub_obs_precio_con_moneda CHECK ((precio IS NULL) = (moneda IS NULL)),
    CONSTRAINT meli_pub_obs_precio_positivo CHECK (precio IS NULL OR precio > 0)
);
CREATE INDEX meli_pub_obs_ultima ON meli_publicacion_observation (item_id, observed_at DESC);
CREATE TRIGGER meli_pub_obs_append_only
    BEFORE UPDATE OR DELETE ON meli_publicacion_observation
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
COMMENT ON TABLE meli_publicacion_observation IS
  'REPRICING 02 (carril M): lo que la corrida necesita ver de una publicacion '
  '(precio del dia, si se podia comprar, visitas). Activa canonica de MeLi = '
  'ultima observacion con estado active. Columnas de dominio, no el JSON de '
  'la API. Sin datos del comprador.';

-- -----------------------------------------------------------------------------
-- 4. ENVIO (L) con origen y escenario con fees partidos
-- -----------------------------------------------------------------------------
ALTER TABLE precio_envio_muestra
    ADD COLUMN canal         estimacion_canal NOT NULL,
    ADD COLUMN origen        TEXT NOT NULL,
    ADD COLUMN familia_id    BIGINT REFERENCES familia (id),
    ADD COLUMN donantes      INTEGER,
    ADD COLUMN regla_version TEXT NOT NULL,
    ADD COLUMN excl_multiproducto INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN excl_sin_producto  INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN cargos_otros       INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN cobrado_n          INTEGER,
    ADD COLUMN cobrado_mediana    money_amount,
    ADD COLUMN cobrado_mediana_currency currency;
ALTER TABLE precio_envio_muestra
    ADD CONSTRAINT precio_muestra_origen_valido
        CHECK (origen IN ('propio', 'familia', 'marketplace')),
    ADD CONSTRAINT precio_muestra_origen_coherente CHECK (
        (origen = 'propio'      AND donantes IS NULL AND familia_id IS NULL)
     OR (origen = 'familia'     AND donantes >= 1    AND familia_id IS NOT NULL)
     OR (origen = 'marketplace' AND donantes >= 1    AND familia_id IS NULL)),
    ADD CONSTRAINT precio_muestra_cobrado_con_moneda
        CHECK ((cobrado_mediana IS NULL) = (cobrado_mediana_currency IS NULL));
CREATE INDEX precio_muestra_ultima
    ON precio_envio_muestra (product_id, platform, canal, calculada_at DESC);
COMMENT ON COLUMN precio_envio_muestra.canal IS
  'REPRICING 02: universo de la muestra (fba|fbm|meli); la 0039 solo conocia '
  'la plataforma.';

ALTER TABLE estimacion_escenario
    ADD COLUMN envio_muestra_id   BIGINT REFERENCES precio_envio_muestra (id),
    ADD COLUMN logistica_origen   TEXT,
    ADD COLUMN miembro_product_id BIGINT REFERENCES product (id),
    ADD COLUMN fee_variable       NUMERIC(14, 4),
    ADD COLUMN fee_fijo           NUMERIC(14, 4),
    ADD COLUMN fee_cotizable      BOOLEAN;
ALTER TABLE estimacion_escenario
    ADD CONSTRAINT estimacion_escenario_logistica_origen_valido
        CHECK (logistica_origen IS NULL
               OR logistica_origen IN ('politica', 'propio', 'familia', 'marketplace')),
    ADD CONSTRAINT estimacion_escenario_muestra_segun_origen
        CHECK ((logistica_origen IN ('propio', 'familia', 'marketplace'))
               = (envio_muestra_id IS NOT NULL)) NOT VALID;
COMMENT ON COLUMN estimacion_escenario.miembro_product_id IS
  'REPRICING 02: publicacion con varios miembros: el que manda (el de P* mas '
  'alto). La cuenta de la fila es la suya; las de los demas van en '
  'componentes. NULL = publicacion de un solo producto.';
COMMENT ON COLUMN estimacion_escenario.fee_variable IS
  'REPRICING 02: fee que escala con el precio, ya partido por el que produce '
  'el escenario. Hasta F.3 queda NULL en toda fila: el codigo de hoy no la '
  'escribe y la 0054 convive con el.';

-- -----------------------------------------------------------------------------
-- 5. CORRIDA: una fila por ejecucion y su medicion de compuerta
-- -----------------------------------------------------------------------------
CREATE TABLE precio_corrida (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform           platform NOT NULL,
    fecha              DATE NOT NULL,
    owner              TEXT NOT NULL,
    estado             TEXT NOT NULL DEFAULT 'abierta',
    iniciada_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrada_at         TIMESTAMPTZ,
    config_version_id  BIGINT NOT NULL REFERENCES config_version (id),
    modo_global        TEXT NOT NULL,
    decisiones INTEGER, movidas INTEGER, virtuales INTEGER,
    retenidas  INTEGER, errores INTEGER, saltadas_por_edicion INTEGER,
    resumen_enviado_at TIMESTAMPTZ,
    CONSTRAINT precio_corrida_estado_valido
        CHECK (estado IN ('abierta', 'cerrada', 'abortada')),
    CONSTRAINT precio_corrida_modo_valido
        CHECK (modo_global IN ('off', 'shadow', 'live')),
    CONSTRAINT precio_corrida_cierre_exige_conteos
        CHECK (estado <> 'cerrada'
               OR (cerrada_at IS NOT NULL AND decisiones IS NOT NULL
                   AND movidas IS NOT NULL AND virtuales IS NOT NULL
                   AND retenidas IS NOT NULL AND errores IS NOT NULL
                   AND saltadas_por_edicion IS NOT NULL))
);
CREATE INDEX precio_corrida_por_dia ON precio_corrida (platform, fecha, id);
CREATE UNIQUE INDEX precio_corrida_un_resumen_por_dia
    ON precio_corrida (platform, fecha) WHERE resumen_enviado_at IS NOT NULL;

CREATE FUNCTION precio_corrida_fecha_utc() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.fecha := (now() AT TIME ZONE 'UTC')::date;
    RETURN NEW;
END;
$$;
CREATE TRIGGER precio_corrida_fecha_utc
    BEFORE INSERT ON precio_corrida
    FOR EACH ROW EXECUTE FUNCTION precio_corrida_fecha_utc();

CREATE FUNCTION precio_corrida_solo_avanza() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_corrida %: una corrida no se BORRA; el historial de '
            'resumes y retenciones cuelga de aqui.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.estado IS DISTINCT FROM OLD.estado THEN
        IF (OLD.estado, NEW.estado) NOT IN (('abierta', 'cerrada'),
                                            ('abierta', 'abortada')) THEN
            RAISE EXCEPTION
                'precio_corrida %: estado % -> % fuera de la progresion '
                '(abierta -> cerrada | abortada, una vez).', OLD.id,
                OLD.estado, NEW.estado
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.cerrada_at IS DISTINCT FROM OLD.cerrada_at
       AND (OLD.cerrada_at IS NOT NULL OR NEW.cerrada_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_corrida %: cerrada_at se sella una vez (NULL -> valor).',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.resumen_enviado_at IS DISTINCT FROM OLD.resumen_enviado_at
       AND (OLD.resumen_enviado_at IS NOT NULL OR NEW.resumen_enviado_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_corrida %: resumen_enviado_at se sella una vez (NULL -> valor).',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.decisiones IS DISTINCT FROM OLD.decisiones
       AND (OLD.decisiones IS NOT NULL OR NEW.decisiones IS NULL) THEN
        RAISE EXCEPTION
            'precio_corrida %: decisiones se sella una vez.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF ROW(NEW.movidas, NEW.virtuales, NEW.retenidas, NEW.errores,
           NEW.saltadas_por_edicion)
       IS DISTINCT FROM
       ROW(OLD.movidas, OLD.virtuales, OLD.retenidas, OLD.errores,
           OLD.saltadas_por_edicion)
       AND ROW(OLD.movidas, OLD.virtuales, OLD.retenidas, OLD.errores,
               OLD.saltadas_por_edicion)
           IS DISTINCT FROM ROW(NULL, NULL, NULL, NULL, NULL) THEN
        RAISE EXCEPTION
            'precio_corrida %: los conteos se sellan una vez (NULL -> valor).',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF ROW(NEW.id, NEW.platform, NEW.fecha, NEW.owner, NEW.iniciada_at,
           NEW.config_version_id, NEW.modo_global)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.platform, OLD.fecha, OLD.owner, OLD.iniciada_at,
           OLD.config_version_id, OLD.modo_global) THEN
        RAISE EXCEPTION
            'precio_corrida %: de una corrida solo avanzan el estado y los '
            'sellos de cierre; identidad y config son inmutables.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER precio_corrida_solo_avanza
    BEFORE UPDATE OR DELETE ON precio_corrida
    FOR EACH ROW EXECUTE FUNCTION precio_corrida_solo_avanza();
COMMENT ON TABLE precio_corrida IS
  'REPRICING 02: cada ejecucion de la corrida (la diaria y sus repasos). De '
  'aqui salen el resumen diario, el bloque de /salud y el selector --corrida '
  'de la reversa en lote.';

CREATE TABLE precio_compuerta_medicion (
    corrida_id  BIGINT NOT NULL REFERENCES precio_corrida (id),
    platform    platform NOT NULL,
    fecha       DATE NOT NULL,
    canal       estimacion_canal NOT NULL,
    medidas     INTEGER NOT NULL,
    nuevas      INTEGER NOT NULL,
    causa       TEXT,
    insumo      TEXT,
    detalle     TEXT,
    medida_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    avisada_at  TIMESTAMPTZ,
    PRIMARY KEY (corrida_id, canal),
    CONSTRAINT precio_medicion_conteos_validos CHECK (medidas >= 0 AND nuevas >= 0),
    CONSTRAINT precio_medicion_causa_valida
        CHECK (causa IS NULL OR causa IN ('movimiento_masivo', 'insumo_sistemico')),
    CONSTRAINT precio_medicion_insumo_solo_sistemico
        CHECK ((insumo IS NOT NULL) = (causa IS NOT DISTINCT FROM 'insumo_sistemico')),
    CONSTRAINT precio_medicion_aviso_solo_si_retuvo
        CHECK (avisada_at IS NULL OR causa IS NOT NULL)
);
CREATE UNIQUE INDEX precio_medicion_un_aviso_por_dia
    ON precio_compuerta_medicion (platform, fecha, canal, causa)
    WHERE avisada_at IS NOT NULL;

CREATE FUNCTION precio_medicion_copia_corrida() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_platform platform;
    v_fecha DATE;
BEGIN
    SELECT c.platform, c.fecha INTO v_platform, v_fecha
      FROM precio_corrida c WHERE c.id = NEW.corrida_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_compuerta_medicion: la corrida % no existe', NEW.corrida_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    NEW.platform := v_platform;
    NEW.fecha := v_fecha;
    RETURN NEW;
END;
$$;
CREATE TRIGGER precio_medicion_copia_corrida
    BEFORE INSERT ON precio_compuerta_medicion
    FOR EACH ROW EXECUTE FUNCTION precio_medicion_copia_corrida();

CREATE FUNCTION precio_medicion_solo_sella_aviso() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_compuerta_medicion: la medicion es historia, no se borra.'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.avisada_at IS DISTINCT FROM OLD.avisada_at
       AND (OLD.avisada_at IS NOT NULL OR NEW.avisada_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_compuerta_medicion %/%: avisada_at se sella una vez '
            '(NULL -> valor).', OLD.corrida_id, OLD.canal
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF ROW(NEW.corrida_id, NEW.platform, NEW.fecha, NEW.canal, NEW.medidas,
           NEW.nuevas, NEW.causa, NEW.insumo, NEW.detalle, NEW.medida_at)
       IS DISTINCT FROM
       ROW(OLD.corrida_id, OLD.platform, OLD.fecha, OLD.canal, OLD.medidas,
           OLD.nuevas, OLD.causa, OLD.insumo, OLD.detalle, OLD.medida_at) THEN
        RAISE EXCEPTION
            'precio_compuerta_medicion %/%: de la medicion solo se sella '
            'avisada_at; lo demas es inmutable.', OLD.corrida_id, OLD.canal
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER precio_medicion_solo_sella_aviso
    BEFORE UPDATE OR DELETE ON precio_compuerta_medicion
    FOR EACH ROW EXECUTE FUNCTION precio_medicion_solo_sella_aviso();
COMMENT ON TABLE precio_compuerta_medicion IS
  'REPRICING 02: una fila con causa ES el disparo del fusible de ese universo '
  'en esa corrida; sin causa, lo que la pantalla dice («7 de 160»). Un aviso '
  'por universo, causa y dia: el indice parcial lo deja sellar una vez.';

-- -----------------------------------------------------------------------------
-- 6. RETENCION y LIBERACION
-- -----------------------------------------------------------------------------
CREATE TABLE precio_retencion (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id BIGINT NOT NULL REFERENCES precio_decision (id),
    corrida_id  BIGINT NOT NULL REFERENCES precio_corrida (id),
    causa       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_retencion_una_por_corrida UNIQUE (decision_id, corrida_id),
    CONSTRAINT precio_retencion_causa_valida
        CHECK (causa IN ('movimiento_masivo', 'insumo_sistemico',
                         'apagador', 'corte_errores', 'goal_cambiado'))
);
CREATE INDEX precio_retencion_por_corrida ON precio_retencion (corrida_id);
CREATE TRIGGER precio_retencion_append_only
    BEFORE UPDATE OR DELETE ON precio_retencion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE FUNCTION precio_retencion_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_resultado TEXT;
    v_canal estimacion_canal;
    v_platform platform;
    v_decision_date DATE;
BEGIN
    SELECT d.resultado, d.canal, d.platform, d.decision_date
      INTO v_resultado, v_canal, v_platform, v_decision_date
      FROM precio_decision d WHERE d.id = NEW.decision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % no existe', NEW.decision_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_resultado NOT IN ('subir', 'bajar') THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % es % y solo una subir/bajar se '
            'retiene (retener no reescribe la decision).', NEW.decision_id,
            v_resultado USING ERRCODE = 'check_violation';
    END IF;
    IF (v_platform, v_decision_date) IS DISTINCT FROM (
        SELECT c.platform, c.fecha FROM precio_corrida c WHERE c.id = NEW.corrida_id
    ) THEN
        RAISE EXCEPTION
            'precio_retencion: la decision % y la corrida % no son de la '
            'misma plataforma y el mismo dia.', NEW.decision_id, NEW.corrida_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.causa IN ('movimiento_masivo', 'insumo_sistemico') THEN
        IF NOT EXISTS (
            SELECT 1 FROM precio_compuerta_medicion m
             WHERE m.corrida_id = NEW.corrida_id
               AND m.canal = v_canal
               AND m.causa = NEW.causa
        ) THEN
            RAISE EXCEPTION
                'precio_retencion: la causa % exige la medicion de esa corrida '
                'y ese canal.', NEW.causa USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER precio_retencion_coherente
    BEFORE INSERT ON precio_retencion
    FOR EACH ROW EXECUTE FUNCTION precio_retencion_coherente();

CREATE TABLE precio_compuerta_liberacion (
    corrida_id   BIGINT PRIMARY KEY REFERENCES precio_corrida (id),
    liberado_por TEXT NOT NULL,
    nota         TEXT NOT NULL,
    liberado_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_liberacion_nota_no_vacia CHECK (btrim(nota) <> '')
);
CREATE TRIGGER precio_liberacion_append_only
    BEFORE UPDATE OR DELETE ON precio_compuerta_liberacion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();

CREATE FUNCTION precio_liberacion_exige_retencion() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM precio_compuerta_medicion m
         WHERE m.corrida_id = NEW.corrida_id AND m.causa IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'precio_compuerta_liberacion: la corrida % no retuvo nada; no hay '
            'que soltar.', NEW.corrida_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER precio_liberacion_exige_retencion
    BEFORE INSERT ON precio_compuerta_liberacion
    FOR EACH ROW EXECUTE FUNCTION precio_liberacion_exige_retencion();

CREATE VIEW v_precio_decision_liberada AS
    SELECT DISTINCT r.decision_id
      FROM precio_retencion r
      JOIN precio_compuerta_liberacion l ON l.corrida_id = r.corrida_id
     WHERE r.causa IN ('movimiento_masivo', 'insumo_sistemico');
COMMENT ON VIEW v_precio_decision_liberada IS
  'REPRICING 02: decisiones cuya retencion por fusible ya fue soltada por el '
  'dueno; la corrida las aplica en el repaso sin volver a contarlas.';

-- -----------------------------------------------------------------------------
-- 7. precio_decision: columnas nuevas y goal_id validado
-- -----------------------------------------------------------------------------
ALTER TABLE precio_decision
    ADD COLUMN corrida_id      BIGINT REFERENCES precio_corrida (id),
    ADD COLUMN goal_id         BIGINT REFERENCES precio_goal (id),
    ADD COLUMN l_origen        TEXT,
    ADD COLUMN senal_evidencia TEXT,
    ADD COLUMN verificacion    TEXT;
ALTER TABLE precio_decision
    ADD CONSTRAINT precio_decision_l_origen_valido
        CHECK (l_origen IS NULL
               OR l_origen IN ('politica', 'propio', 'familia', 'marketplace')),
    ADD CONSTRAINT precio_decision_evidencia_valida
        CHECK (senal_evidencia IS NULL
               OR senal_evidencia IN ('unidades', 'trafico')),
    ADD CONSTRAINT precio_decision_verificacion_valida
        CHECK (verificacion IS NULL OR verificacion IN ('cotizada', 'lineal')),
    ADD CONSTRAINT precio_decision_muestra_segun_origen
        CHECK ((l_origen IN ('propio', 'familia', 'marketplace'))
               = (envio_muestra_id IS NOT NULL)) NOT VALID;
CREATE INDEX precio_decision_por_dia ON precio_decision (platform, decision_date);
CREATE INDEX precio_decision_por_corrida ON precio_decision (corrida_id);

CREATE OR REPLACE FUNCTION precio_decision_coherente() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_lid BIGINT;
    v_plat platform;
    v_prod BIGINT;
    v_mues_prod BIGINT;
    v_mues_plat platform;
    v_goal NUMERIC(6, 4);
    v_goal_mode precio_mode;
    v_hoy DATE := (now() AT TIME ZONE 'UTC')::date;
BEGIN
    IF NEW.escenario_id IS NOT NULL THEN
        SELECT e.listing_id, e.platform INTO v_lid, v_plat
          FROM estimacion_escenario e
         WHERE e.id = NEW.escenario_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: escenario_id % inexistente', NEW.escenario_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: escenario % es del listing %/% y no de %/%',
                NEW.escenario_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.fee_observation_id IS NOT NULL THEN
        SELECT f.listing_id, f.platform INTO v_lid, v_plat
          FROM estimacion_fee_observation f
         WHERE f.id = NEW.fee_observation_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: fee_observation_id % inexistente',
                NEW.fee_observation_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: fee % es del listing %/% y no de %/%',
                NEW.fee_observation_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.cotizacion_id IS NOT NULL THEN
        SELECT c.listing_id, c.platform INTO v_lid, v_plat
          FROM precio_cotizacion c
         WHERE c.id = NEW.cotizacion_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: cotizacion_id % inexistente', NEW.cotizacion_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: cotizacion % es del listing %/% y no de %/%',
                NEW.cotizacion_id, v_lid, v_plat, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.envio_muestra_id IS NOT NULL THEN
        SELECT m.product_id, m.platform INTO v_mues_prod, v_mues_plat
          FROM precio_envio_muestra m
         WHERE m.id = NEW.envio_muestra_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: envio_muestra_id % inexistente',
                NEW.envio_muestra_id
                USING ERRCODE = 'check_violation';
        END IF;
        v_prod := NEW.product_id;
        IF v_prod IS NULL THEN
            SELECT l.product_id INTO v_prod
              FROM listing l
             WHERE l.id = NEW.listing_id AND l.platform = NEW.platform;
        END IF;
        IF (v_mues_prod, v_mues_plat) IS DISTINCT FROM (v_prod, NEW.platform) THEN
            RAISE EXCEPTION
                'precio_decision: muestra % es del product %/% y no de %/%',
                NEW.envio_muestra_id, v_mues_prod, v_mues_plat, v_prod, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.product_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM v_precio_unidad_miembro m
             WHERE m.listing_id = NEW.listing_id
               AND m.platform = NEW.platform
               AND m.product_id = NEW.product_id
        ) THEN
            RAISE EXCEPTION
                'precio_decision: product_id % no es miembro del listing %',
                NEW.product_id, NEW.listing_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.goal_id IS NOT NULL THEN
        SELECT g.listing_id, g.platform, g.mode, g.margen_goal_pct
          INTO v_lid, v_plat, v_goal_mode, v_goal
          FROM precio_goal g
         WHERE g.id = NEW.goal_id
           AND g.valid_from <= v_hoy
           AND (g.valid_to IS NULL OR v_hoy < g.valid_to);
        IF NOT FOUND
           OR (v_lid, v_plat) IS DISTINCT FROM (NEW.listing_id, NEW.platform)
           OR NOT (v_goal_mode = NEW.mode
                   OR (v_goal_mode = 'live' AND NEW.mode = 'shadow')) THEN
            RAISE EXCEPTION
                'precio_decision: el goal_id % ya no es el vigente de hoy '
                'para %/% (reeditado o cerrado: goal_reemplazado)',
                NEW.goal_id, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT g.margen_goal_pct INTO v_goal
          FROM precio_goal g
         WHERE g.listing_id = NEW.listing_id
           AND g.platform = NEW.platform
           AND (g.mode = NEW.mode
                OR (g.mode = 'live' AND NEW.mode = 'shadow'))
           AND g.valid_from <= v_hoy
           AND (g.valid_to IS NULL OR v_hoy < g.valid_to);
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'precio_decision: sin goal vigente en % del mode % para %/%',
                v_hoy, NEW.mode, NEW.listing_id, NEW.platform
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    NEW.goal := v_goal;

    RETURN NEW;
END;
$$;
COMMENT ON FUNCTION precio_decision_coherente IS
  'REPRICING 02 0054: igual a la 0039 con tres predicados nuevos: (0) si la '
  'decision trae goal_id, se valida ESE goal (mismo listing, vigente hoy, '
  'mode compatible: un goal live ampara una decision shadow y jamas al '
  'revés); sin goal_id se conserva la busqueda de hoy con el mismo predicado '
  'de modo; (a) un goal shadow JAMAS ampara una decision live; (b) el '
  'product_id puede ser cualquier MIEMBRO de la unidad y la muestra de envio '
  'se compara contra el miembro que manda. `goal` sigue siendo un numero con '
  'una fuente: lo asigna este trigger.';

-- -----------------------------------------------------------------------------
-- 8. precio_cambio: cierre por lectura viva y rastro de corrida/lote
-- -----------------------------------------------------------------------------
ALTER TABLE precio_cambio DROP CONSTRAINT precio_cambio_confirmado_por_valido;
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_confirmado_por_valido
    CHECK (confirmado_por IS NULL
           OR confirmado_por IN ('observacion', 'virtual', 'lectura_viva'));
ALTER TABLE precio_cambio
    ADD COLUMN corrida_id   BIGINT REFERENCES precio_corrida (id),
    ADD COLUMN lote_reversa TEXT;
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_lote_solo_reversa
    CHECK (lote_reversa IS NULL OR es_reversa);
CREATE INDEX precio_cambio_por_corrida ON precio_cambio (corrida_id);
CREATE INDEX precio_cambio_por_lote ON precio_cambio (lote_reversa)
    WHERE lote_reversa IS NOT NULL;

CREATE OR REPLACE FUNCTION precio_cambio_sella_transicion() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_cambio es el ledger de TODO lo que salió (o habría salido) '
            'a la plataforma: una fila no se borra.'
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NOT OLD.aplicado THEN
        RAISE EXCEPTION
            'precio_cambio %: virtual (aplicado = false) inmutable tras nacer: '
            'nació cerrado y consume cooldown y freno tal cual nació.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.estado IS DISTINCT FROM OLD.estado THEN
        IF (OLD.estado, NEW.estado) IN (
            ('pendiente', 'enviado'),
            ('pendiente', 'error'),
            ('enviado', 'confirmado'),
            ('enviado', 'no_confirmado')
        ) THEN
            NULL;
        ELSE
            RAISE EXCEPTION
                'precio_cambio %: estado % -> % fuera de la progresión sellada '
                '(pendiente -> enviado | error, enviado -> confirmado | '
                'no_confirmado; el cierre es por observación del día '
                'siguiente, S6).', OLD.id, OLD.estado, NEW.estado
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF NEW.enviado_at IS DISTINCT FROM OLD.enviado_at
       AND (OLD.enviado_at IS NOT NULL OR NEW.enviado_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: enviado_at ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.ack IS DISTINCT FROM OLD.ack
       AND (OLD.ack IS NOT NULL OR NEW.ack IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: ack ya sellado o se intenta des-sellar: '
            'el sello es UNA vez y solo NULL -> valor.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_precio IS DISTINCT FROM OLD.readback_precio
       AND (OLD.readback_precio IS NOT NULL OR NEW.readback_precio IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_precio ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_precio_currency IS DISTINCT FROM OLD.readback_precio_currency
       AND (OLD.readback_precio_currency IS NOT NULL
            OR NEW.readback_precio_currency IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_precio_currency ya sellado o se intenta '
            'des-sellar.', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_estado IS DISTINCT FROM OLD.readback_estado
       AND (OLD.readback_estado IS NOT NULL OR NEW.readback_estado IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_estado ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.readback_at IS DISTINCT FROM OLD.readback_at
       AND (OLD.readback_at IS NOT NULL OR NEW.readback_at IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: readback_at ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.confirmado_por IS DISTINCT FROM OLD.confirmado_por
       AND (OLD.confirmado_por IS NOT NULL OR NEW.confirmado_por IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: confirmado_por ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.error_code IS DISTINCT FROM OLD.error_code
       AND (OLD.error_code IS NOT NULL OR NEW.error_code IS NULL) THEN
        RAISE EXCEPTION
            'precio_cambio %: error_code ya sellado o se intenta des-sellar.',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;

    IF ROW(NEW.id, NEW.decision_id, NEW.listing_id, NEW.platform,
           NEW.precio_antes, NEW.precio_antes_currency,
           NEW.precio_observado_antes, NEW.precio_observado_antes_currency,
           NEW.precio_despues, NEW.precio_despues_currency,
           NEW.aplicado, NEW.es_reversa, NEW.reversa_de, NEW.corrida_id,
           NEW.lote_reversa, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.decision_id, OLD.listing_id, OLD.platform,
           OLD.precio_antes, OLD.precio_antes_currency,
           OLD.precio_observado_antes, OLD.precio_observado_antes_currency,
           OLD.precio_despues, OLD.precio_despues_currency,
           OLD.aplicado, OLD.es_reversa, OLD.reversa_de, OLD.corrida_id,
           OLD.lote_reversa, OLD.created_at) THEN
        RAISE EXCEPTION
            'precio_cambio %: de una fila del cambio SOLO se sellan estado '
            '(por la progresión) y columnas de sello (NULL -> valor, una vez). '
            'El cambio nace ANTES de la escritura y no se reescribe.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
COMMENT ON FUNCTION precio_cambio_sella_transicion IS
  'REPRICING 02 0054: la 0039 con `corrida_id` y `lote_reversa` como '
  'columnas inmutables de la fila. La progresión de estados y los sellos '
  'NULL -> valor una vez no cambian.';

-- -----------------------------------------------------------------------------
-- 9. precio_goal: reedicion el mismo dia y rastro de siembra
-- -----------------------------------------------------------------------------
ALTER TABLE precio_goal DROP CONSTRAINT precio_goal_unico_por_fecha;
CREATE UNIQUE INDEX precio_goal_unico_por_fecha
    ON precio_goal (listing_id, platform, valid_from)
    WHERE valid_to IS DISTINCT FROM valid_from;
ALTER TABLE precio_goal
    ADD COLUMN origen       TEXT,
    ADD COLUMN lote         TEXT,
    ADD COLUMN m_referencia NUMERIC(8, 4);
ALTER TABLE precio_goal ADD CONSTRAINT precio_goal_origen_valido
    CHECK (origen IS NULL OR origen IN ('manual', 'margen_de_hoy', 'cambio_de_modo'));

CREATE OR REPLACE FUNCTION precio_goal_solo_cierra_vigencia() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'precio_goal es histórico: una vigencia publicada no se BORRA. Se '
            'CIERRA (UPDATE valid_to) y el goal nuevo entra como fila nueva.'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.valid_to IS DISTINCT FROM OLD.valid_to
       AND (OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL) THEN
        RAISE EXCEPTION
            'precio_goal %: valid_to sólo puede pasar de NULL a una fecha, una vez. '
            'Mover el corte de una vigencia cerrada o reabrirla reescribe el '
            'período en que ese goal aplicó.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF ROW(NEW.id, NEW.listing_id, NEW.platform, NEW.margen_goal_pct,
           NEW.mode, NEW.valid_from, NEW.creado_por, NEW.go_literal,
           NEW.origen, NEW.lote, NEW.m_referencia, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.id, OLD.listing_id, OLD.platform, OLD.margen_goal_pct,
           OLD.mode, OLD.valid_from, OLD.creado_por, OLD.go_literal,
           OLD.origen, OLD.lote, OLD.m_referencia, OLD.created_at) THEN
        RAISE EXCEPTION
            'precio_goal %: de una fila publicada sólo se puede cerrar la vigencia '
            '(valid_to). Corregir el goal es INSERTAR una fila nueva con '
            'vigencia nueva.', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
COMMENT ON FUNCTION precio_goal_solo_cierra_vigencia IS
  'REPRICING 02 0054: la 0039 con `origen`, `lote` y `m_referencia` como '
  'columnas inmutables de la fila publicada. La unica mutacion sigue siendo '
  'cerrar la vigencia.';

CREATE VIEW v_precio_goal_vigente AS
    SELECT g.id AS goal_id, g.listing_id, g.platform, g.margen_goal_pct,
           g.mode, g.valid_from, g.origen, g.m_referencia
      FROM precio_goal g
     WHERE g.valid_from <= (now() AT TIME ZONE 'UTC')::date
       AND (g.valid_to IS NULL OR (now() AT TIME ZONE 'UTC')::date < g.valid_to);
COMMENT ON VIEW v_precio_goal_vigente IS
  'REPRICING 02: el predicado de vigencia del goal en un solo lugar. Lo leen '
  'la corrida, la cobertura y la pantalla; los tres dejaron de copiarlo.';

-- -----------------------------------------------------------------------------
-- 10. GRANTs (mismos cuatro roles; ninguno nuevo)
-- -----------------------------------------------------------------------------
GRANT SELECT ON listing_miembro, ledger_event_atribucion,
                meli_publicacion_observation, precio_corrida, precio_retencion,
                precio_compuerta_medicion, precio_compuerta_liberacion,
                v_precio_unidad_miembro, v_precio_venta_unidad,
                v_precio_goal_vigente, v_precio_decision_liberada
    TO app_read, app_decide, app_admin, app_ingest;

GRANT INSERT ON precio_corrida, precio_retencion, precio_compuerta_medicion
    TO app_decide;
GRANT UPDATE (estado, cerrada_at, decisiones, movidas, virtuales, retenidas,
              errores, saltadas_por_edicion, resumen_enviado_at)
    ON precio_corrida TO app_decide;
GRANT UPDATE (avisada_at) ON precio_compuerta_medicion TO app_decide;

GRANT INSERT ON precio_compuerta_liberacion TO app_admin;

GRANT INSERT, UPDATE ON listing_miembro TO app_ingest;
GRANT INSERT ON ledger_event_atribucion, meli_publicacion_observation TO app_ingest;
GRANT SELECT ON precio_envio_muestra TO app_ingest;

GRANT USAGE ON SEQUENCE precio_corrida_id_seq, precio_retencion_id_seq TO app_decide;
GRANT USAGE ON SEQUENCE meli_publicacion_observation_id_seq TO app_ingest;

-- -----------------------------------------------------------------------------
-- 11. Candado de la migracion (patron del DO final de la 0039): siembra y
-- revierte, y FALLA si alguno de estos casos no se comporta como debe. Al
-- salir no queda ni una fila sembrada: todo vive en el bloque interior que
-- termina con la excepcion marcada que el exterior traga.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_prod BIGINT;
    v_lid1 BIGINT;
    v_lid2 BIGINT;
    v_lid3 BIGINT;
    v_lid4 BIGINT;
    v_lid5 BIGINT;
    v_cfg BIGINT;
    v_run BIGINT;
    v_ofid BIGINT;
    v_feeid BIGINT;
    v_costo BIGINT;
    v_pol BIGINT;
    v_esc BIGINT;
    v_gid BIGINT;
    v_gid_cerrado BIGINT;
    v_gid_reeditado BIGINT;
    v_did BIGINT;
    v_did_sombra BIGINT;
    v_corrida BIGINT;
    v_corrida_resumen BIGINT;
    v_corrida_aviso1 BIGINT;
    v_corrida_aviso2 BIGINT;
    v_corrida_sin_retencion BIGINT;
    v_hoy DATE := (now() AT TIME ZONE 'UTC')::date;
    v_ok BOOLEAN := false;
    v_n BIGINT;
BEGIN
    BEGIN
        INSERT INTO config_version (label, settings)
        VALUES ('zz-candado-0054', '{}'::jsonb) RETURNING id INTO v_cfg;
        INSERT INTO product (odoo_sku, name)
        VALUES ('zz-candado-0054', 'Candado 0054') RETURNING id INTO v_prod;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0054-1', 'zz-SKU-0054-1')
        RETURNING id INTO v_lid1;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0054-2', 'zz-SKU-0054-2')
        RETURNING id INTO v_lid2;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0054-3', 'zz-SKU-0054-3')
        RETURNING id INTO v_lid3;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0054-4', 'zz-SKU-0054-4')
        RETURNING id INTO v_lid4;
        INSERT INTO listing (product_id, platform, external_id, seller_sku)
        VALUES (v_prod, 'amazon_mx', 'zz-ASIN-0054-5', 'zz-SKU-0054-5')
        RETURNING id INTO v_lid5;
        INSERT INTO ingest_run (source, finished_at, rows_written, ok)
        VALUES ('accounting_sku_costs', now(), 1, true)
        RETURNING id INTO v_run;
        INSERT INTO estimacion_oferta_observation (listing_id, platform, seller_sku,
            asin, canal, price_amount, price_currency, fetched_at, observed_at,
            source_event_id, canonical_input, context_fingerprint)
        VALUES (v_lid1, 'amazon_mx', 'zz-SKU-0054-1', 'zz-ASIN-0054-1', 'fba',
            100.00, 'MXN', now() - interval '2 hours', now(),
            'zz-candado-0054-oferta', '{}'::jsonb, 'zz-huella')
        RETURNING id INTO v_ofid;
        INSERT INTO estimacion_fee_observation (oferta_observation_id, listing_id,
            platform, seller_sku, asin, canal, quoted_price_amount,
            quoted_price_currency, total_fees, fees_estimated_at, fetched_at,
            observed_at, estado, source_event_id, canonical_input,
            context_fingerprint)
        VALUES (v_ofid, v_lid1, 'amazon_mx', 'zz-SKU-0054-1', 'zz-ASIN-0054-1',
            'fba', 100.00, 'MXN', 12.00, now(), now(), now(), 'success',
            'zz-candado-0054-fee', '{}'::jsonb, 'zz-huella')
        RETURNING id INTO v_feeid;
        INSERT INTO sku_cost (product_id, cost_amount, cost_currency, includes_tax,
            valid_from, ingest_run_id)
        VALUES (v_prod, 20.00, 'MXN', false, '2026-01-01', v_run)
        RETURNING id INTO v_costo;
        INSERT INTO estimacion_politica_version (label, universo, formula_version,
            settings, valid_from)
        VALUES ('zz-candado-0054', 'amazon_mx/fba', 'v1',
            '{"iva_divisor": 1.16, "isr_tasa": 0.025}'::jsonb, '2026-01-01')
        RETURNING id INTO v_pol;

        -- Convivencia (positivo): el INSERT de escenario disponible de hoy,
        -- sin ninguna columna nueva, ENTRA.
        INSERT INTO estimacion_escenario
            (listing_id, platform, seller_sku, asin, canal, valoracion_date,
             observed_at, politica_version_id, formula_version,
             oferta_observation_id, fee_observation_id, sku_cost_id,
             costo_validation_run_id, costo_validated_at, moneda, contribucion,
             contribucion_pct, estado, motivos, componentes, exclusiones,
             canonical_input, context_fingerprint, source_event_id)
        VALUES (v_lid1, 'amazon_mx', 'zz-SKU-0054-1', 'zz-ASIN-0054-1', 'fba',
            v_hoy, now(), v_pol, 'v1', v_ofid, v_feeid, v_costo, v_run, now(),
            'MXN', 55.00, 0.55, 'disponible', '[]'::jsonb, '[]'::jsonb,
            '[]'::jsonb, '{}'::jsonb, 'zz-huella', 'zz-candado-0054-esc')
        RETURNING id INTO v_esc;

        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por, go_literal)
        VALUES (v_lid1, 'amazon_mx', 0.30, 'live', '2026-09-01', 'candado',
            'go candado 0054') RETURNING id INTO v_gid;
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por)
        VALUES (v_lid2, 'amazon_mx', 0.30, 'shadow', '2026-09-01', 'candado');
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por, go_literal)
        VALUES (v_lid3, 'amazon_mx', 0.30, 'live', '2026-09-01', 'candado',
            'go candado 0054');
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, valid_to, creado_por, go_literal)
        VALUES (v_lid4, 'amazon_mx', 0.30, 'live', '2026-09-01', v_hoy - 1,
            'candado', 'go candado 0054') RETURNING id INTO v_gid_cerrado;

        -- Convivencia (positivo): el INSERT de decision de hoy, sin ninguna
        -- columna nueva, ENTRA.
        INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,
            goal, m_actual, p_actual, p_actual_currency, p_objetivo,
            p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor,
            i_currency, c_valor, c_currency, f_valor, f_currency, l_valor,
            l_currency, r_valor, r_currency)
        VALUES (v_lid1, 'amazon_mx', 'subir', NULL, 'live', 0.30, 0.25, 100.00,
            'MXN', 110.00, 'MXN', 110.00, 'MXN', 10.00, 'MXN', 20.00, 'MXN',
            12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN') RETURNING id INTO v_did;

        -- (positivo): decision shadow bajo goal live ENTRA.
        INSERT INTO precio_decision (listing_id, platform, resultado, motivo, mode,
            goal, m_actual, p_actual, p_actual_currency, p_objetivo,
            p_objetivo_currency, p_aplicado, p_aplicado_currency, i_valor,
            i_currency, c_valor, c_currency, f_valor, f_currency, l_valor,
            l_currency, r_valor, r_currency)
        VALUES (v_lid3, 'amazon_mx', 'mantener', 'en_tolerancia', 'shadow', 0.30,
            0.30, 100.00, 'MXN', 100.00, 'MXN', 100.00, 'MXN', 10.00, 'MXN',
            20.00, 'MXN', 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN')
        RETURNING id INTO v_did_sombra;

        -- (positivo): goal reeditado el mismo dia ENTRA (anulada + vigente).
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por)
        VALUES (v_lid5, 'amazon_mx', 0.30, 'shadow', v_hoy, 'candado')
        RETURNING id INTO v_gid_reeditado;
        UPDATE precio_goal SET valid_to = v_hoy WHERE id = v_gid_reeditado;
        INSERT INTO precio_goal (listing_id, platform, margen_goal_pct, mode,
            valid_from, creado_por)
        VALUES (v_lid5, 'amazon_mx', 0.40, 'shadow', v_hoy, 'candado');

        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida;
        UPDATE precio_corrida SET estado = 'cerrada', cerrada_at = now(),
            decisiones = 1, movidas = 0, virtuales = 1, retenidas = 0,
            errores = 0, saltadas_por_edicion = 0 WHERE id = v_corrida;
        UPDATE precio_corrida SET resumen_enviado_at = now() WHERE id = v_corrida;

        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida_resumen;
        UPDATE precio_corrida SET estado = 'cerrada', cerrada_at = now(),
            decisiones = 0, movidas = 0, virtuales = 0, retenidas = 0,
            errores = 0, saltadas_por_edicion = 0
         WHERE id = v_corrida_resumen;

        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida_aviso1;
        INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas, nuevas,
            causa, detalle, avisada_at)
        VALUES (v_corrida_aviso1, 'fba', 10, 6, 'movimiento_masivo',
                'candado', now());
        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida_aviso2;

        INSERT INTO precio_corrida (platform, owner, config_version_id,
            modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow')
        RETURNING id INTO v_corrida_sin_retencion;

        -- Negativos: cada bloque espera el rechazo; si la operacion pasa, el
        -- marcador P0001 re-lanza y la migracion falla.

        BEGIN
            INSERT INTO precio_decision (listing_id, platform, resultado, motivo,
                mode, p_actual_currency, p_objetivo_currency, p_aplicado_currency,
                i_currency, c_currency, f_currency, l_currency, r_currency)
            VALUES (v_lid2, 'amazon_mx', 'mantener', 'en_tolerancia', 'live',
                'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN', 'MXN');
            RAISE EXCEPTION '0054: decision live bajo goal shadow NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_decision (listing_id, platform, resultado, motivo,
                mode, goal_id, goal, m_actual, p_actual, p_actual_currency,
                p_objetivo, p_objetivo_currency, p_aplicado, p_aplicado_currency,
                i_valor, i_currency, c_valor, c_currency, f_valor, f_currency,
                l_valor, l_currency, r_valor, r_currency)
            VALUES (v_lid4, 'amazon_mx', 'subir', NULL, 'live', v_gid_cerrado,
                0.30, 0.25, 100.00, 'MXN', 110.00, 'MXN', 110.00, 'MXN', 10.00,
                'MXN', 20.00, 'MXN', 12.00, 'MXN', 5.00, 'MXN', 8.00, 'MXN');
            RAISE EXCEPTION '0054: decision con goal_id ya cerrado NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_envio_muestra (product_id, platform, canal,
                ventana_desde, ventana_hasta, envios, valor, valor_currency,
                origen, familia_id, donantes, regla_version)
            VALUES (v_prod, 'amazon_mx', 'fbm', '2026-04-01', '2026-09-30', 3,
                90.00, 'MXN', 'familia', NULL, NULL, 'v1');
            RAISE EXCEPTION '0054: muestra familia sin donantes NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_compuerta_liberacion (corrida_id, liberado_por,
                nota) VALUES (v_corrida_sin_retencion, 'candado', 'zz');
            RAISE EXCEPTION '0054: liberacion sin retencion NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
        END;

        BEGIN
            UPDATE precio_goal SET origen = 'manual' WHERE id = v_gid_reeditado;
            RAISE EXCEPTION '0054: UPDATE de origen sobre goal sembrado NO fue rechazado';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN restrict_violation THEN NULL;
        END;

        BEGIN
            UPDATE precio_corrida SET estado = 'abierta' WHERE id = v_corrida;
            RAISE EXCEPTION '0054: corrida cerrada -> abierta NO fue rechazada';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN check_violation THEN NULL;
            WHEN restrict_violation THEN NULL;
        END;

        BEGIN
            UPDATE precio_corrida SET resumen_enviado_at = now()
             WHERE id = v_corrida_resumen;
            RAISE EXCEPTION '0054: segundo resumen del mismo dia NO fue rechazado';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN unique_violation THEN NULL;
            WHEN restrict_violation THEN NULL;
        END;

        BEGIN
            INSERT INTO precio_compuerta_medicion (corrida_id, canal, medidas,
                nuevas, causa, detalle, avisada_at)
            VALUES (v_corrida_aviso2, 'fba', 9, 5, 'movimiento_masivo',
                    'candado', now());
            RAISE EXCEPTION '0054: segundo aviso del mismo universo y dia NO fue rechazado';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN unique_violation THEN NULL;
        END;

        SET ROLE app_decide;
        BEGIN
            INSERT INTO precio_compuerta_liberacion (corrida_id, liberado_por,
                nota) VALUES (v_corrida_aviso1, 'candado', 'zz');
            RAISE EXCEPTION '0054: INSERT de app_decide en liberacion NO fue rechazado';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN insufficient_privilege THEN NULL;
        END;
        RESET ROLE;

        SET ROLE app_admin;
        BEGIN
            INSERT INTO precio_corrida (platform, owner, config_version_id,
                modo_global) VALUES ('amazon_mx', 'candado', v_cfg, 'shadow');
            RAISE EXCEPTION '0054: INSERT de app_admin en corrida NO fue rechazado';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN insufficient_privilege THEN NULL;
        END;
        BEGIN
            INSERT INTO precio_retencion (decision_id, corrida_id, causa)
            VALUES (v_did, v_corrida, 'apagador');
            RAISE EXCEPTION '0054: INSERT de app_admin en retencion NO fue rechazado';
        EXCEPTION
            WHEN raise_exception THEN RAISE;
            WHEN insufficient_privilege THEN NULL;
        END;
        RESET ROLE;

        v_ok := true;
        RAISE EXCEPTION 'candado_0054_revertir' USING ERRCODE = 'P0001';
    EXCEPTION WHEN raise_exception THEN
        IF NOT v_ok OR SQLERRM <> 'candado_0054_revertir' THEN
            RAISE;
        END IF;
    END;

    SELECT count(*) INTO v_n FROM precio_corrida;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en precio_corrida', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_retencion;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en precio_retencion', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_compuerta_medicion;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en precio_compuerta_medicion', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_compuerta_liberacion;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en precio_compuerta_liberacion', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM listing_miembro;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en listing_miembro', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM ledger_event_atribucion;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en ledger_event_atribucion', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM meli_publicacion_observation;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % filas en meli_publicacion_observation', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_goal WHERE creado_por = 'candado';
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % goals sembrados', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM precio_decision
     WHERE listing_id IN (v_lid1, v_lid2, v_lid3, v_lid4, v_lid5);
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó % decisiones sembradas', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM estimacion_escenario
     WHERE source_event_id = 'zz-candado-0054-esc';
    IF v_n <> 0 THEN
        RAISE EXCEPTION '0054: el candado dejó el escenario sembrado';
    END IF;
    PERFORM 1 FROM product WHERE odoo_sku = 'zz-candado-0054';
    IF FOUND THEN
        RAISE EXCEPTION '0054: el candado dejó la semilla de catalogo';
    END IF;
    PERFORM 1 FROM config_version WHERE label = 'zz-candado-0054';
    IF FOUND THEN
        RAISE EXCEPTION '0054: el candado dejó la config sembrada';
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 12. Config: las claves nuevas nacen AQUI (ningun paso manual)
-- -----------------------------------------------------------------------------
INSERT INTO config_version (label, settings)
SELECT 'REPRICING 02 0054: apagador, fusible y senal',
       COALESCE((SELECT settings FROM config_version ORDER BY id DESC LIMIT 1),
                '{}'::jsonb) || jsonb_build_object(
         'precio_modo_global',                'shadow',
         'precio_modo_universo',              jsonb_build_object('amazon_mx/fba', 'shadow'),
         'precio_fusible_frac',               0.50,
         'precio_fusible_min_movimientos',    10,
         'precio_insumo_salto_pct',           0.05,
         'precio_insumo_salto_frac',          0.30,
         'precio_corte_errores_consecutivos', 5,
         'precio_senal_trafico_min',          200,
         'precio_cohorte_caida_pct',          0.25,
         'precio_envio_ventana_dias',         180);

COMMIT;
