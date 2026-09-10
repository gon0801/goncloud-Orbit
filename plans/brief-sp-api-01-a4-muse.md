# Brief para Muse: SP-API 01 A.4 — ingesta Listings Items + Inventario FBA

Base `origin/master` `1291bb0` (A.1/A.2/A.2b/A.3 ya mergeadas; tras `git fetch`
usa el HEAD vigente de `origin/master`). UN PR (`feat/sp-api-01-a4`), revisado
por el lead antes de mergear. Trabajas en el mismo checkout que el lead:
**commitea antes de cambiar de rama** y nunca saltes los candados de commit.

Contrato: fila A.4 de `plans/sp-api-01.md` y la seccion "A.4 · Listings +
Inventario" de `plans/brief-sp-api-01-fase-a-muse.md`. Este brief no reabre
ninguna decision.

## Resultado de la tarea

Orbit lee de Amazon, una vez al dia y solo lectura, el ESTADO de cada listing
propio (Listings Items) y el inventario FBA completo (summaries), para MX y US,
y los guarda append-only en tablas propias. **D2 (cerrada por el dueno): el
bridge sigue siendo la fuente de precio y stock de `listing`; ninguna linea de
esta tarea escribe en `listing`** — SP-API aporta estado del listing e
inventario como observaciones propias.

## Decisiones ya cerradas (no reabrir)

D1 las cinco fuentes; D2 bridge manda en precio/stock; D4 cadencia diaria;
D5 un solo refrescador LWA en `app/spapi/`; D6 MX + US; D7 sin purga.

## Hechos verificados (Fase 0, `docs/evidencia/sp-api-01/`)

- Listings 2021-08-01 (acta 0.3): `GET /listings/2021-08-01/items/{sellerId}/{sku}`
  con `marketplaceIds`. Respuesta 200 con `sku` + `summaries`; cada summary trae
  `asin`, `conditionType`, `createdDate`, `itemName`, `lastUpdatedDate`,
  `mainImage`, `marketplaceId`, `productType`, `status`. Rate limit
  `x-amzn-ratelimit-limit: 5.0`. `sellerId` = `accountInfo.id` del perfil Ads
  (acta 0.5; MX y US reportan el mismo, verificado en A.3).
- Inventario FBA v1 (acta 0.4): `GET /fba/inventory/v1/summaries` con
  `granularityType=Marketplace`, `granularityId=marketplaceIds=<id>` (la forma
  exacta que la sonda verifico con 200). Respuesta: `inventorySummaries` bajo
  `payload`, y **`pagination` es HERMANA de `payload`** (no hija: la correccion
  de la ronda 2 de la sonda; la clave top real es `inventorySummaries` con
  `pagination` al mismo nivel — lee el acta y el modelo oficial antes de
  escribir el parser). ~50 summaries por pagina (MX: 22 paginas, 1071). Cada
  summary: `asin`, `condition`, `fnSku`, `lastUpdatedTime`, `productName`,
  `sellerSku`, `stores`, `totalQuantity`. Rate limit `2.0/s`. Recorrido
  completo verificado sin token repetido ni pagina vacia con token.
- Ambas actas conciliadas contra el bridge (regla 10): el universo de la
  ingesta de listings es `seller_sku` presente en `listing` (la tabla tiene
  `seller_sku` y `external_id` = ASIN); inventario se recorre completo, sin
  universo previo.

## Piezas que ya existen (reutilizar, jamas duplicar)

- `app/spapi/client.py`: `SpapiClient` con allowlist default-deny que YA cubre
  esta tarea: `construir_ruta_listings(seller_id, sku)` (plantilla
  `/listings/2021-08-01/items/` con sellerId y sku validados) y
  `RUTA_INVENTARIO = "/fba/inventory/v1/summaries"` en `RUTAS_FIJAS`.
  **No abras la allowlist mas que esto.** `MERCADOS` (platform → marketplaceId)
  ya esta ahi.
