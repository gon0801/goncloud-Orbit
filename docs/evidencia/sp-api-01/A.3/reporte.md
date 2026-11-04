# SP-API 01 / A.3 — Pase Pricing con Buy Box (evidencia)

Base: `origin/master` post-A.2 (A.2b PR #240 en revisión; esta rama supone
que A.2b ocupa la 0031 y usa la **0032** — si cambia, se renumera al merge).
Rama: `feat/sp-api-01-a3`. D2: ninguna escritura en `listing` (solo lee el
universo `external_id` por plataforma).

## Que cambia
- Migración `0032_spapi_pricing.sql`: `spapi_price_observation` append-only
  (clave `(asin, platform, observed_at)`, CHECKs de dinero parejo por par,
  conteos no negativos, triggers anti UPDATE/DELETE/TRUNCATE, índice
  `(platform, metric_date)`, GRANTs como 0028). 0 ofertas = fila con
  `offers_count=0` y precios NULL.
- `app/spapi/pricing.py`: universo desde `listing`, 2 llamadas por ASIN
  (ofertas + competitivo, params exactos de la sonda 0.2), `CuboTasa(1,
  0.5/s)` local, `ingest_run spapi_pricing` con sello ok=false best-effort,
  CLI `ingest spapi_pricing --platform [--max-asins] [--seller-id]`.
- `app/spapi/client.py`: `CuboTasa` promovido (una copia); `orders.py`
  migrado a él sin cambiar comportamiento (199 del focal intactos).
- `app/cli.py`: pipeline `spapi_pricing`.

## Decisiones con evidencia (no inventadas)
- Seller propio único `A29XRL07YRN0L` en MX/US/CA: Ads `GET /v2/profiles`
  del 2026-09-09 (`accountInfo.type=seller`; acta 0.5 citó el de MX). Sin
  ese dato `buy_box_is_own` queda NULL (columna lo permite).
- `own_listing_price`: oferta propia en offers; respaldo competitivo con
  `belongsToRequester` solo cuando hay ofertas pero la nuestra no está.
  Con 0 ofertas: NULLs aunque el competitivo traiga dato (brief A.3).
- Moneda fuera de MXN/USD o monto ilegítimo = oferta no utilizable; con
  ofertas pero cero utilizables la fila no se escribe (`precio_sin_moneda`).
- Ronda F1–F4 (PR #241): transacción por ASIN + sello en la suya (F1);
  403/404 = skip `http_40x`, 401/429 persistentes = fatales, resto cuenta
  contra umbral 20 % / 25 seguidos (F2); conteos y mínimo desde `Summary`
  con página como respaldo (F3, formas pineadas del modelo oficial
  `productPricingV0.json` de `amzn/selling-partner-api-models`);
  `status=Success` sin clave `Offers` = cero ofertas (F4).
  `llamadas` cuenta intentos (la tasa se gasta aunque fallen).

## Conciliación contra E/0.2 (MockTransport)
MX real (`IsBuyBoxWinner`, `ListingPrice`, `SellerId`,
`IsFulfilledByAmazon`) y US real (0 ofertas + competitivo con dato):
pase de 3 ASINs → 2 filas, 6 llamadas, 5 esperas de 2 s en el cubo,
run `ok=true, rows_written=2, rows_skipped=1 ("1x precio_sin_moneda")`,
buybox/conteos/mínimo/`metric_date` verificados en base, fila US con
`offers_count=0` y NULLs. Re-pase mismo `observed_at`: 0 escritas,
`duplicada` al skip.

## Comandos y salidas (sin secretos)
`uv run --frozen python -m pytest -q tests/test_spapi_pricing.py`
→ `22 passed` (0 skips: migración 0001+0032 en BD desechable, con clave,
CHECKs, append-only y grants ± verificados; COMMENT F3 de precedencia
Summary→página incluidos).
Focal (pricing, orders, redaction, spapi_client, sonda, fees, fotos,
arquitectura, cli) → `221 passed`.
Mutante (regla 9, primera ronda): sin el respaldo competitivo el test
falla (`1 failed`); con él, verde. Archivo restaurado íntegro.
Mutante (regla 9, ronda F1–F4): con `app/spapi/pricing.py` escondido vía
stash, los 8 tests nuevos fallan (`8 failed, 1 passed`: el guarda de
respaldo pasa igual, esperado); con el fix, verde. Restaurado íntegro.

## Revisión del lead PR #241 (7 bloqueantes, los de A.3 aquí)
- #1 Forma real de competitivePrice (modelo oficial `productPricingV0.json`:
  payload LISTA de Price, ruta `Product.CompetitivePricing.CompetitivePrices`;
  `PriceType` sí trae `ListingPrice`/`LandedPrice`): `_competitivas_de`
  reescrita estricta (payload objeto = fatal `contrato` que cuenta al
  umbral); fixtures rehechos a la forma real. `belongsToRequester` NO está
  en el modelo: lectura tolerante documentada, pendiente de pinar en sonda
  (E/0.2 solo registró claves_top).
- #5 Cubo dentro del cliente: `get/post_fees(..., limitador=)` consume por
  intento (el reintento 429 también) y `fijar_tasa` sigue a
  `x-amzn-RateLimit-Limit` (nuevo `tasa_anunciada`; ilegible se ignora). Sin
  `limitador`, comportamiento A.1 intacto (fees/fotos/sonda no lo pasan).
  Pricing pasa su cubo y `recorrer_ordenes` acepta `cubo=` opcional
  (commit `cbd3807`; sin cubo construye el de Orders: fees/fotos/sonda
  intactos). Nota de merge: PR #240 también toca `orders.py`; al rebasear
  tras A.2b, conservar ambos hunks (el de aquí son 3 líneas + 1 test).
- #6 Red a F2: `httpx.HTTPError` cuenta al umbral con skip `red` (timeout
  aislado tras 5 éxitos no detiene el pase; test parametrizado
  ConnectError/ReadTimeout).
- #7a `ingest_run.llamadas` (migración 0033 + CHECK no negativo): pricing la
  sella en ok y en fatal; asserts en pase/F1/red.
- #7b Negativas SQL: buybox/mínimo en ambas direcciones, conteos negativos,
  TRUNCATE bloqueado.
- #4 Redacción: fuera `MINIMO_SECRETO` (era de A.2 en master); todo valor no
  vacío se redacta; fixture `test_redaction.py` usa secreto corto
  distintivo en vez del `"T"` que rompía `nextToken`.
- Refactor exigido por ruff PLR0915: cuerpo por ASIN a `_procesar_asin` con
  `_Avance` (comportamiento idéntico, tests intactos).
Mutante (regla 9, revisión): sin los 3 archivos de app, `16 failed`
(pricing+redacción; cliente ni importa: `tasa_anunciada` ausente); con el
fix, `44 passed` (pricing+cliente+redacción).
`ruff check` + `ruff format --check` → verde.
`pre-commit run --all-files` → verde (abajo, antes del commit).

## Cierre
DoD A.3: pytest focal verde, pase de 2 llamadas/ASIN con duración en el
`print` (y en `started/finished_at`), E/A.3 conciliada contra 0.2.
Pendiente del lead: review antes de A.4. No se tocó el plan ni ROADMAP.
