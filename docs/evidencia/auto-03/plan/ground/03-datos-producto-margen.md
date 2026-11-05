# Ground 3: identidad, métricas Ads por producto, margen, pantalla y stock (explorador, 2026-11-04)

## Overview
- **Rama:** el checkout local está en `resumen-ads-04-diseno`, HEAD `5adf1be0`. Va 24 commits detrás de `origin/master` (`e6bab315`), y el `master` local está viejo (`31ba12a1`). Entre HEAD y origin/master solo cambian `app/meli/catalogo.py` y `migrations/0075_meli_sku_alias.sql`. Todo lo demás vale igual para origin/master.
- **Identidad:** `product` (llave `odoo_sku`) → `listing` (una por `(platform, ASIN)`) → `ad_entity` kind `product_ad` con `listing_id`. El vínculo se hace **solo por ASIN**. El SKU del anuncio no se guarda en `ad_entity`.
- **Métricas Ads por producto:** viven en una tabla propia, `ads_product_metric_observation`. Su grano es `(platform, asin, sku, metric_date, observed_at)` y **no** está ligada a `ad_entity`. No hay una vista tipo `v_metric_latest`: cada lector colapsa con `DISTINCT ON`. La madurez "metric_date + 30 días" ya existe en código puro.
- **Margen:** `v_economia_producto` → `v_margen_producto` (0018). La ventana es `[2026-02-20, D-15)`, excluye ads y exige 30 días con venta, cobertura de 95% y moneda única. La frescura (3 días) **no** la aplica la vista sino `goals._motivo_dato_invalido`.
- **Pisos y pantalla:** los pisos de PAUSE (100 clics / 500 MXN / 40 USD) son de hoja y se evalúan contra el agregado de cortes de la hoja. La pantalla `/productos-sin-clics` no tiene ficha en verify-orbit. El stock solo es una observación: el bridge manda.

## 1. Identidad de producto
- `product(id, odoo_sku UNIQUE, name, active)` en `migrations/0001_initial.sql:95-104`. El comentario dice que la identidad canónica es el SKU de Odoo, no el seller_sku.
- `listing(id, product_id FK, platform, external_id, seller_sku, …)` con `UNIQUE(platform, external_id)` en `0001_initial.sql:106-133`. `external_id` es el ASIN o el MLM. Un producto puede tener de 2 a 4 listings (`:125-128`).
- `ad_entity(id, platform, kind, external_id, parent_id FK self, name, listing_id FK listing, match_type, keyword_text)` con `UNIQUE(platform, kind, external_id)` en `0001_initial.sql:250-281`. `app_ingest` solo puede hacer UPDATE de `name`/`listing_id` (`:277-281`). `first_seen_at` sale de `0017_first_seen_at.sql:24-31`: es un **piso, no la verdad**; las filas anteriores a la migración traen la hora de la migración.
- El kind `product_ad` se agregó en `0004_ad_entity_kind_product_ad.sql:20`. El motivo dicho ahí: en US, 0 de 48 ad groups tenían un solo ASIN.
- **Cómo se vincula:** `app/ads/structure.py:427-436` busca `listings.get((platform, item.asin))` y escribe `listing_id`, o NULL si no hay listing. Hay contadores para "product ad sin listing" y "sin costo".
  - El item de plan solo trae `asin` (`app/ads/structure_plan.py:49`).
  - `ad_entity` **no tiene columna SKU**.
  - Los ARCHIVED nuevos no se materializan (`structure.py:69`). Los existentes que llegan ARCHIVED explícitos en el payload se marcan así en `ad_entity_state` (`structure.py:223-232`, `:442-448`).
  - `ad_entity_state` es un caché mutable: `status` ENABLED/PAUSED/ARCHIVED, `synced_at` (`0001_initial.sql:644-661`).
- **Product ads de un ad group:** son los `ad_entity` con `kind='product_ad' AND parent_id = <ad_group>`, unidos a `ad_entity_state.status`. Así se usa hoy:
  - Para la familia por campaña, con product ads ENABLED o PAUSED (`app/cycle.py:503-514`). El colapso a familia es **por campaña**, no por ad group (`goals.familia_efectiva_por_campana`, `app/optimizer/goals.py:970`).
  - En `retiro_anuncios._SQL_ANUNCIOS` (`app/retiro_anuncios.py:64-87`): toma el SKU de `listing.seller_sku`, no del anuncio, y excluye ad groups con un impulso sin `impulso_lectura.es_final`.
  - La pantalla cuenta los product ads activos con `listing_id IS NULL` (`app/pantalla_productos.py:250-257`).
