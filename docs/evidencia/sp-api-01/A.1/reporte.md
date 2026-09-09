# SP-API 01 / A.1 — Cliente unico (evidencia)

Base: `origin/master` `835a9a2`. Rama: `feat/sp-api-01-a1`.
Plan: `plans/sp-api-01.md` tarea A.1. Decisiones D1–D7 cerradas (no se reabren).

## Que cambia
- Nuevo `app/spapi/` con `client.py` (`SpapiClient` + `cliente_compartido()`):
  un solo refrescador LWA por proceso y credenciales (token cacheado 60 s,
  `refreshes` observable), guard default-deny por igualdad literal (GET
  fijos + plantillas listings/ofertas/catalogo + unico POST feesEstimate),
  401 un refresh, 429 un reintento acotado, 5xx/red sin retry, errores
  minimos scrubbados (`SpapiRechazoLWA` con `status`). Sin `app.ads.*`.
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

## Ronda cross-review (una sola, quality-kit; kimi + grok)
- kimi entrego: 1 media + 5 bajas, todas atendidas abajo. Grok no entrego:
  colgado dos veces seguidas (tope 300 s, exit 124; segundo intento con diff
  acotado a 27 KB, mismo resultado): se declara no disponible.
- (media) Refrescador unico no cableado en produccion: cierto, los
  call-sites no inyectaban. Arreglo: `cliente_compartido()` en
  `app/spapi/client.py` (una instancia por proceso y credenciales) y default
  a el en `ProductFeesClient` y `FotosPublicacion` cuando usan red y reloj
  reales; con transporte o reloj de tests, instancia privada (sin fuga entre
  tests). `estimacion_ingest.py:282` y el singleton `fotos_publicacion` ya
  comparten sin cambiar sus call-sites.
- (baja) Mapeo por subcadena `"rechazado"`: ahora `SpapiRechazoLWA` con
  atributo `status`; fees clasifica por tipo.
- (baja) `except Exception` en fotos: acotado a
  `(SpapiError, httpx.HTTPError, OSError)`; lo inesperado propaga.
- (baja) Timeout LWA de fotos 8 s → 30 s (default del cliente unico):
  cambio declarado; impacto acotado (otros hilos salen por
  `acquire(timeout=10)`).
- (baja) Higiene de tests: `syspath_prepend` con restauracion automatica y
  aserciones de alias exactas (`is`); test nuevo del compartido con
  limpieza del registro.
- (baja) Restos: fuera `_spapi_inyectado` sin leer y el `split("?")` muerto
  de `validar_get` (comportamiento identico: los `?` ya se rechazaron).

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
DoD A.1: pytest focal verde (125: 13 nuevos + sondas/fees/fotos/
arquitectura), cero refresh duplicado (test compartido + default
`cliente_compartido` en produccion), E/A.1 con diff vacio salvo el timeout
LWA de fotos declarado arriba. Cross-review de una ronda aplicado (kimi;
grok no disponible). Pendiente del lead: review antes de A.2. No se toco
`plans/ROADMAP.md` ni el plan.
