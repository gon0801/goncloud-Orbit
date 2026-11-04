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
       AND l.platform = cg.platform
       AND l.seller_sku = NEW.seller_sku;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'campana_grupo_producto: listing % no es del producto % en la '
            'plataforma del grupo %, o seller_sku % no es el del listing',
            NEW.listing_id, NEW.product_id, NEW.grupo_id, NEW.seller_sku
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
  'plataforma del grupo, y seller_sku es EL del listing (la FK sola no lo '
  'garantiza; sin el SKU el POST /sp/productAds fallaria hasta el HTTP).';

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
-- v_margen_producto: v_target_margen_plataforma (0016) con grano product_id.
--   ventas: solo lineas con product_id; cobertura por MONTO; COGS vigente a
--           la fecha en la MISMA moneda (o la linea es NO cubierta).
--   cargos con order_id: pertenecen a su orden (sin filtro de fecha propio,
--           A5) y se PRORRATEAN a cada producto por su monto de venta dentro
--           de la orden (spec §8), contando solo lineas CUBIERTAS.
--   cargos sin order_id: de plataforma, en ventana, prorrateados por la
--           participacion de la venta cubierta del producto en la venta
--           total de la plataforma.
--   guards de la plataforma (moneda unica, fees_sin_tipo = 0,
--           cobertura >= 0.95, cubierta > 0) + guard PROPIO dias >= 30 sobre
--           la ventana [2026-02-20, D-15) con arranque FIJO (decision escrita
--           del dueno, tarea 1; pineados contra app/fabrica_plan.py por test)
--           -> margen NULL.
--   ads (fee_type = 'ads') EXCLUIDO: es el numerador del ACoS del motor.
--   fees_sin_tipo es un COUNT de GUARD fail-loud, NO dinero: se infla cuando
--           una orden trae varias lineas de cargo del mismo producto (cuenta
--           lineas, no montos). Es intencional: cualquier fee sin tipo anula
--           el margen (NULL); no hay doble conteo de dinero.
--   cargos_con_orden/cargos_sin_orden/cogs publican 0 por COALESCE cuando no
--           hay cargos (igual que v_target_margen_plataforma): esas columnas
--           MIDEN y no son NULL-aware; el MARGEN si va NULL ante cada guard.
-- ---------------------------------------------------------------------------
CREATE VIEW v_margen_producto AS
WITH ventana AS (
    -- arranque FIJO 2026-02-20 (= al primer valid_from de sku_cost; decision
    -- escrita del dueno, tarea 1). Es un literal, NO se deriva de sku_cost.
    -- hoy = fecha UTC FIJADA en la expresion (D-6): CURRENT_DATE sigue la
    -- TimeZone de la sesion y moveria el guard de 30 dias segun quien consulte.
    SELECT DATE '2026-02-20' AS desde, (now() AT TIME ZONE 'UTC')::date - 15 AS hasta
),
ventas AS (
    SELECT l.platform, l.product_id, l.event_date, l.order_id,
           l.amount, l.amount_currency,
           CASE WHEN c.id IS NOT NULL AND c.cost_currency = l.amount_currency
                THEN c.cost_amount * l.quantity END AS cogs_linea
      FROM ledger_event l
      CROSS JOIN ventana v
      LEFT JOIN sku_cost c
        ON c.product_id = l.product_id
       AND l.event_date >= c.valid_from
       AND (c.valid_to IS NULL OR l.event_date < c.valid_to)
     WHERE l.kind = 'sale' AND l.product_id IS NOT NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
),
orden_cubierta AS (
    -- venta cubierta por orden: base del prorrateo de sus cargos. n_monedas:
    -- guard del DENOMINADOR (r3 codex 2): una orden con lineas cubiertas en
    -- dos monedas no puede prorratear (regla 4 — NULL, no suma a ciegas).
    SELECT platform, order_id, SUM(amount) AS venta_orden,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas
     WHERE order_id IS NOT NULL AND cogs_linea IS NOT NULL
     GROUP BY platform, order_id
),
cargos_orden AS (
    SELECT l.platform, l.order_id,
           SUM(l.amount) AS monto,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo
      FROM ledger_event l
      JOIN orden_cubierta o ON o.platform = l.platform AND o.order_id = l.order_id
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
     GROUP BY l.platform, l.order_id
),
cargos_producto AS (
    SELECT v.platform, v.product_id,
           SUM(co.monto * v.amount / o.venta_orden) AS cargos_con_orden,
           SUM(co.fees_sin_tipo) AS fees_sin_tipo,
           -- Guard de moneda de los cargos, DOS casos (regla 4): MAX(co.n_monedas)
           -- = fees en dos monedas DENTRO de una orden (co.moneda es MAX por orden
           -- y colapsaria el caso); COUNT(DISTINCT co.moneda) = fees en dos monedas
           -- ENTRE ordenes (D-3: MAX(moneda) era lexicografico y fail-open). Solo
           -- uno de los dos deja un hueco (D-4, review del lead PR #172).
           GREATEST(MAX(co.n_monedas), COUNT(DISTINCT co.moneda)) AS n_monedas_cargos,
           MAX(o.n_monedas) AS n_monedas_orden,
           MAX(co.moneda) AS moneda_cargos
      FROM ventas v
      JOIN orden_cubierta o ON o.platform = v.platform AND o.order_id = v.order_id
      JOIN cargos_orden co ON co.platform = v.platform AND co.order_id = v.order_id
     WHERE v.cogs_linea IS NOT NULL
     GROUP BY v.platform, v.product_id
),
plataforma AS (
    SELECT l.platform,
           SUM(l.amount) AS monto_sin_orden,
           COUNT(*) FILTER (WHERE l.fee_type IS NULL) AS fees_sin_tipo,
           COUNT(DISTINCT l.amount_currency) AS n_monedas,
           MAX(l.amount_currency::text) AS moneda
      FROM ledger_event l
      CROSS JOIN ventana v
     WHERE l.kind IN ('fee', 'refund', 'withholding')
       AND COALESCE(l.fee_type, '') <> 'ads'
       AND l.order_id IS NULL
       AND l.event_date >= v.desde AND l.event_date < v.hasta
     GROUP BY l.platform
),
venta_plataforma AS (
    -- n_monedas: guard del DENOMINADOR (r3 codex 2): el sistema viejo
    -- reportaba MXN hasta en amazon_us; con ventas de la plataforma en dos
    -- monedas la fraccion no puede calcularse (regla 4).
    SELECT platform, SUM(amount) AS venta_total,
           COUNT(DISTINCT amount_currency) AS n_monedas
      FROM ventas GROUP BY platform
),
ag AS (
    SELECT platform, product_id,
           SUM(amount) AS venta_total,
           SUM(amount) FILTER (WHERE cogs_linea IS NOT NULL) AS venta_cubierta,
           SUM(cogs_linea) AS cogs_conocido,
           COUNT(DISTINCT event_date) AS dias_con_venta,
           COUNT(DISTINCT amount_currency) AS n_monedas,
           MAX(amount_currency) AS moneda_unica
      FROM ventas
     GROUP BY platform, product_id
),
fresco AS (
    SELECT MAX(started_at) AS ledger_fresco_at
      FROM ingest_run
     WHERE source = 'accounting_ledger_events' AND ok
)
SELECT a.platform,
       a.product_id,
       (SELECT desde FROM ventana) AS ventana_desde,
       (SELECT hasta FROM ventana) AS ventana_hasta,
       a.venta_total,
       a.venta_cubierta,
       COALESCE(cp.cargos_con_orden, 0) AS cargos_con_orden,
       COALESCE(p.monto_sin_orden, 0) * COALESCE(a.venta_cubierta, 0)
           / NULLIF(vp.venta_total, 0) AS cargos_sin_orden,
       COALESCE(a.cogs_conocido, 0) AS cogs,
       CASE WHEN a.venta_total > 0 THEN COALESCE(a.venta_cubierta, 0) / a.venta_total END
           AS cobertura,
       a.dias_con_venta,
       COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) AS fees_sin_tipo,
       CASE
           WHEN a.n_monedas <> 1 THEN NULL
           WHEN COALESCE(cp.n_monedas_cargos, 0) > 1 OR COALESCE(p.n_monedas, 0) > 1 THEN NULL
           WHEN cp.moneda_cargos IS NOT NULL AND cp.moneda_cargos <> a.moneda_unica::text THEN NULL
           WHEN p.moneda IS NOT NULL AND p.moneda <> a.moneda_unica::text THEN NULL
           WHEN COALESCE(cp.fees_sin_tipo, 0) + COALESCE(p.fees_sin_tipo, 0) > 0 THEN NULL
           WHEN a.venta_cubierta IS NULL OR a.venta_cubierta <= 0 THEN NULL
           WHEN a.venta_cubierta / NULLIF(a.venta_total, 0) < 0.95 THEN NULL
           WHEN a.dias_con_venta < 30 THEN NULL
           WHEN COALESCE(cp.n_monedas_orden, 0) > 1 THEN NULL
           WHEN vp.n_monedas > 1 THEN NULL
           ELSE 100.0 * (a.venta_cubierta
                + COALESCE(cp.cargos_con_orden, 0)
                + COALESCE(p.monto_sin_orden, 0) * a.venta_cubierta / NULLIF(vp.venta_total, 0)
                - COALESCE(a.cogs_conocido, 0)) / a.venta_cubierta
       END AS margen_neto_pct,
       fr.ledger_fresco_at,
       CASE WHEN a.n_monedas = 1 THEN a.moneda_unica END AS moneda
  FROM ag a
  JOIN venta_plataforma vp ON vp.platform = a.platform
  CROSS JOIN fresco fr
  LEFT JOIN cargos_producto cp ON cp.platform = a.platform AND cp.product_id = a.product_id
  LEFT JOIN plataforma p ON p.platform = a.platform;

