# SELECT de evidencia — ORBIT 06 · 1.2

Corrida del **lead** tras merge + deploy formal de `0006_contribucion_entidad.sql`.
Solo lectura. Cero mutaciones.

Ventana sellada: `D_corte = (now() AT TIME ZONE 'UTC')::date - 15`,
dias `[D_corte-89, D_corte]` via `v_metric_mature`.

## 1. Contribucion por entidad (rango en ambas plataformas)

```sql
SELECT platform,
       count(*) AS entidades,
       count(*) FILTER (WHERE rango_invertido) AS con_rango_invertido,
       count(*) FILTER (WHERE fx_source = 'nearest_prior') AS fx_nearest,
       count(*) FILTER (WHERE fx_source = 'exact') AS fx_exact,
       count(*) FILTER (WHERE fx_source IS NULL) AS fx_null,
       bool_and(no_decisoria) AS todas_no_decisorias,
       bool_and(contrib_sin_halo IS NOT NULL AND contrib_con_halo IS NOT NULL)
           AS par_halo_completo,
       min(contrib_sin_halo) AS contrib_sin_min,
       max(contrib_con_halo) AS contrib_con_max
  FROM v_contribucion_entidad
 GROUP BY platform
 ORDER BY platform;
```

## 2. Muestra de filas (citar rango)

```sql
SELECT platform, ad_entity_id, kind, metric_currency,
       metric_date_from, metric_date_to,
       ad_revenue_sum, revenue_same_sku_sum, cost_sum,
       cogs_sin_halo, cogs_con_halo,
       contrib_sin_halo, contrib_con_halo,
       rango_invertido, fx_source, no_decisoria, etiqueta,
       cargos_incluidos, cargos_excluidos, precio_as_of
  FROM v_contribucion_entidad
 ORDER BY platform, contrib_con_halo NULLS LAST
 LIMIT 20;
```

## 3. Cobertura por motivo (ausentes contados)

```sql
SELECT platform, motivo, count(*) AS entidades
  FROM v_contribucion_cobertura
 GROUP BY platform, motivo
 ORDER BY platform, entidades DESC;
```

## 4. Residual campaign sin contraparte (v_tacos)

```sql
SELECT platform, mes, gasto_ads, gasto_campaign_sin_contraparte, tacos_pct,
       filas_gasto_sin_costo, filas_gasto_sin_tasa
  FROM v_tacos
 WHERE mes >= date_trunc('month', (now() AT TIME ZONE 'UTC')::date - 15 - 89)
 ORDER BY platform, mes;
```

## 5. Desfase gasto ads metricas vs ledger

```sql
SELECT platform, currency, gasto_metricas, gasto_ledger_ads, desfase,
       filas_metricas_sin_costo, filas_ledger_ads
  FROM v_desfase_gasto_ads
 ORDER BY platform, currency;
```

## 6. Candado D8 (`quantity ≈ 1`)

Debe devolver **0 filas**. Si no, el supuesto caduco: parar y revisar.

```sql
SELECT platform, count(*), sum(quantity)
  FROM ledger_event
 WHERE kind = 'sale'
   AND event_date >= (now() AT TIME ZONE 'UTC')::date - 15 - 89
   AND event_date <= (now() AT TIME ZONE 'UTC')::date - 15
   AND quantity IS DISTINCT FROM 1
 GROUP BY platform;
```

## 7. Trampa FX (par invertido = cero filas)

```sql
SELECT count(*) AS filas_usd_mxn
  FROM fx_resolve(
      (now() AT TIME ZONE 'UTC')::date - 20,
      'USD'::currency, 'MXN'::currency);

SELECT count(*) AS filas_mxn_usd_debe_ser_cero
  FROM fx_resolve(
      (now() AT TIME ZONE 'UTC')::date - 20,
      'MXN'::currency, 'USD'::currency);
```

## 8. Fail-loud cost NULL en v_tacos (vivo)

```sql
SELECT count(*) AS filas_cost_null_en_grano
  FROM v_metric_mature m
  JOIN ad_entity e ON e.id = m.ad_entity_id
 WHERE e.kind IN ('keyword', 'product_target')
   AND m.cost IS NULL
   AND m.metric_date >= (now() AT TIME ZONE 'UTC')::date - 15 - 89;
```