- `CuboTasa` (limitador por proceso, compartido por orders/pricing; el cubo
  viaja dentro de `client.get` y honra `x-amzn-RateLimit-Limit`): listings
  `CuboTasa(capacidad=5, tasa=5.0)`, inventario `CuboTasa(capacidad=2, tasa=2.0)`
  (rate limits pineados de las actas; sin burst supuesto mas alla del medido).
- `VENDEDORES_PROPIOS` (marketplaceId → sellerId) vive hoy en
  `app/spapi/pricing.py`. Muevelo a un lugar compartido (`app/spapi/client.py`
  o `app/spapi/__init__.py`) **sin cambiar comportamiento**: los tests de
  pricing intactos y en verde.
- El patron de ingesta de `app/spapi/orders.py` y `app/spapi/pricing.py`:
  `ejecutar_ingesta` con `ingest_run` (abrir run, sello `_sellar` con
  ok/escritas/skips/`skip_reason`/`llamadas`), parseo puro separado del IO,
  `sanear` en todo cuerpo de respuesta, errores scrubbeados, `main(argv)` con
  retorno int, CLI fail-closed sin DSN.
- `app/cli.py`: tupla `choices` del subcomando `ingest` — agrega
  `spapi_listings` y `spapi_inventario` con el mismo patron.
- `app/redaction.py`, `tests/test_architecture.py` (leelo completo antes de
  empezar: modulos de `app/` ≤ 900 lineas).

## Migracion 0035 (un solo archivo, una transaccion; la 0034 es el fix del GRANT de `ingest_run.llamadas` — verificalo con `ls migrations` al abrir el PR)

- `spapi_listing_estado_observation(id, seller_sku, asin NULL, platform,
  status, product_type NULL, last_updated_date NULL, api_version,
  observed_at)` — append-only por `(seller_sku, platform, observed_at)`
  (UNIQUE), trigger `prohibir_mutacion` (UPDATE/DELETE/TRUNCATE), GRANTs:
  `app_ingest` INSERT+SELECT(+sequence), `app_read`/`app_decide` solo SELECT.
  `status` es la lista oficial de Listings (OPEN/CLOSED/etc.): guardala TEXT
  sin enum inventado (las formas no atestiguadas no se pinean).
- `spapi_inventario_observation(id, seller_sku, asin NULL, fn_sku NULL,
  platform, metric_date, observed_at, total_quantity, fulfillable_quantity
  NULL)` — append-only por `(seller_sku, platform, observed_at)`; cantidades
  enteras >= 0; `metric_date` = dia UTC de `observed_at` exigido por trigger
  con UTC fijado EN LA EXPRESION (`AT TIME ZONE 'UTC'`, patron
  `spapi_price_tiempo_coherente` de 0032; NUNCA en CHECK). Mismos GRANTs.
- Dato faltante = NULL / fila no escrita (regla 3); `total_quantity` ausente
  = fila no escrita y contada en `rows_skipped` (es el dato de la tabla; las
  demas columnas toleran NULL declarado).
- `COMMENT ON` con la razon en cada pieza (tabla, columnas con criterio,
  triggers). No requiere archivo de reversa (tablas nuevas, expansivo:
  la reversa operativa es apagar la ingesta; las tablas se conservan).

## Ingesta

- **Listings** (`app/spapi/listings.py`, source `spapi_listings`): universo =
  `seller_sku` de `listing` por plataforma. Una llamada por SKU (5/s: ~70 s
  para ~340 SKUs MX). Error por SKU (404/403/5xx) = skip contado y la corrida
  sigue (patron `_FalloHttp` de pricing); racha de fallos aborta con el mismo
  criterio de `_vigilar_umbral`. `status`/`lastUpdatedDate` vienen de
  `summaries[0]` del marketplace pedido (si hay varios summaries, el del
  `marketplaceId` pedido; si no hay summary = fila no escrita, skip contado).
