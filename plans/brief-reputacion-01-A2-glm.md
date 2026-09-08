# REPUTACION 01 — Brief para GLM: tarea A.2 (ingesta snapshots)

Implementa la tarea **A.2** de `plans/reputacion-01.md` bajo el acta
aprobada `docs/evidencia/reputacion-01/0.5/confirmacion.md`: ingesta
de snapshots MeLi (API oficial, diaria) + Amazon (junglee, semanal),
CLI manual y spec del cron. En **una rama y un PR contra master**.

Ajuste sellado por el acta respecto al plan: Keepa esta FUERA
(E/0.4: 402 sin plan); "snapshots oficiales" = MeLi API + junglee
(E/0.2 verificado). A.2 escribe `reputation_snapshot` y
`review_event`; `seller_reputation_snapshot` y `meli_question` son
de A.4 (otro brief, mismo archivo, despues que tu).

## Resultado esperado

`python -m app.cli reputacion snapshot --fuente meli|amazon` trae
rating/count por publicacion vinculada (y reviews con texto MeLi) y
los deja append-only en UNA transaccion: corte a mitad = cero filas.
Re-corrida = cero duplicados. Junglee aborta si excede cobertura o
costo. Todo error/log pasa por `redaction` (cero secretos en salida).

## Base verificada

