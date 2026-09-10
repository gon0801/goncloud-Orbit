# Brief para Muse: SP-API 01 A.5 — salud en /salud y alertas

Base `origin/master` `20cc7b9` (A.1/A.2/A.2b/A.3/A.4 ya mergeadas; tras
`git fetch` usa el HEAD vigente de `origin/master`). UN PR
(`feat/sp-api-01-a5`), revisado por el lead antes de mergear. Trabajas en el
mismo checkout que el lead: **commitea antes de cambiar de rama** y nunca
saltes los candados de commit.

Contrato: fila A.5 de `plans/sp-api-01.md` y la seccion "A.5 · Salud y
alertas" de `plans/brief-sp-api-01-fase-a-muse.md`. Este brief no reabre
ninguna decision.

## Resultado de la tarea

`/salud` muestra la salud de las cuatro ingestas SP-API (`spapi_orders`,
`spapi_pricing`, `spapi_listings`, `spapi_inventario`) por plataforma, y un
fallo real llega por Telegram via `app/notifica.py`. **Un fallo de SP-API —
incluido el refresh LWA caido — jamas tumba el ciclo de Ads** (procesos y
credenciales distintos; test de aislamiento obligatorio). Es la ultima tarea
de codigo de la Fase A: despues vienen A.R (revision) y el deploy final
(A.6), que son del lead y del dueno.

## Decisiones ya cerradas (no reabrir)

D1 las cinco fuentes; D2 bridge manda en precio/stock; D4 cadencia diaria;
D5 un solo refrescador LWA en `app/spapi/`; D6 MX + US; D7 sin purga.

## Piezas que ya existen (reutilizar, jamas duplicar)

- `app/api_dashboard.py::salud` (l.809): devuelve `{"plataformas": {...}}`;
  cada plataforma ya tiene watermark, ultimo ciclo, historico, skips, quota.
  A.5 agrega la clave `spapi` a ese dict por plataforma — **sin rutas nuevas**:
  mismo endpoint (`app/ui.py::pagina_salud`, l.422), misma pagina
  (`app/templates/salud.html`).
- `ingest_run` (columnas `source, started_at, finished_at, rows_written,
  rows_skipped, skip_reason, ok, llamadas`): es la UNICA fuente de la salud
  y de las alertas (regla 2). Las cuatro ingestas ya sellan ahi cada corrida
  con su `source`.
- Sellos de fallo de las 4 ingestas (`orders.py:496`, `pricing.py:646`,
  `listings.py:415`, `inventario.py:374`): todas hacen `except BaseException`
  → `_sellar(ok=False, motivo=scrub(str(exc)) or type(exc).__name__)`. Hoy el
  motivo no distingue taxonomicamente LWA vs 429 vs red vs contrato.
- `app/notifica.py`: `_config_canal()` (Telegram, fail-silent si falta config)
  y `_envia_texto(texto, transport=None)` (l.166/205); funciones publicas con
  el patron a copiar: `notifica_encola` (l.559), `notifica_digest` (l.571),
  `notifica_harvest_failed` (l.596), `notifica_cap_agotado` (l.611). **No
  cambies ninguna existente ni el contrato de `notifica_*`**: solo agrega una
  funcion nueva con kwarg `transport` para tests.
- `app/spapi/client.py`: `SpapiError` (l.103), `SpapiNoPermitida`; el 429 lo
  reintenta el cliente UNA vez (espera acotada por `Retry-After`, tope 60 s)
  y si persiste propaga; un refresh LWA fallido se manifiesta como excepcion
  del cliente en la primera llamada. Un 429 que llega al sello YA es
  persistente (sobrevivio al reintento).
- `app/redaction.py` (`scrub`), `tests/test_architecture.py` (leelo completo
  antes de empezar: limites de lineas por modulo; `app/api_dashboard.py` ya
  es grande — la logica nueva vive en `app/spapi/salud.py` y el dashboard
  solo la llama).

## Alcance exacto

1. **`app/spapi/salud.py` (modulo nuevo)**: toda la logica.
   - `bloque_salud(conn, platform) -> dict`: por cada una de las 4 fuentes,
     lee de `ingest_run` la ultima corrida (started/finished, ok, filas,
     skips, skip_reason, llamadas), la ultima corrida con 429 y la ultima con
     fallo de LWA (fechas y motivo). Fuente sin corridas = entradas en None,
     nunca error (listings/inventario aun no corren en produccion).
   - `evaluar_alertas(conn, fuente, platform) -> None`: la llaman las 4
     ingestas justo despues de sellar el run. Lee las ultimas corridas de esa
     fuente+plataforma y dispara `notifica_spapi_fallo` SOLO en flanco:
     - **fallo sostenido**: la corrida recien sellada y la anterior ambas
       ok=false, y la anterior a esas no estaba en racha fallida (una alerta
       por racha, no una por corrida).
     - **429 persistente**: corrida sellada con motivo 429 (ya sobrevivio al
       reintento del cliente).
     - **refresh LWA fallido**: corrida sellada con motivo LWA; alerta
       inmediata con el matiz explicito de que el ciclo de Ads no se afecta.
   - `evaluar_alertas` es fail-silent: ninguna excepcion suya puede romper la
     ingesta que la llamo (envuelvela o demuestralo con test).
