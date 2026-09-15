# Brief para Muse: vigilante del cron SP-API (aviso del silencio)

Base `origin/master` tras `git fetch` (HEAD vigente; este brief nació sobre
`41bc6aa`). Rama desde el remoto, **jamás** desde tu master local; `git log
origin/master..HEAD` solo con los commits de esta tarea. **Una sola tarea: el
vigilante.** No toca F2, no toca las ingestas, no toca el wrapper existente.
Se toma **después** de cerrar el PR de R.1 (trabajas en un solo checkout).

Contrato: `docs/DEPLOY.md` §«Ingestas SP-API diarias» (wrapper
`/mnt/data/appdata/orbit/spapi-diario.sh`, una línea de crontab con `flock`
a las 05:00 UTC, `job_key=spapi:diario`) y `app/spapi/salud.py` (regla 2:
**`ingest_run` es la única fuente** de salud y alertas; `evaluar_alertas`
es fail-silent). Este brief es ejecutable y subordinado a esos documentos.

## El hueco que se tapa

Hoy, si una ingesta **falla**, ella misma sella su `ingest_run` y
`evaluar_alertas` avisa por Telegram (una vez por racha). Pero si el cron
**no dispara** (crontab pisado, `flock` atorado con la corrida de ayer,
servidor reiniciado en la ventana, contenedor caído), no hay fila que fallar
y nadie se entera: el silencio no avisa. El vigilante convierte el silencio
en una fila faltante y avisa.

## Resultado exacto

1. **`app/spapi/vigilante.py`** (nuevo, delgado). Una función pura
   `faltantes(filas, *, desde, hasta) -> list[tuple[str, str]]` que recibe
   las filas de `ingest_run` de la ventana y devuelve los pares
   `(source, platform)` que **no tienen** una corrida con `started_at` en
   `[desde, hasta)` **y** `finished_at IS NOT NULL`, para el producto
   cartesiano `FUENTES_SPAPI × PLATAFORMAS_SPAPI` (ocho pares;
   `PLATAFORMAS_SPAPI = ("amazon_mx", "amazon_us")` vive aquí como
   constante nombrada, con comentario de que el wrapper es la fuente del
   número ocho). Una corrida con `ok = false` **cuenta como presente**: ya
   avisó por su camino; el vigilante no re-avisa fallos, solo ausencias.
   Una corrida empezada y no terminada (`finished_at IS NULL`) cuenta como
   **ausente**: colgada es silencio.
2. **Lector** `lee_ventana(conn, *, desde, hasta)` con un solo SELECT sobre
   `ingest_run` (`source, platform, started_at, finished_at, ok`) filtrado
   por `source = ANY(FUENTES_SPAPI)` y `started_at >= desde AND started_at
   < hasta`. Rol de lectura (`ORBIT_DSN_READ`), nada más.
3. **Aviso**: en `app/notifica.py`, builder puro
   `aviso_spapi_silencio(faltantes, desde, hasta) -> str` con este texto
   literal (una línea por par faltante, orden fuente→plataforma estable):

   ```text
   [Orbit] ALERTA SP-API sin corrida
   ventana: 2026-09-16 04:30 UTC → 2026-09-16 07:30 UTC
   faltan 3 de 8:
   - spapi_pricing / amazon_mx
   - spapi_pricing / amazon_us
   - spapi_inventario / amazon_us
   El cron de las 05:00 UTC no dejó estas corridas en ingest_run. Revisar crontab de gon, flock y el log spapi-diario.log.
   ```

   y sender `notifica_spapi_silencio(faltantes, desde, hasta, *,
   transport=None) -> bool` con **el mismo contrato fail-silent** de
   `notifica_spapi_fallo` (canal inactivo → `True`; excepción → warning con
   scrub y `False`; jamás levanta). Cuando no falta nada **no se envía
   nada**: el vigilante es silencioso en verde.
4. **Fallo de lectura también avisa**: si el SELECT revienta (DB caída,
   DSN roto), el vigilante manda un aviso distinto,
   `aviso_spapi_vigilante_ciego(motivo)`:

   ```text
   [Orbit] ALERTA vigilante SP-API sin lectura
   no pude leer ingest_run: <motivo con scrub>
   No sé si el cron corrió. Revisar Postgres y el DSN de lectura.
   ```

