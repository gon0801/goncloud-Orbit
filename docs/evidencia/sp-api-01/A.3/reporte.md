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
- != 200 en cualquier llamada = aborto ok=false (contrato estricto, F3).

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
→ `13 passed` (0 skips: migración 0001+0032 en BD desechable, con clave,
CHECKs, append-only y grants ± verificados).
Focal (orders, redaction, spapi_client, sonda, fees, fotos, arquitectura,
cli) → `199 passed` (el refactor a `CuboTasa` no movió nada).
Mutante (regla 9): sin el respaldo competitivo el test falla
(`1 failed`); con él, verde. Archivo restaurado íntegro.
`ruff check` + `ruff format --check` → verde.
`pre-commit run --all-files` → verde (abajo, antes del commit).

## Cierre
DoD A.3: pytest focal verde, pase de 2 llamadas/ASIN con duración en el
`print` (y en `started/finished_at`), E/A.3 conciliada contra 0.2.
Pendiente del lead: review antes de A.4. No se tocó el plan ni ROADMAP.
