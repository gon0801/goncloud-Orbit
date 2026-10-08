-- =============================================================================
-- REPRICING 02 -- modelo de datos sintetizado (BOSQUEJO, no se aplica)
-- Base: candidato A de la arena, con los injertos que registra juicio.md.
--
-- Todo es ADITIVO sobre la 0039 (produccion tiene goals 6-11, ~9 cambios y
-- decisiones diarias desde el 20-sep). Ninguna fila existente se toca.
--
-- Dos archivos, en este orden, y NINGUNO mas en los carriles:
--   0053_precio02_canal_meli.sql   solo el valor nuevo del enum (su propia
--                                  transaccion: no se puede usar donde nace)
--   0054_precio02_base.sql         todo lo demas, de los cuatro carriles
-- Cada uno con su `00NN_reversa_*.sql` (patron de 0049-0052).
-- Numeros reservados por si un carril descubre DDL que falto: F=0055, M=0056,
-- G=0057, S=0058. Asi dos carriles no piden el mismo numero.
--
-- Dos excepciones a "solo agregar", declaradas (ver el spec, costos):
--   (x1) `precio_goal_unico_por_fecha` pasa de UNIQUE a indice unico parcial.
--   (x2) `precio_cambio_confirmado_por_valido` admite un valor mas.
-- Las dos ENSANCHAN: toda fila que hoy es valida lo sigue siendo.
-- `listing.product_id` NO se toca: sigue NOT NULL (producto ancla).
-- Todo CHECK nuevo sobre una tabla con filas vivas (`precio_decision`,
-- `estimacion_escenario`) va `NOT VALID`: vale para lo nuevo y no revisa la
-- historia. Sin eso la migracion falla en produccion.
--
-- Patrones de acceso dominantes y que los sirve (si la respuesta fuera "luego
-- un indice", la estructura estaria mal):
--   a. goals vigentes de una plataforma        v_precio_goal_vigente (indice un_vigente)
--   b. decision anterior de una unidad         precio_decision_unica_por_dia (existe)
--   c. decisiones de HOY de una plataforma     precio_decision_por_dia (nuevo)
--   d. pendiente de aplicar hoy                c + precio_cambio_por_decision (existe)
--   e. ultima muestra de envio de un producto  precio_muestra_ultima (nuevo)
--   f. ventas por unidad por dia               ledger_atribucion_por_listing (nuevo)
--   g. cambios de un dia o corrida (reversa)   precio_cambio_por_corrida (nuevo)
--   h. ultima observacion de una publicacion   meli_pub_obs_ultima (nuevo)
-- =============================================================================


-- #############################################################################
-- 0053_precio02_canal_meli.sql
-- #############################################################################
-- Solo esto. No re-ejecutable (un segundo ADD VALUE del mismo literal falla),
-- igual que la 0004. Sin BEGIN/COMMIT propios: el runner ya la envuelve.
ALTER TYPE estimacion_canal ADD VALUE 'meli';


-- #############################################################################
-- 0054_precio02_base.sql
-- #############################################################################
BEGIN;

-- -----------------------------------------------------------------------------
-- 1. IDENTIDAD: la publicacion es la unidad de precio; sus miembros, aparte
-- -----------------------------------------------------------------------------
-- `listing` ya es "ASIN o MLM id" (comentario de la 0001) y todas las tablas
-- `precio_*` cuelgan de `(listing_id, platform)`. Una publicacion de MeLi es
-- UNA fila de `listing`: un goal, una decision al dia, un cambio abierto. Lo
-- que no cabe es `product_id`: 48 de 65 publicaciones venden varios productos.
--
-- `listing.product_id` NO cambia y sigue NOT NULL. En MeLi guarda el producto
-- ANCLA: el del primer miembro mapeado (SKU ascendente). Es identidad, no
-- costo: el costo y el envio salen siempre de `listing_miembro`. Siete modulos
-- de `app/` y doce vistas hacen JOIN por esa columna; con NULL las filas de
-- MeLi desaparecerian en silencio de margen, contribucion y familias.
-- Una publicacion sin NINGUN miembro mapeado no puede tener fila en `listing`:
-- se queda en `meli_publicacion_observation` y la cobertura la cuenta como
-- `sin_listing` con sus SKUs a la vista.

CREATE TABLE listing_miembro (
    listing_id   BIGINT   NOT NULL,
    platform     platform NOT NULL,
    variante_ext TEXT     NOT NULL DEFAULT '',   -- id de variacion; '' = sin variantes
    seller_sku   TEXT     NOT NULL,
    product_id   BIGINT   REFERENCES product (id),  -- NULL = variante sin mapear
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
  'ancla de `listing.product_id` es identidad; el costo sale de aqui. Variantes '
  'despues (D8): si MeLi las separa en publicaciones propias son filas nuevas '
  'de `listing`; si el dueno quiere goal por variante bajo un solo precio, es '
  'la tabla hija `precio_goal_variante` del final de este archivo.';

-- UNA respuesta a "que productos vende esta unidad", para Amazon y MeLi.
CREATE VIEW v_precio_unidad_miembro AS
    SELECT l.id AS listing_id, l.platform, ''::text AS variante_ext,
           l.seller_sku, l.product_id
      FROM listing l
     WHERE l.platform <> 'meli'
    UNION ALL
    SELECT m.listing_id, m.platform, m.variante_ext, m.seller_sku, m.product_id
      FROM listing_miembro m
     WHERE m.activo;


-- -----------------------------------------------------------------------------
-- 2. LEDGER: a que publicacion pertenece cada evento (G11, G23)
-- -----------------------------------------------------------------------------
-- `ledger_event` es append-only (triggers de la 0001): no se le puede agregar
-- `listing_id` a la historia. La atribucion vive al lado. La ingesta relee el
-- snapshot completo cada dia, asi que la primera corrida llena TODA la
-- historia (D2: nada que esperar).
CREATE TABLE ledger_event_atribucion (
    ledger_event_id BIGINT PRIMARY KEY REFERENCES ledger_event (id),
    platform        platform NOT NULL,
    listing_id      BIGINT,                       -- NULL = indeterminado, con su porque
    variante_ext    TEXT,
    resuelto_por    TEXT NOT NULL,
    ingest_run_id   BIGINT NOT NULL REFERENCES ingest_run (id),
    CONSTRAINT ledger_atribucion_listing_fk FOREIGN KEY (listing_id, platform)
        REFERENCES listing (id, platform),
    CONSTRAINT ledger_atribucion_resuelto_valido
        CHECK (resuelto_por IN ('sku', 'externo_unico', 'producto_unico',
                                'canal_orden', 'indeterminado')),
    CONSTRAINT ledger_atribucion_indeterminado_sin_listing
        CHECK ((resuelto_por = 'indeterminado') = (listing_id IS NULL))
);
CREATE INDEX ledger_atribucion_por_listing ON ledger_event_atribucion (listing_id);
CREATE TRIGGER ledger_atribucion_append_only
    BEFORE UPDATE OR DELETE ON ledger_event_atribucion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
-- Escritor unico: la ingesta del ledger (`app_ingest`), INSERT ... ON CONFLICT
-- DO NOTHING. Un evento atribuido no se re-atribuye: si el mapeo cambia, las
-- ventas viejas quedan como estaban y se cuenta la diferencia.

-- Ventas por UNIDAD. Sustituye al SELECT por `product_id` de `_insumos_ventas`.
-- Las ventas del producto sin publicacion decidible salen con listing_id NULL:
-- el adaptador las cuenta en `HistoriaUnidad.sin_atribuir`.
CREATE VIEW v_precio_venta_unidad AS
    SELECT e.platform, a.listing_id, a.variante_ext, e.product_id,
           e.event_date, e.quantity, e.amount, e.amount_currency, e.order_id,
           a.resuelto_por
      FROM ledger_event e
      LEFT JOIN ledger_event_atribucion a ON a.ledger_event_id = e.id
     WHERE e.kind = 'sale';
-- El costo de envio por orden NO es vista: es `app/precio/envio.py` (puro,
-- con la regla `max(labman, etiqueta) + HB` versionada y probada). La fuente
-- del cargo se deriva del prefijo de `ledger_event.source_event_id`
-- (= dedupe_key de contabilidad). Si la sonda muestra que el prefijo no la
-- trae, se agrega aqui `cargo_fuente TEXT` (numero reservado 0055).


-- -----------------------------------------------------------------------------
-- 3. MELI: observacion diaria de la publicacion (G24, G27)
-- -----------------------------------------------------------------------------
-- La tabla nace vacia en el corte 0; la llena la ingesta del carril M con el
-- `ClienteMeli` GET-only que reputacion ya usa a diario.
CREATE TABLE meli_publicacion_observation (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id       TEXT NOT NULL,
    metric_date   DATE NOT NULL,                  -- dia UTC de captura
    observed_at   TIMESTAMPTZ NOT NULL,
    estado        TEXT NOT NULL,                  -- active | paused | closed | under_review
    precio        money_amount,
    moneda        currency,
    stock         INTEGER,
    tipo_publicacion TEXT,                        -- entrada de la cotizacion de cargos
    categoria_id  TEXT,
    logistica     TEXT,
    visitas       INTEGER,                        -- senal temprana (D10); NULL = sin dato
    variantes     JSONB NOT NULL DEFAULT '[]',    -- [{variante_ext, seller_sku, stock}]
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
-- 4. ENVIO (L) con origen (G15-G17)
-- -----------------------------------------------------------------------------
-- `precio_envio_muestra` existe y esta VACIA: por eso los NOT NULL entran sin
-- default. El importe va en la moneda en que se midio (MXN tambien en US); la
-- conversion a la moneda de venta la hace la estimacion y queda en el
-- componente `logistica` del escenario (original, normalizado, tasa).
ALTER TABLE precio_envio_muestra
    ADD COLUMN canal         estimacion_canal NOT NULL,
    ADD COLUMN origen        TEXT NOT NULL,
    ADD COLUMN familia_id    BIGINT REFERENCES familia (id),
    ADD COLUMN donantes      INTEGER,
    ADD COLUMN regla_version TEXT NOT NULL,
    -- lo que NO entro, contado (ningun silencio); a nivel de la corrida del job
    ADD COLUMN excl_multiproducto INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN excl_sin_producto  INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN cargos_otros       INTEGER NOT NULL DEFAULT 0,
    -- evidencia, no entra a `L` (G17): envio cobrado al cliente. NULL = la
    -- plataforma no lo entrega (US); 0 = lo entrega y nadie pago.
    ADD COLUMN cobrado_n          INTEGER,
    ADD COLUMN cobrado_mediana    money_amount,
    ADD COLUMN cobrado_mediana_currency currency;
ALTER TABLE precio_envio_muestra
    ADD CONSTRAINT precio_muestra_origen_valido
        CHECK (origen IN ('propio', 'familia', 'marketplace')),
    -- Un imputado no puede pasar por medido: `propio` no trae donantes ni
    -- familia; `familia` trae las dos; `marketplace` trae donantes y no familia.
    ADD CONSTRAINT precio_muestra_origen_coherente CHECK (
        (origen = 'propio'      AND donantes IS NULL AND familia_id IS NULL)
     OR (origen = 'familia'     AND donantes >= 1    AND familia_id IS NOT NULL)
     OR (origen = 'marketplace' AND donantes >= 1    AND familia_id IS NULL)),
    ADD CONSTRAINT precio_muestra_cobrado_con_moneda
        CHECK ((cobrado_mediana IS NULL) = (cobrado_mediana_currency IS NULL));
-- Con `origen <> 'propio'`, `envios` (ya existe, > 0) cuenta las ordenes de
-- los DONANTES; este producto aporto cero.
CREATE INDEX precio_muestra_ultima
    ON precio_envio_muestra (product_id, platform, canal, calculada_at DESC);

-- El escenario dice de donde salio su L (regla 2: el motor no recalcula).
ALTER TABLE estimacion_escenario
    ADD COLUMN envio_muestra_id   BIGINT REFERENCES precio_envio_muestra (id),
    ADD COLUMN logistica_origen   TEXT,
    ADD COLUMN miembro_product_id BIGINT REFERENCES product (id),
    -- Fees ya partidos (injerto de B), en la moneda del escenario: lo que
    -- escala con el precio y lo que no. `leer_cuenta` deja de parsear el arbol
    -- y `ReferralFee` no sale del adaptador. `fee_cotizable = false` = el
    -- universo no tiene cotizador real: variable y fijo vienen de lo medido en
    -- el ledger y el motor cierra `lineal`.
    ADD COLUMN fee_variable       NUMERIC(14, 4),
    ADD COLUMN fee_fijo           NUMERIC(14, 4),
    ADD COLUMN fee_cotizable      BOOLEAN;
ALTER TABLE estimacion_escenario
    ADD CONSTRAINT estimacion_escenario_logistica_origen_valido
        CHECK (logistica_origen IS NULL
               OR logistica_origen IN ('politica', 'propio', 'familia', 'marketplace')),
    ADD CONSTRAINT estimacion_escenario_muestra_segun_origen
        CHECK ((logistica_origen IN ('propio', 'familia', 'marketplace'))
               = (envio_muestra_id IS NOT NULL)) NOT VALID,
    -- desde esta migracion un escenario `disponible` siempre dice su origen
    ADD CONSTRAINT estimacion_escenario_disponible_exige_origen
        CHECK (estado <> 'disponible' OR logistica_origen IS NOT NULL) NOT VALID,
    -- ... y sus fees partidos
    ADD CONSTRAINT estimacion_escenario_disponible_exige_fees_partidos
        CHECK (estado <> 'disponible'
               OR (fee_variable IS NOT NULL AND fee_fijo IS NOT NULL
                   AND fee_cotizable IS NOT NULL)) NOT VALID;
-- MeLi en `estimacion_oferta_observation` y `estimacion_escenario`: `asin`
-- lleva el id de la publicacion y `seller_sku` el SKU del miembro que manda
-- (ambas NOT NULL en la 0028; no se relajan). Deuda de nombre declarada.
-- NOT VALID: vale para toda fila NUEVA; las historicas (FBA, origen implicito
-- `politica`) no se validan ni se tocan.
COMMENT ON COLUMN estimacion_escenario.miembro_product_id IS
  'Publicacion con varios miembros: el que manda (el de P* mas alto). La '
  'cuenta de la fila es la suya; las de los demas van en `componentes` como '
  'entradas `miembro:<sku>` con pertenece_a_total = false.';
-- Las politicas de los universos nuevos (`amazon_mx/fbm`, `amazon_us/fbm`,
-- `meli/meli`) son FILAS de `estimacion_politica_version`, no DDL: las
-- inserta el dueno con su script cuando la sonda de cada universo cierra.


-- -----------------------------------------------------------------------------
-- 5. CORRIDA: una fila por ejecucion (G3, G6, G7, G9)
-- -----------------------------------------------------------------------------
CREATE TABLE precio_corrida (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform           platform NOT NULL,
    fecha              DATE NOT NULL,             -- dia UTC de la base, por trigger
    owner              TEXT NOT NULL,
    estado             TEXT NOT NULL DEFAULT 'abierta',
    iniciada_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrada_at         TIMESTAMPTZ,
    config_version_id  BIGINT NOT NULL REFERENCES config_version (id),
    modo_global        TEXT NOT NULL,
    -- conteos, se sellan al cerrar
    decisiones INTEGER, movidas INTEGER, virtuales INTEGER,
    retenidas  INTEGER, errores INTEGER, saltadas_por_edicion INTEGER,
    -- el resumen diario sale UNA vez por plataforma y dia (injerto de C)
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
-- A lo mas UN resumen diario por plataforma y dia.
CREATE UNIQUE INDEX precio_corrida_un_resumen_por_dia
    ON precio_corrida (platform, fecha) WHERE resumen_enviado_at IS NOT NULL;
-- Trigger `precio_corrida_fecha_utc` (BEFORE INSERT): fecha := dia UTC de la
--   base, ignora la del cliente (mismo trato que precio_decision_fecha_utc).
-- Trigger `precio_corrida_solo_avanza` (BEFORE UPDATE OR DELETE):
--   * estado solo `abierta -> cerrada | abortada`, una vez;
--   * conteos y `resumen_enviado_at`: solo NULL -> valor, una vez;
--   * platform, fecha, owner, iniciada_at, config_version_id, modo_global:
--     inmutables; DELETE prohibido.
-- Una corrida que muere queda `abierta`; la siguiente de esa plataforma la
-- pasa a `abortada` al arrancar. /salud avisa si a las 14:00 UTC no hay
-- corrida `cerrada` del dia ni resumen enviado (hombre muerto).
COMMENT ON TABLE precio_corrida IS
  'REPRICING 02: cada ejecucion de la corrida. Varias por dia y plataforma '
  '(la diaria y sus repasos). De aqui salen el resumen diario, el bloque de '
  '/salud y el selector --corrida de la reversa en lote.';

-- La medicion de la compuerta, UNA por corrida y universo (injerto de C: el
-- fusible se mide por `platform/canal`, no por plataforma).
CREATE TABLE precio_compuerta_medicion (
    corrida_id  BIGINT NOT NULL REFERENCES precio_corrida (id),
    canal       estimacion_canal NOT NULL,
    medidas     INTEGER NOT NULL,         -- decisiones de hoy con cuenta (m_actual)
    nuevas      INTEGER NOT NULL,         -- intenciones nuevas no liberadas
    causa       TEXT,                     -- NULL = paso
    insumo      TEXT,
    detalle     TEXT,
    medida_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    avisada_at  TIMESTAMPTZ,              -- el aviso `compuerta` sale UNA vez
    PRIMARY KEY (corrida_id, canal),
    CONSTRAINT precio_medicion_conteos_validos CHECK (medidas >= 0 AND nuevas >= 0),
    CONSTRAINT precio_medicion_causa_valida
        CHECK (causa IS NULL OR causa IN ('movimiento_masivo', 'insumo_sistemico')),
    -- el insumo se nombra si y solo si la causa es `insumo_sistemico`
    CONSTRAINT precio_medicion_insumo_solo_sistemico
        CHECK ((insumo IS NOT NULL) = (causa IS NOT DISTINCT FROM 'insumo_sistemico')),
    CONSTRAINT precio_medicion_aviso_solo_si_retuvo
        CHECK (avisada_at IS NULL OR causa IS NOT NULL)
);
-- Trigger `precio_medicion_solo_sella_aviso` (BEFORE UPDATE OR DELETE): lo
-- unico mutable es `avisada_at`, de NULL a valor, una vez. DELETE prohibido.
COMMENT ON TABLE precio_compuerta_medicion IS
  'REPRICING 02: una fila con causa ES el disparo del fusible de ese universo '
  'en esa corrida. No hay tabla de fusible aparte que sincronizar. Se escribe '
  'tambien cuando pasa, para que la pantalla diga «7 de 160».';


-- -----------------------------------------------------------------------------
-- 6. RETENCION y LIBERACION (G2, G3, G4, G5)
-- -----------------------------------------------------------------------------
-- `precio_decision` es append-only y la decision se persiste ANTES de medir
-- la compuerta: retener no puede ser una columna suya. Tampoco es un
-- resultado nuevo: la decision dice la verdad (`subir`, con su cuenta y su
-- cotizacion) y la retencion dice que ESA corrida no la aplico y por que.
CREATE TABLE precio_retencion (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_id BIGINT NOT NULL REFERENCES precio_decision (id),
    corrida_id  BIGINT NOT NULL REFERENCES precio_corrida (id),
    causa       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT precio_retencion_una_por_corrida UNIQUE (decision_id, corrida_id),
    CONSTRAINT precio_retencion_causa_valida
        CHECK (causa IN ('movimiento_masivo', 'insumo_sistemico',
                         'apagador', 'corte_errores'))
);
CREATE INDEX precio_retencion_por_corrida ON precio_retencion (corrida_id);
CREATE TRIGGER precio_retencion_append_only
    BEFORE UPDATE OR DELETE ON precio_retencion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
-- Trigger `precio_retencion_coherente` (BEFORE INSERT): la decision es
-- `subir`/`bajar`, es de la plataforma de la corrida y del MISMO dia; con
-- causa `movimiento_masivo`/`insumo_sistemico` existe la fila de
-- `precio_compuerta_medicion` de esa corrida y del canal de la decision con
-- esa misma causa.

-- La escribe el dueno (`app_admin`); la corrida (`app_decide`) solo la lee.
-- Dos actores, dos tablas: no comparten ninguna fila.
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
-- Trigger `precio_liberacion_exige_retencion` (BEFORE INSERT): la corrida
-- tiene al menos una medicion con causa. No se suelta lo que no se retuvo.
-- Soltar una corrida suelta todos sus universos retenidos.
-- Soltada = existe la fila. No hay columna `liberado` que sincronizar.

-- Lo que la corrida pregunta al aplicar y al medir, en un solo lugar:
CREATE VIEW v_precio_decision_liberada AS
    SELECT DISTINCT r.decision_id
      FROM precio_retencion r
      JOIN precio_compuerta_liberacion l ON l.corrida_id = r.corrida_id
     WHERE r.causa IN ('movimiento_masivo', 'insumo_sistemico');


-- -----------------------------------------------------------------------------
-- 7. precio_decision: columnas y dos reglas de trigger
-- -----------------------------------------------------------------------------
ALTER TABLE precio_decision
    ADD COLUMN corrida_id      BIGINT REFERENCES precio_corrida (id),
    ADD COLUMN goal_id         BIGINT REFERENCES precio_goal (id),  -- el que el motor LEYO
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
    -- desde hoy, mover precio exige decir como se comprobo el objetivo
    ADD CONSTRAINT precio_decision_mover_exige_verificacion
        CHECK (resultado NOT IN ('subir', 'bajar') OR verificacion IS NOT NULL) NOT VALID,
    -- Regla 3 + D3: desde hoy, toda L escrita dice su origen, y toda L
    -- medida o imputada apunta a su muestra. Las ~120 filas historicas (FBA,
    -- sin columna) no se validan.
    ADD CONSTRAINT precio_decision_l_exige_origen
        CHECK (l_valor IS NULL OR l_origen IS NOT NULL) NOT VALID,
    ADD CONSTRAINT precio_decision_muestra_segun_origen
        CHECK ((l_origen IN ('propio', 'familia', 'marketplace'))
               = (envio_muestra_id IS NOT NULL)) NOT VALID;
CREATE INDEX precio_decision_por_dia ON precio_decision (platform, decision_date);
CREATE INDEX precio_decision_por_corrida ON precio_decision (corrida_id);
-- `prioridad` se queda (nullable) y deja de escribirse: sin cupo nadie ordena.

-- CREATE OR REPLACE FUNCTION precio_decision_coherente(): igual a la 0039
-- salvo TRES predicados (el resto del cuerpo, identico):
--
--   (0) GOAL (injerto de C). Hoy el trigger busca "el goal vigente" y pisa
--       `NEW.goal := v_goal`. Con goals editables en pantalla eso deja una
--       fila con un goal distinto del que el motor uso para decidir. Pasa a:
--       si `NEW.goal_id` viene, se valida ESE goal (mismo listing y
--       plataforma, vigente hoy) y `NEW.goal := su margen`; si ya no esta
--       vigente, el INSERT revienta con `goal_reemplazado` y la corrida lo
--       cuenta en `saltadas_por_edicion`. Si `NEW.goal_id` es NULL (codigo
--       viejo durante el deploy) se conserva la busqueda de hoy.
--
--   (a) MODO. Hoy:   AND g.mode = NEW.mode
--       Pasa a:      AND (g.mode = NEW.mode
--                         OR (g.mode = 'live' AND NEW.mode = 'shadow'))
--       Un goal `live` ampara una decision `shadow` (el apagador o el
--       universo la bajaron). Un goal `shadow` JAMAS ampara una `live`.
--       Sin este cambio el apagador en `shadow` reventaria cada INSERT.
--
--   (b) PRODUCTO. Hoy: NEW.product_id = listing.product_id
--       Pasa a:        NEW.product_id IN (SELECT product_id
--                                           FROM v_precio_unidad_miembro
--                                          WHERE listing_id = NEW.listing_id
--                                            AND platform = NEW.platform)
--       y el chequeo de la muestra de envio compara contra NEW.product_id
--       (el miembro que manda) en vez de `listing.product_id`.
--
-- `precio_cambio_nacimiento` no cambia: `aplicado = (decision.mode = 'live')`
-- sigue siendo cierto con el modo EFECTIVO guardado en la decision.


-- -----------------------------------------------------------------------------
-- 8. precio_cambio: cierre por lectura viva y rastro de lote (G5, G6)
-- -----------------------------------------------------------------------------
-- (x2) Ensancha el vocabulario. El readback solo confirma EN POSITIVO: si la
-- lectura de despues trae el precio nuevo, cierra; nunca decide no_confirmado.
ALTER TABLE precio_cambio DROP CONSTRAINT precio_cambio_confirmado_por_valido;
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_confirmado_por_valido
    CHECK (confirmado_por IS NULL
           OR confirmado_por IN ('observacion', 'virtual', 'lectura_viva'));
ALTER TABLE precio_cambio
    ADD COLUMN corrida_id   BIGINT REFERENCES precio_corrida (id),  -- NULL en reversas
    ADD COLUMN lote_reversa TEXT;                                   -- huella del lote
ALTER TABLE precio_cambio ADD CONSTRAINT precio_cambio_lote_solo_reversa
    CHECK (lote_reversa IS NULL OR es_reversa);
CREATE INDEX precio_cambio_por_corrida ON precio_cambio (corrida_id);
CREATE INDEX precio_cambio_por_lote ON precio_cambio (lote_reversa)
    WHERE lote_reversa IS NOT NULL;
-- CREATE OR REPLACE FUNCTION precio_cambio_sella_transicion(): identica, con
-- `corrida_id` y `lote_reversa` agregadas al ROW(...) de columnas inmutables.
-- La progresion de estados NO cambia (pendiente -> enviado | error,
-- enviado -> confirmado | no_confirmado).
--
-- Sin columna nueva para "error sin efecto": se DERIVA al leer,
--   sin_efecto := estado = 'error'
--                 AND (error_code = 'huerfana_sin_efecto'
--                      OR (readback_precio, readback_precio_currency)
--                         = (precio_antes, precio_antes_currency))
-- El cooldown no cuenta un cambio `sin_efecto`.


-- -----------------------------------------------------------------------------
-- 9. precio_goal: reedicion el mismo dia y rastro de siembra (G28, G29)
-- -----------------------------------------------------------------------------
-- (x1) Sembrar a las 10:00 y ajustar a las 10:05 es el flujo de D6+D7. Con el
-- UNIQUE de la 0039 la segunda fila choca con la primera aunque ya este
-- anulada (`valid_to = valid_from`). El indice parcial ignora las anuladas;
-- `precio_goal_un_vigente` y `precio_goal_sin_solape` siguen intactos.
ALTER TABLE precio_goal DROP CONSTRAINT precio_goal_unico_por_fecha;
CREATE UNIQUE INDEX precio_goal_unico_por_fecha
    ON precio_goal (listing_id, platform, valid_from)
    WHERE valid_to IS DISTINCT FROM valid_from;
ALTER TABLE precio_goal
    ADD COLUMN origen       TEXT,          -- manual | margen_de_hoy | cambio_de_modo
    ADD COLUMN lote         TEXT,          -- huella del plan que la escribio
    ADD COLUMN m_referencia NUMERIC(8, 4); -- margen de la unidad al sembrar
ALTER TABLE precio_goal ADD CONSTRAINT precio_goal_origen_valido
    CHECK (origen IS NULL OR origen IN ('manual', 'margen_de_hoy', 'cambio_de_modo'));
-- La banda sigue en el CHECK de la 0039 (0.10-0.60) Y en la config
-- (`precio_goal_min/max_pct`): dos fuentes que ya existian; la config solo
-- puede estrechar. No se toca aqui.

-- El predicado de vigencia vivia copiado en cuatro lugares (goals_write,
-- fuentes, corrida, trigger). Los tres de Python leen esta vista.
CREATE VIEW v_precio_goal_vigente AS
    SELECT g.id AS goal_id, g.listing_id, g.platform, g.margen_goal_pct,
           g.mode, g.valid_from, g.origen, g.m_referencia
      FROM precio_goal g
     WHERE g.valid_from <= (now() AT TIME ZONE 'UTC')::date
       AND (g.valid_to IS NULL OR g.valid_to > (now() AT TIME ZONE 'UTC')::date);


-- -----------------------------------------------------------------------------
-- 10. GRANTs (mismos cuatro roles; ninguno nuevo)
-- -----------------------------------------------------------------------------
GRANT SELECT ON listing_miembro, ledger_event_atribucion,
                meli_publicacion_observation, precio_corrida, precio_retencion,
                precio_compuerta_medicion, precio_compuerta_liberacion,
                v_precio_unidad_miembro, v_precio_venta_unidad,
                v_precio_goal_vigente, v_precio_decision_liberada
    TO app_read, app_decide, app_admin, app_ingest;

-- corrida, reversa y el job de muestras de envio
GRANT INSERT ON precio_corrida, precio_retencion, precio_compuerta_medicion TO app_decide;
GRANT UPDATE (estado, cerrada_at, decisiones, movidas, virtuales, retenidas,
              errores, saltadas_por_edicion, resumen_enviado_at)
    ON precio_corrida TO app_decide;
GRANT UPDATE (avisada_at) ON precio_compuerta_medicion TO app_decide;
-- `precio_envio_muestra` tiene UN escritor: el job de muestras (`app_decide`,
-- grant de la 0039). La estimacion (`app_ingest`) solo la lee.
-- (INSERT en precio_envio_muestra y los UPDATE de sello de precio_cambio ya
--  los da la 0039 a app_decide.)

-- el dueno, desde la pantalla
GRANT INSERT ON precio_compuerta_liberacion TO app_admin;
-- (INSERT y UPDATE(valid_to) en precio_goal ya son de app_admin.)

-- ingestas: catalogo MeLi, ledger, estimacion
GRANT INSERT, UPDATE ON listing_miembro TO app_ingest;
GRANT INSERT ON ledger_event_atribucion, meli_publicacion_observation TO app_ingest;
GRANT SELECT ON precio_envio_muestra TO app_ingest;   -- la estimacion lee su L

GRANT USAGE ON SEQUENCE precio_corrida_id_seq, precio_retencion_id_seq TO app_decide;
GRANT USAGE ON SEQUENCE meli_publicacion_observation_id_seq TO app_ingest;


-- -----------------------------------------------------------------------------
-- 11. Candado de la migracion (patron del DO final de la 0039)
-- -----------------------------------------------------------------------------
-- Un bloque DO que siembra y REVIERTE, y falla la migracion si:
--   * app_decide puede INSERT en precio_compuerta_liberacion;
--   * app_admin puede INSERT en precio_retencion o en precio_corrida;
--   * una decision `live` entra bajo un goal `shadow`;
--   * una decision `shadow` NO entra bajo un goal `live`;
--   * una muestra `familia` entra sin donantes;
--   * una decision nueva con l_valor entra sin l_origen;
--   * una liberacion entra para una corrida que no retuvo;
--   * una decision con `goal_id` de un goal ya cerrado entra;
--   * una decision `subir` nueva entra sin `verificacion`;
--   * una segunda corrida del mismo dia sella otro `resumen_enviado_at`;
--   * una corrida pasa de `cerrada` a `abierta`;
--   * un goal reeditado el mismo dia NO entra.
-- Y que al terminar no queda ni una fila sembrada.


-- -----------------------------------------------------------------------------
-- 12. Config: las claves nuevas nacen AQUI (ningun paso manual; injerto de C)
-- -----------------------------------------------------------------------------
-- `config_version` es append-only y se lee la ultima: se inserta una version
-- que COPIA la vigente y le suma las claves nuevas (patron de D.0; una fila
-- solo con las claves nuevas dejaria a Ads sin caps). `ORDER BY id DESC` sobre
-- el entero, no sobre texto (la leccion del `config_id` 9 contra 20).
--
-- INSERT INTO config_version (label, settings)
-- SELECT 'REPRICING 02 0054: apagador, fusible y senal',
--        settings || jsonb_build_object(
--          'precio_modo_global',                'shadow',  -- = comportamiento de hoy
--          'precio_modo_universo',              jsonb_build_object('amazon_mx/fba', 'shadow'),
--          'precio_fusible_frac',               0.50,      -- D1: la mitad
--          'precio_fusible_min_movimientos',    10,
--          'precio_insumo_salto_pct',           0.05,
--          'precio_insumo_salto_frac',          0.30,
--          'precio_corte_errores_consecutivos', 5,
--          'precio_senal_trafico_min',          200,
--          'precio_cohorte_caida_pct',          0.25,
--          'precio_envio_ventana_dias',         180)       -- sellado en E.2
--   FROM config_version ORDER BY id DESC LIMIT 1;
--
-- Los valores son propuesta del lead, con cota en `config.py`; el dueno los
-- cambia en /settings. Universo sin entrada en `precio_modo_universo` = `off`.
-- Las `precio_cap_*` se quedan inertes en la config: nadie las lee, y
-- borrarlas antes del deploy haria salir con `exit 2` a la version vieja.

COMMIT;


-- #############################################################################
-- NO SE CREA AHORA -- la puerta de D8 "variantes despues", para que se vea
-- que entra sin reescribir nada de lo de arriba
-- #############################################################################
-- CREATE TABLE precio_goal_variante (
--     goal_id         BIGINT NOT NULL REFERENCES precio_goal (id),
--     variante_ext    TEXT   NOT NULL,
--     margen_goal_pct NUMERIC(6, 4) NOT NULL CHECK (margen_goal_pct BETWEEN 0.10 AND 0.60),
--     PRIMARY KEY (goal_id, variante_ext)
-- );
-- Hija pura de `precio_goal`: nace y muere con su goal (misma vigencia), no
-- toca ninguna unicidad de goal, decision ni cambio. La unidad sigue teniendo
-- UN precio y UNA decision al dia; lo unico que cambia es que
-- `miembro_que_manda` recibe un goal distinto por miembro.
