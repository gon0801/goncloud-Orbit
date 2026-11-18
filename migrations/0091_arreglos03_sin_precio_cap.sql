-- sonda-aplicada: SELECT NOT (settings ? 'precio_cap_meli') AND pg_get_functiondef('apply_cap_de_config(text)'::regprocedure) NOT LIKE '%precio_cap_%' FROM config_version ORDER BY id DESC LIMIT 1
-- 0091 (ARREGLOS 03 P-D, precio.md F4): se borran las claves de tope de
-- precios `precio_cap_*` que nadie lee. El freno de volumen real pasa a ser
-- el tope del motor (mas de 50 cambios en vivo en un dia, compuerta.py), y
-- `apply_cap_de_config` vuelve a los ocho mapeos de Ads de la 0002.
--
-- La funcion queda con el texto exacto de migrations/0002_apply.sql (los
-- ocho mapeos ads_optimizer:*, con su COMMENT) y la config vigente pierde
-- `precio_cap_amazon_mx`, `precio_cap_amazon_us` y `precio_cap_meli` en una
-- config_version nueva. La cuota de precios (0039, tope 5/dia) nunca limito
-- nada: ninguna escritura del motor de precios la consultaba.
--
-- Sin BEGIN/COMMIT: tools/despliegue/desplegar.sh la aplica en una sola
-- transaccion (psql -1). Reversa:
-- 0091_reversa_arreglos03_sin_precio_cap.sql.

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


INSERT INTO config_version (label, settings)
SELECT 'ARREGLOS 03: sin precio_cap_*',
       settings - 'precio_cap_amazon_mx' - 'precio_cap_amazon_us' - 'precio_cap_meli'
  FROM config_version ORDER BY id DESC LIMIT 1;
