# SP-API 01 / A.4 — Listings + Inventario (evidencia)

Base: `origin/master` vigente al abrir el PR (post A.1/A.2/A.2b/A.3; la
0034 es el fix del GRANT de `ingest_run.llamadas`). Rama:
`feat/sp-api-01-a4`. D2: ninguna escritura en `listing` (test D2 con
snapshot por fila).

## Que nace

- Migración `0035_spapi_listings_inventario.sql` (un archivo, una
  transacción): `spapi_listing_estado_observation(seller_sku, asin NULL,
  platform, status TEXT, product_type NULL, last_updated_date NULL,
  api_version NOT NULL, observed_at, ingest_run_id)` append-only por
  `(seller_sku, platform, observed_at)`; `spapi_inventario_observation(
  seller_sku, asin NULL, fn_sku NULL, platform, metric_date, observed_at,
  total_quantity NOT NULL, fulfillable_quantity NULL, api_version NOT
  NULL, ingest_run_id)` append-only por `(seller_sku, platform, observed_at)`
  + CHECK cantidades >= 0 + trigger `spapi_inventario_tiempo_coherente`
  (`metric_date = (observed_at AT TIME ZONE 'UTC')::date`, patrón 0032,
  UTC en la expresión, nunca en CHECK). Triggers anti
  UPDATE/DELETE/TRUNCATE + GRANTs como 0032 en ambas. Sin reversa (tablas
  nuevas, expansivo).
- `app/spapi/listings.py` (479 líneas, source `spapi_listings`): universo
  `seller_sku` de `listing` por plataforma; 1 llamada/SKU a
  `/listings/2021-08-01/items/{sellerId}/{sku}?marketplaceIds=` (5/s,
  cubo 5/5.0 dentro del get, honra header); estado de summaries[0] del
  marketplace pedido (sin summary = skip `sin_summary`); error por SKU
  con política `_FalloHttp` + `_vigilar_umbral` (mismo criterio que
  pricing); CLI `ingest spapi_listings --platform [--max-skus]
  [--seller-id]`.
- `app/spapi/inventario.py` (445 líneas, source `spapi_inventario`):
  recorrido completo de `/fba/inventory/v1/summaries`
  (`granularityType=Marketplace`, `granularityId=` +
  `marketplaceIds=<id>`, forma exacta de la sonda; `nextToken` como
  param, modelo oficial `fbaInventory.json`) con guardas hermanas de
  orders y aviso → `ok=false` (`paginacion_incompleta:<aviso>`,
  conservando lo escrito); 2/s (cubo 2/2.0); sin sellerSku o sin
  totalQuantity = fila no escrita (`sin_sku`/`sin_cantidad`); CLI
  `ingest spapi_inventario --platform [--max-paginas]`.
- `VENDEDORES_PROPIOS` subido de `pricing.py` a `app/spapi/client.py`
  (mismo contenido; `pricing.VENDEDORES_PROPIOS` sigue resolviendo por
  import; tests de pricing intactos y verdes).
- `app/cli.py`: choices + despacho `spapi_listings`/`spapi_inventario`.

## Decisiones con evidencia (no inventadas)

- Shapes de fixtures = actas 0.3/0.4 + modelos oficiales
  (`fbaInventory.json`: `GetInventorySummariesResponse` =
  `payload` + `pagination` + `errors`; `InventorySummary` con
  `totalQuantity` entero; `nextToken` como query param).
- `fulfillable_quantity` casi siempre NULL: solo viene con
  `details=true` (modelo oficial), que el pase no pide (la sonda no lo
  pidió); columna anulable declarada así en su COMMENT.
- `status` TEXT sin enum: las formas no atestiguadas no se pinean.
- Sin `seller_sku` en `listing` (NULL/blank) = fuera del universo (SQL).

## Conciliación contra E/0.3 y E/0.4 (MockTransport)

- Listings: summary completo → fila (OPEN/PRODUCT/B0TEST0001); summary de
  otro marketplace entre varios → elige el pedido; 404 → `1x http_404`;
  sin summary → `1x sin_summary`. Pase de 3 SKUs → 1 fila, 3 llamadas,
  `ok=true`; re-pase mismo `observed_at` → `duplicada`.