- `anuncio_retiro` guarda `ad_entity_id, ad_id_externo, ad_group_externo, campana_externa, sku, asin`. Sus motivos están restringidos por CHECK a `impulso|sin_pedidos|subfamilia` (`migrations/0066_bids02_retiro_anuncios.sql:22-40`; lo mismo en la intención, `0069…:40`).
- **MeLi→producto (3470797f):** está solo en origin/master. Crea `meli_sku_alias(seller_sku PK, product_id FK, evidencia)` (`0075_meli_sku_alias.sql:5-20`). `app/meli/catalogo.py` lo usa para mapear el SKU de MeLi al producto. `listing_miembro` tiene `CHECK platform='meli'` (`0054_precio02_base.sql:16-28`). **No toca la identidad Amazon.**

## 2. Métricas Ads por producto
- Tabla `ads_product_metric_observation` en `migrations/0020_ads_producto_metrica.sql:19-80`.
  - Columnas: `platform, advertised_asin, advertised_sku, metric_date, observed_at, metric_currency, impressions, clicks, cost, purchases30d, sales30d, purchases_same_sku30d, attributed_sales_same_sku_30d, campaign_ids JSONB, ad_ids JSONB, source_report_id, ingest_run_id`.
  - PK `(platform, advertised_asin, advertised_sku, metric_date, observed_at)`.
  - Dedupe parcial por `source_report_id` (`:65-68`). CHECKs de que mismo SKU ≤ total (`:51-58`). Append-only por trigger (`0023_append_only_bloque_b.sql:19-34`).
  - El comentario de la tabla dice: "el gzip SP solo trae filas con actividad: ASIN ausente = Sin datos, no cero" (`:74-80`). `campaign_ids`/`ad_ids` son "JAMAS insumo para repartir" (`:92-95`).
  - La moneda la sella el trigger por plataforma (`:101-149`).
- Reporte `PRODUCTOS_CFG` en `app/ads/reports.py:369-401`: `spAdvertisedProduct` con `groupBy ["advertiser"]`. No se debe pedir `salesSameSku30d` porque devuelve 400. El INSERT está en `:458-469`.
  - Las filas de varias campañas o ad groups del mismo `(asin, sku, fecha)` se **suman** antes de escribir (`_planea_filas_productos`, `:1148-1160`).
  - Una fila inválida "envenena" la clave completa (`:1121-1145`).
- **Ingesta:** `app/ads/diario.py:1-19` corre `python -m app.cli ingest diario` sobre el rango D-31..D-1 (`:319`), con cron a las `07:10/10:40/13:15` UTC (`:56`) y reintentos `--solo-faltantes`. La guarda "al día" exige filas D-1 de una run ok de hoy (`app/metricas_listas.py:43-55`).
  - **Efecto:** cada fecha se reobserva cerca de 31 veces, la última hacia `metric_date + 31`, si el cron corrió esos días.
- **Desde cuándo hay datos:** según la lectura de producción (`docs/evidencia/auto-03/diseno/lectura-2026-10-25.md:55-57`), del 2026-08-08 al 2026-10-23, con 77 fechas. Es consistente con el contrato vivo del 2026-09-06 (`reports.py:375`).
- **Colapso:** no hay vista. Para `ads_metric_observation` existen `v_metric_latest` y `metrics_as_of(ts)` (`0001_initial.sql:1159-1196`); **para productos, nada**. Cada lector repite `DISTINCT ON (asin, sku, metric_date) … ORDER BY observed_at DESC`:
  - `app/fabrica_web.py:359-368`
  - `app/pantalla_productos.py:212-218`
  - `app/spapi/precio_mercado.py:182`
  - `app/evaluacion_catalogo.py:155-163`, en Python
- **Madurez por fecha ya implementada:** `app/evaluacion_catalogo.py:125-131`.
  ```python
  limite = dt.datetime.combine(fecha + dt.timedelta(days=30), dt.time.min, dt.UTC)
  return observed_at >= limite
  ```
  - `evaluar_ads` (`:134-219`) colapsa, exige `maduro = all(...)` por fecha (`:168`) y suma con veneno: cualquier None da None (`:111-122`).
  - Calcula `halo30d = sales30d - attributed_same_sku` (`:178`) y la etiqueta `gasto_sin_ventas` (`:190-192`).
  - Lo usa `/api/fabrica/evaluacion` (`app/api_fabrica.py:244`, adaptador en `fabrica_web.py:355+`), por listing.