- Rama desde `origin/master` **`7f5e40e`** (merge PR #200, A.1).
  Verificalo: `git merge-base --is-ancestor 7f5e40e origin/master`.
- Migracion 0024 en master + `tests/test_reputacion_migracion.py`
  (7 passed). Suite master: verde en CI del PR #200.
- Shapes verificados (contratos, no hipotesis):
  - MeLi E/0.3 (`docs/evidencia/reputacion-01/0.3/shapes.json`):
    scan 65 items, `/items` (health), `/reviews/item` (avg, total,
    levels, reviews con rate/title/content/date/status). **total=0
    trae avg=0: ese 0 es sin-dato → `rating=NULL`, `review_count=0`.**
  - junglee E/0.2: input `{"categoryOrProductUrls": [{"url":
    .../dp/ASIN}], "maxItemsPerStartUrl": 1}` por
    `run-sync-get-dataset-items`; item con `stars`, `reviewsCount`,
    `input`+`originalAsin` (pedido) vs `asin` (padre). **Grano
    padre**: `external_id`=pedido, `parent_asin`=devuelto.
- Costo junglee medido: ~$0.0025/producto (2 corridas chicas; la
  primera corrida real fija el numero).
- AppFlowy `REPUTACION 01` sigue **In progress**; la cierra el lead.

## Lectura obligatoria

1. `AGENTS.md`, `docs/CONTEXTO.md`.
2. `plans/reputacion-01.md` (A.2 + propiedad/concurrencia) y el acta
   `docs/evidencia/reputacion-01/0.5/confirmacion.md` COMPLETA.
3. `E/0.1` (inventario: 518 ASINs, 65 items, divergencias),
   `E/0.2`, `E/0.3/shapes.json`.
4. `migrations/0024_reputacion.sql`,
   `tests/test_reputacion_migracion.py`.
5. Patron: `app/disponibilidad.py` (plan puro + sync) y convenciones
   CLI en `app/cli.py` (tu subcomando las sigue, no inventa otras).
6. Secretos: `app/ads/config.py` + `app/redaction.py`.
7. Parser viejo de referencia (NO copiar): `_parse_apify_amazon_item`
   en el stack anterior leia `stars/reviewsCount` — confirma campos.

## Archivos del PR

| Archivo | Cambio |
|---|---|
| `app/reputacion.py` | NUEVO: clientes MeLi+junglee + plan + sync. Estructura extensible: A.4 agrega seller/preguntas aqui (deja sitio, no stubs). |
| `app/cli.py` | Subcomando `reputacion snapshot --fuente meli\|amazon` (+ `--fecha`, `--dry-run`). Solo aditivo. |
| `tests/test_reputacion.py` | NUEVO: HTTP simulado + PG desechable. |
| `docs/evidencia/reputacion-01/A.2/` | `reporte.md` (decisiones D-GLM-A2-*, evidencia roja/verde, spec del cron para A.7). |
| `plans/reputacion-01.md` | Marcar A.2 cc:DONE solo con DoD comprobado. |

No toques migraciones, `app/notifica.py`, dashboard, manifest,
AppFlowy, cron real ni DEPLOY.md (cron/DEPLOY son de A.7, lead).

## Secuencia y contratos

### 1. Clientes read-only con guardia

- MeLi: SOLO GET a `api.mercadolibre.com`, `httpx` con transport
  inyectable. Guardia default-deny (test revienta ante POST/PUT/
  DELETE sin tocar red). A.4 reutiliza este cliente: disena para eso.
- Token MeLi: lee `meli_tokens.json` de `ORBIT_SECRETS_DIR` (patron
  `AdsCredentials`); ante 401, UN refresh + rewrite ATOMICA + UN
  reintento. Gotcha traspaso §1.1: unico refrescador de SU archivo.
- junglee: `POST .../run-sync-get-dataset-items` (acto pagado a
  Apify, permitido; el guard aplica a plataformas comerciales).
  Timeout 300s por llamada (poll interno del actor), `maxItemsPer-
  StartUrl` acotado por CLI (default 1 en sonda manual; A.7 define
  el de prod). Aborta y registra si costo/cobertura excede topes
  (`--tope-usd`, `--max-productos`); jamas reintento automatico
  (cada intento gasta).
- Backoff+jitter ante 429/5xx; circuit breaker por corrida (N fallos
  = aborto honesto con resumen parcial, codigo != 0).
- Costo por corrida junglee en el resumen (items x tarifa + compute
  si visible; si no: `costo: estimado`, declarado, no verificado).

### 2. Plan puro + sync transaccional (patron disponibilidad)

- `observed_at` unico por corrida (parametro, default now UTC);
  `fetched_at` = instante de cada respuesta/pagina.
- Conciliacion D2: solo `(platform, external_id)` en `listing`
  (MeLi: formato en `app/listings.py`, verificado, no supuesto).
  Sin match = skip contado, fila NO escrita. Divergencias de E/0.1
  (ASIN distinto, 8 items fuera de API, 13 SKUs fuera de Odoo) se
  respetan: lo ambiguo se omite y se reporta, nunca se rellena.
- MeLi por item: snapshot (avg/total/levels→extra, health→extra)
  + `review_event` por review (rate/title/content/date/status→
  publicada). Regla total=0 del sello.
- junglee por ASIN: snapshot (`external_id`=pedido,
  `parent_asin`=devuelto, stars, reviewsCount). `productPageReviews`
  se IGNORA en A.2 (hallazgo no verificado E/0.2; ni siquiera stub).
- **UNA transaccion por corrida**: test corta a mitad (excepcion
  inyectada tras N inserts) y la DB queda con CERO filas nuevas.
- Idempotencia: misma corrida x2 = 0 nuevas (`ON CONFLICT DO
  NOTHING` + contadores insertadas/idempotentes/skips).

### 3. CLI

- `python -m app.cli reputacion snapshot --fuente meli --fecha D`
  / `--fuente amazon [--max-productos N] [--tope-usd X]`,
  `--dry-run` (plan sin escribir, imprime resumen). Convenciones de
  `app/cli.py` (job_key, DSN ingest, salida). Resumen JSON a stdout,
  secretos/DSN jamas (redaction). Cero snapshots con inputs
  validos = fallo (codigo != 0), no exito.

### 4. Spec del cron (para A.7, en E/A.2/reporte.md)

job_key, horario del acta (`30 9 * * *` meli diario,
`0 10 * * 1` amazon), comando exacto, logs, precondiciones
(secrets que el lead cablea: `meli_tokens.json`,
`apify_token.json`). NO registres el cron ni edites DEPLOY.md.

## Matriz minima de validacion

| Camino | Evidencia necesaria |
|---|---|
| Shapes | Fakes = JSON de E/0.3 + item junglee E/0.2 (incl. padre!=pedido, item 0 reviews). |
| Rating total=0 | NULL + count 0 en DB. |
| Grano padre | external_id pedido + parent_asin devuelto; test dedicado. |
| Transaccion | Corte a mitad → 0 filas (PG real). |
| Idempotencia | x2 = 0 nuevas; observed_at distinto suma. |
| Dedup reviews | Re-observado no duplica. |
| Catalogo | Sin match = skip contado (incl. 1 caso E/0.1). |
| Guardia MeLi | POST/PUT/DELETE revientan sin red. |
| 401+refresh | 1 refresh, atomico, 1 reintento; 2do 401 aborta. |
| Tope junglee | Exceso de tope aborta honesto (codigo != 0, parcial declarado). |
| Redaction | Key/token falsos en error/log: ausentes en salida (test dedicado). |
| CLI | Manual OK x fuente + dry-run sin writes (PG real). |

Regresiones con poder discriminante (rojo previo o mutante
declarado). Verde que no discrimina no cuenta.

## Proceso y limites

1. Decisiones `D-GLM-A2-*` en E/A.2/reporte.md ANTES del codigo.
   Duda de negocio o cambio de contrato: al lead.
2. TDD por etapa con evidencia roja/verde.
3. Postgres REAL desechable; `-rs`, cero skips en integracion.
4. Localmente SOLO `tests/test_reputacion.py`. Bateria completa en
   CI; abre el PR y lee Quality del ultimo commit.
5. Ruff y pre-commit; jamas `--no-verify` ni candados relajados.
6. Sin SSH goncloud, APIs reales (MeLi/junglee TODO simulado; cada
   corrida junglee real gasta — solo el lead corre prod),
   credenciales reales, migraciones en vivo, cron, deploy, merge,
   AppFlowy o DEPLOY.md. El lead lleva prod/tracker/sondas.
7. Commits Conventional en espanol, metadatos reales.
8. Excluido: A.4 (seller/preguntas/claims), A.5, A.6, A.3, texto
   `productPageReviews`, Keepa, Account Health, Buy Box, scheduler
   propio. No reconstruyas A.1.

```bash
.venv/bin/python -m pytest tests/test_reputacion.py -q -rs
.venv/bin/ruff check app/reputacion.py app/cli.py tests/test_reputacion.py
.venv/bin/ruff format --check app/reputacion.py app/cli.py tests/test_reputacion.py
PATH="$PWD/.venv/bin:$PATH" pre-commit run --all-files
git diff --check
git log --oneline origin/master..HEAD   # SOLO commits de este brief
```

## Entrega al lead

PR: **`feat(reputacion-01): ingesta snapshots MeLi+Amazon (A.2)`**.

Enlace, SHA, resumen, decisiones/residuales, evidencia roja/verde,
skips explicados, CI verde del ultimo commit. Criterio de cierre:
una transaccion probada, idempotencia y dedup en PG real, guardia y
redaction probadas, topes junglee probados, CLI real (no solo
importado), Ruff/pre-commit verdes, suite en CI. El lead revisa,
mergea y corre la primera ingesta real (MeLi diaria + junglee del
lunes siguiente) tras A.7... correccion: tras merge de A.2 el lead
valida con CLI manual; el cron se enciende en A.7.