5. **CLI**: pipeline `spapi_vigilante` en `app/cli.py`, registrado igual que
   `spapi_orders` y compañía. Flags: `--desde` y `--hasta` (ISO UTC;
   default: hoy 04:30 UTC y ahora), `--dry-run` (evalúa e imprime, no
   envía). Salida a stdout en una línea por par (`presente`/`ausente`) y un
   resumen `faltan N de 8`. Exit code: `0` sin faltantes, `1` con
   faltantes (después de intentar el aviso), `2` si no pudo leer (después
   de intentar el aviso ciego). En `--dry-run` los mismos códigos, sin
   envío. `desde` debe ser estrictamente menor que `hasta`: un intervalo
   invertido o vacío se rechaza con exit 2 **antes** de abrir la base y sin
   enviar nada (un aviso por ventana inválida sería falso).
6. **Ops en `docs/DEPLOY.md`** (nueva subsección «Vigilante SP-API 07:30»
   dentro de «Crons de Orbit», aditiva): la línea exacta de crontab, con
   `flock` y log, `job_key=spapi:vigilante`, que **no contenga
   `app.cli ingest`** para que el instalador de ORBIT 03 no la borre
   (hallazgo H1 de sp-api-01 A.R):

   ```cron
   # job_key=spapi:vigilante  aviso si el cron spapi:diario no dejó sus 8 corridas
   30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1 python -m app.cli spapi_vigilante >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1
   ```

   Más el runbook de instalación y **la prueba del silencio**, que corre el
   dueño con `!`: (a) `--dry-run` sobre la ventana de hoy debe decir
   `faltan 0 de 8` y exit 0; (b) una ventana pasada donde no existió ninguna
   corrida SP-API (por ejemplo `--desde 2026-01-01T04:30:00Z --hasta
   2026-01-01T07:30:00Z`) debe listar 8 ausentes, mandar el aviso real por
   Telegram y salir con 1; (c) tras instalar la línea, `crontab -l | grep
   spapi:vigilante` la muestra y sigue ahí después de re-correr el
   instalador de ORBIT 03. La reversa es borrar la línea del crontab: el
   vigilante no escribe nada en la base.

## Antes de escribir una línea