- Las hojas (`ads_metric_observation`) solo reciben kinds `campaign / keyword / product_target / ad_group`, este último por search terms (`reports.py:325-364`). **Los `product_ad` no tienen métricas en `ads_metric_observation`.** Las hojas también usan 30d (`_METRICAS`, `reports.py:308-315`).

## 3. Margen
- `v_margen_producto` en `migrations/0018_fabrica_campanas.sql:283-436`.
  - Ventana `DATE '2026-02-20'` a `(now() AT TIME ZONE 'UTC')::date - 15` (`:289`). Se evalúa al consultar, así que no es congelada.
  - Excluye `fee_type='ads'` (`:324`, `:354`).
  - Pone el margen en NULL ante: mezcla de monedas, `fees_sin_tipo>0`, `venta_cubierta<=0`, `cobertura<0.95`, `dias_con_venta<30`, o denominadores en varias monedas (`:397-412`).
  - `ledger_fresco_at = MAX(started_at)` de `ingest_run` con source `accounting_ledger_events` y ok (`:378-382`). Es **global**, el mismo valor para todas las plataformas.
- `v_economia_producto` en `0021_economia_observada.sql:33-179`.
  - Columnas: `platform, product_id, ventana_desde, ventana_hasta, venta_total, venta_cubierta, cargos_con_orden, cargos_sin_orden, cogs, cobertura, dias_con_venta, fees_sin_tipo, margen_neto_pct, ledger_fresco_at, moneda, integridad_ok, muestra_limitada, muestra_venta, muestra_margen_neto_pct`.
  - Las columnas maduras salen directo de `v_margen_producto`. La muestra de 1 a 29 días es "solo UI, jamas target" (`:26-30`, `:164-177`).
  - El grano es `(platform, product_id)`; el join a listing lo hace el consumidor (`:20-24`).
  - La vista **no** filtra por frescura.
- **Lector Python:** `app/economia_observada.py:76-113` con `por_producto` y `por_listing`. No hay ningún lector en el ciclo; el ciclo solo lee `v_target_margen_plataforma` y `v_margen_familia` (`app/cycle.py:479-494`).
- **Cómo resuelve goals:**
  - `MedicionMargen` en `goals.py:607-620`.
  - `resuelve_target_margen(medicion, fraccion, hoy, ultimo, setting, dias_min)` en `:880-932`. Aplica `fraccion × margen` y luego un **clamp a [10, 45]** (`:927`; `MARGEN_BANDA_MIN/MAX` en `:569-570`). Además usa un paso asimétrico de bajada de 0.5 por ciclo y convergencia al setting cuando el dato es inválido (`:910-923`).
  - `_motivo_dato_invalido` (`:935-951`) devuelve `sin_margen`, `cobertura_baja`, `ventana_corta` (`dias_min`: 60 plataforma, 30 familia; `:581-582`) o `ledger_rancio`. Este último aplica si `ledger_fresco_at.date() < hoy - 3d` (`MARGEN_RANCIO_DIAS=3`, `:583`).
  - `hoy_de_ventana = ventana_hasta + 15` (`:638-641`).
  - Cascada: `_nucleo_target_acos` (`:279-303`) y `cascada_target_acos_con_procedencia` (`:341-392`). Hay cinco peldaños en `PELDANOS_CASCADA` (`:270-276`), y `target_acos_ciclo.procedencia` tiene un CHECK espejo (`0048_margen_familia.sql`, comentario `:23-25`).
- **Fracción:** `fraccion_desde_settings` (`goals.py:659-680`) lee `ads_target_fraccion_margen_<plat>`. Si está ausente devuelve None y el peldaño queda en `sin_fraccion`. Si no está en (0, 1] lanza ValueError.
  - Llamadores: `app/cycle.py:2049` y `:2328`, `app/api_dashboard.py:1862`, `app/config_write.py:357`, `app/fabrica_plan.py:334`.

