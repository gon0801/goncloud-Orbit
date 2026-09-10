# SP-API 01 / A.5 — Salud y alertas (evidencia)

Base `origin/master` 20cc7b9 (A.1–A.4 mergeadas). Rama `feat/sp-api-01-a5`.
Última tarea de código de Fase A; A.R y A.6 son del lead/dueño.

## Qué muestra /salud de nuevo

Por plataforma, clave `spapi`: por cada fuente (`spapi_orders`,
`spapi_pricing`, `spapi_listings`, `spapi_inventario`) la última corrida
(id, started/finished, ok, filas, skips, skip_reason, llamadas), la
última con 429 y la última con fallo LWA. Sin corridas = todo `None`.
La página `/salud` renderiza la tabla SP-API por plataforma (sin fetch
nuevo, estilo existente). Captura textual con datos sembrados (MX: 1 ok
orders + 1 pricing 429; US vacío):

```json
{"spapi_orders": {"ultima": {"id": 1, "ok": true, "rows_written": 4,
  "llamadas": 1, ...}, "ultima_429": null, "ultimo_lwa": null},
 "spapi_pricing": {"ultima": {"ok": false, ...},
  "ultima_429": {"skip_reason": "http_429: pricing status=429", ...},
  "ultimo_lwa": null},
 "spapi_listings": {"ultima": null, "ultima_429": null, "ultimo_lwa": null},
 "spapi_inventario": {"ultima": null, "ultima_429": null, "ultimo_lwa": null}}
```

## Cuándo alerta y cuándo NO

`evaluar_alertas` (la llaman las 4 ingestas tras sellar, fail-silent)
dispara `notifica_spapi_fallo` SOLO en flanco: (a) motivo `lwa_fallido`
en la última (inmediata, con "El ciclo de Ads no se afecta"); (b)
motivo `http_429` en la última (inmediata: ya sobrevivió al reintento);
(c) fallo sostenido: dos últimas `ok=false` abriendo racha. La tercera
fallida seguida NO alerta; una ok rompe la racha. Sin canal = True
silencioso (no es fallo).

## Prefijos de motivo que nacen

`lwa_fallido` (SpapiAuthError por tipo), `http_429` / `http_5xx`
(formato `status=NNN` que genera nuestro código, pineado en tests),
`red` (httpx.HTTPError por tipo), `contrato` (cajón: umbral, 401
persistente, resto). Cambio de una línea en la rama except de cada
ingesta (`prefijo_motivo` en `client.py`).

## Migración 0036 (JUSTIFICACIÓN EN EL PR)

`ingest_run.platform` (ENUM, NULL = pre-A.5 sin atribuir, SIN grant de
UPDATE a propósito). Por qué es inevitable: A.5 exige historial POR
FUENTE+PLATAFORMA solo desde `ingest_run`, pero `SOURCE` es global y la
columna no existía; con el cron diario las corridas alternan plataforma
y el flanco jamás se cumpliría, y /salud mezclaría MX/US. Derivarlo de
las observaciones es peor: una corrida totalmente fallida (LWA caído, 0
filas — justo la que debe alertar) no deja rastro ahí. Alternativas
descartadas: source por plataforma (rompe historia y constantes),
racha en memoria (el cron es un proceso fresco por corrida), tabla de
estado (prohibida por el brief). SELECT explícito por columna para los
4 roles + asserts (positivo y negativo) en el mismo archivo, patrón
0034.

## Comandos y salidas (sin secretos)

`uv run --frozen python -m pytest -q tests/test_spapi_salud.py`
→ `18 passed` (todo en rojo primero: error de colección sin `salud.py`).
Suite completa → `2096 passed` (incluye dashboard/UI; el fixture
compartido `SQL_MIGRACION` suma 0033+0034+0036 porque /salud siempre
lee esas columnas — aditivas puras).
`ruff check` + `ruff format --check` → verde.
`pre-commit run --all-files` → verde (abajo, antes del commit).

## Mutantes (regla 9)

- M1 (sin prefijo en orders): 3 tests en rojo; con él, verde.
- M2 (alerta en cada fallida): flanco en rojo; con racha, verde.
- M3 (429 clasificado como contrato): taxonomía + sello 429 en rojo.
Archivos restaurados íntegros (diff vacío contra copias).

## Qué NO toca

`app/ads/*` (test AST: 5 archivos sin imports `app.ads`; el ciclo corre
en otro proceso con otras credenciales — LWA caído solo sella+alerta),
`notifica_*` existentes, esquema salvo 0036, cron instalado (solo
propuesta en DEPLOY), producción (cero corridas reales: todo
MockTransport + BD desechable), plan/ROADMAP/manifest/CHAT-CONTEXT,
tracker.
