# SP-API 01 / A.1 — Cliente unico (evidencia)

Base: `origin/master` `835a9a2`. Rama: `feat/sp-api-01-a1`.
Plan: `plans/sp-api-01.md` tarea A.1. Decisiones D1–D7 cerradas (no se reabren).

## Que cambia
- Nuevo `app/spapi/` con `client.py` (`SpapiClient`): un solo refrescador LWA
  por proceso (token cacheado 60 s, `refreshes` observable), guard default-deny
  por igualdad literal (GET fijos + plantillas listings/ofertas/catalogo +
  unico POST feesEstimate), 401 un refresh, 429 un reintento acotado,
  5xx/red sin retry, errores minimos scrubbados. Sin `app.ads.*`.
- `tools/sonda_spapi.py` importa de `app.spapi` (subclase fina + re-export;
  admite ademas Catalog Items; sondeos Fase 0 intactos).
- `app/estimacion_fees.py` y `app/publicacion_fotos.py` usan el refrescador
  unico (instancia compartida por proceso, inyectable); validacion HTTP,
  retries, rate-limit, throttle, cache y 404/401/403 intactos.
- Tests nuevos `tests/test_spapi_client.py` (10): allowlist sin HTTP, POST
  fees unico, 401/429, 5xx/red sin retry, scrub, reutilizacion por la sonda,
  un solo refresh fees+fotos compartiendo instancia (POST LWA = 1).

## Que NO toca
- `app/ads/*`, `listing` (precio/stock), migraciones (A.1 no crea tablas),
  `/salud`, `app/notifica.py`, cron, produccion, tracker.

## Comandos y salidas (focal, sin secretos)
Base (antes del cambio):
`uv run --frozen python -m pytest -q tests/test_sonda_spapi.py tests/test_estimacion_fees.py tests/test_publicacion_fotos.py`
→ `93 passed` (sonda 23 + fees + fotos).

Despues:
`uv run --frozen python -m pytest -q tests/test_spapi_client.py tests/test_sonda_spapi.py tests/test_estimacion_fees.py tests/test_publicacion_fotos.py tests/test_architecture.py`
→ `122 passed` (10 nuevos + 23 + fees + fotos + arquitectura).

Lint:
`ruff check` + `ruff format --check` sobre `app/spapi app/estimacion_fees.py app/publicacion_fotos.py tools/sonda_spapi.py tests/test_spapi_client.py`
→ `All checks passed`, `already formatted`.

## Diff de comportamiento (vacio salvo lo declarado)
- Sonda: los 23 tests Fase 0 verdes antes y despues; unica expansion
  declarada: `validar_get` admite Catalog Items 2022-04-01 (A.1 lo exige).
- Fees/fotos: suites intactas y verdes; mismo numero de reintentos y
  mapeo de errores (LWA rechazado → `fee_http_error`; token ausente →
  `fee_error`; red/timeout propagan `httpx` igual que antes).
- Discriminante (`/tmp/muta_spapi.py`, `PYTHONPATH=. uv run --frozen python`):
  `MUTA1: sin guard emite HTTP` (el allowlist morderia) y
  `MUTA2: dos instancias separadas = 2 POST LWA (shared da 1)`.

## Limites oficiales (actas 0.1–0.5, sin colas por stack)
Orders 0.0056 req/s burst 20; Pricing 0.5/s; Listings 5/s; Inventario 2/s;
Fees 1/s burst 2 (token-bucket propio conservado). Cliente reacciona al 429.

## Cierre
DoD A.1: pytest focal verde, cero refresh duplicado (test compartido),
E/A.1 con diff vacio. Pendiente del lead: review una ronda + cross-review
sugerido (kimi) antes de A.2. No se toco `plans/ROADMAP.md` ni el plan.
