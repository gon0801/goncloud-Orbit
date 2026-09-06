-- =============================================================================
--  FABRICA 01 (spec docs/superpowers/specs/2026-09-05-fabrica-campanas-grupos-design.md
--  §8) — PostgreSQL 16.
--
--  Grupos de 5 campanas SP creados por tools/fabrica_campanas.py con go del
--  dueno. El VINCULO campana<->grupo es la tabla campana_grupo_rol (nunca el
--  nombre en Amazon). Ledger de creacion fabrica_lote/fabrica_lote_paso con
--  el patron keyword_archivo_manual (0014): intencion durable ANTES del HTTP,
--  ack JSONB, readback, estados planeado/applied/failed. Biblioteca de
--  keywords/negativos por (tipo_producto, platform, texto): F1 la LEE al
--  sembrar; F2 la escribe desde harvest/negatives aplicados. harvest_excepcion
--  nace aqui (schema) y se puebla en F2 con go del dueno (decision 4).
--  v_margen_producto: misma maquinaria de v_target_margen_plataforma (0016)
--  con grano ledger_event.product_id (tarea 3 de plans/fabrica-01.md).
-- =============================================================================

CREATE TYPE campana_rol AS ENUM (
  'auto_discovery', 'category_phrase', 'product_targeting', 'category_broad', 'category_exact'
);
COMMENT ON TYPE campana_rol IS
  'FABRICA 01 §3: los 5 roles fijos de un grupo. La exact recibe el harvest.';

-- ---------------------------------------------------------------------------
-- Ledger de creacion
-- ---------------------------------------------------------------------------
CREATE TABLE fabrica_lote (
  lote          TEXT        PRIMARY KEY,
  platform      platform    NOT NULL,
  tipo_producto TEXT        NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  nombre_base   TEXT        NOT NULL CHECK (btrim(nombre_base) <> ''),
  go_literal    TEXT        NOT NULL CHECK (btrim(go_literal) <> ''),
  huella        TEXT        NOT NULL,
  plan          JSONB       NOT NULL,
  modo_goal     TEXT        NOT NULL CHECK (modo_goal IN ('shadow', 'live')),
  estado        TEXT        NOT NULL
                CHECK (estado IN ('planeado', 'applied', 'failed', 'desarmado')),
  detalle       TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
);
COMMENT ON TABLE fabrica_lote IS
  'FABRICA 01 §5: un lote = una corrida real de la fabrica. `plan` congela el '
  'dry-run autorizado (huella, target, bids, budgets, semillas, productos); '
  'nace planeado ANTES del primer HTTP y se sella applied/failed al final; '
  'desarmado = las 5 pausadas por --desarmar.';

CREATE TABLE fabrica_lote_paso (
  id              BIGSERIAL   PRIMARY KEY,
  lote            TEXT        NOT NULL REFERENCES fabrica_lote(lote),
  orden           INT         NOT NULL,
  rol             campana_rol NOT NULL,
  recurso         TEXT        NOT NULL CHECK (recurso IN
                    ('campaign', 'ad_group', 'product_ad', 'keyword', 'target', 'negative_keyword')),
  request_payload JSONB       NOT NULL,
  external_id     TEXT,
  ack             JSONB,
  readback_estado TEXT,
  estado          TEXT        NOT NULL CHECK (estado IN ('planeado', 'applied', 'failed')),
  intentado_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (lote, orden),
  CONSTRAINT paso_evidencia_applied CHECK (
    estado <> 'applied'
    OR (external_id IS NOT NULL AND ack IS NOT NULL AND readback_estado IS NOT NULL)
  )
);
CREATE INDEX ON fabrica_lote_paso (estado) WHERE estado IN ('planeado', 'failed');
COMMENT ON TABLE fabrica_lote_paso IS
  'FABRICA 01 §5/§8: un paso por POST (campana, ad group, cada product ad, '
  'cada semilla). Fila planeado + commit ANTES del HTTP (regla 7); applied '
  'exige el id externo, el ack y el readback que lo confirmaron; failed puede '
  'no traer readback (el POST lanzo). --reconciliar cruza planeado/failed '
  'contra el LIST real.';

