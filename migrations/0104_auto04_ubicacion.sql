-- sonda-aplicada: SELECT to_regclass('public.ubicacion_evaluacion') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'campana_ajuste' AND column_name = 'evaluacion_id')
-- 0104 (AUTO-04 practica y camino live): ajustes automaticos por ubicacion.
--
-- Dos tablas append-only nuevas y una frontera de roles sobre campana_ajuste:
--
--   * `ubicacion_evaluacion` guarda UNA fila por campana y ciclo con el
--     `CasoCampana` congelado (`entrada`) y el veredicto de la politica pura
--     `ubicacion_v1`. En `shadow` es el registro de practica y jamas toca
--     `campana_ajuste` ni `v_cambio_bid`.
--   * `ubicacion_sin_aplicar` registra por que una evaluacion `live` con
--     veredicto `mover` no llego a Amazon (cuota, tope de espera, cambio
--     externo, config cambiada, etc.).
--   * `campana_ajuste.evaluacion_id` enlaza el unico registro de cambios
--     ejecutados con la evaluacion que lo ordeno. El trigger
--     `campana_ajuste_0104_frontera_roles` impide que `app_decide` (el motor)
--     inserte una fila que no nazca de una evaluacion `live` con veredicto
--     `mover`, y que el camino humano (`app_admin`) apunte a una evaluacion.
--   * `apply_cap_de_config` suma los dos mapeos `ubicacion` de la cuota diaria
--     fail-closed.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). Reversa: 0104_reversa_auto04_ubicacion.sql.

CREATE TABLE ubicacion_evaluacion (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle_id          BIGINT      NOT NULL REFERENCES optimizer_cycle (id),
    campana_id        BIGINT      NOT NULL REFERENCES ad_entity (id),
    platform          platform    NOT NULL CHECK (platform IN ('amazon_mx', 'amazon_us')),
    modo              TEXT        NOT NULL CHECK (modo IN ('shadow', 'live')),
    politica          TEXT        NOT NULL CHECK (politica <> ''),
    config_version_id BIGINT      NOT NULL REFERENCES config_version (id),
    antes_config_id   BIGINT      NOT NULL
                                  REFERENCES ads_campana_config_observation (id),
    decided_at        TIMESTAMPTZ NOT NULL,
    ventana_desde     DATE        NOT NULL,
    ventana_hasta     DATE        NOT NULL,
    moneda            currency    NOT NULL,
    entrada           JSONB       NOT NULL,
    veredicto         TEXT        NOT NULL CHECK (veredicto IN ('mantener', 'mover')),
    motivo            TEXT        NOT NULL CHECK (motivo <> ''),
    ubicacion         TEXT        NULL
                                  CHECK (ubicacion IN ('arriba_de_busqueda',
                                                       'resto_de_busqueda',
                                                       'paginas_de_producto')),
    de_pct            INTEGER     NULL CHECK (de_pct BETWEEN 0 AND 900),
    a_pct             INTEGER     NULL CHECK (a_pct BETWEEN 0 AND 900),
    CONSTRAINT ubicacion_evaluacion_ventana
        CHECK (ventana_desde <= ventana_hasta),
    -- Regla 3: un `mantener` no lleva ubicacion ni porcentajes inventados, y un
    -- `mover` trae siempre los tres datos y mueve de verdad.
    CONSTRAINT ubicacion_evaluacion_veredicto_coherente
        CHECK ((veredicto = 'mantener'
                AND ubicacion IS NULL AND de_pct IS NULL AND a_pct IS NULL)
               OR (veredicto = 'mover'
                   AND ubicacion IS NOT NULL AND de_pct IS NOT NULL
                   AND a_pct IS NOT NULL AND de_pct <> a_pct)),
    UNIQUE (cycle_id, campana_id)
);

CREATE INDEX ubicacion_evaluacion_campana_idx
    ON ubicacion_evaluacion (platform, campana_id, id DESC);