Lee y anota el SHA en el PR: `app/spapi/salud.py` completo (contrato de
`ingest_run`, `FUENTES_SPAPI`, fail-silent), `app/notifica.py`
(`notifica_spapi_fallo`, `_envia_texto`, `canal_activo`), `app/cli.py`
(cómo se registra un pipeline `spapi_*` y cómo se resuelve el DSN de
lectura), `docs/DEPLOY.md` §«Crons de Orbit» y §«Ingestas SP-API diarias»,
`tests/test_notifica.py` y el test existente de `app/spapi/salud.py` como
modelo de fixtures. Baseline con DSN real antes del primer rojo:

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_notifica.py tests/test_spapi_salud.py tests/test_architecture.py
```

## Archivos y fronteras

Puedes tocar solo: `app/spapi/vigilante.py` (nuevo), `app/notifica.py`
(**aditivo**: dos builders y un sender nuevos; los `notifica_*` existentes
intactos), `app/cli.py` (registro del pipeline, aditivo), `docs/DEPLOY.md`
(subsección nueva, aditiva), `tests/test_spapi_vigilante.py` (nuevo),
`tests/test_notifica.py` y `tests/test_architecture.py` (aditivos).

Prohibido: `app/spapi/salud.py` y las cuatro ingestas, el wrapper
`spapi-diario.sh` y su línea de crontab, `migrations/`, `app/ads/*`,
cualquier cosa de F2. Sin migración: el vigilante **solo lee**. Sin canal
nuevo: reusa `_envia_texto`. Aislamiento Ads igual que `salud.py`: cero
import directo de `app.ads` (el candado de arquitectura lo verifica; agrega
`app/spapi/vigilante.py` a la misma regla que `salud.py`).

**Cero producción, cero SSH, cero secretos, cero Amazon. Prohibido tocar el
contenedor de producción y el crontab del servidor: la instalación y la
prueba del silencio las corre el dueño con `!`, siguiendo tu runbook.**

## Orden TDD obligatorio

1. `faltantes` pura, rojo-primero: ocho presentes → `[]`; una fuente sin
   fila → ese par; fila con `ok=false` → presente; fila con
   `finished_at=None` → ausente; fila fuera de la ventana (ayer) → ausente;
   fila de una `source` ajena (`amazon_ads_structure_v2`) → ignorada;
   plataforma `NULL` (corridas pre-A.5) → ignorada, jamás error.
2. Builders puros con **igualdad exacta** del texto (no `in`), incluido el
   orden de los pares y el `faltan N de 8`.
3. Sender: canal inactivo → `True` sin llamada; transport que responde
   `ok=false` → `False`; transport que levanta → `False` y warning con
   scrub; **nunca** levanta.
4. Validación de la ventana: `desde >= hasta` → exit 2 sin SELECT ni envío
   (spy en el sender y en el lector).
5. Lector con DSN real: fixture siembra `ingest_run` con las combinaciones
   del paso 1 y verifica el SELECT (y que el rol de lectura basta).
6. CLI: `--dry-run` no llama al sender (spy); exit codes 0/1/2; `--desde`
   futuro → 8 ausentes; DB rota (DSN inválido) → aviso ciego y exit 2.
7. `docs/DEPLOY.md` al final, con el texto de la línea de crontab copiado
   de la constante del test que la valida (un test que la línea de la doc
   no contiene `app.cli ingest` y sí `spapi:vigilante`, `flock` y el log).

## Mutantes que deben morir

- contar `ok=false` como ausente (re-avisar fallos);
- contar `finished_at IS NULL` como presente;
- ventana con `<=` en `hasta` o sin `desde` (contar ayer); aceptar `desde >= hasta`;
- olvidar una plataforma (7 pares) o una fuente;
- enviar también cuando no falta nada;
- `--dry-run` que envía;
- exit 0 con faltantes; exit 0 con DB rota; no avisar con DB rota;
- sender que levanta en vez de devolver `False`;
- texto del aviso sin la lista de pares o con orden no determinista;
- línea de crontab que contenga `app.cli ingest` o sin `flock`.

## DoD binario

1. `python -m app.cli spapi_vigilante --dry-run` sobre el fixture imprime
   los ocho pares y `faltan N de 8` con el exit code correcto.
2. `faltantes`, los builders y el sender tienen tests de igualdad exacta y
   los mutantes de arriba mueren (documentados en el PR con su rojo).
3. `notifica_*` existentes intactos (diff de `app/notifica.py` solo agrega).
4. Candado de arquitectura verde y ampliado al módulo nuevo.
5. `docs/DEPLOY.md` con la subsección, la línea de crontab y la prueba del
   silencio en tres pasos.
6. Ruff, `pre-commit run --all-files` y la batería completa verdes **una
   sola vez en CI** sobre el SHA final.

## Verificación y entrega

```bash
ORBIT_TEST_DSN=<dsn-test> uv run --frozen python -m pytest -q \
  tests/test_spapi_vigilante.py tests/test_notifica.py tests/test_architecture.py
uv run --frozen ruff check app/spapi/vigilante.py app/notifica.py app/cli.py tests/
uv run --frozen ruff format --check app/spapi/vigilante.py app/notifica.py app/cli.py tests/
pre-commit run --all-files
pre-commit run --hook-stage pre-push   # el push normal también lo ejecuta; jamás --no-verify
```

PR a `master` desde `origin/master`, carril **gate**, **en cola**: no se
mergea hasta el APPROVE del lead; el dueño mergea al final del día en el
orden que el lead le pase, y la instalación es posterior al merge.

La descripción del PR incluye: SHA base, baseline literal, rojos por bloque,
salida literal del `--dry-run` sobre el fixture (verde y con ausentes), el
texto literal de los dos avisos, mutantes con su rojo, el diff de
`app/notifica.py` mostrando que solo agrega, la línea de crontab final, y el
enlace al CI verde. Residuales a declarar: el vigilante no verifica el
contenido de las corridas (eso es `salud.py`), y una corrida presente pero
con `rows_written = 0` no es silencio.

Loop de cross-review, sin tope de rondas: **grok** revisa sobre tu SHA (es
infra: crontab, flock, log, instalador de ORBIT 03); lo que encuentre lo
corriges en el mismo PR y grok vuelve; cuando grok sale limpio entra el
lead; si el lead encuentra algo, vuelve a ti y grok lo vuelve a ver. Solo el
APPROVE del lead cierra el loop.