COMMENT ON VIEW v_margen_producto IS
  'FABRICA 01 §4/§8: margen neto % POR PRODUCTO con la maquinaria de '
  'v_target_margen_plataforma (0016) salvo la ventana: [2026-02-20, D-15) UTC '
  '(arranque fijo, decision escrita del dueno 2026-09-05), COGS a la '
  'fecha en la misma moneda, cobertura por monto, cargos no-ads con order_id '
  'prorrateados por el monto del producto dentro de su orden (solo lineas '
  'cubiertas), cargos sin order_id prorrateados por la participacion del '
  'producto en la venta de la plataforma. NULL ante mezcla de moneda '
  '(incluidos los DENOMINADORES del prorrateo: la orden cubierta o la venta '
  'total de la plataforma en mas de una moneda — regla 4), '
  'fees_sin_tipo > 0, cobertura < 0.95, dias < 30 o cubierta <= 0 (regla 3). '
  'fees_sin_tipo es un COUNT de guard fail-closed (se infla con varias '
  'lineas de cargo de una misma orden; intencional, no es dinero). Las '
  'columnas cargos_*/cogs publican 0 por COALESCE (miden); solo el margen '
  'usa NULL. Solo MIDE: el target del grupo lo deriva app/fabrica_plan.py y '
  'queda congelado en campana_grupo.';

GRANT SELECT ON v_margen_producto TO app_read, app_ingest, app_decide, app_admin;

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