CREATE TRIGGER ubicacion_evaluacion_append_only
    BEFORE UPDATE OR DELETE ON ubicacion_evaluacion
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ubicacion_evaluacion_append_only_truncate
    BEFORE TRUNCATE ON ubicacion_evaluacion
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE ubicacion_evaluacion IS
  'AUTO-04 (0104): evaluacion diaria de las tres ubicaciones ajustables de '
  'una campana Amazon. Append-only (regla 5). `entrada` congela el '
  'CasoCampana completo para el replay determinista y `antes_config_id` '
  'apunta a la configuracion juzgada; en modo shadow es practica y nunca se '
  'convierte en ajuste.';

CREATE TABLE ubicacion_sin_aplicar (
    evaluacion_id BIGINT      PRIMARY KEY REFERENCES ubicacion_evaluacion (id),
    motivo        TEXT        NOT NULL
                              CHECK (motivo IN ('cuota_agotada',
                                                'tope_campanas_en_espera',
                                                'cambio_externo',
                                                'config_cambio',
                                                'modo_no_live',
                                                'error_put',
                                                'producto_sin_evidencia',
                                                'ajuste_en_curso',
                                                'pujas_pendientes')),
    registrado_el TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER ubicacion_sin_aplicar_append_only
    BEFORE UPDATE OR DELETE ON ubicacion_sin_aplicar
    FOR EACH ROW EXECUTE FUNCTION prohibir_mutacion();
CREATE TRIGGER ubicacion_sin_aplicar_append_only_truncate
    BEFORE TRUNCATE ON ubicacion_sin_aplicar
    FOR EACH STATEMENT EXECUTE FUNCTION prohibir_mutacion();

COMMENT ON TABLE ubicacion_sin_aplicar IS
  'AUTO-04 (0104): una fila por evaluacion live con veredicto mover que NO '
  'se envio a Amazon, con el motivo del vocabulario cerrado. Append-only.';

ALTER TABLE campana_ajuste
    ADD COLUMN evaluacion_id BIGINT NULL REFERENCES ubicacion_evaluacion (id);

CREATE UNIQUE INDEX campana_ajuste_evaluacion_uniq
    ON campana_ajuste (evaluacion_id) WHERE evaluacion_id IS NOT NULL;

-- El sello de 0067 sigue siendo la UNICA mutacion valida, ahora con
-- evaluacion_id tambien inmutable.
CREATE OR REPLACE FUNCTION campana_ajuste_0067_solo_confirmado() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.campana_id IS DISTINCT FROM NEW.campana_id
       OR OLD.platform IS DISTINCT FROM NEW.platform
       OR OLD.clase IS DISTINCT FROM NEW.clase
       OR OLD.antes_config_id IS DISTINCT FROM NEW.antes_config_id
       OR OLD.despues IS DISTINCT FROM NEW.despues
       OR OLD.huella IS DISTINCT FROM NEW.huella
       OR OLD.actor IS DISTINCT FROM NEW.actor
       OR OLD.go_literal IS DISTINCT FROM NEW.go_literal
       OR OLD.regresa_a IS DISTINCT FROM NEW.regresa_a
       OR OLD.evaluacion_id IS DISTINCT FROM NEW.evaluacion_id
       OR OLD.creado_el IS DISTINCT FROM NEW.creado_el
       OR OLD.confirmado_el IS NOT DISTINCT FROM NEW.confirmado_el
       OR OLD.confirmado_el IS NOT NULL THEN
        RAISE EXCEPTION
            'campana_ajuste: SOLO-CONFIRMADO: el unico UPDATE valido sella '
            'confirmado_el de una fila pendiente, sin tocar lo demas'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- Frontera de roles (AUTO-04). El motor escribe como `app_decide` y solo puede
-- registrar un movimiento que nacio de una evaluacion `live` con veredicto
-- `mover` de la MISMA campana, plataforma y configuracion juzgada. El camino
-- humano (`app_admin`, dueño y reversa) inserta con `evaluacion_id NULL`.
--
-- "es app_decide" se resuelve con `pg_has_role(...)`. Un superusuario es
-- miembro implicito de TODOS los roles, asi que la condicion lo deja en el
-- camino `app_admin`: es el rol de las migraciones y de las pruebas, y jamas
-- el de un servicio en produccion (orbit_decide solo es miembro de
-- `app_decide`).
CREATE FUNCTION campana_ajuste_0104_frontera_roles() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_eval ubicacion_evaluacion%ROWTYPE;
BEGIN
    IF pg_has_role(current_user, 'app_decide', 'MEMBER')
       AND NOT pg_has_role(current_user, 'app_admin', 'MEMBER') THEN
        IF NEW.evaluacion_id IS NULL
           OR NEW.clase <> 'ajuste_ubicacion'
           OR NEW.regresa_a IS NOT NULL
           OR NEW.go_literal <> 'AUTO_UBICACION'
           OR NEW.actor <> 'orbit_ubicacion' THEN
            RAISE EXCEPTION
                'campana_ajuste: FRONTERA-ROLES: app_decide solo inserta '
                'movimientos automaticos de ubicacion con evaluacion_id, '
                'clase ajuste_ubicacion, sin regresa_a, literal '
                'AUTO_UBICACION y actor orbit_ubicacion'
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO v_eval
          FROM ubicacion_evaluacion
         WHERE id = NEW.evaluacion_id;
        IF NOT FOUND
           OR v_eval.modo <> 'live'
           OR v_eval.veredicto <> 'mover'
           OR v_eval.campana_id <> NEW.campana_id
           OR v_eval.platform <> NEW.platform
           OR v_eval.antes_config_id <> NEW.antes_config_id THEN
            RAISE EXCEPTION
                'campana_ajuste: FRONTERA-ROLES: la evaluacion % no es un '
                'movimiento live de la misma campana, plataforma y '
                'configuracion juzgada', NEW.evaluacion_id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        IF NEW.evaluacion_id IS NOT NULL THEN
            RAISE EXCEPTION
                'campana_ajuste: FRONTERA-ROLES: el camino app_admin no '
                'apunta a una evaluacion (evaluacion_id debe ser NULL)'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER campana_ajuste_0104_frontera_roles
    BEFORE INSERT ON campana_ajuste
    FOR EACH ROW EXECUTE FUNCTION campana_ajuste_0104_frontera_roles();

COMMENT ON FUNCTION campana_ajuste_0104_frontera_roles IS
  'AUTO-04 (0104): separa los dos caminos de escritura de campana_ajuste. '
  'app_decide (motor de ubicaciones) solo inserta filas que nacen de una '
  'evaluacion live con veredicto mover de la misma campana, plataforma y '
  'configuracion, con literal AUTO_UBICACION y actor orbit_ubicacion. '
  'app_admin (humano y reversa) inserta siempre con evaluacion_id NULL.';

CREATE OR REPLACE FUNCTION apply_cap_de_config(p_motor TEXT) RETURNS INTEGER
LANGUAGE sql STABLE
SET search_path = pg_catalog, public
AS $$
    WITH v_clave AS (
        SELECT CASE p_motor
                   WHEN 'ads_optimizer:amazon_us:bid'       THEN 'ads_apply_cap_amazon_us_bid'
                   WHEN 'ads_optimizer:amazon_us:pause'     THEN 'ads_apply_cap_amazon_us_pause'
                   WHEN 'ads_optimizer:amazon_us:negative'  THEN 'ads_apply_cap_amazon_us_negative'
                   WHEN 'ads_optimizer:amazon_us:harvest'   THEN 'ads_apply_cap_amazon_us_harvest'
                   WHEN 'ads_optimizer:amazon_us:ubicacion' THEN 'ads_apply_cap_amazon_us_ubicacion'
                   WHEN 'ads_optimizer:amazon_mx:bid'       THEN 'ads_apply_cap_amazon_mx_bid'
                   WHEN 'ads_optimizer:amazon_mx:pause'     THEN 'ads_apply_cap_amazon_mx_pause'
                   WHEN 'ads_optimizer:amazon_mx:negative'  THEN 'ads_apply_cap_amazon_mx_negative'
                   WHEN 'ads_optimizer:amazon_mx:harvest'   THEN 'ads_apply_cap_amazon_mx_harvest'
                   WHEN 'ads_optimizer:amazon_mx:ubicacion' THEN 'ads_apply_cap_amazon_mx_ubicacion'
                   ELSE NULL
               END AS clave
    ),
    v_vigente AS (
        SELECT settings
          FROM config_version
         ORDER BY id DESC
         LIMIT 1
    )
    SELECT (v.settings ->> k.clave)::INTEGER
      FROM v_clave k CROSS JOIN v_vigente v
     WHERE k.clave IS NOT NULL;
$$;

COMMENT ON FUNCTION apply_cap_de_config IS
  'El mapeo sellado y TESTEADO config->quota (brief §5.2): clave '
  'ads_apply_cap_<platform>_<kind> para el motor ads_optimizer:<platform>:<kind>, '
  'con platform en {amazon_us, amazon_mx} y kind en {bid, pause, negative, '
  'harvest, ubicacion}. Devuelve NULL si el motor esta fuera del vocabulario, '
  'si no hay config_version, o si la clave no existe en la config vigente: en '
  'los tres casos el INSERT de quota revienta (fail-closed: sin clave NO nace '
  'fila del dia -> cero applies).';

GRANT SELECT ON ubicacion_evaluacion TO app_read, app_decide, app_ingest, app_admin;
GRANT INSERT ON ubicacion_evaluacion TO app_decide;
GRANT USAGE, SELECT ON SEQUENCE ubicacion_evaluacion_id_seq TO app_decide;

GRANT SELECT ON ubicacion_sin_aplicar TO app_read, app_decide, app_admin;
GRANT INSERT ON ubicacion_sin_aplicar TO app_decide;

GRANT INSERT, UPDATE (confirmado_el) ON campana_ajuste TO app_decide;
GRANT INSERT ON ads_campana_config_observation TO app_decide;
GRANT USAGE, SELECT ON SEQUENCE campana_ajuste_id_seq TO app_decide;
GRANT USAGE, SELECT ON SEQUENCE ads_campana_config_observation_id_seq TO app_decide;

DO $$
BEGIN
    IF NOT has_table_privilege('app_read', 'ubicacion_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'ubicacion_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_ingest', 'ubicacion_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'ubicacion_evaluacion', 'SELECT')
       OR NOT has_table_privilege('app_decide', 'ubicacion_evaluacion', 'INSERT')
       OR has_table_privilege('app_decide', 'ubicacion_evaluacion', 'UPDATE')
       OR has_table_privilege('app_decide', 'ubicacion_evaluacion', 'DELETE')
       OR has_table_privilege('app_read', 'ubicacion_evaluacion', 'INSERT')
       OR has_table_privilege('app_ingest', 'ubicacion_evaluacion', 'INSERT') THEN
        RAISE EXCEPTION '0104: privilegios de ubicacion_evaluacion invalidos';
    END IF;
    IF NOT has_table_privilege('app_decide', 'ubicacion_sin_aplicar', 'INSERT')
       OR NOT has_table_privilege('app_read', 'ubicacion_sin_aplicar', 'SELECT')
       OR NOT has_table_privilege('app_admin', 'ubicacion_sin_aplicar', 'SELECT')
       OR has_table_privilege('app_ingest', 'ubicacion_sin_aplicar', 'INSERT') THEN
        RAISE EXCEPTION '0104: privilegios de ubicacion_sin_aplicar invalidos';
    END IF;
    IF NOT has_table_privilege('app_decide', 'campana_ajuste', 'INSERT')
       OR NOT has_column_privilege('app_decide', 'campana_ajuste', 'confirmado_el', 'UPDATE')
       OR NOT has_table_privilege('app_decide', 'ads_campana_config_observation', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'campana_ajuste', 'INSERT')
       OR NOT has_table_privilege('app_admin', 'ads_campana_config_observation', 'INSERT')
       OR has_table_privilege('app_ingest', 'campana_ajuste', 'INSERT')
       OR has_table_privilege('app_read', 'campana_ajuste', 'INSERT') THEN
        RAISE EXCEPTION '0104: privilegios de campana_ajuste invalidos';
    END IF;
    IF NOT has_sequence_privilege('app_decide', 'campana_ajuste_id_seq', 'USAGE')
       OR NOT has_sequence_privilege(
           'app_decide', 'ads_campana_config_observation_id_seq', 'USAGE')
       OR NOT has_sequence_privilege('app_decide', 'ubicacion_evaluacion_id_seq', 'USAGE') THEN
        RAISE EXCEPTION '0104: privilegios de secuencia invalidos';
    END IF;
END
$$;
