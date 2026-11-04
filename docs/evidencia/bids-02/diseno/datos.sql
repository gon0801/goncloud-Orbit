-- datos.sql del runner A: tablas, vistas y CHECK nuevos o cambiados.
-- BOSQUEJO, no migracion: faltan BEGIN/COMMIT, REVOKE por objeto, bloque DO de privilegios, triggers
-- append-only y GRANTs finos. Cada bloque dice a que migracion pertenece y que carril lo necesita.
-- Numeracion reservada (la ultima aplicada es 0052) para que los carriles no choquen:
--   Este archivo agrupa por tema. El plan (plans/bids-02.md, "Convivencia con REPRICING 02") reparte una
--   migracion por tarea y SUS numeros mandan:
--     0060 (0.b) vistas v_hoja_activa y v_cambio_bid
--     0061 (V.1) ads_campana_config_observation y su vista vigente
--     0062 (V.2) ads_placement_observation
--     0063 (T.1) retiro de los peldanos cache_estado y default
--     0064 (I.1) campana_grupo.tipo y su candado de roles
--     0065 (I.3) impulso, impulso_lectura y los estados nuevos de fabrica_lote
--     0066 (I.4) anuncio_retiro (con columna `modo`: pausa o archivo) y anuncio_reposicion
--     0067 (V.3) campana_ajuste y la tercera rama de v_cambio_bid
--   Los encabezados de bloque de abajo conservan el numero del primer tema de cada grupo.
-- =====================================================================================================
-- 0060  BASE DE LECTURA: dos vistas que hoy estan escritas tres veces a mano
-- =====================================================================================================

-- "Hoja activa" (hoja, ad group y campana ENABLED) con su tipo de campana. Hoy ese triple JOIN vive en
-- _SQL_DECISORAS (cycle.py), en v_entidad_inerte y en cada pantalla. Una definicion, una fuente.
-- El tipo se decide primero por la campana (AUTO) y despues por la hoja, porque las campanas automaticas
-- tambien tienen product targets (las clausulas automaticas).
CREATE VIEW v_hoja_activa AS
SELECT k.id                AS hoja_id,
       k.platform,
       k.kind,
       k.parent_id         AS ad_group_id,
       ag.parent_id        AS campana_id,
       CASE
           WHEN sc.targeting_type = 'AUTO'      THEN 'automatica'
           WHEN k.kind = 'product_target'       THEN 'product_targeting'
           WHEN k.match_type = 'EXACT'          THEN 'exact'
           WHEN k.match_type = 'PHRASE'         THEN 'phrase'
           WHEN k.match_type = 'BROAD'          THEN 'broad'
       END                 AS tipo_campana,          -- NULL = no clasificable; la pantalla lo cuenta aparte
       sk.current_bid,
       sk.bid_currency
  FROM ad_entity k
  JOIN ad_entity ag       ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  JOIN ad_entity c        ON c.id = ag.parent_id AND c.kind = 'campaign'
  JOIN ad_entity_state sk ON sk.ad_entity_id = k.id  AND sk.status = 'ENABLED'
  JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id AND sg.status = 'ENABLED'
  JOIN ad_entity_state sc ON sc.ad_entity_id = c.id  AND sc.status = 'ENABLED'
 WHERE k.kind IN ('keyword', 'product_target');

-- Historial de bids aplicados por hoja. Fuente unica de: trayectoria del motor (sustituye a
-- _SQL_EN_COOLDOWN para bids y a _SQL_ULTIMO_BID_APLICADO), pantalla de keywords danadas y tablero de
-- encogimiento. Dos origenes de filas:
--   (a) bids del motor confirmados por un ciclo live (los mismos filtros del cooldown de hoy);
--   (b) reversas de bid confirmadas en el ledger (regreso pedido por el dueno): el bid vuelve al old_value
--       de la decision revertida. Hoy estas reversas son invisibles para el cooldown y para D.2, y por eso
--       el motor vuelve a recortar al dia siguiente.
-- `origen` distingue el regreso por desplome (lo decide el motor, motivo congelado) del recorte o subida.
CREATE VIEW v_cambio_bid AS
SELECT d.ad_entity_id                         AS hoja_id,
       da.confirmed_at                        AS confirmado_el,
       d.old_value                            AS bid_antes,
       d.new_value                            AS bid_despues,
       d.value_currency                       AS moneda,
       CASE WHEN d.inputs->>'motivo' = 'regreso_por_desplome'
            THEN 'regreso_por_desplome' ELSE 'motor' END AS origen,
       d.id                                   AS decision_id
  FROM decision_application da
  JOIN decision d         ON d.id = da.decision_id AND d.kind = 'bid'
  JOIN optimizer_cycle oc ON oc.id = da.applied_cycle_id AND oc.mode = 'live'
 WHERE da.verify_ok IS TRUE
   AND d.old_value IS NOT NULL AND d.new_value IS NOT NULL AND d.new_value <> d.old_value