- **Inventario** (`app/spapi/inventario.py`, source `spapi_inventario`):
  recorrido completo con guardas de paginacion hermanas de las de orders
  (token repetido, pagina vacia con token, tope de paginas): un aviso de
  paginacion sella el run `ok=false` conservando lo escrito (patron A.2b:
  `paginacion_incompleta:<aviso>; <skips>`). 2/s: ~90 s para 22 paginas.
- Ambas: `llamadas` logicas en `ingest_run` (semantica ya documentada en
  0033), CLI `ingest spapi_listings|spapi_inventario --platform` con los
  mismos flags razonables que pricing (`--max-*` para sondas acotadas).

## Reglas duras (identicas a las de A.1–A.3)

- Cero escrituras a Amazon. Cero `app.ads.write`. Cero `app/ads/*` tocado.
- Append-only real (triggers); corregir = insertar fila nueva.
- Invariante de tiempo SOLO en trigger con UTC fijado; cada invariante nuevo
  con su test, y antes del test el `SELECT` real en produccion (regla 8;
  tienes `ORBIT_DSN_READ` por hostname `orbit-db-1`).
- **Test D2 obligatorio**: correr ambas ingestas (MockTransport) y demostrar
  que `listing.listing_price`/`price_currency` y cualquier columna de stock
  NO cambian (snapshot antes/despues por fila).
- Redaccion en todo log/error; ningun secreto ni PII persistido.
- Cada regression se demuestra fallando contra el codigo anterior (regla 9).
  Los tests deben morder: el lead los muta.
- Migraciones: las pruebas en la base de test/CI; **nunca en produccion** (la
  aplica el dueno con el runbook). Ninguna corrida real contra produccion:
  valida con `httpx.MockTransport` y las actas de Fase 0 como fixtures.

## Tests minimos (todos en rojo primero)

1. Migracion: claves unicas, triggers append-only (INSERT nuevo ok, UPDATE/
   DELETE/TRUNCATE revientan), trigger de `metric_date` (rechaza dia
   inconsistente; inmune a `SET TIME ZONE`), GRANTs con asserts negativos.
2. Parser listings con el shape del acta 0.3 (summary completo, summary sin
   `lastUpdatedDate`, sin summary del marketplace pedido, 404 de SKU).
3. Parser inventario con `pagination` HERMANA de `payload` (shape real del
   acta 0.4: `payload.inventorySummaries` + `pagination.nextToken`), token
   repetido, pagina vacia con token, ultima pagina sin token.
4. E2E con MockTransport: doble corrida idempotente (misma clave, filas
   nuevas por `observed_at`, cero duplicados de clave), sello ok=true y
   ok=false con `skip_reason` compuesto.
5. D2: `listing` intacto tras ambas ingestas.
6. CLI sin DSN falla cerrado (retorno 2, cero HTTP).

## Evidencia y cierre

`docs/evidencia/sp-api-01/A.4/reporte.md` con: comandos corridos, salidas de
tests, conteo de filas por shape de las actas 0.3/0.4 (conciliacion con
MockTransport, regla 10 contra la fuente externa documentada), y el resultado
de las mutaciones del lead. **No marques filas del plan ni toques
`plans/ROADMAP.md`/`plans/manifest.json`/`docs/CHAT-CONTEXT.md`: el lead
cierra A.4 tras la review.** A.5 (salud/alertas, propuesta de cron) es otra
tarea: no la adelantes.

## Entrega del PR

Rama `feat/sp-api-01-a4` desde `origin/master`; commits normales;
`pre-commit run --all-files` verde; PR a `master` con CI (suite completa). En
la descripcion: que tablas y columnas nacen, que se lee de Amazon y con que
limite, y que NO toca (`listing`, `app/ads/*`, cron, produccion). Sin ssh de
escritura, sin produccion, sin tracker.
