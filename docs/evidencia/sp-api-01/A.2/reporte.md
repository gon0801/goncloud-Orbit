# SP-API 01 / A.2 — Ingesta Orders (evidencia)

Base: `origin/master` post-A.1 (merge #237). Rama: `feat/sp-api-01-a2`.
Plan: `plans/sp-api-01.md` tarea A.2. D2 citada: ninguna escritura en
`listing` (solo lee `MERCADOS` del cliente para el `marketplace_id`).

## Que cambia
- Migración `0030_spapi_orders.sql`: `spapi_order_observation` append-only
  con unicidad `(amazon_order_id, last_updated_time)` (clave de E/0.1),
  dinero `(valor, moneda)` con CHECK parejo (regla 4), trigger
  `spapi_order_tiempo_coherente` (purchase <= updated, no en CHECK), índice
  `(platform, last_updated_time)`, GRANTs (`app_ingest` escribe,
  `app_read/_decide/_admin` leen, como 0028). Sin columnas de comprador ni
  dirección.
- `app/spapi/orders.py`: `parsear_orden` (resumen 2026-01-01, tolerante a
  alias v0; identidad exigida, resto ausente = NULL), `parametros_ventana`
  (`lastUpdatedAfter` = max − 1 día de solape; primera: `createdAfter` 30
  días, Zulu), `recorrer_ordenes` (guardas token repetido / página vacía,
  status != 200 fatal), `ejecutar_ingesta` (abre/sella `ingest_run`
  `spapi_orders`, `ON CONFLICT DO NOTHING` contado como skip, sello
  ok=false best-effort), `main` (`--platform`, fail-closed sin DSN).
- `app/cli.py`: pipeline `spapi_orders` en choices, ayuda y despacho.

## Que NO toca
- `app/ads/*`, `listing`, `/salud`, `app/notifica.py`, cron, producción.
  Primera corrida real la hace el dueño tras el deploy (brief).

## Conciliación contra E/0.1 (MockTransport)
Fixture con las claves reales del acta (`orderId`, `createdTime`,
`lastUpdatedTime`, `orderItems`, `salesChannel`, `pagination.nextToken`):
2 páginas, 4 resúmenes (3 válidos + 1 sin tiempo) → 3 filas, run sellada
`ok=true, rows_written=3, rows_skipped=1 ("1x sin_identidad")`, total
`100.0000 MXN` verificado en base. Re-corrida: 0 escritas, `duplicada` al
skip, conteo total 3 (idempotente). Segunda ventana:
`lastUpdatedAfter=2026-09-07T11:00:00Z` (= max − 1 día); primera:
`createdAfter=2026-08-10T12:00:00Z` (30 días, Zulu).

## Comandos y salidas (sin secretos)
`uv run --frozen python -m pytest -q tests/test_spapi_orders.py`
→ `17 passed` (0 skips: Postgres local vivo; migración 0001+0030 aplicada
en BD desechable, con unicidad, trigger y grants verificados).
Focal ampliado (spapi_client, sonda, fees, fotos, arquitectura, cli)
→ `174 passed`.
Mutante (regla 9): sin la guarda `next_token_repetido` el test falla
(`1 failed`); con ella, verde. Archivo restaurado íntegro (`diff` vacío).
`ruff check` + `ruff format --check` → verde.
`pre-commit run --all-files` → verde.

## Cierre
DoD A.2: pytest focal verde, re-corrida sin duplicar, E/A.2 conciliada
contra 0.1. Regla 8: sin SELECT previo aplicable (tabla nueva, cero filas
en producción; invariante de dominio fuente, no de dato existente).
Pendiente del lead: review antes de A.3. No se tocó `plans/ROADMAP.md`.
