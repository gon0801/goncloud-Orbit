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

`app/ads/*` — CORRECCIÓN de esta ronda (ver abajo): el test AST sigue
valiendo (5 archivos sin imports `app.ads` DIRECTOS), pero en runtime
`import app.spapi.salud` SÍ mete `app.ads` y `app.ads.config` en
`sys.modules` vía `app/notifica.py:38`. Sin riesgo funcional (módulo
inerte: constantes y dataclasses, cero IO; nunca `app.ads.write`/`client`,
pineado con allowlist explícita en subproceso). El ciclo de Ads corre en
otro proceso con otras credenciales — LWA caído solo sella+alerta.
`notifica_*` existentes, esquema salvo 0036 (+0037, ver ronda review),
cron instalado (solo propuesta en DEPLOY), producción (cero corridas
reales: todo MockTransport + BD desechable), plan/ROADMAP/manifest/
CHAT-CONTEXT, tracker.

## Ronda de review del lead (2026-09-10, sobre 7b7dc54)

Auditoría del lead: 29 mutantes, 21 sobrevivieron. Esta ronda corrige los
5 bloqueantes + 6 baratas + 3 opcionales del dueño. Cada test nuevo se
verificó en ROJO contra su mutación exacta (driver `/tmp/mutar.py`: aplica
el mutante, corre, restaura) y en VERDE con el fix. Archivos restaurados
tras cada mutación (`git status` limpio de artefactos).

### Semántica final de las alertas (para el PR)

Sale UNA alerta por racha, si y solo si la corrida recién sellada ABRE una
situación nueva: (a) clase inmediata (`lwa_fallido`/`http_429`): la corrida
sellada anterior NO era fallida de esa MISMA clase (sin historia, ok, u
otra clase); (b) resto: segunda fallida consecutiva (`_racha_fallida`).
Las dos rutas son excluyentes (nunca dos mensajes por corrida). Un CAMBIO
DE CLASE dentro de una racha (`contrato` → `lwa_fallido`) SÍ re-alerta: es
información nueva (otra causa, otra acción). La tercera fallida seguida NO
re-alerta; una ok rompe la racha. Sin canal = True silencioso.

### Qué se corrigió

- B1: `evaluar_alertas` leía con SELECT suelto (conexión INTRANS, sello en
  savepoint descartable). Ahora lee en su propio `with conn.transaction()`
  y notifica FUERA. Tests: conexión IDLE tras evaluar; dos ingestas
  seguidas verificadas DESDE OTRA conexión.
- B2: las "inmediatas" alertaban en CADA corrida (24 mensajes de viernes a
  lunes). Ahora: una por racha + re-alerta por cambio de clase. Tests: tres
  429 = 1, tres LWA = 1, contrato×2+lwa = 2, una contrato sola = 0.
- B3: el candado de GRANT no ejercía INSERT (REVOKE dejaba 142 verdes con
  las 4 ingestas rotas). Ahora: assert positivo de INSERT en el `DO $$` de
  0036 + ejercicio real con `SET ROLE app_ingest` en el test.
- B4: el historial incluía abiertas y asumía "la más nueva". Ahora:
  `ok IS NOT NULL` + ancla `id <= run_id` (run_id ya estaba en scope en las
  8 llamadas). `/salud` SIGUE mostrando abiertas (informativo, `—`): la
  asimetría queda documentada en el docstring.
- B5: test de cableado parametrizado a los 4 pipelines (LWA caído: main=1,
  `ok=false`, prefijo, `platform == "amazon_mx"`, exactamente 1 mensaje).
- Baratas: candado ads endurecido (AST + grafo runtime en subproceso, sin el
  `sys.modules.pop` muerto); DEPLOY con orden explícito (0035+0036 ANTES de
  reconstruir la app); 0036 declara "No re-runnable"; `starts_with` en vez
  de `LIKE` (+trampa `httpX429`); asserts exactos (etiquetas fuente/plataforma,
  frase Ads completa y su ausencia en 429, secreto sin fuga, `_historial`
  roto con log "no pudo correr", ids exactos en bloque/API, fila real en
  plantilla); 4 tests de `notifica_spapi_fallo`/`aviso_spapi_fallo`.
