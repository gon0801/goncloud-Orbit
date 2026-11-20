# TEMPORADA 01 I — Margen histórico y vigente antes del despliegue

Fecha de la consulta: **2026-11-20 (UTC)**. Rol `orbit_read`, transacción `READ ONLY`, un solo
`SELECT` por la entrada estándar de `docs/evidencia/arreglos-01/lee_prod.sh` (solo lectura, sin
escrituras ni accesos de escritura).

## Método

- **Plataforma:** la fila viva de `v_target_margen_plataforma` (definición de 0086) contrastada,
  en la misma consulta, con la definición `SELECT` de la vista en `migrations/0086_arreglos02_cargos.sql`
  cambiando **solo** los dos límites de vigencia de `sku_cost` a la fecha UTC de la consulta:
  `c.valid_from <= hoy AND (c.valid_to IS NULL OR hoy < c.valid_to)` con
  `hoy = (now() AT TIME ZONE 'UTC')::date`. Esa variante es la aritmética de `margen_vigente_pct`
  que aplicará 0092.
- **Producto:** los productos con `meta_pct` no nula en `evaluacion_producto_ciclo` del último
  ciclo `ads_optimizer` terminado por plataforma, uniendo por `platform` y `product_id`; el margen
  histórico sale de `v_margen_producto` (0086) y el vigente de la misma definición con los dos
  límites de vigencia cambiados a la fecha UTC de la consulta.
- `None` significa sin dato (sin fila o margen NULL por guarda). El cambio se reporta en puntos
  porcentuales (pp), margen vigente − margen histórico.

## Filas exactas

| fecha UTC | tipo | plataforma | id producto | margen histórico % | margen vigente % |
| --- | --- | --- | --- | --- | --- |
| 2026-11-20 | plataforma | amazon_mx | None | 46.074539 | 46.551054 |
| 2026-11-20 | plataforma | amazon_us | None | 29.599859 | 29.802912 |
| 2026-11-20 | plataforma | meli | None | 35.231012 | 35.336856 |
| 2026-11-20 | producto | amazon_mx | 333 | 41.460272 | 41.188516 |

## Cambio esperado por plataforma (puntos porcentuales, vigente − histórico)

| plataforma | histórico % | vigente % | cambio (pp) |
| --- | --- | --- | --- |
| amazon_mx | 46.074539 | 46.551054 | +0.476515 |
| amazon_us | 29.599859 | 29.802912 | +0.203053 |
| meli | 35.231012 | 35.336856 | +0.105844 |

La meta derivada por plataforma (fracción × margen) sube en la misma proporción desde el ciclo
siguiente al despliegue; si un costo sube, la meta baja 0.5 por ciclo como hoy y R3 juzga desde el
primer día con el equilibrio nuevo.

Para el producto 333 de amazon_mx el margen vigente es 0.271756 pp **menor** que el histórico
(41.188516 − 41.460272): el costo de hoy de ese producto pesa más que el costo de la fecha de sus
ventas, y su meta derivada baja en consecuencia desde el ciclo siguiente.