-- ---------------------------------------------------------------------------
-- Grupo
-- ---------------------------------------------------------------------------
CREATE TABLE campana_grupo (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  platform            platform     NOT NULL,
  tipo_producto       TEXT         NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  nombre_base         TEXT         NOT NULL CHECK (btrim(nombre_base) <> ''),
  lote                TEXT         NOT NULL UNIQUE REFERENCES fabrica_lote(lote),
  target_acos_pct     NUMERIC(6, 2) NOT NULL CHECK (target_acos_pct > 0),
  target_derivado_pct NUMERIC(10, 4) NOT NULL,
  fraccion            NUMERIC(6, 4) NOT NULL CHECK (fraccion > 0 AND fraccion <= 1),
  target_procedencia  TEXT         NOT NULL,
  go_literal          TEXT         NOT NULL,
  created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE campana_grupo IS
  'FABRICA 01 §4: target CONGELADO al crear = clamp(fraccion x margen minimo '
  'de los productos, [10, 45]); target_derivado_pct es el crudo pre-clamp y '
  'target_procedencia lo explica. El re-ajuste posterior queda FUERA '
  '(residual 3). tipo_producto = etiqueta del dueno (decision 7).';

CREATE TABLE campana_grupo_rol (
  grupo_id              BIGINT      NOT NULL REFERENCES campana_grupo(id),
  rol                   campana_rol NOT NULL,
  ad_entity_id          BIGINT      NOT NULL REFERENCES ad_entity(id),
  ad_group_ad_entity_id BIGINT      NOT NULL REFERENCES ad_entity(id),
  PRIMARY KEY (grupo_id, rol),
  UNIQUE (ad_entity_id),
  UNIQUE (ad_group_ad_entity_id)
);
COMMENT ON TABLE campana_grupo_rol IS
  'FABRICA 01 §7/§8: EL vinculo campana<->grupo (jamas por nombre). Una '
  'campana pertenece a lo sumo a un grupo. El ad group de cada rol va '
  'GUARDADO (no resuelto por parent_id): el destino del harvest de F2 es un '
  'SELECT directo sobre rol = category_exact.';

-- Patron goal_scope_campana_real: la FK sola no garantiza kinds.
CREATE FUNCTION campana_grupo_rol_kinds() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'campana_grupo_rol: ad_entity_id % no es kind=campaign', NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM 1 FROM ad_entity
     WHERE id = NEW.ad_group_ad_entity_id AND kind = 'ad_group' AND parent_id = NEW.ad_entity_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'campana_grupo_rol: ad_group_ad_entity_id % no es un ad_group hijo de %',
            NEW.ad_group_ad_entity_id, NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campana_grupo_rol_kinds
    BEFORE INSERT OR UPDATE ON campana_grupo_rol
    FOR EACH ROW EXECUTE FUNCTION campana_grupo_rol_kinds();
COMMENT ON FUNCTION campana_grupo_rol_kinds IS
  'FABRICA 01: la campana del rol es kind=campaign y su ad group es '
  'kind=ad_group con parent_id = esa campana (la FK sola no lo garantiza).';

CREATE TABLE campana_grupo_producto (
  grupo_id        BIGINT NOT NULL REFERENCES campana_grupo(id),
  product_id      BIGINT NOT NULL REFERENCES product(id),
  listing_id      BIGINT NOT NULL REFERENCES listing(id),
  seller_sku      TEXT   NOT NULL CHECK (btrim(seller_sku) <> ''),
  margen_neto_pct NUMERIC(10, 4) NOT NULL,
  PRIMARY KEY (grupo_id, product_id)
);
COMMENT ON TABLE campana_grupo_producto IS
  'FABRICA 01 §4/§8: snapshot al alta de cada producto del grupo con el '
  'margen que entro al minimo y el SKU del product ad (listing.seller_sku).';

-- Patron campana_grupo_rol_kinds: la FK sola admite un listing de OTRO
-- producto o de OTRA plataforma; el snapshot debe cuadrar.
CREATE FUNCTION campana_grupo_producto_listing() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1
      FROM listing l
      JOIN campana_grupo cg ON cg.id = NEW.grupo_id
     WHERE l.id = NEW.listing_id
       AND l.product_id = NEW.product_id
       AND l.platform = cg.platform;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'campana_grupo_producto: listing % no es del producto % en la '
            'plataforma del grupo %', NEW.listing_id, NEW.product_id, NEW.grupo_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER campana_grupo_producto_listing
    BEFORE INSERT OR UPDATE ON campana_grupo_producto
    FOR EACH ROW EXECUTE FUNCTION campana_grupo_producto_listing();
COMMENT ON FUNCTION campana_grupo_producto_listing IS
  'FABRICA 01: el listing del snapshot pertenece AL producto y a la '
  'plataforma del grupo (la FK sola no lo garantiza).';

-- ---------------------------------------------------------------------------
-- Biblioteca acumulativa por tipo_producto (decision 6)
-- ---------------------------------------------------------------------------
CREATE TABLE keyword_biblioteca (
  id            BIGSERIAL   PRIMARY KEY,
  tipo_producto TEXT        NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  platform      platform    NOT NULL,
  texto         TEXT        NOT NULL CHECK (btrim(texto) <> ''),
  origen        TEXT        NOT NULL,
  orders        INT         NOT NULL DEFAULT 0 CHECK (orders >= 0),
  cost          money_amount,
  revenue       money_amount,
  moneda        currency,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tipo_producto, platform, texto),
  CONSTRAINT biblioteca_dinero_con_moneda
    CHECK ((cost IS NULL AND revenue IS NULL) OR moneda IS NOT NULL)
);
COMMENT ON TABLE keyword_biblioteca IS
  'FABRICA 01 §6: terminos con historial por (tipo_producto, platform). F1 '
  'siembra phrase/broad con orders >= 1 y product targeting con los ASIN-like; '
  'F2 la alimenta desde cada harvest aplicado. Dinero con moneda (regla 4).';