- Inventario: página 1 (2 reseñas + token) + página 2 (1 con qty 0 + 1
  sin cantidad → `1x sin_cantidad`) → 3 filas, 2 llamadas, `ok=true`;
  re-pase → `duplicada`. Token repetido → `ok=false` con 2 filas y
  motivo `paginacion_incompleta:next_token_repetido`.
- D2 (`test_listing_intacto_tras_ambas_ingestas`): snapshot completo de
  `listing` (7 columnas) idéntico antes/después de ambas ingestas.

## Comandos y salidas (sin secretos)

`uv run --frozen python -m pytest -q tests/test_spapi_listings.py tests/test_spapi_inventario.py`
→ `31 passed` (0 skips: 0001+0033+0034+0035 en BD desechable;
26 base + 5 de la ronda grok, todos en rojo primero).
Focal (listings, inventario, orders, pricing, cliente, redacción, fees,
fotos, arquitectura, sonda, cli) → `276 passed`.
`ruff check` + `ruff format --check` → verde.
`pre-commit run --all-files` → verde (abajo, antes del commit).

## Mutantes (regla 9)

- M1 (marketplace): sin el filtro `marketplaceId == pedido` en
  `parsear_estado` → `2 failed`; con él, verde. Restaurado íntegro
  (diff contra copia vacío).
- M2 (paginación): `nextToken` → `paginationToken` (el bug clásico del
  acta 0.4) → E2E `1 failed`; con `nextToken`, verde. Restaurado íntegro.
- M3 (D2): `UPDATE listing` furtivo en el pase → D2 `1 failed`; sin él,
  verde. Restaurado íntegro.

## Regla 8 (nota honesta)

El `SELECT` real en producción no fue posible: el brief prohíbe ssh y
producción en esta tarea. El invariante de tiempo es el patrón 0032
(`AT TIME ZONE 'UTC'` en la expresión), ya desplegado y corriendo en
producción desde A.6; el test `test_trigger_metric_date_rechaza_dia_y_es_inmune_a_timezone`
demuestra en BD desechable que rechaza el día inconsistente y que con
`SET TIME ZONE 'America/Mexico_City'` el `::date` local daría 09-08
mientras el trigger exige el día UTC 09-09.

## Cross-review grok (una ronda, quality-kit)

Grok colgó 3 veces con el diff completo (exit 124, tope 300 s); entregó
con `-Archivos` por módulo + `-TimeoutSec 600`. Verificado hallazgo por
hallazgo contra el repo antes de corregir (ninguno se aplicó a ciegas).

Inventario (`app/spapi/inventario.py`):

- H1 [media] el sello de aviso tiraba el detalle de skips, contra el
  patrón orders (que concatena `paginacion_incompleta:<aviso>; <skips>`).
  Fix + test espejo del de orders. Válido.
- H2 [media] el `except` sellaba `escritas` del contador Python aunque la
  transacción única hiciera rollback, y `llamadas=0` fijo. Fix: `escritas=0`
  (como orders) + `medidor` (Counter) que `recorrer_summaries` acumula por
  intento para sellar llamadas reales. Tests de ambos. Válido.
- H3 [baja] `metric_date=momento.date()` usaba día de pared. Fix: helper
  `_dia_utc` (UTC explícito) + test unitario. Válido.

Listings (`app/spapi/listings.py`):

- H1 [alta] el modelo oficial define `summaries[].status` como LISTA
  (`BUYABLE`/`DISCOVERABLE`); el brief habla de escalar (`OPEN`/`CLOSED`)
  y el acta 0.3 no pinó el tipo. Fix: `_estado_texto` acepta ambas
  (escalar tal cual, lista como join ordenado) + COMMENT actualizado +
  test. Pendiente del lead: pinar la forma real en sonda (mismo trato
  que `belongsToRequester` en A.3).
- H2 [media] `_procesar_sku` no atrapaba `httpx.HTTPError` (pricing F2
  sí). Fix: rama `red` al umbral + test de timeout aislado. Válido.

Focal tras la ronda: `276 passed`.

## Cierre

DoD A.4: focal verde, D2 demostrado, E/A.4 conciliada contra 0.3/0.4.
No se tocó: `listing` (cero escrituras), `app/ads/*`, crons, plan,
ROADMAP, manifest, CHAT-CONTEXT. A.5 no adelantada. Sin tracker.