UNION ALL
SELECT d.ad_entity_id,
       a.finished_at,                         -- sello del resultado en el ledger (0002_apply.sql:305)
       NULL::numeric,                         -- el bid previo al regreso lo deriva el lector: es el
                                              -- bid_despues del cambio anterior de esa hoja
       d.old_value,
       d.value_currency,
       'regreso_del_dueno',
       d.id
  FROM apply_attempt a
  JOIN decision d ON d.id = a.decision_id AND d.kind = 'bid'
 WHERE a.tipo = 'reversa' AND a.resultado = 'ok';
-- Nota de replay: esta vista alimenta `caso.trayectoria`, que se CONGELA entera en decision.inputs. El
-- replay nunca vuelve a leer la vista.

-- =====================================================================================================
-- 0061  VER LO INVISIBLE: configuracion de campana y reporte por placement
-- =====================================================================================================

-- Presupuesto, estrategia de puja y ajustes de placement de cada campana. Append-only y solo cuando algo
-- cambia: la tabla es la HISTORIA (ad_entity_state se pisa en cada sync y por eso no sirve).
-- El presupuesto llega sin moneda en el payload; se guarda con la del perfil, igual que los bids.
CREATE TABLE ads_campana_config_observation (
    id                  BIGSERIAL PRIMARY KEY,
    ad_entity_id        BIGINT      NOT NULL REFERENCES ad_entity (id),   -- kind = 'campaign' (trigger)
    observed_at         TIMESTAMPTZ NOT NULL,
    presupuesto_diario  NUMERIC,
    presupuesto_moneda  TEXT,
    estrategia_puja     TEXT,            -- texto tal cual de Amazon; el vocabulario real se sella con la sonda
    ajuste_top_pct      INTEGER,
    ajuste_resto_pct    INTEGER,
    ajuste_producto_pct INTEGER,
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
-- Ojo (trampa "NULL en CHECK"): un CHECK con NULL pasa. Por eso los ajustes van con COALESCE explicito y
-- la moneda con igualdad de nulidad, no con `presupuesto_moneda IN (...)`.

CREATE VIEW v_campana_config_vigente AS
SELECT DISTINCT ON (ad_entity_id) *
  FROM ads_campana_config_observation
 ORDER BY ad_entity_id, observed_at DESC;

-- Metricas por placement. Tabla y corrida propias: meter placements en ads_metric_observation crearia un
-- tercer grano del mismo dinero (el segundo ya duplico el gasto una vez). Bitemporal como las demas.
CREATE TABLE ads_placement_observation (
    platform         platform    NOT NULL,
    ad_entity_id     BIGINT      NOT NULL REFERENCES ad_entity (id),      -- campana
    placement        TEXT        NOT NULL,
    metric_date      DATE        NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    metric_currency  TEXT        NOT NULL,
    impressions      BIGINT,
    clicks           BIGINT,
    cost             NUMERIC,
    orders           BIGINT,
    ad_revenue       NUMERIC,
    top_of_search_is NUMERIC,     -- impression share de top of search, si el reporte lo entrega
    source_report_id TEXT        NOT NULL,
    CONSTRAINT placement_vocabulario
        CHECK (placement IN ('top_de_busqueda', 'resto_de_busqueda', 'pagina_de_producto', 'fuera_de_amazon')),
    PRIMARY KEY (platform, ad_entity_id, placement, metric_date, observed_at)
);
-- El mapeo del texto de Amazon (placementClassification) a este vocabulario vive en la frontera de
-- ingesta; un valor desconocido salta la fila y se cuenta (regla 3), no se inventa categoria.

-- Ajustes de campana que aprueba el dueno (agregado tras las pruebas del 2026-10-09). Append-only: una fila
-- por ajuste, escrita ANTES del HTTP y confirmada con el readback. `antes` y `despues` guardan la
-- configuracion completa en columnas tipadas de la observacion (no JSON de Amazon): el regreso aplica `antes`.
CREATE TABLE campana_ajuste (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campana_id       BIGINT      NOT NULL REFERENCES ad_entity(id),
    platform         platform    NOT NULL,
    clase            TEXT        NOT NULL CHECK (clase IN ('fuera_de_amazon', 'ajuste_ubicacion', 'presupuesto')),
    antes_config_id  BIGINT      NOT NULL REFERENCES ads_campana_config_observation(id),
    despues          JSONB       NOT NULL,   -- ConfigCampana.como_json() (tipo de dominio, dinero como string)
    huella           TEXT        NOT NULL UNIQUE,
    actor            TEXT        NOT NULL CHECK (actor <> ''),
    go_literal       TEXT        NOT NULL,
    regresa_a        BIGINT      NULL REFERENCES campana_ajuste(id),   -- esta fila es el regreso de otra
    creado_el        TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmado_el    TIMESTAMPTZ NULL,       -- NULL = el readback no coincidio o el HTTP fallo
    CONSTRAINT campana_ajuste_un_regreso UNIQUE (regresa_a)
);
-- v_cambio_bid gana una tercera rama: por cada campana_ajuste confirmado de clase 'ajuste_ubicacion' (y de
-- estrategia, si algun dia se agrega), una fila por hoja de esa campana con origen 'ajuste_de_campana',
-- bid_antes = bid_despues = bid vigente y confirmado_el = campana_ajuste.confirmado_el.
-- Tablas nuevas del programa: suma una mas en verify/Launch.md y verify/Doctor.md.

-- =====================================================================================================
-- 0062  IMPULSO DE PRODUCTOS Y RETIRO DE ANUNCIOS
-- =====================================================================================================

-- Un impulso = un producto (una publicacion) = un grupo de DOS roles (category_exact + auto_discovery),
-- una campana y un ad group por rol. Por eso campana_grupo, campana_grupo_rol y campana_grupo_producto
-- NO cambian: la restriccion "un ad group por (grupo, rol)" se cumple tal cual.
-- La fila es inmutable. El estado se DERIVA de la ultima lectura y del estado del lote (v_impulso_estado).
CREATE TABLE impulso (
    id                 BIGSERIAL PRIMARY KEY,
    platform           platform    NOT NULL,
    listing_id         BIGINT      NOT NULL REFERENCES listing (id),
    product_id         BIGINT      NOT NULL REFERENCES product (id),
    grupo_id           BIGINT      NOT NULL UNIQUE REFERENCES campana_grupo (id),
    lote               TEXT        NOT NULL UNIQUE REFERENCES fabrica_lote (lote),
    subfamilia         TEXT        NOT NULL,              -- etiqueta que eligio el dueno (tipo_producto del grupo)
    tope_monto         NUMERIC     NOT NULL,
    tope_moneda        TEXT        NOT NULL,
    presupuesto_diario NUMERIC     NOT NULL,              -- del impulso entero; cada campana lleva la mitad
    plazo_dias         INTEGER     NOT NULL,
    lanzado_el         TIMESTAMPTZ NOT NULL,
    actor              TEXT        NOT NULL,
    go_literal         TEXT        NOT NULL,
    CONSTRAINT impulso_tope_positivo  CHECK (tope_monto > 0 AND presupuesto_diario > 0 AND plazo_dias > 0),
    CONSTRAINT impulso_tope_cabe      CHECK (presupuesto_diario <= tope_monto),
    CONSTRAINT impulso_moneda         CHECK (tope_moneda IN ('MXN', 'USD'))
);
-- Un producto no puede tener dos impulsos vivos en el mismo mercado. "Vivo" depende del estado derivado,
-- asi que el candado va en el lanzamiento (advisory lock por listing + lectura de v_impulso_estado), no
-- en un indice parcial sobre una columna que no existe.
-- Trigger (no CHECK): la moneda del tope es la de la plataforma, y el listing es de ese producto y plataforma.

-- Lectura diaria del vigia. Append-only. La clave (impulso_id, corte) hace idempotente la corrida del dia.
-- Cada lectura guarda el veredicto que produjo y lo que el vigia hizo: es el historial que ve el dueno.
CREATE TABLE impulso_lectura (
    impulso_id      BIGINT      NOT NULL REFERENCES impulso (id),
    corte           DATE        NOT NULL,          -- ultimo dia con ingesta incluido
    leido_el        TIMESTAMPTZ NOT NULL,
    dias_observados INTEGER     NOT NULL,
    impresiones     BIGINT,                        -- NULL = desconocido (alguna fila NULL), nunca cero inventado
    clics           BIGINT,
    gasto           NUMERIC,
    pedidos         BIGINT,
    veredicto       TEXT        NOT NULL,
    es_final        BOOLEAN     NOT NULL,
    hecho           TEXT        NOT NULL,
    CONSTRAINT lectura_veredicto CHECK (veredicto IN
        ('en_curso', 'vende', 'clics_sin_ventas', 'impresiones_sin_clics', 'sin_impresiones')),
    CONSTRAINT lectura_hecho CHECK (hecho IN ('nada', 'pausado', 'graduado', 'retiro_de_bolsas')),
    CONSTRAINT lectura_en_curso_no_final CHECK (NOT (veredicto = 'en_curso' AND es_final)),
    PRIMARY KEY (impulso_id, corte)
);

CREATE VIEW v_impulso_estado AS
SELECT i.*,
       l.corte, l.dias_observados, l.impresiones, l.clics, l.gasto, l.pedidos,
       l.veredicto, l.es_final,
       f.estado AS estado_lote
  FROM impulso i
  JOIN fabrica_lote f ON f.lote = i.lote
  LEFT JOIN LATERAL (
        SELECT * FROM impulso_lectura x WHERE x.impulso_id = i.id ORDER BY x.corte DESC LIMIT 1
  ) l ON TRUE;

-- La fabrica pausa y reanuda las campanas de un lote sin darlo por desarmado.
-- CAMBIA el CHECK de fabrica_lote.estado: se agrega 'pausado' (reversible); 'desarmado' sigue terminal.
ALTER TABLE fabrica_lote DROP CONSTRAINT fabrica_lote_estado_check;   -- CHECK en linea de 0018:35-36; nombre real: confirmar con \d
ALTER TABLE fabrica_lote ADD  CONSTRAINT fabrica_lote_estado_check
    CHECK (estado IN ('planeado', 'applied', 'failed', 'pausado', 'desarmado'));
-- Cada PUT de pausa o reanudacion deja su fila en fabrica_lote_paso ANTES del HTTP (ledger pre-HTTP),
-- con recurso 'campaign' y el `state` pedido en request_payload. No hace falta columna nueva.

-- Retiro de un product ad de una bolsa vieja y su reposicion. Dos tablas append-only en vez de una fila
-- que se actualiza: "retirado y no repuesto" se deriva (v_anuncio_retirado).
CREATE TABLE anuncio_retiro (
    id                BIGSERIAL PRIMARY KEY,
    platform          platform    NOT NULL,
    motivo            TEXT        NOT NULL,
    lote_retiro       TEXT        NOT NULL,           -- 'retiro-<huella>'; agrupa lo que el dueno confirmo junto
    impulso_id        BIGINT      REFERENCES impulso (id),   -- solo motivo 'impulso'
    ad_entity_id      BIGINT      NOT NULL REFERENCES ad_entity (id),   -- el product ad archivado
    ad_id_externo     TEXT        NOT NULL,
    ad_group_externo  TEXT        NOT NULL,
    campana_externa   TEXT        NOT NULL,
    sku               TEXT        NOT NULL,
    asin              TEXT        NOT NULL,
    retirado_el       TIMESTAMPTZ NOT NULL,
    actor             TEXT        NOT NULL,
    CONSTRAINT retiro_motivo CHECK (motivo IN ('impulso', 'sin_pedidos', 'subfamilia')),
    CONSTRAINT retiro_impulso_coherente CHECK ((motivo = 'impulso') = (impulso_id IS NOT NULL)),
    UNIQUE (ad_entity_id)                              -- un anuncio se retira una sola vez (archivar no se repite)
);

CREATE TABLE anuncio_reposicion (
    retiro_id        BIGINT      PRIMARY KEY REFERENCES anuncio_retiro (id),   -- una reposicion por retiro
    ad_id_nuevo      TEXT        NOT NULL,
    estado_nuevo     TEXT        NOT NULL,
    repuesto_el      TIMESTAMPTZ NOT NULL,
    actor            TEXT        NOT NULL,
    CONSTRAINT reposicion_estado CHECK (estado_nuevo IN ('ENABLED', 'PAUSED'))
);

CREATE VIEW v_anuncio_retirado AS
SELECT r.*, (p.retiro_id IS NOT NULL) AS repuesto
  FROM anuncio_retiro r
  LEFT JOIN anuncio_reposicion p ON p.retiro_id = r.id;

-- Arreglo de una consulta existente (no es esquema, se anota aqui porque depende del retiro):
-- _SQL_PAREJAS_CAMPANA_FAMILIA (cycle.py) cuenta product ads sin filtrar estado; un anuncio archivado
-- sigue definiendo la familia de la campana. Debe filtrar `ad_entity_state.status IN ('ENABLED','PAUSED')`.

-- =====================================================================================================
-- 0063  TARGET: retirar dos peldanos que nunca decidieron
-- =====================================================================================================

-- `cache_estado` (ad_entity_state.acos_target llega vacio en todas las corridas) y `default` (55 %) salen
-- de la escalera. Sin ningun peldano, la hoja se salta con motivo `sin_target`: un target inventado de 55 %
-- es justo lo que prohibe la regla 3.
-- ANTES de aplicar (regla 8): SELECT procedencia, count(*) FROM target_acos_ciclo GROUP BY 1;
-- se espera solo 'margen_plataforma'. Si aparece alguno de los dos, el CHECK conserva ese valor para filas
-- historicas y solo el codigo deja de emitirlo.
ALTER TABLE target_acos_ciclo DROP CONSTRAINT target_acos_ciclo_procedencia_check;   -- 0046:17, ampliado en 0048:231
ALTER TABLE target_acos_ciclo ADD  CONSTRAINT target_acos_ciclo_procedencia_check
    CHECK (procedencia IN ('goal_campana', 'goal_plataforma', 'margen_familia', 'margen_plataforma',
                           'setting_plataforma'));

-- =====================================================================================================
-- LO QUE NO CAMBIA (y por que)
-- =====================================================================================================
-- decision, decision_application, apply_attempt, apply_queue: sin cambios. El regreso por desplome es una
--   decision kind 'bid' normal; el regreso del dueno es una 'reversa' del ledger que ya existe.
-- decision_kind, campana_rol: sin valores nuevos.
-- campana_grupo_rol: sin columnas nuevas. El impulso no necesita N ad groups por campana. Lo unico que gana
--   la fabrica es saber de que tipo es el grupo (injerto de runner-b), en la migracion 0062:
--     ALTER TABLE campana_grupo ADD COLUMN tipo text NOT NULL DEFAULT 'fabrica5'
--       CHECK (tipo IN ('fabrica5', 'impulso'));
--     + trigger campana_grupo_rol_roles_de_impulso: un grupo impulso admite solo category_exact y
--       auto_discovery y rechaza un tercer rol. _roster_hermanas devuelve roster vacio para ese tipo, y la
--       fase de negatives cruzados cierra sin POST ni reversa (el defecto grave de G1 no aparece).
-- config_version: sin esquema nuevo; claves nuevas de settings (lectores fail-closed en goals.py):
--     ads_gasto_para_concluir_<platform>   (ausente = 350 MXN / 36 USD)
--     ads_target_fraccion_margen_amazon_us = 0.8 al encender (decision D1 del dueno)
--   y una clave que se conserva con vocabulario nuevo: ads_bid_politica_<platform> = 'niveles_v3';
--   ausente = el motor no mueve bids en esa plataforma (interruptor fail-closed, injerto de runner-c).
-- optimizer_cycle.notes.target: gana la clave `paso_politica` = 'asimetrico_v1' (JSON dentro de notes).