- Opt-7: índice `(source, platform, id DESC)` en migración **0037 NUEVA**
  (no dentro de 0036): al pedirlo, 0036 ya estaba mergeada en
  `origin/master` (PR #246 mergeado durante la ronda) y su aplicación en
  producción no es verificable desde aquí — editarla rompería cualquier
  entorno que ya la aplicó. `git log origin/master -- 0036` confirma el
  merge; el deploy (A.6) sigue pendiente del dueño.
- Opt-8: guarda `_spapi_de` en `/salud` (espejo de `_quota_de`): con
  `bloque_salud` roto la pantalla responde 200 y conserva el resto.
- Opt-9: `_vigilar_umbral` lleva `ultimo status=` (muro de 503s sella
  `http_5xx`, no "contrato"). Límite declarado: muerte SOLO por red
  (sin status) sigue en `contrato` — cambiarlo es decisión de taxonomía
  del lead, no de esta ronda.

### Mutación exacta que mata cada test nuevo

- `test_evaluar_alertas_deja_conexion_idle` / `test_dos_ingestas_...otra_conexion`:
  M-a `ultimas = _historial(...)` sin `with conn.transaction()` → INTRANS / 0 filas.
- `test_429_tres_seguidos...` / `test_lwa_tres_seguidos...`:
  M-b inmediata sin comparar anterior → 3 mensajes.
- `test_cambio_de_clase_..._re_alerta`:
  M-c inmediata sin comparar CLASE → 1 mensaje.
- `test_una_sola_contrato_sin_historia...`:
  M-d `if len(ultimas) < 2: return ultimas[0][3] is False` → 1 mensaje.
- `test_huerfana_abierta_no_enmascara_racha`: M-e sin `AND ok IS NOT NULL` → 0.
- `test_ancla_corrida_sellada_ante_nuevas`: M-f sin `AND id <= %s` → 0.
  (En el camino: la guarda `ultimas[0][0] != run_id` era muerta con el ancla
  SQL — se quitó en vez de dejar código inverificable.)
- `test_bloque_salud_lee_solo_ingest_run`: M-g `LIKE 'http_429%'` → la trampa
  `httpX429` cae en `ultima_429`; ORDER BY ASC / skips hardcodeado / started=fin
  → ids y valores exactos fallan.
- `test_429_persistente_alerta_inmediata`: M-h fuente/plataforma
  intercambiados → etiquetas `fuente:`/`plataforma:` fallan; M-i matiz en
  todas → ausencia en 429 falla.
- `test_lwa_alerta_con_matiz_ads` + builder de notifica: M-i → frase/matiz fallan.
- `test_motivo_con_secreto_no_sale_por_telegram`: sin doble `scrub` en el
  aviso → el secreto llega al mensaje.
- `test_historial_roto_no_impide_el_sello`: `_historial` que levanta
  `InFailedSqlTransaction` → sin try/except el sello revienta; sin código
  (return None) el log "no pudo correr" falta.
- `test_migracion_0036_platform_y_grants`: M-j `REVOKE INSERT ... FROM
  app_ingest` en 0036 → el `DO $$` revienta y el INSERT con SET ROLE da
  `InsufficientPrivilege`.
- `test_aislamiento_...` (4 params): M-n sin `evaluar_alertas` en el except
  de pricing/listings/inventario → 0 mensajes; M-o run con `platform NULL`
  → `platform == "amazon_mx"` falla.
- `test_aislamiento_grafo_runtime_solo_config_inerte`: cualquier import
  nuevo de `app.ads.*` bajo `app.spapi.salud` → allowlist distinta.
- `test_ui_salud_...` / dashboard: ORDER BY ASC, celdas en `-`.
- `test_ui_salud_sobrevive_bloque_spapi_roto`: M-m sin `_spapi_de` → 500.
- `test_muro_503_umbral_sella_http_5xx` + `test_vigilar_umbral_racha...`:
  M-l umbral sin `ultimo status` → sello `contrato`.
- `test_notifica_spapi_fallo_*` (4): M-k sin try/except → el builder roto
  levanta (la red rota la traga `_envia_texto`: por eso el discriminante es
  el builder, no el tumbo).

### Comandos y salidas (sin secretos)

- `uv run --frozen python -m pytest -q tests/test_spapi_salud.py` → `37 passed`.
- `uv run --frozen python -m pytest -q` → `2119 passed` (37 salud + 4 notifica
  nuevos + resto intacto; 1 fallo intermedio del builder sin canal, corregido:
  el test necesitaba canal configurado para llegar al builder).
- `ruff check app/ tests/` + `ruff format --check app/ tests/` → verde.
- `pre-commit run --all-files` → verde (abajo, antes del commit).
- Mutaciones: 15/15 en ROJO (M-a–M-o salvo M-f/M-g manuales, también rojos).

### Nota de rama

Al arrancar la ronda, el PR #246 ya estaba en MERGED (merge `f46722c` en
`origin/master`) — los commits van encima de `feat/sp-api-01-a5` sin rebase,
y el CI del PR ya no corre sobre esta rama: lo verde aquí es suite local +
pre-commit. El lead abre el PR que corresponda al cerrar A.5.
