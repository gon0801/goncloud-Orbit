-- D.0 (REPRICING 01): readback DESPUÉS de la migración y la siembra. Solo
-- lectura: `correr.sh` lo manda como lector dentro de `BEGIN READ ONLY`, con
-- salida `clave|valor` (psql -tA -F'|'). Lo que la fila D.0 pide ver: las
-- cinco tablas `precio_*` con cero filas, `listing_id_platform_key`, los
-- índices parciales, el EXCLUDE, los triggers, los caps (`precio:*` = 5 y los
-- de Ads iguales que antes), la `config_version` nueva y sus 20 claves.

SELECT 'tablas', string_agg(tablename, ',' ORDER BY tablename)
  FROM pg_tables
 WHERE schemaname = 'public' AND tablename LIKE 'precio\_%';
SELECT 'filas:precio_goal', count(*)::text FROM precio_goal;
SELECT 'filas:precio_decision', count(*)::text FROM precio_decision;
SELECT 'filas:precio_cotizacion', count(*)::text FROM precio_cotizacion;
SELECT 'filas:precio_envio_muestra', count(*)::text FROM precio_envio_muestra;
SELECT 'filas:precio_cambio', count(*)::text FROM precio_cambio;
SELECT 'listing_id_platform_key', count(*)::text
  FROM pg_constraint
 WHERE conrelid = 'public.listing'::regclass AND conname = 'listing_id_platform_key';
SELECT 'exclude:precio_goal_sin_solape', count(*)::text
  FROM pg_constraint
 WHERE conrelid = 'public.precio_goal'::regclass
   AND conname = 'precio_goal_sin_solape' AND contype = 'x';
SELECT 'indices_parciales', count(*)::text
  FROM pg_index
 WHERE indexrelid IN ('public.precio_goal_un_vigente'::regclass,
                      'public.precio_cambio_abierto_unico'::regclass)
   AND indpred IS NOT NULL;
SELECT 'triggers:' || tgrelid::regclass::text, count(*)::text
  FROM pg_trigger
 WHERE tgrelid IN ('public.precio_goal'::regclass, 'public.precio_decision'::regclass,
                   'public.precio_cotizacion'::regclass,
                   'public.precio_envio_muestra'::regclass,
                   'public.precio_cambio'::regclass)
   AND NOT tgisinternal AND tgenabled <> 'D'
 GROUP BY tgrelid
 ORDER BY 1;
SELECT 'cap:' || m, coalesce(apply_cap_de_config(m)::text, 'NULL')
  FROM unnest(ARRAY[
      'ads_optimizer:amazon_mx:bid', 'ads_optimizer:amazon_mx:pause',
      'ads_optimizer:amazon_mx:negative', 'ads_optimizer:amazon_mx:harvest',
      'ads_optimizer:amazon_us:bid', 'ads_optimizer:amazon_us:pause',
      'ads_optimizer:amazon_us:negative', 'ads_optimizer:amazon_us:harvest',
      'precio:amazon_mx', 'precio:amazon_us', 'precio:meli'
  ]) AS m
 ORDER BY m;
SELECT 'config_id', max(id)::text FROM config_version;
SELECT 'config_label', coalesce(label, '') FROM config_version ORDER BY id DESC LIMIT 1;
SELECT 'clave:' || e.key, e.value::text
  FROM (SELECT settings FROM config_version ORDER BY id DESC LIMIT 1) v,
       jsonb_each(v.settings) AS e
 WHERE e.key LIKE 'precio\_%'
 ORDER BY e.key;
