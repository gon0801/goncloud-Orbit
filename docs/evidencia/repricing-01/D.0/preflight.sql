-- D.0 (REPRICING 01): estado de la base ANTES de tocar nada. Solo lectura:
-- `correr.sh` lo manda como lector dentro de `BEGIN READ ONLY`, con salida
-- `clave|valor` (psql -tA -F'|') que el guion lee para decidir cada paso.

SELECT 'pg_version', current_setting('server_version');
-- ausente | completa | parcial: las cinco tablas y la constraint de `listing`.
-- La 0039 va en UNA transacción, así que «parcial» solo sale de una mano
-- ajena; el guion corta ahí antes de migrar o sembrar (CodeRabbit, #317).
SELECT 'estado_0039',
       CASE count(*) FILTER (WHERE presente)
           WHEN 0 THEN 'ausente' WHEN count(*) THEN 'completa' ELSE 'parcial'
       END
  FROM (VALUES
      (to_regclass('public.precio_goal') IS NOT NULL),
      (to_regclass('public.precio_decision') IS NOT NULL),
      (to_regclass('public.precio_cotizacion') IS NOT NULL),
      (to_regclass('public.precio_envio_muestra') IS NOT NULL),
      (to_regclass('public.precio_cambio') IS NOT NULL),
      (EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'listing_id_platform_key'))
  ) AS o(presente);
SELECT 'btree_gist', count(*)::text FROM pg_extension WHERE extname = 'btree_gist';
SELECT 'config_id', id::text FROM config_version ORDER BY id DESC LIMIT 1;
SELECT 'config_label', coalesce(label, '') FROM config_version ORDER BY id DESC LIMIT 1;
SELECT 'claves_precio', count(*)::text
  FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) v,
       jsonb_object_keys(v.settings) AS k
 WHERE k LIKE 'precio\_%';
SELECT 'listing_dup_id_platform', count(*)::text
  FROM (SELECT id, platform FROM listing GROUP BY 1, 2 HAVING count(*) > 1) d;
SELECT 'cap:' || m, coalesce(apply_cap_de_config(m)::text, 'NULL')
  FROM unnest(ARRAY[
      'ads_optimizer:amazon_mx:bid', 'ads_optimizer:amazon_mx:pause',
      'ads_optimizer:amazon_mx:negative', 'ads_optimizer:amazon_mx:harvest',
      'ads_optimizer:amazon_us:bid', 'ads_optimizer:amazon_us:pause',
      'ads_optimizer:amazon_us:negative', 'ads_optimizer:amazon_us:harvest'
  ]) AS m
 ORDER BY m;
