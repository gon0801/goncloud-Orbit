# Brief para Muse: SP-API 01 Fase A — cliente único, ingesta diaria y salud

Base `origin/master` `835a9a2` (Fase 0 cerrada, PR #236). Cinco PRs, uno por tarea
del plan (`plans/sp-api-01.md` § Fase A: A.1 → A.2 → A.3 → A.4 → A.5), cada uno
desde `origin/master` tras `git fetch`, revisado por el lead una ronda antes del
siguiente. Trabajas en el mismo checkout que el lead: **commitea antes de cambiar
de rama** y nunca saltes los candados de commit.

## Resultado de la fase
Orbit lee de Amazon, una vez al día y solo lectura, pedidos, precios con Buy Box,
estado de listings e inventario FBA, para MX y US, y los guarda append-only en
tablas propias. No decide nada con ellos (eso es Fase B). El bridge sigue siendo la
fuente de precio y stock de `listing` (D2): ninguna tarea escribe ahí.

## Decisiones ya cerradas por el dueño (2026-09-09), no reabrir
D1 las cinco fuentes; D2 bridge manda en precio/stock, SP-API aporta Buy Box,
competencia, estado e inventario como observaciones propias; D3 Finances fuera;
D4 cadencia diaria; D5 un solo refrescador LWA en `app/spapi/`; D6 MX + US; D7 sin
purga: todo se conserva.

## Hechos verificados (Fase 0, `docs/evidencia/sp-api-01/0.1–0.5/`)
- Orders: v0 y 2026-01-01 responden. **Ingesta con 2026-01-01** (`GET
  /orders/2026-01-01/orders`, `marketplaceIds`, `createdAfter`/`lastUpdatedAfter`,
  `paginationToken`, respuesta `orders[]` + `pagination.nextToken`); v0 se retira
  el 2027-03-27 y queda solo en la sonda. Sin `includedData=BUYER`: cero PII.
  Rate limit 0.0056 req/s, burst 20.
- Pricing v0: `GET /products/pricing/v0/items/{asin}/offers` (`ItemCondition=New`)
  trae `IsBuyBoxWinner`, `ListingPrice`, `SellerId`, `IsFulfilledByAmazon`;
  `GET /products/pricing/v0/competitivePrice` complementa. Rate limit 0.5 req/s.
  US puede devolver 0 ofertas: ausencia, no error.
- Listings 2021-08-01: `GET /listings/2021-08-01/items/{sellerId}/{sku}` con
  `summaries` (`status`, `productType`, `lastUpdatedDate`). Rate limit 5/s.
  `sellerId` = `accountInfo.id` del perfil Ads (MX `A29XRL07YRN0L`; US el suyo).
- Inventario FBA v1: `GET /fba/inventory/v1/summaries`, `pagination` **hermana**
  de `payload`, ~50 por página (MX: 22 páginas, 1071 summaries). Rate limit 2/s.
- Sellers v1: `marketplaceParticipations` como identidad; `account` no trae sellerId.
- Auth: LWA con `amazon_credentials.json` desde `ORBIT_SECRETS_DIR`, header
  `x-amz-access-token`, host `sellingpartnerapi-na.amazon.com`. Timestamps a
  Amazon en Zulu (`...Z`).

## Piezas que ya existen (reutilizar, jamás duplicar)
- `tools/sonda_spapi.py`: allowlist `validar_get`, `sanear` (PII), `rate_limit_de`,
  `siguiente_token`, `SondaClient` (refresh, 401 una vez, 429 acotado). El cliente
  de producción nace de ahí; después la sonda importa de `app.spapi` (una copia).
- `app/ads/client.py`: patrón sellado de guard default-deny, redacción, retries.
- `app/estimacion_fees.py` (POST `/products/fees/v0/listings/{sku}/feesEstimate`,
  con su propio LWA) y `app/publicacion_fotos.py` (`FotosPublicacion._acceso`, GET
  Catalog Items): los dos refrescadores que A.1 unifica.
- `ingest_run` (columnas `source, started_at, finished_at, rows_written,
  rows_skipped, skip_reason, ok`): registra cada corrida con `source`
  `spapi_orders` | `spapi_pricing` | `spapi_listings` | `spapi_inventario`.
- `python -m app.cli ingest <pipeline>` (`app/cli.py`, tupla `choices`): agrega los
  cuatro pipelines. Cron del usuario `gon` en goncloud (patrón existente:
  `docker exec orbit-app-1 python -m app.cli ingest structure` a las 06:45 UTC).
- `/salud`: `app/api_dashboard.py::salud` (JSON por plataforma) y `app/ui.py`
  (página). `app/notifica.py`: `notifica_*` existentes; agrega una función nueva
  con el mismo patrón, sin cambiar las que hay.
- `app/redaction.py` (`register_secret`, `scrub`, `install_scrub_filter`).

## Reglas duras
- Cero escrituras a Amazon. Cero `app.ads.write` en `app/spapi/` (candado
  `test_imports_del_cliente_de_escritura_acotados`). Cero `app/ads/*` tocado.
- Datos: append-only `(entidad, metric_date u observed_at)`; corregir = insertar.
  Dinero `NUMERIC(14,4)` + enum `currency`, prohibido float; precio sin moneda =
  fila no escrita y contada en `rows_skipped`. Dato faltante = NULL/fila no
  escrita, nunca constante. Invariantes de tiempo en trigger con UTC fijado, no en
  CHECK. Cada invariante nuevo del esquema con su test, y antes del test el
  `SELECT` real en producción (regla 8; tienes `ORBIT_DSN_READ` por hostname
  `orbit-db-1`). `COMMENT ON` con la razón. GRANTs: `orbit_ingest` escribe,
  `orbit_read` lee, como en `migrations/0018`.
- Migraciones: expansivas, numeradas al abrir el PR (`ls migrations`; hoy la última
  es `0029`), una transacción, jamás editar una sellada. **No las apliques en
  producción**: las aplica el dueño con el runbook (backup del schema, `psql
  --single-transaction -v ON_ERROR_STOP=1`, verificación como `orbit_read`).
  Tú las pruebas en la base de test/CI.
- Ninguna corrida de ingesta contra la base de producción: la primera la hace el
  dueño con `!` tras el deploy. Tú validas con `httpx.MockTransport` y con las
  actas de Fase 0 como fixtures.
- Arquitectura: módulos de `app/` ≤ 900 líneas, complejidad bajo ruff; paquete
  `app/spapi/` con `client.py`, `orders.py`, `pricing.py`, `listings.py`,
  `inventario.py`, `salud.py`. Lee `tests/test_architecture.py` completo antes de
  empezar. Redacción en todo log; ningún secreto ni PII persistido.
- Rate limit: limitador local por proceso a partir de `x-amzn-RateLimit-Limit`,
  sin Redis ni colas (decisión del stack). 429: reintento acotado; nunca loop.
- Cada regresión se demuestra fallando contra el código anterior (regla 9). Los
  tests deben morder: el lead los muta.

## Tareas y entregas

### A.1 · Cliente único (`app/spapi/client.py`) — PR 1
- `SpapiClient`: un refrescador LWA por proceso (token cacheado con margen),
  allowlist default-deny por igualdad literal: los GET de las cinco fuentes +
  Catalog Items (`/catalog/2022-04-01/items/{asin}`) + el único POST permitido,
  `/products/fees/v0/listings/{sku}/feesEstimate` (lectura implementada como POST,
  mismo criterio que `recommend_bids` en Ads). 401 un refresh forzado; 429
  acotado; 5xx/red sin retry; errores con cuerpo scrubbado (patrón
  `AdsApiErrorMutacion`).
- Migra `app/estimacion_fees.py` y `app/publicacion_fotos.py` a este cliente
  **sin cambiar comportamiento**: `tests/test_estimacion_fees.py` (incl.
  `test_allowlist_*`) y `tests/test_publicacion_fotos.py` intactos y verdes.
  `tools/sonda_spapi.py` pasa a importar de `app.spapi` (sus 42 tests intactos).
- Tests nuevos: allowlist sin HTTP; un solo refresh con dos módulos en el mismo
  proceso (contador de POST a LWA = 1); 401/429; scrub.
- DoD del plan: pytest focal verde, cero refresh duplicado, E/A.1 con diff de
  comportamiento vacío. Sugerencia del lead: cross-review de una ronda (kimi).

### A.2 · Ingesta Orders (`app/spapi/orders.py`) — PR 2
- Migración: `spapi_order_observation(amazon_order_id, platform, marketplace_id,
  purchase_date, last_updated_time, order_status, fulfillment_channel,
  sales_channel, order_total_amount NUMERIC(14,4) NULL, order_total_currency
  currency NULL, number_of_items, api_version, observed_at)` con unicidad
  `(amazon_order_id, last_updated_time)` (clave definida en E/0.1: identificador de
  orden + última actualización); re-corrida idempotente. Sin campos de comprador
  ni dirección: la migración no tiene columnas para ellos.
- Ventana: `lastUpdatedAfter` = último `last_updated_time` observado menos 1 día
  (solape), `createdAfter` inicial 30 días en la primera corrida. Paginación con
  guardas (token repetido, página vacía con token) como tests.
- `ingest_run` `spapi_orders`; CLI `ingest spapi_orders --platform`.

### A.3 · Ingesta Pricing (`app/spapi/pricing.py`) — PR 3
- Universo: ASINs propios en `listing` por plataforma (MX + US), un pase diario.
- Migración: `spapi_price_observation(asin, platform, metric_date, observed_at,
  own_listing_price NUMERIC NULL + currency, buy_box_price + currency, buy_box_seller_id,
  buy_box_is_own bool NULL, offers_count, fba_offers_count, lowest_price + currency)`
  append-only por `(asin, platform, observed_at)`. Precio sin moneda = fila no
  escrita y `rows_skipped`. 0 ofertas = fila con `offers_count=0` y precios NULL.
- Rate limit 0.5/s respetado por el limitador local; el pase completo puede tardar
  (~2 llamadas por ASIN): deja el conteo y la duración en `ingest_run`.

### A.4 · Listings + Inventario (`app/spapi/listings.py`, `inventario.py`) — PR 4
- `spapi_listing_estado_observation(seller_sku, asin, platform, status,
  product_type, last_updated_date, observed_at)` y
  `spapi_inventario_observation(seller_sku, asin, fn_sku, platform, metric_date,
  observed_at, total_quantity, fulfillable_quantity NULL)`.
- **Ninguna escritura en `listing`** (D2): test que lo demuestre (la ingesta corre
  y `listing.price/quantity` no cambian).
- Universo de listings: `seller_sku` con mapeo (`listing`); inventario: recorrido
  completo con `pagination` hermana de `payload` (E/0.4).

### A.5 · Salud y alertas (`app/spapi/salud.py`) — PR 5
- `/salud` gana por plataforma un bloque `spapi` con, por fuente, última corrida
  (`ingest_run`), `ok`, filas, skips, último 429, último fallo de LWA. Sin rutas
  nuevas: mismo endpoint, misma página.
- Alertas por `app/notifica.py` (función nueva `notifica_spapi_fallo`, patrón de
  las existentes): fallo sostenido (dos corridas seguidas), 429 persistente,
  refresh LWA fallido. **Un fallo de LWA en SP-API no toca el ciclo de Ads**
  (procesos y credenciales distintos): test de aislamiento.
- Propuesta de cron (en `docs/DEPLOY.md`, no la instales): cuatro líneas
  `docker exec orbit-app-1 python -m app.cli ingest spapi_<fuente> --platform
  <p>`, entre 05:00 y 06:30 UTC, antes del sync de estructura de las 06:45.

## Evidencia y cierre
Por PR: `docs/evidencia/sp-api-01/A.x/` con comandos, salidas de tests, y para
A.2–A.4 la conciliación contra las actas de Fase 0 con `MockTransport`. No marques
filas del plan como hechas ni toques `plans/ROADMAP.md`: el lead cierra A.x tras
cada review, y A.R/A.6 (deploy, migraciones en producción, primera corrida real,
cron) son del lead y del dueño.

## Entrega de cada PR
Rama `feat/sp-api-01-a<N>` desde `origin/master`; commits normales;
`pre-commit run --all-files` verde; PR a `master` con CI (suite completa). En la
descripción: qué tablas y columnas nacen, qué se lee de Amazon y con qué límite,
y qué NO toca. Sin ssh de escritura, sin producción, sin tracker.