## 4. Pisos de PAUSE y evidencia por ad group
- `app/optimizer/cortes.py:62-69`: `O_MIN=3, C_MIN=60, Z_MIN=14`, `F_PAUSE=100`, `LEGACY_PAUSE=100`.
  - `umbral_corte(evidencia, 'pause')` (`:118-140`) devuelve `max(100, ceil(clics/orders × 1.5))` si el grupo califica 3/60/14 (`_califica`, `:104-115`); si no, 100.
- Pisos de costo: `PAUSE_COST_MIN = {amazon_us: 40, amazon_mx: 500}` en `app/optimizer/bid.py:109-111`. El replay histórico usa `REPLAY_PAUSE_COST_PRE_CORTES03` (`:105`).
- Regla: `_decide_pause` (`bid.py:207-240`) dispara con `orders == 0 AND clicks >= umbral AND cost >= piso`. Antes exige agregado `completa` (7 fechas), moneda correcta y orders/clicks/cost no None.
- **Congelado:** `_corte_json` (`app/cycle.py:706-746`) guarda en `inputs.corte` los campos `umbral_clicks_usado`, `elegible`, `expected_clicks` (string), `evidencia{clicks, orders, fechas, ventana_desde, ventana_hasta, observed_at_max}`, `cost_min_usado` (motor de bids) y `piso_cost_usado`/`aov` (solo negative). Se usa en `cycle.py:920`, `:978` y `:1048`.
- `EvidenciaAdGroup` (`app/optimizer/windows.py:235-257`) es la suma de hojas keyword+product_target del ad group sobre `D-90..D-10` desde `v_metric_latest` (`_SQL_EVIDENCIA_AD_GROUP`, `:375-387`; constantes en `:131-142`). El ad group es el "proxy de producto sellado" (`cortes.py:21-23`).

## 5. Pantalla de productos (BIDS 02 P.4)
- API `GET /api/…/productos-sin-clics` en `app/api_dashboard.py:1975-1996`; la UI está en `app/ui.py:724-742`, con la plantilla `productos_sin_clics.html`. Es solo SELECT, en `app/pantalla_productos.py:303-422`.
- **Secciones:**
  - Candidatos: producto con product ad, ad group y campaña ENABLED; al menos 2 unidades en 90 días de `ledger_event`; menos de 20 clics Ads en 60 días (`:15-19`, `:199-226`).
  - Impulsos, desde `v_impulso_estado` con clics `fuera_de_amazon` de `ads_placement_observation` (`:259-283`).
  - Sin pedido en 330 días (`:228-248`).
  - `anuncios_sin_producto` (`:250-257`).
  - Retiros abiertos, sueltos o por impulso (`:285-300`).
- No muestra margen, gasto por producto, ACoS, halo ni stock.
- Acciones: `POST /api/fabrica/impulso/{plan,lanzar,{id}/pausar}` y `/retiro/{plan,ejecutar,reponer}` (`app/api_fabrica.py:586-725`); JS en `app/static/js/productos.js:5-9`.
- **Ficha verify-orbit:** no existe. `.cursor/skills/verify-orbit/features/` solo tiene `campanas, contribucion, cortes, decisiones, fabrica, inertes, resumen, salud, settings`, y ninguna menciona `productos-sin-clics`.

## 6. Stock y disponibilidad
- `own_items` no existe en este repo; solo se cita como referencia del sistema viejo (`0025_reputacion_sin_fk.sql:9`).
- `disponibilidad_observation(platform, seller_sku, metric_date, fuente fba|fbm, quantity NULL=desconocido, fetched_at, observed_at)` en `0022_disponibilidad_snapshot.sql:19-62`.
  - La llena `app/disponibilidad.py:1-31` desde un **snapshot SQLite del bridge** que el lead copia por SSH (`tools/disponibilidad_snapshot.py`). No es un cron.
  - FBA y FBM no se suman. `amazon_inventory_cache` está prohibido. La llave es el seller_sku y se concilia contra `listing`.
- `spapi_inventario_observation` (`0035_spapi_listings_inventario.sql:74-100`) y `spapi_listing_estado_observation` (`:19-36`) dicen: "Observacion, no fuente de stock (D2: el bridge sigue mandando en precio/stock de `listing`)" (`:4-6`).
  - Las leen `app/ledger_atribucion.py:77`, que deriva el canal, y `app/spapi/precio_mercado.py:173`.