2. **Taxonomia del motivo (decision guiada, recomendada)**: para que salud y
   alertas clasifiquen sin parsear texto libre, las 4 ingestas sellan la rama
   `except` con prefijo taxonomico en el motivo: `lwa_fallido`, `http_429`,
   `http_5xx`, `red`, `contrato` (el detalle scrubbeado va despues del
   prefijo). Cambio minimo y localizado en la rama `except` de cada modulo,
   con su test por modulo. Si prefieres clasificar por el texto del motivo
   sin tocar las ingestas, justificalo en el PR; lo que NO vale es inventar
   una tabla nueva de estado.
3. **`app/api_dashboard.py::salud`**: agrega `"spapi": bloque_salud(conn,
   plataforma)` al dict de cada plataforma. Nada mas.
4. **`app/templates/salud.html`**: seccion SP-API por plataforma con la tabla
   por fuente (ultima corrida, ok, filas/skips, ultimo 429, ultimo LWA).
   Estilo de las secciones existentes; sin fetch nuevo.
5. **`app/notifica.py`**: agrega `notifica_spapi_fallo(fuente, platform,
   motivo, *, transport=None) -> bool` con el patron de las existentes
   (mensaje en espanol, redactado, fail-silent).
6. **`docs/DEPLOY.md`**: PROPUESTA de cron (no la instales): las 8 corridas
   (4 fuentes x 2 plataformas, o 4 si el CLI ya corre ambas en una pasada —
   verifica los flags reales de cada pipeline) con el patron existente
   `docker exec orbit-app-1 python -m app.cli ingest spapi_<fuente>
   --platform <p>`, entre 05:00 y 06:30 UTC, antes del sync de estructura de
   las 06:45. Deja margen para la duracion medida de pricing (~2 llamadas por
   ASIN).

## Reglas duras (identicas a las de A.1–A.4)

- Cero escrituras a Amazon. Cero `app.ads.write`. Cero `app/ads/*` tocado.
- **Test de aislamiento obligatorio**: con el refresh LWA caido
  (MockTransport), la ingesta sella ok=false, alerta, y termina limpia
  (`main` retorna codigo de error SIN excepcion no controlada); y el bloque
  spapi no toca nada del ciclo de Ads (ni importa `app.ads`).
- No inventes canal de alerta: solo `app/notifica.py` con su config actual.
- Alertas solo en flanco: dos corridas fallidas seguidas = una alerta; la
  tercera corrida fallida de la misma racha NO vuelve a alertar.
- **Sin migracion** (ninguna tabla ni columna nueva: todo se deriva de
  `ingest_run`). Si crees necesitar una, justificalo ANTES en el PR y recuerda
  que los GRANT de `orbit_ingest` son POR COLUMNA (bug de produccion 0033→
  0034): toda columna nueva lleva su GRANT explicito con assert negativo.
- Redaccion en todo log/error/mensaje; ningun secreto ni PII en Telegram.
- Cada regression se demuestra fallando contra el codigo anterior (regla 9).
  Los tests deben morder: el lead los muta.
- Ninguna corrida real contra produccion: valida con `httpx.MockTransport` y
  `ingest_run` sembrado en la base de test/CI.

## Tests minimos (todos en rojo primero)

1. `bloque_salud`: sin corridas (todo None, sin error), con corridas ok, con
   corridas fallidas de cada clase (429, LWA) — cada campo sale de
   `ingest_run` y de ningun otro lado.
2. Prefijo taxonomico: por cada una de las 4 ingestas, un fallo simulado de
   cada clase sella el motivo con su prefijo (el test mata la mutacion de
   quitar el prefijo).
3. Flanco: dos corridas seguidas fallidas = exactamente una alerta; la
   tercera de la racha = cero alertas nuevas; una corrida ok rompe la racha.
4. 429 persistente y LWA: alerta en la primera corrida sellada con ese
   motivo; el mensaje de LWA dice que el ciclo de Ads sigue.
5. Aislamiento Ads: LWA caido en la ingesta → run ok=false + alerta +
   retorno limpio; `app/spapi/salud.py` e ingestas no importan `app.ads`.
6. `/salud`: la pagina responde 200 con el bloque spapi renderizado (caso con
   corridas y caso sin corridas).
7. `evaluar_alertas` fail-silent: con `notifica` roto (transport que
   revienta), la ingesta termina y sella igual.

## Evidencia y cierre

`docs/evidencia/sp-api-01/A.5/reporte.md` con: comandos corridos, salidas de
tests, captura textual del bloque spapi de `/salud` con datos sembrados, y el
resultado de las mutaciones del lead. **No marques filas del plan ni toques
`plans/ROADMAP.md`/`plans/manifest.json`/`docs/CHAT-CONTEXT.md`: el lead
cierra A.5 tras la review.** A.R y el deploy final (A.6: migracion 0035 +
cron) son del lead y del dueno: no los adelantes.

## Entrega del PR

Rama `feat/sp-api-01-a5` desde `origin/master`; commits normales;
`pre-commit run --all-files` verde; PR a `master` con CI (suite completa). En
la descripcion: que muestra `/salud` de nuevo, cuando alerta y cuando NO,
que prefijos de motivo nacen, y que NO toca (`app/ads/*`, `notifica_*`
existentes, esquema, cron instalado, produccion). Sin ssh de escritura, sin
produccion, sin tracker.
