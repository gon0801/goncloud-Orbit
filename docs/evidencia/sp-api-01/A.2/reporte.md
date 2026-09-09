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
→ `23 passed` (0 skips: Postgres local vivo; migración 0001+0030 aplicada
en BD desechable, con unicidad, triggers, append-only y grants ±
verificados). `tests/test_redaction.py` → `2 passed`.
Focal ampliado (spapi_client, sonda, fees, fotos, arquitectura, cli)
→ `174 passed`.
Mutante (regla 9): sin la guarda `next_token_repetido` el test falla
(`1 failed`); con ella, verde. Archivo restaurado íntegro (`diff` vacío).
`ruff check` + `ruff format --check` → verde.
`pre-commit run --all-files` → verde.

## Ronda única del lead (F1–F6, un commit)
- F1: `register_secret` ignora valores < 8 chars (un corto redactaba medio
  universo: el token "T" de fixture rompia "nextToken"; demostrado rojo con
  2 failed y verde con 2 passed en `tests/test_redaction.py` nuevo);
  fixtures con tokens largos y únicos (≥ 16 chars).
- F2: triggers `spapi_order_append_only` (UPDATE/DELETE por fila) y
  `spapi_order_append_only_truncate` (por sentencia), reusando
  `prohibir_mutacion()` de 0001; UPDATE/DELETE verificados que truenan
  (`RestrictViolation`, el ERRCODE real del patron).
- F3: `recorrer_ordenes` fatal (`contrato inesperado: sin lista orders`,
  sella ok=false) si el contenedor no es dict, falta la clave `orders` u
  `orders` no es lista; lista vacía con clave presente sigue válida
  (ventana sin pedidos). Tres tests parametrizados + uno de vacía válida.
- F4: token bucket por corrida en `orders.py` (capacidad 20, recarga
  0.0056/s, E/0.1; reloj/espera del cliente): 20 páginas sin espera, la
  21ª espera ≈ 178 s (test con reloj falso). Sin Redis/colas por stack.
- F5: clave `UNIQUE (platform, amazon_order_id, last_updated_time)` y
  `ON CONFLICT` en consecuencia; misma orden+tiempo en otra plataforma sí
  entra (test).
- F6: permisos negativos (`app_read`/`app_decide` sin INSERT, `app_ingest`
  sin UPDATE/DELETE) con `has_table_privilege`.

## Cierre
DoD A.2: pytest focal verde (199 con arquitectura y cli), re-corrida sin
duplicar, E/A.2 conciliada contra 0.1. Regla 8: sin SELECT previo
aplicable (tabla nueva, cero filas en producción; invariante de dominio
fuente, no de dato existente). No se tocó el plan ni `plans/ROADMAP.md`.

## Apartado A.2b — pedidos con estado y total
Origen: las runs 140/141 (224 pedidos) guardaron estado, canal y total en
NULL; el resumen sin `includedData` solo trae identidad, fechas,
salesChannel e items. Acta de sonda: `docs/evidencia/sp-api-01/A.2b/sonda.md`
(3 GET MX con `FULFILLMENT,PROCEEDS`, claves sin valores, cero PII;
PROCEEDS no exigió permiso especial; dos ventanas posteriores vacías, sin
reintentos para no quemar el 0.0056/s).
- Ingesta: `includedData=FULFILLMENT,PROCEEDS` (jamas BUYER/RECIPIENT);
  `fulfillment.fulfillmentStatus` → estado, `fulfilledBy` → canal
  (análogo AFN/MFN de v0), `proceeds.grandTotal` → total; ausente = NULL.
- Migración `0031`: clave bitemporal
  `(platform, amazon_order_id, last_updated_time, observed_at)` (patrón
  0026/0027) + vista `v_spapi_order_ultima` (última por pedido); sin tocar
  ni borrar filas (las 224 existentes siguen y se re-observan).
- Tests rojo→verde: parseo con secciones falla en el parser viejo; e2e
  falla en el `ON CONFLICT` viejo (`no unique constraint matching`,
  código y esquema cambian juntos); en verde, dos corridas dejan dos
  observaciones y la vista muestra la última completa. PII de RECIPIENT
  filtrada aunque venga; unicidad nueva, vista y permisos ± verificados.
- No se aplica la migración ni se corre la ingesta en producción (dueño
  con el script del lead). Sin tocar el plan.

## Ronda única A.2b (F1–F4, un commit)
- F1: `test_pide_secciones_en_cada_peticion` afirma
  `includedData=FULFILLMENT,PROCEEDS` en cada petición real y
  `test_jamas_pide_buyer_ni_recipient` que esos valores jamás salen; sin
  el parámetro el primero falla (KeyError, mutante muerto).
- F2: `--desde YYYY-MM-DD` fuerza `lastUpdatedAfter` ignorando el máximo
  (test de ventana + e2e sobre máximo reciente + uso inválido). Backfill
  del dueño, UNA vez tras el deploy:
  `docker exec orbit-app-1 python -m app.cli ingest spapi_orders
  --platform amazon_mx --desde 2026-08-10` (y lo mismo con `amazon_us`);
  con la clave bitemporal no toca ninguna fila existente.
- F3: `COMMENT ON COLUMN` de `order_status` y `fulfillment_channel` en
  0031 (sección primero, alias plano de respaldo; envío vs ciclo). La
  precedencia la fija `test_estados_de_ciclo_y_envio_no_se_mezclan`
  (reemplaza a `test_seccion_prefiere_a_clave_plana`, ver abajo).
- F4: `migrations/0031_reversa_spapi_orders_bitemporal.sql` (patrón
  0011_reversa_*; solo antes de la primera re-observación, la guarda
  aborta si ya hay tripletas repetidas) con `test_reversa_0031`; test PII
  afirma sobre el objeto saneado (valores fuera), no sobre campos que
  pasan igual.

## Revisión del lead PR #240 (bloqueantes #2, #3, #7c)
- #2 `fulfillment_status` separado (0031 `ADD COLUMN` + COMMENT por
  vocabulario): `order_status` SOLO ciclo (`orderStatus` plano),
  `fulfillment_status` SOLO envío (`fulfillment.fulfillmentStatus`); la
  reversa lo quita y verifica su ausencia. `test_seccion_prefiere_a_clave_plana`
  se reemplaza por `test_estados_de_ciclo_y_envio_no_se_mezclan`; la vista
  del e2e ahora lee `(NULL, 'Shipped', ...)` en la corrida de sección.
- #3 Paginación incompleta (`next_token_repetido`, `pagina_vacia_con_token`,
  `limite_max_paginas` — este último sin test hasta hoy,
  `test_tope_de_paginas_para_y_marca`) sella `ok=false` con motivo
  `paginacion_incompleta:<aviso>` conservando las filas;
  `ResultadoIngesta.ok=False` y CLI sale 1 (reparar con `--desde`).
  E2E `test_paginacion_incompleta_sella_ok_false_con_lo_escrito`.
- #7c Unicidad por plataforma: la primera fila se commitea ANTES del
  duplicado (antes el rollback la borraba y el insert cruzado entraba
  contra tabla vacía). Mutante: clave sin `platform` → el test falla;
  con ella, verde.
Mutante (regla 9, revisión): sin `app/spapi/orders.py`, `4 failed, 32 passed`;
con el fix, `36 passed` (orders). Focal (orders, cliente, redacción,
fees, fotos, arquitectura, sonda, cli) → `212 passed`.