CREATE TABLE negative_biblioteca (
  id            BIGSERIAL   PRIMARY KEY,
  tipo_producto TEXT        NOT NULL CHECK (tipo_producto ~ '^[a-z0-9_]+$'),
  platform      platform    NOT NULL,
  texto         TEXT        NOT NULL CHECK (btrim(texto) <> ''),
  origen        TEXT        NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tipo_producto, platform, texto)
);
COMMENT ON TABLE negative_biblioteca IS
  'FABRICA 01 §6: negativos por (tipo_producto, platform). F1 siembra la '
  'auto_discovery con ellos; F2 la alimenta desde cada negative aplicado.';

-- ---------------------------------------------------------------------------
-- Excepciones de harvest (schema en F1; se puebla en F2 con go, decision 4)
-- ---------------------------------------------------------------------------
CREATE TABLE harvest_excepcion (
  ad_entity_id              BIGINT      PRIMARY KEY REFERENCES ad_entity(id),
  destino_campaign_external TEXT        NOT NULL CHECK (btrim(destino_campaign_external) <> ''),
  destino_ad_group_external TEXT        NOT NULL CHECK (btrim(destino_ad_group_external) <> ''),
  go_literal                TEXT        NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE harvest_excepcion IS
  'FABRICA 01 §7 (F2): destino de harvest CONGELADO de una campana sin grupo, '
  'migrada una a una con go del dueno («que queden asi ya»). F1 solo crea la '
  'tabla; nadie la escribe hasta F2.';

-- Patron goal_scope_campana_real: la excepcion es de una CAMPANA sin grupo.
CREATE FUNCTION harvest_excepcion_kind() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM 1 FROM ad_entity WHERE id = NEW.ad_entity_id AND kind = 'campaign';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'harvest_excepcion: ad_entity_id % no existe o no es kind=campaign',
            NEW.ad_entity_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER harvest_excepcion_kind
    BEFORE INSERT OR UPDATE ON harvest_excepcion
    FOR EACH ROW EXECUTE FUNCTION harvest_excepcion_kind();
COMMENT ON FUNCTION harvest_excepcion_kind IS
  'FABRICA 01: la excepcion de harvest se congela sobre kind=campaign (la FK '
  'sola admite cualquier entidad).';

-- ---------------------------------------------------------------------------
-- GRANTs: app_admin escribe (la fabrica corre con ORBIT_DSN_ADMIN); el motor
-- (app_decide) y la lectura solo leen. USAGE SOLO de las secuencias de las
-- tablas nuevas y SOLO para app_admin (el unico que inserta; app_ingest y
-- app_decide tienen SELECT puro, que no toca secuencias).
-- ---------------------------------------------------------------------------
GRANT SELECT ON fabrica_lote, fabrica_lote_paso, campana_grupo, campana_grupo_rol,
    campana_grupo_producto, keyword_biblioteca, negative_biblioteca, harvest_excepcion
    TO app_read, app_ingest, app_decide, app_admin;
GRANT INSERT, UPDATE ON fabrica_lote, fabrica_lote_paso, campana_grupo, campana_grupo_rol,
    campana_grupo_producto, keyword_biblioteca, negative_biblioteca, harvest_excepcion
    TO app_admin;
GRANT USAGE ON SEQUENCE fabrica_lote_paso_id_seq, campana_grupo_id_seq,
    keyword_biblioteca_id_seq, negative_biblioteca_id_seq TO app_admin;