## Gotchas para AUTO-03
1. **Rama:** el checkout está en `resumen-ads-04-diseno`, 24 commits detrás de origin/master, y el `master` local está todavía más viejo. 0075 (`meli_sku_alias`) existe solo en origin/master. El próximo número de migración libre en origin/master es 0076.
2. **El SKU del anuncio no está en Orbit.** `ad_entity` no guarda SKU, y `listing` es único por ASIN, así que tiene un solo `seller_sku`. El reporte trae `(asin, sku)` y puede traer varios SKU por ASIN. La regla "colapsa por mercado, ASIN, SKU" sí se puede aplicar a las métricas. Pero vincular un `product_ad` concreto con su fila `(asin, sku)` solo es posible por ASIN, o por `ad_ids` JSONB, que es trazabilidad agregada que "JAMAS" debe usarse para repartir. La pantalla actual une solo por ASIN y suma los SKU (`pantalla_productos.py:212-218`).
3. **No hay métricas por product_ad ni por ad group de producto.** La tabla de producto suma todas las campañas y ad groups del mismo `(asin, sku, fecha)`. No se puede separar el gasto de un producto por ad group ni por anuncio sin violar la "política 0.4". Por eso, "pausar solo los anuncios que contribuyeron" no tiene dato por anuncio, salvo la lista `ad_ids`.
4. **La madurez de +30 días ya existe** (`evaluacion_catalogo._fecha_madura`), igual que la suma con veneno y el halo. No existe ninguna vista de colapso para esta tabla ni un `as_of` bitemporal: el replay tendría que congelar los valores. "Fecha sin fila" se trata como ausencia; `evaluar_ads` solo cuenta `muestra` y no exige todas las fechas del intervalo.
5. **El margen no lleva frescura en la vista.** `ledger_fresco_at` es global, no por plataforma, y lo filtra solo `goals._motivo_dato_invalido` (3 días). Si se reutiliza `resuelve_target_margen`, también vienen el clamp a [10, 45], el paso asimétrico y la convergencia. Eso contradice "8% × 0.5 → 4%, sin piso de 10%". Además, `v_margen_producto` usa `now()`, así que la ventana se mueve al consultar.
6. **Dos ventanas de margen distintas:** la de producto exige 30 días con venta (`0018:405`) y la de plataforma 60 (`goals.py:581`). La spec dice "30 días con venta" para producto, y eso coincide con 0018.
7. **Los pisos de PAUSE son de hoja.** Se comparan contra el agregado de cortes de la hoja y exigen `completa` (7 fechas). `umbral_corte` es adaptativo por ad group (al menos 100, puede subir). "100 clics" es el fallback y piso, no un valor fijo. Al pasar a grano producto hay que elegir entre `F_PAUSE`/`LEGACY_PAUSE` o el adaptativo; `EvidenciaAdGroup` sale de las hojas, no del reporte de producto.
8. **Familia por campaña, no por ad group.** El peldaño `margen_familia` colapsa por campaña (`cycle.py:503-514`) con product ads ENABLED o PAUSED. La "huella de alcance por ad group" de la spec es otro grano. Tampoco existe hoy un concepto de "ad group exclusivo".
9. **Los archivados nunca vistos no existen** como `ad_entity` (`structure.py:69`). La "alta demostrada del anuncio" solo tiene `first_seen_at`, que es un piso (0017). No hay fecha de creación en Amazon.
10. **"campana_grupo" no es el ad group de Amazon.** Es el grupo de la fábrica de Orbit (`campana_grupo`, `campana_grupo_producto`, `campana_grupo_rol`). Un impulso abierto se detecta como impulso sin `impulso_lectura.es_final` (`retiro_anuncios.py:81-85`).
11. **Pantalla y regla 3:** los candidatos usan `coalesce(c.clics, 0) < 20` (`pantalla_productos.py:225`), así que un producto sin filas Ads aparece como "sin clics". Su `activos` exige product ad, ad group y campaña ENABLED.
12. **Motivos de retiro restringidos por CHECK** (`impulso|sin_pedidos|subfamilia`) en `anuncio_retiro` (0066:38) y en la intención (0069:40). `producto_sin_ventas` requiere migración. El SKU del retiro sale de `listing.seller_sku`.
13. **Stock:** no hay fuente automática con cadencia. `disponibilidad_observation` depende de una copia manual del bridge, y la tabla de SP-API está declarada como "no fuente" (D2).
