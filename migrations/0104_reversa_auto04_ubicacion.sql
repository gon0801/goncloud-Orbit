-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0104 (AUTO-04 ajustes por ubicacion).
--
-- Borra las dos tablas append-only nuevas, la columna `campana_ajuste.
-- evaluacion_id` con su indice y su frontera de roles, revoca los privilegios
-- que la 0104 dio a `app_decide` y devuelve `apply_cap_de_config` a los ocho
-- mapeos de la 0091. La tabla y la funcion existentes conservan su version de
-- 0067, sin `evaluacion_id`.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). Re-runnable (IF EXISTS).
-- ---------------------------------------------------------------------------

DROP TRIGGER IF EXISTS campana_ajuste_0104_frontera_roles ON campana_ajuste;
DROP FUNCTION IF EXISTS campana_ajuste_0104_frontera_roles();

ALTER TABLE campana_ajuste DROP COLUMN IF EXISTS evaluacion_id;

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

REVOKE INSERT, UPDATE (confirmado_el) ON campana_ajuste FROM app_decide;
REVOKE INSERT ON ads_campana_config_observation FROM app_decide;
REVOKE USAGE, SELECT ON SEQUENCE campana_ajuste_id_seq FROM app_decide;
REVOKE USAGE, SELECT ON SEQUENCE ads_campana_config_observation_id_seq FROM app_decide;

DROP TABLE IF EXISTS ubicacion_sin_aplicar;
DROP TABLE IF EXISTS ubicacion_evaluacion;

CREATE OR REPLACE FUNCTION apply_cap_de_config(p_motor TEXT) RETURNS INTEGER
LANGUAGE sql STABLE
SET search_path = pg_catalog, public
AS $$
    WITH v_clave AS (
        SELECT CASE p_motor
                   WHEN 'ads_optimizer:amazon_us:bid'      THEN 'ads_apply_cap_amazon_us_bid'
                   WHEN 'ads_optimizer:amazon_us:pause'    THEN 'ads_apply_cap_amazon_us_pause'
                   WHEN 'ads_optimizer:amazon_us:negative' THEN 'ads_apply_cap_amazon_us_negative'
                   WHEN 'ads_optimizer:amazon_us:harvest'  THEN 'ads_apply_cap_amazon_us_harvest'
                   WHEN 'ads_optimizer:amazon_mx:bid'      THEN 'ads_apply_cap_amazon_mx_bid'
                   WHEN 'ads_optimizer:amazon_mx:pause'    THEN 'ads_apply_cap_amazon_mx_pause'
                   WHEN 'ads_optimizer:amazon_mx:negative' THEN 'ads_apply_cap_amazon_mx_negative'
                   WHEN 'ads_optimizer:amazon_mx:harvest'  THEN 'ads_apply_cap_amazon_mx_harvest'
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
  'harvest}. Devuelve NULL si el motor esta fuera del vocabulario, si no hay '
  'config_version, o si la clave no existe en la config vigente: en los tres '
  'casos el INSERT de quota revienta (fail-closed, sellado 8: sin clave NO '
  'nace fila del dia -> cero applies, y el estado fail-closed es VISIBLE en '
  'Salud, no disfraz de rampa sana).';
