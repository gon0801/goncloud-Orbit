-- ---------------------------------------------------------------------------
-- REVERSA DE LA MIGRACION 0091 (ARREGLOS 03 P-D).
--
-- Restaura apply_cap_de_config con el texto exacto de la 0039 (los ocho
-- mapeos de Ads mas los tres precio:*) con su COMMENT, y devuelve las tres
-- claves `precio_cap_*` a la config vigente en 1000000 (el valor de hoy: el
-- tope original de 5/dia nunca limite nada). La sonda de la ida vuelve a dar
-- falso: el cuerpo de la funcion vuelve a traer precio_cap_*.
--
-- NO se aplica en el despliegue normal: la corre tools/despliegue/rollback.sh
-- en una sola transaccion (psql -1). Re-runnable.
-- ---------------------------------------------------------------------------

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
                   WHEN 'precio:amazon_mx'                 THEN 'precio_cap_amazon_mx'
                   WHEN 'precio:amazon_us'                 THEN 'precio_cap_amazon_us'
                   WHEN 'precio:meli'                      THEN 'precio_cap_meli'
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
  'REPRICING 01 A.0: el mapeo sellado de 0002 (ocho motores '
  'ads_optimizer:<platform>:<kind>, intactos) más los tres del motor de '
  'precios —precio:amazon_mx, precio:amazon_us y precio:meli hacia '
  'precio_cap_amazon_mx/_us/_meli—. Vocabulario cerrado: fuera del mapa, '
  'NULL y el INSERT de quota revienta (fail-closed).';


INSERT INTO config_version (label, settings)
SELECT 'ARREGLOS 03 P-D: reversa, precio_cap_* en 1000000',
       settings || '{"precio_cap_amazon_mx": 1000000, "precio_cap_amazon_us": 1000000, "precio_cap_meli": 1000000}'::jsonb
  FROM config_version ORDER BY id DESC LIMIT 1;
