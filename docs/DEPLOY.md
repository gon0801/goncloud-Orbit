# DEPLOY — Orbit en goncloud (Postgres 16 + app)

> Runbook de operación de la base viva y del servicio `app` (API + CLI).
> Responde la pregunta de reconstrucción: **¿cómo levanto esto desde
> cero?** (ver "Reconstruir desde cero" y "Recuperación desde backups"
> al final — el de la base se verificó en vivo; el de la app se verificó
> en 4.1 con `curl /health` y `ss -lntp`).

## Dónde vive

- **Servidor:** `goncloud` (acceso por `ssh goncloud`), junto a `bridge` y
  `accounting`, como manda `docs/CONTEXTO.md`.
- **Dir de deploy:** `/mnt/data/appdata/orbit/`
  - `docker-compose.yml` — copia del repo (fuente de verdad: el repo).
  - `Dockerfile`, `pyproject.toml`, `uv.lock`, `app/`,
    `tools/fabrica_campanas.py` — contexto de build
    de la imagen `app` (se copian del repo; ver "Servicio app").
  - `.env` — `POSTGRES_USER=orbit` + `POSTGRES_PASSWORD` + los DSN por
    servicio (incluido `ORBIT_DSN_TEST`). Permisos `600`, **nunca se
    commitea** (está en `.gitignore`).
  - `secrets/` — credenciales (LWA, etc.). Dir `700`, archivos `600`,
    dueño **uid 10001** (4.1: ya no root — el contenedor `app` corre como
    `user: "10001:10001"` y los lee sin relajar permisos). **Jamás relajar
    estos permisos.**
  - `backups/` — dumps diarios (`700`; dumps `600`).
  - `backup.sh` + cron — ver "Backups".
  - `logs/` — stdout de los crons de Orbit (escribible por `gon`).
- **Contenedores:** `orbit-db-1` (imagen `postgres:16`, volumen
  `orbit_pgdata`) y `orbit-app-1` (imagen construida del repo con
  `uv sync --frozen`).
- **Red:** bind `127.0.0.1:5432` (Postgres) y `127.0.0.1:8010` +
  `10.13.13.1:8010` (app; la segunda es la IP de la interfaz WireGuard
  `wg0` — DASHBOARD 01 / 2.1: el dashboard se ve del cel/compu por VPN).
  Nunca `0.0.0.0` (lección de los puertos 8055/8056, 7 semanas
  expuestos); la allowlist EXACTA de hosts la sella
  `tests/test_compose_deploy.py`. `10.13.13.1` es RFC1918 point-to-point:
  solo el host y los peers cifrados del túnel llegan (INPUT policy DROP +
  CrowdSec, cero DNAT — evidencia en ORBIT 16). El acceso remoto a la
  base sigue siendo por túnel SSH.

## Levantar / parar

```bash
ssh goncloud
cd /mnt/data/appdata/orbit
docker compose up -d        # levanta db+app (o nada si ya están)
docker compose ps           # db: 127.0.0.1:5432->5432 ; app: 127.0.0.1:8010->8000 y 10.13.13.1:8010->8000
docker exec orbit-db-1 pg_isready -U orbit   # accepting connections
curl -sS http://127.0.0.1:8010/health        # {"status":"ok"}
```

`docker compose up -d --no-deps --build app` construye/recrea SOLO la
app. **`--no-deps` es obligatorio:** un `up --build app` sin él recreó
`orbit-db-1` en 4.1 (el volumen `orbit_pgdata` persistió — 5,897
entidades intactas — pero hubo ~1 s de downtime). Verificar después
que el container id de `orbit-db-1` no cambió. El compose en el server
debe ser LF (CRLF de un scp desde Windows cuenta como cambio de
config y dispara recreate).

## Servicio app (API + CLI)

Imagen construida **en el server** desde el repo, con el lockfile
pinneado. Uvicorn sirve `app.main:app` en el puerto 8000 *dentro* del
contenedor; compose publica `127.0.0.1:8010:8000` (8010 se verificó
libre con `ss -lntp` el 2026-08-23; si un día está ocupado, elegir otro
loopback y documentarlo aquí — jamás caer a `0.0.0.0`).

**Por qué `ORBIT_PG_HOST=db`:** los DSN del `.env` apuntan a
`127.0.0.1:5432` (bind del host, para psql/túnel/backup). Dentro del
contenedor ese address es el propio `app`, no Postgres. El rewrite en
`app.db.connect` (`aplicar_host_override`) sustituye el loopback por el
nombre del servicio compose `db`. En el host y en CI la var no existe y
el DSN no se toca. El `.env` no se duplica ni se reescribe.

**Por qué `user: "10001:10001"` (4.1, non-root):** antes la app corría como
`0:0` porque `secrets/` era `root:root 0600`. Resuelto en el server SIN
relajar permisos: los archivos pasaron a uid **10001** (mismos `600`, dir
`700`) y el contenedor corre con ese uid. Ceremonia (una vez, como root):

```bash
chown -R 10001:10001 /mnt/data/appdata/orbit/secrets
stat -c '%a %u:%g %n' /mnt/data/appdata/orbit/secrets \
  /mnt/data/appdata/orbit/secrets/*   # dir 700, archivos 600, todo 10001
```

Si un secret NUEVO se crea como root (p. ej. al rotar el token), hay que
darle el uid: `chown 10001:10001 secrets/<archivo>` tras escribirlo (el
chmod 600 se mantiene). El mount es `:ro` (el contenedor no puede escribir
ni relajar permisos) — **excepción**: desde REPUTACION 01/A.7 es `:rw`
para que el refresh OAuth MeLi persista `meli_tokens.json` (MeLi rota el
refresh_token; con `:ro` el cron moriría al 2º día). Cambio autorizado por
el dueño con respaldo y reversa (ver sección Reputación v1). El invariante
"jamás relajar permisos de archivos" sigue intacto (700/600, uid 10001).

**Env por servicio (4.1):** ni `db` ni `app` declaran `env_file: .env`
(heredaban TODO). `db` recibe solo `POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` y `app` recibe solo los 4 DSN de servicio
(`INGEST`/`DECIDE`/`READ`/`ADMIN`), todo por interpolación del `.env`
(compose lo lee al parsear; el archivo sigue `600` root). `ORBIT_DSN_TEST`
NO entra a ningún contenedor: su rol tiene `ADMIN OPTION` sobre `app_*`
(escritura en prod) y solo lo usa la suite local por túnel.

**Qué se monta:** SOLO `secrets/` (`:rw` desde A.7 por refresh OAuth
MeLi; era read-only — ver excepción arriba). Ni backups, ni `.env`
como archivo (los DSN llegan por interpolación). `.dockerignore`
excluye `.env` y `secrets/` del contexto de build: no entran a la imagen.
El mismo contenedor corre API (`ORBIT_DSN_READ` + `ORBIT_DSN_ADMIN` para
veto/reversas con token) y CLI (`INGEST`/`DECIDE`); el bind es loopback.

> **RESUELTO (ORBIT 04 4.1):** el camino `/reversa/*` escribe filas en el
> ledger `apply_attempt`, cuyo `GRANT INSERT` en 0002 es SOLO de
> `app_decide`. El `orbit_admin` original (solo `app_admin`) podía VETAR
> pero NO revertir. Wiring cerrado en vivo 2026-08-27:
> `GRANT app_decide TO orbit_admin;` (el admin hereda el INSERT del ledger;
> verificado con `pg_has_role`). El GRANT además quedó DENTRO del script de
> creación de usuarios de abajo (P1 Greptile PR #36: si solo existe como
> operación viva, una instalación reconstruida vetaría pero no revertiría).

Reconstruir/actualizar **solo la app** (Postgres intacto):

```bash
ssh goncloud
cd /mnt/data/appdata/orbit
# 1) COPIAR EL CODIGO PRIMERO — el paso que se olvida y falla EN SILENCIO.
#    El server NO es un checkout de git: `--build` construye lo que hay en
#    /mnt/data/appdata/orbit/app, no lo que hay en master. Sin copiar,
#    reconstruye el codigo VIEJO y no avisa. Desde el repo, en la maquina
#    del lead (git archive, no scp: garantiza finales de linea LF):
#
#      git archive --format=tar origin/master app Dockerfile .dockerignore \
#        pyproject.toml uv.lock tools/fabrica_campanas.py \
#        | ssh goncloud "cd /mnt/data/appdata/orbit && tar -xf -"
#
#    OJO: `origin/master`, NO `master` — con el checkout en otra rama el ref
#    local queda viejo (paso el 2026-08-30: se copio un master de 12 h antes).
#    Antes de construir, verificar que los .py del server son IDENTICOS a
#    origin/master por md5, y respaldar el app/ anterior (app.bak-predeploy-<fecha>).
# 2) Construir y recrear:
docker compose up -d --no-deps --build app
# COMO SABER SI EL DEPLOY HIZO ALGO (2026-08-30): si el paso `COPY app ./app`
# sale `CACHED` y el contenedor dice `Running` en vez de `Recreated`, la
# imagen NO cambio: te falto copiar el codigo. Un deploy real dice
# `Recreated` y el digest de la imagen es distinto al anterior.
curl -sS http://127.0.0.1:8010/health
ss -lntp | grep 8010    # debe decir 127.0.0.1:8010, NUNCA *:8010
# secrets/ sin tocar:
stat -c '%a %U:%G %n' /mnt/data/appdata/orbit/secrets \
  /mnt/data/appdata/orbit/secrets/*
```

### Crear campanas desde el dashboard

En **Campañas → Crear campañas** (`/campanas/nuevas`) se elige Amazon MX o US,
los productos elegibles y los presupuestos y pujas de las cinco campanas del grupo.
**Pujas sugeridas por Amazon** hace un POST de lectura al endpoint v4 de
recomendaciones y rellena solo los roles con respuesta completa. Si Amazon
omite un target, ese rol queda para captura manual; no hay fallback inventado.
**Revisar plan** consulta los datos de Orbit sin escribir en Amazon y firma la
fuente y las ternas de recomendacion junto con el resto del plan.
La revision muestra el target calculado, su procedencia, semillas y presupuesto
diario total en la moneda de la plataforma.

Crear exige el mismo token de escritura de Settings y la confirmacion literal
`CREAR 5 CAMPAÑAS`. El token viaja solo en `x-orbit-token` y no se guarda en el
navegador. Las campanas nacen **activas** y pueden gastar: `shadow` significa que
el optimizador observa sus ajustes, no que la campana de Amazon esta pausada.

El historial permite consultar el lote despues de recargar o perder la conexion.
Un segundo envio del mismo plan devuelve el lote ya registrado sin repetir POSTs
de creacion. Si hubo un resultado incierto, revisar el detalle y reconciliar antes
de completar el registro; **Pausar grupo** pausa las campanas con ID conocido.
Los resultados inciertos sin ID requieren verificar la consola de Amazon.

La API web reutiliza `tools/fabrica_campanas.py`, incluido en la imagen: al copiar
el build se debe incluir ese archivo, ademas de `app/`. No requiere nuevas
credenciales ni migracion. La validacion de deploy puede usar health, paginas,
catalogo y previsualizacion; nunca crear campanas automaticamente como smoke test.
La sonda real del motor de fabrica sigue pendiente por decision del dueno.

CLI (el mismo camino que el cron; `exec` hereda el env del contenedor,
incluido `ORBIT_PG_HOST` y los DSN):

```bash
cd /mnt/data/appdata/orbit
docker compose exec -T app python -m app.cli ingest structure
docker compose exec -T app python -m app.cli ingest metrics \
  --fecha "$(date -u -d '31 days ago' +%F)" \
  --fecha-fin "$(date -u -d '1 day ago' +%F)"
docker compose exec -T app python -m app.cli cycle --platform amazon_us
docker compose exec -T app python -m app.cli cycle --platform amazon_mx
```

`up --build` se corre como **root** (`.env` es `600` root, y **se queda
así**). El crontab de `gon` no puede `docker compose exec` porque compose
abre el `.env` al parsear el proyecto (interpolación de los DSN y de
`POSTGRES_*`) y `gon` no puede leerlo. `gon` está en el grupo `docker`: el
cron usa `docker exec orbit-app-1`, el mismo contenedor, el mismo env. Un
`compose exec` manual como root sí funciona.

## Ingesta de costos desde contabilidad (ORBIT 06 0.1)

El contenedor NO monta la base de contabilidad (contrato: SOLO `secrets/`) y
la base está en modo WAL: el pipeline lee un **snapshot** producido con la API
`.backup()` de SQLite (consistente con WAL; un `cp` directo deja fuera el WAL —
lección pagada). Acceso a contabilidad **read-only por construcción**: el
pipeline abre el snapshot con `mode=ro` y solo hace SELECT. Decisiones y
mediciones: `plans/orbit-06.md` §Decisiones de la 0.1.

```bash
ssh goncloud
# 1) snapshot de la SQLite de contabilidad (host, stdlib, no toca la original).
#    chmod 644: la app corre como UID 10001 y docker cp conserva el uid del
#    archivo; un 600 de root dejaria el snapshot ilegible dentro (codex, ronda 1).
python3 -c "import sqlite3; src=sqlite3.connect('file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro', uri=True); dst=sqlite3.connect('/tmp/accounting-snapshot.db'); src.backup(dst); dst.close(); src.close()" \
  && chmod 644 /tmp/accounting-snapshot.db
# 2) meter el snapshot al contenedor y correr la ingesta (mismo camino que el
#    cron: docker exec, no compose exec — el .env del proyecto es 600 root)
docker cp /tmp/accounting-snapshot.db orbit-app-1:/tmp/accounting-snapshot.db
docker exec orbit-app-1 python -m app.cli ingest costs --sqlite /tmp/accounting-snapshot.db
# 3) limpieza del snapshot (host y contenedor). En el contenedor con -u 0:
#    /tmp tiene sticky bit y el archivo llega owner=root (docker cp conserva
#    el uid numerico), asi que el 10001 de la app no puede borrarlo.
rm /tmp/accounting-snapshot.db
docker exec -u 0 orbit-app-1 rm /tmp/accounting-snapshot.db
```

**Cadencia: DIARIA, decidida por el dueño el 2026-08-30 y ya agendada.** El
runbook de arriba vive en `/mnt/data/appdata/orbit/refresh_costos.sh` (los 4
pasos con `trap` de limpieza) y corre por el crontab de `gon`:

```
15 8 * * * /mnt/data/appdata/orbit/refresh_costos.sh >> /mnt/data/appdata/orbit/logs/costos.log 2>&1
```

Las **08:15 UTC** caen después de los dos syncs de accounting
(`sync_ads_to_ledger.py` 06:30, `sync_fx_rates.py` 08:00) y 25 min antes de
los ciclos (08:40/08:41): costos, FX y ledger llegan frescos —del MISMO
día— antes de que el motor decida. Hasta la review del PR #144 corría a las
07:30, media hora ANTES del sync de FX: ver §Refresco diario contable.

Por qué diaria y no semanal, **medido**: los costos rotan poco —15 días con
cambios en 6.5 meses, casi siempre de 1 a 6 SKUs— pero el **2026-08-18
cambiaron 937 de golpe**. Con cadencia semanal, un evento así deja cada
número de margen mal hasta 6 días. Y correr la ingesta sin cambios es
**no-op** (verificado: corridas 30-34 con `rows_written=0`), así que el costo
de correrla a diario es despreciable. Contabilidad ya se sincroniza con Odoo
cada hora, así que el snapshot siempre trae el dato fresco.

## Ingesta de listings desde el bridge (ORBIT 06 0.2)

Mismo patrón de snapshot que los costos, contra la SQLite del **bridge**
(tercera fuente; el contenedor tampoco la ve). Fuente:
`amazon_listing_prices` + `amazon_sku_mapping`; el puente SKU↔Odoo es
`amazon_sku_mapping` — unir por texto de SKU está PROHIBIDO (ver
`plans/orbit-06.md` §Obstáculos de la 0.2 y §Decisiones de la 0.2).

```bash
ssh goncloud
# 1) snapshot del bridge (misma API .backup(); chmod 644 por el UID 10001 de la app)
python3 -c "import sqlite3; src=sqlite3.connect('file:/mnt/data/appdata/bridge/data/bridge.db?mode=ro', uri=True); dst=sqlite3.connect('/tmp/bridge-snapshot.db'); src.backup(dst); dst.close(); src.close()" \
  && chmod 644 /tmp/bridge-snapshot.db
# 2) al contenedor y correr (mismo camino que el cron)
docker cp /tmp/bridge-snapshot.db orbit-app-1:/tmp/bridge-snapshot.db
docker exec orbit-app-1 python -m app.cli ingest listings --sqlite /tmp/bridge-snapshot.db
# 3) limpieza (host y contenedor con -u 0: sticky bit + uid numerico de docker cp)
rm /tmp/bridge-snapshot.db
docker exec -u 0 orbit-app-1 rm /tmp/bridge-snapshot.db
```

## Ingesta de estimación (MARGEN ESTIMADO A.3)

Snapshot **del bridge** (mismo patrón `.backup()` que listings). Fuente:
`amazon_listing_prices`; destino: `estimacion_oferta_observation` +
`estimacion_fee_observation` vía Product Fees SP-API (consulta, universo FBA MX).
No toca bridge ni accounting.

```bash
ssh goncloud
# 1) snapshot del bridge (consistente con WAL)
python3 -c "import sqlite3; src=sqlite3.connect('file:/mnt/data/appdata/bridge/data/bridge.db?mode=ro', uri=True); dst=sqlite3.connect('/tmp/bridge-estimacion-snapshot.db'); src.backup(dst); dst.close(); src.close()" \
  && chmod 644 /tmp/bridge-estimacion-snapshot.db
# 2) al contenedor y correr
docker cp /tmp/bridge-estimacion-snapshot.db orbit-app-1:/tmp/bridge-estimacion-snapshot.db
docker exec orbit-app-1 python -m app.cli ingest estimacion --sqlite /tmp/bridge-estimacion-snapshot.db
# 3) limpieza
rm /tmp/bridge-estimacion-snapshot.db
docker exec -u 0 orbit-app-1 rm /tmp/bridge-estimacion-snapshot.db
```

Cadencia recomendada: **`refresh_estimacion.sh`** cada 6 h (:45), diez minutos
después del refresco acreditado del bridge a :35, con snapshot único por corrida. Copia desplegada:
`/mnt/data/appdata/orbit/refresh_estimacion.sh` (origen: `tools/refresh_estimacion.sh`).

Cron sugerido (usuario `gon`):

```cron
45 */6 * * * /mnt/data/appdata/orbit/refresh_estimacion.sh >> /mnt/data/appdata/orbit/logs/estimacion.log 2>&1
```

Re-correr con el mismo evento fuente es no-op (dedupe por `source_event_id`).
Cada listing falla aislado; la corrida sella `ingest_run` con filas/errores.

## Ingesta de tipos de cambio (ORBIT 06 0.5)

Misma SQLite de contabilidad y mismo runbook de snapshot que los costos.
Fuente: `currency_rates`. Destino: `fx_rate` (append-only). **Las etiquetas
de la fuente están invertidas** respecto al valor: `(MXN, USD, ~17)` significa
pesos por dólar; la ingesta escribe `(USD, MXN, ~17)` para que
`fx_resolve(fecha,'USD','MXN')` multiplique bien. Decisiones:
`plans/orbit-06.md` §Decisiones de la 0.5. `fx_resolve` no se toca.

```bash
ssh goncloud
# 1) snapshot de contabilidad (idéntico al de costos; se puede reutilizar
#    /tmp/accounting-snapshot.db si acaba de generarse)
python3 -c "import sqlite3; src=sqlite3.connect('file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro', uri=True); dst=sqlite3.connect('/tmp/accounting-snapshot.db'); src.backup(dst); dst.close(); src.close()" \
  && chmod 644 /tmp/accounting-snapshot.db
# 2) al contenedor y correr
docker cp /tmp/accounting-snapshot.db orbit-app-1:/tmp/accounting-snapshot.db
docker exec orbit-app-1 python -m app.cli ingest fx --sqlite /tmp/accounting-snapshot.db
# 3) limpieza
rm /tmp/accounting-snapshot.db
docker exec -u 0 orbit-app-1 rm /tmp/accounting-snapshot.db
```

Cadencia: **DIARIA desde ORBIT 06 2.2 (2026-09-04)** — `refresh_costos.sh`
(08:15 UTC) corre `costs` + `fx` + `ledger` del MISMO snapshot, cada uno a
su log (`costos.log`/`fx.log`/`ledger.log`), sin tumbarse entre si. Re-correr
es no-op por PK.

## Ingesta del ledger desde contabilidad (ORBIT 06 0.6)

Misma SQLite de contabilidad y mismo runbook de snapshot que los costos.
Fuente: `ledger_events`. Destino: `ledger_event` (append-only, tres índices
de dedupe). MeLi se excluye contada; `amazon` se renombra a `amazon_mx`.
El ISR sin `order_id` **entra** (no se prorratea en 0.6). Un fee positivo
(reversa) **no se voltea**: se salta por `ledger_convencion_signos`.
Decisiones: `plans/orbit-06.md` §Decisiones de la 0.6.

```bash
ssh goncloud
# 1) snapshot de contabilidad (idéntico al de costos; se puede reutilizar
#    /tmp/accounting-snapshot.db si acaba de generarse)
python3 -c "import sqlite3; src=sqlite3.connect('file:/mnt/data/appdata/accounting/data/accounting.db?mode=ro', uri=True); dst=sqlite3.connect('/tmp/accounting-snapshot.db'); src.backup(dst); dst.close(); src.close()" \
  && chmod 644 /tmp/accounting-snapshot.db
# 2) al contenedor y correr
#    ORBIT_DSN_INGEST del contenedor apunta a 127.0.0.1 (bind del host);
#    dentro de la red compose el host de Postgres es `db`. Reescribirlo
#    al vuelo (mismo truco que cualquier ingest vía docker exec).
docker cp /tmp/accounting-snapshot.db orbit-app-1:/tmp/accounting-snapshot.db
docker exec -e ORBIT_DSN_INGEST="$(docker exec orbit-app-1 printenv ORBIT_DSN_INGEST | sed 's/@127.0.0.1:/@db:/')" \
  orbit-app-1 python -m app.cli ingest ledger --sqlite /tmp/accounting-snapshot.db
# 3) limpieza
rm /tmp/accounting-snapshot.db
docker exec -u 0 orbit-app-1 rm /tmp/accounting-snapshot.db
```

Cadencia: **DIARIA desde ORBIT 06 2.2 (2026-09-04)** — `refresh_costos.sh`
(08:15 UTC) corre `costs` + `fx` + `ledger` del MISMO snapshot, cada uno a
su log (`costos.log`/`fx.log`/`ledger.log`), sin tumbarse entre si.
Re-correr es no-op por los tres índices de dedupe (`rows_written=0`,
conflictos contados en `rows_skipped`). En el cron NO se reescribe el DSN:
`app/db.py` ya mapea `@127.0.0.1:` → `@db:` con `ORBIT_PG_HOST`; el truco
`-e ORBIT_DSN_INGEST=...` de arriba queda como alternativa manual.
Alternativa sin reescribir DSN: correr desde el host contra el puerto publicado
(`127.0.0.1:5432`) con `ORBIT_DSN_INGEST` del `.env`.

## Crons de Orbit (crontab de `gon`, ADITIVO)

Tres jobs NUEVOS en el crontab de `gon`. Los de accounting (y el resto:
EHV, heartbeat, etc.) **no se tocan**. El backup de Postgres sigue en el
crontab de **root** (`30 3 * * * backup.sh`) — tampoco se toca.

El server está en UTC: estas horas SON UTC.

| UTC   | job_key | comando |
|-------|---------|---------|
| 06:45 | `ingest:structure` | `python -m app.cli ingest structure` |
| 07:10 | `ingest:metrics` | `python -m app.cli ingest metrics --fecha D-31 --fecha-fin D-1` |
| 07:20 | `ingest:metrics:productos` | `python -m app.cli ingest metrics --fecha D-31 --fecha-fin D-1 --productos` (ORBIT 19 B.1; el reporte `spAdvertisedProduct` puede tardar hasta ~25 min por perfil, presupuesto de poll propio) |
| 08:40 | `ads_optimizer:amazon_us` + `ads_optimizer:amazon_mx` | `python -m app.cli cycle --platform …` (los dos, en serie) |

`job_key` del ciclo es `app.cycle.job_key_de` (`ads_optimizer:<platform>`),
la misma fuente que el CLI. Los de ingesta quedan como comentario en el
crontab y como `ingest_run.source` (`amazon_ads_structure_v2` /
`amazon_ads_reports_v3`).

### Ingestas SP-API diarias 05:00–06:30 (A.5, PROPUESTA — NO instalada)

Ocho corridas **en serie dentro de UN wrapper**, no ocho líneas de
crontab. La revisión A.R (hallazgo H1) encontró tres defectos en la
versión anterior de esta propuesta, y los tres tienen la misma raíz:
ocho líneas independientes de Vixie no son una secuencia.

1. **No corrían en serie de verdad.** El texto decía «en serie a
   propósito» (los limitadores son por proceso y dos procesos contra la
   misma quota Amazon se canibalizan), pero el artefacto no encadenaba
   nada: si pricing MX se pasaba de su slot, US arrancaba encima.
2. **Colchón de ~2 min.** Presupuesto medido de pricing MX: 342 ASIN ×
   2 llamadas a 0.5/s ≈ 22.8 min en un slot de 25. Un solo 429 con
   `Retry-After` de 60 s (el tope del cliente) lo revienta.
3. **El instalador de ORBIT 03 las borraba.** El comando idempotente de
   más abajo filtra `grep -v "app.cli ingest"`, y las ocho líneas
   calzaban ese patrón: re-aplicar el bloque de Ads las eliminaba del
   crontab **en silencio y sin error**.

**Wrapper** (`/mnt/data/appdata/orbit/spapi-diario.sh`, `chmod +x`), que
resuelve 1 y 2 — la secuencia es del shell, no del reloj, así que un
retraso corre el resto en vez de solaparlo:

```bash
#!/usr/bin/env bash
# SP-API 01 A.5: las 8 ingestas diarias, EN SERIE y en un solo proceso.
# Sin `set -e`: una fuente que falla no debe cancelar las siguientes
# (cada una sella su propio ingest_run y alerta sola, A.5).
set -uo pipefail
for fuente in spapi_orders spapi_listings spapi_inventario spapi_pricing; do
  for mercado in amazon_mx amazon_us; do
    echo "=== $(date -u +%FT%TZ) $fuente $mercado ==="
    docker exec orbit-app-1 python -m app.cli ingest "$fuente" --platform "$mercado" || true
  done
done
```

Las tres baratas van primero y **pricing al final**: es la larga y la
menos urgente, así que un sobrepaso suyo no le come la ventana a nadie.
Presupuesto: orders/listings/inventario ~2 min cada una (~12 min) +
pricing MX ~23 + US ~12 ≈ **47 min**. Arrancando 05:00 termina ~05:47,
con casi una hora de margen antes del sync de estructura de 06:45.

**Una sola línea de crontab**, que resuelve 3 — no contiene `app.cli
ingest`, así que el instalador de Ads no la toca, y su `job_key` es
propio (`spapi:`, no `ingest:`):

```cron
# job_key=spapi:diario  SP-API 01 A.5 (las 8 ingestas, wrapper en serie)
00 5 * * * /usr/bin/flock -n /tmp/spapi-diario.lock /mnt/data/appdata/orbit/spapi-diario.sh >> /mnt/data/appdata/orbit/logs/spapi-diario.log 2>&1
```

`flock -n`: si la corrida de ayer sigue viva, la de hoy sale de
inmediato en vez de duplicar la quota. El redirect a `logs/` es el mismo
patrón de los crons ya instalados; sin él, stdout se va al mail de cron
o se pierde.

**Al instalar (A.6), verificar las dos convivencias**: que la línea
sobreviva a re-correr el instalador de ORBIT 03 (`crontab -l | grep
spapi:diario` después de re-aplicarlo), y que el wrapper sea ejecutable
y su log exista tras la primera corrida. Instalar es A.6 (dueño), no
esta tarea.

### Vigilante SP-API 07:30 (aviso del silencio)

Si una ingesta **falla**, ella misma sella su `ingest_run` y `salud.py`
avisa. Pero si el cron **no dispara** (crontab pisado, `flock` atorado,
reinicio en la ventana), no hay fila que fallar y nadie se entera. El
vigilante (`app/spapi/vigilante.py`, solo lectura con `ORBIT_DSN_READ`)
convierte el silencio en aviso: a las 07:30 UTC revisa la ventana
04:30–07:30 de `ingest_run` y, si faltan corridas, manda UN Telegram
con los pares ausentes. En verde es silencioso (no envía nada); si no
puede leer, manda el aviso ciego. No verifica el contenido de las
corridas (eso es `salud.py`): una corrida presente, aunque sea
`ok = false` o `rows_written = 0`, no es silencio.

**Límite conocido: contenedor caído no avisa.** El vigilante corre
dentro de `orbit-app-1` vía `docker exec`: si el contenedor no está
corriendo, el `docker exec` falla en el host, Python nunca arranca y
NO sale el aviso por Telegram — solo queda el error de docker en
`spapi-vigilante.log`. Un aviso que sobreviva al contenedor caído
tendría que salir del propio host, y los secretos de Telegram viven
en `secrets/` (700 root), fuera del alcance del usuario del cron:
es una tarea aparte, fuera de este PR.

**Línea de crontab** (copiada de `LINEA_CRONTAB_VIGILANTE` en
`tests/test_spapi_vigilante.py`; el test pinza que esté aquí exacta):
top-level `spapi_vigilante`, **sin `app.cli ingest`** para que el
instalador de ORBIT 03 no la borre (mismo hallazgo H1 que `spapi:diario`),
con `flock`, log y `job_key` propio:

```cron
# job_key=spapi:vigilante  aviso si el cron spapi:diario no dejó sus 8 corridas
30 7 * * * /usr/bin/flock -n /tmp/spapi-vigilante.lock docker exec orbit-app-1 python -m app.cli spapi_vigilante >> /mnt/data/appdata/orbit/logs/spapi-vigilante.log 2>&1
```

**Instalación** (dueño, con `!`, posterior al merge): agregar las dos
líneas al crontab de `gon` (`crontab -e`; nada más se toca) y verificar
con el paso (c) de abajo. La **reversa** es borrar la línea del
crontab: el vigilante no escribe nada en la base.

**Prueba del silencio** (dueño, con `!`, tres pasos). Antes del paso (b),
avisa en el chat de Telegram que el siguiente aviso es una prueba: manda un
Telegram real, igual a un incidente, y es la única forma de probar el canal
de punta a punta (una sola vez, el día de la instalación).

(a) `--dry-run` sobre la ventana de hoy debe decir `faltan 0 de 8` y
salir 0 (si el cron 05:00 ya corrió):
`docker exec orbit-app-1 python -m app.cli spapi_vigilante --dry-run`
(b) Una ventana pasada donde no existió ninguna corrida SP-API debe
listar 8 ausentes, mandar el aviso REAL por Telegram y salir 1:
`docker exec orbit-app-1 python -m app.cli spapi_vigilante --desde
2026-01-01T04:30:00Z --hasta 2026-01-01T07:30:00Z`
(c) Tras instalar la línea, `crontab -l | grep spapi:vigilante` la
muestra, y sigue ahí después de re-correr el instalador de ORBIT 03.

**ORDEN DE DEPLOY de A.6 — las tres migraciones, en este orden** (patrón de
comando en «Aplicar migraciones», más abajo):

1. `migrations/0035_spapi_listings_inventario.sql`
2. `migrations/0036_ingest_run_platform.sql`
3. `migrations/0037_ingest_run_salud_idx.sql`
4. Recién entonces, reconstruir la app.

`0035` y `0036` van ANTES del rebuild y no son opcionales: si el código sale
primero, las 4 ingestas truenan al abrir el run (`platform` inexistente) y
`/salud` muestra el bloque SP-API vacío (con la guarda A.5, sin tumbar la
pantalla). `0037` depende de `0036` (indexa esa columna) y **no bloquea el
rebuild** —solo crea el índice de `/salud` y el candado del `INSERT`—, pero
se aplica igual antes de exponer `/salud`: sin él, cada carga de la página
recorre `ingest_run` entera por cada fuente y plataforma. Omitir `0037` no
rompe nada, solo degrada; omitir `0035`/`0036` sí rompe.

### Refresco diario contable 08:15 (ORBIT 06 2.2)

La línea `15 8 * * * .../refresh_costos.sh` (que ya existía para costos, a
las 07:30 hasta la review del PR #144)
corre ahora los tres pipelines del MISMO snapshot, en este orden: `costs`,
`fx`, `ledger` — cada uno a su log (`costos.log`/`fx.log`/`ledger.log`)
más una línea resumen a stdout (llega a `costos.log`). Un pipeline caído
NO tumba a los otros (cada uno sella su `ingest_run` ok/false); el exit
final es != 0 si alguno falló. El script versionado vive en
`tools/refresh_costos.sh` (la copia del server es la desplegada).
**Orden de la cadena (corregido por el lead en la review):** 08:15 UTC cae
después de los DOS syncs de accounting — `sync_ads_to_ledger.py` (:30 cada
6 h → 06:30) y `sync_fx_rates.py` (08:00) — y 25 min antes de los ciclos
08:40/08:41 (los tres pipelines tardan segundos). A las 07:30 el snapshot se
tomaba 30 min ANTES del sync de FX: el FX de Orbit quedaba estructuralmente
un día atrasado. Respaldo del crontab previo:
`archive/crontab-gon.20260904-021331.lead-fx-orden`.
Estreno 2026-09-04: costs run 74 ok no-op; fx run 75 ok +3 tasas (máx
2026-09-02); ledger run 76 ok +217 eventos (8,041 → 8,258).

### Profundidad de la tirada diaria de métricas (sello 4.2)

Evidencia de 1.5 (corrida 2026-08-22/23): reporting v3 sirve **95 días**
de métricas (`spCampaigns`) y ~65 de search terms. El rango máximo de
**un** request es **31 días** (`MAX_RANGO_DIAS` en `app/ads/reports.py`,
verificado: un rango mayor revienta).

La atribución de search terms madura 7 días → mínimo operacional
**D-8..D-1**. Las columnas 30d de métricas maduran 30. Una tirada de
solo "ayer" congelaría cada día con ~1 día de maduración (hallazgo
codex 1.4, regla 6).

**Sello: el cron diario pide D-31..D-1** (31 días, el tope de un
request). Por qué no 95d: (1) un request no puede pedir más de 31 — partir
en 4 requests alarga la ventana de API **sin** ganar maduración extra en
días que el backfill y las tiradas previas ya cubrieron; (2) 31 días cubre
enteras las columnas 30d y de sobra el mínimo D-8..D-1 de terms; (3) el
costo de re-tirar es solo tiempo de reporte; la bitemporalidad hace el
re-pull dedupe-safe (observación nueva, el motor colapsa a la más
reciente). El lookback de 95d queda como capacidad de **backfill**, no
como cadencia diaria.

Bloque para el crontab de `gon` (idempotente: el filtro borra SOLO las
líneas de Orbit, no las de accounting):

```bash
ssh goncloud 'bash -s' <<'SCRIPT'
set -euo pipefail
STAMP=$(date -u +%Y%m%d-%H%M%S)
crontab -u gon -l > /mnt/data/appdata/orbit/archive/crontab-gon.$STAMP
mkdir -p /mnt/data/appdata/orbit/logs
chown gon:gon /mnt/data/appdata/orbit/logs
ORBIT_BLOCK=$(cat <<'CRON'
# === Orbit (ORBIT 03 / 4.2) ===
# job_key=ingest:structure
45 6 * * * docker exec orbit-app-1 python -m app.cli ingest structure >> /mnt/data/appdata/orbit/logs/ingest-structure.log 2>&1
# job_key=ingest:metrics  profundidad D-31..D-1 (sello 4.2; max API 31d cubre atribucion 30d)
# Vixie cron: % sin escapar se vuelve newline y trunca el comando (hallazgo codex).
10 7 * * * FECHA=$(date -u -d "31 days ago" +\%F) FECHA_FIN=$(date -u -d "1 day ago" +\%F) && docker exec orbit-app-1 python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN" >> /mnt/data/appdata/orbit/logs/ingest-metrics.log 2>&1
# job_key=ingest:metrics:productos  ORBIT 19 B.1 (spAdvertisedProduct por ASIN/SKU; poll hasta 25 min/reporte)
20 7 * * * FECHA=$(date -u -d "31 days ago" +\%F) FECHA_FIN=$(date -u -d "1 day ago" +\%F) && docker exec orbit-app-1 python -m app.cli ingest metrics --fecha "$FECHA" --fecha-fin "$FECHA_FIN" --productos >> /mnt/data/appdata/orbit/logs/ingest-productos.log 2>&1
# job_key=ads_optimizer:amazon_us + ads_optimizer:amazon_mx
40 8 * * * docker exec orbit-app-1 python -m app.cli cycle --platform amazon_us >> /mnt/data/appdata/orbit/logs/optimizer.log 2>&1
41 8 * * * docker exec orbit-app-1 python -m app.cli cycle --platform amazon_mx >> /mnt/data/appdata/orbit/logs/optimizer.log 2>&1
CRON
)
{ crontab -u gon -l 2>/dev/null | grep -v "Orbit (ORBIT 03" | grep -v "job_key=ingest:" | grep -v "job_key=ads_optimizer" | grep -v "app.cli ingest" | grep -v "app.cli cycle" ; printf "%s\n" "$ORBIT_BLOCK" ; } | crontab -u gon -
crontab -u gon -l
SCRIPT
```

Diff obligatorio contra el respaldo: las líneas de accounting deben
seguir byte-iguales. Solo aparecen las 3 (más comentarios) de Orbit.

## Usuarios y DSN

El esquema crea 4 roles de permisos **NOLOGIN** (`app_ingest`, `app_decide`,
`app_read`, `app_admin`). Para conectarse hace falta un usuario LOGIN por
servicio, miembro del rol que le corresponde. Los valores viven SOLO en el
`.env` del server, jamás en el repo:

```dotenv
ORBIT_DSN_INGEST=postgresql://orbit_ingest:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_DECIDE=postgresql://orbit_decide:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_READ=postgresql://orbit_read:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_ADMIN=postgresql://orbit_admin:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_TEST=postgresql://orbit_test:<pass>@127.0.0.1:5432/postgres
```

- `orbit_read`: SELECT sí, UPDATE/INSERT no (verificado en vivo).
- `orbit_ingest`: INSERT en tablas de ingesta sí (`ingest_run`, métricas…).
- `orbit_test`: `CREATEDB CREATEROLE NOSUPERUSER` — para correr la suite
  sin alcance destructivo de superusuario (ver "Correr los tests").
- El superusuario del contenedor es `orbit` (solo migraciones y admin).

**Crear los usuarios (comandos exactos, password generada en el server):**

```bash
ssh goncloud 'bash -s' <<'SCRIPT'
set -euo pipefail
ENVF=/mnt/data/appdata/orbit/.env
gen() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32; }
sed -i '/^ORBIT_DSN_/d' "$ENVF"   # re-corrida = DSNs nuevos, sin duplicados
for svc in ingest decide read admin; do
  P=$(gen)
  docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbit_$svc') THEN
    EXECUTE format('CREATE ROLE orbit_$svc LOGIN PASSWORD %L', '$P');
  ELSE
    EXECUTE format('ALTER ROLE orbit_$svc LOGIN PASSWORD %L', '$P');
  END IF;
END \$\$;
GRANT app_$svc TO orbit_$svc;
SQL
  echo "ORBIT_DSN_${svc^^}=postgresql://orbit_${svc}:${P}@127.0.0.1:5432/orbit" >> "$ENVF"
done
# 4.1: orbit_admin necesita TAMBIEN app_decide — las reversas /reversa/*
# insertan en apply_attempt, cuyo GRANT INSERT (0002) es solo de app_decide.
# Sin esta linea una instalacion reconstruida vetaria pero NO revertiria
# (InsufficientPrivilege). Idempotente (GRANT es no-op si ya la tiene).
docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -q \
  -c 'GRANT app_decide TO orbit_admin'
# rol de test: CREATEDB/CREATEROLE, SIN superusuario
PT=$(gen)
docker exec -i orbit-db-1 psql -U orbit -d postgres -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbit_test') THEN
    CREATE ROLE orbit_test LOGIN CREATEDB CREATEROLE NOSUPERUSER;
  END IF;
END \$\$;
DO \$\$ BEGIN EXECUTE format('ALTER ROLE orbit_test PASSWORD %L', '$PT'); END \$\$;
SQL
# La suite crea roles LOGIN temporales con membresia app_admin (tests de
# veto/escritura): necesita ADMIN OPTION. Sin esta linea una instalacion
# reconstruida NO puede correr la suite en vivo (CodeRabbit PR #36).
docker exec -i orbit-db-1 psql -U orbit -d postgres -v ON_ERROR_STOP=1 -q \
  -c 'GRANT app_read, app_ingest, app_decide, app_admin TO orbit_test WITH ADMIN OPTION'
echo "ORBIT_DSN_TEST=postgresql://orbit_test:${PT}@127.0.0.1:5432/postgres" >> "$ENVF"
chmod 600 "$ENVF"
SCRIPT
```

> **Estado actual del cluster (resuelto en 4.1, 2026-08-27):** para que la
> suite local cree roles LOGIN temporales con membresía `app_admin` (tests
> del endpoint de veto), `orbit_test` recibió además `GRANT app_read,
> app_ingest, app_decide, app_admin TO orbit_test WITH ADMIN OPTION`. Es
> membresía de CLUSTER: quien tenga el DSN de test puede `SET ROLE` a
> escritura en la base prod. **Decisión 4.1: se QUEDA mientras la suite
> pueda correr contra el cluster vivo por túnel** (sin ella los tests de
> veto/escritura no pueden crear sus roles). Mitigación vigente: bind
> loopback + túnel + password root-only + el DSN de test jamás sale del
> `.env` del server. La revocación queda atada a mover la base de test
> fuera del cluster de prod:
>
> ```sql
> REVOKE app_read, app_ingest, app_decide, app_admin FROM orbit_test;
> ```
>
> **HITO DE REVOCACIÓN (ORBIT 05 preflight 1.7, 2026-08-29)**: se cierra el
> "sin fecha" — así es como una excepción temporal se vuelve permanente. La
> revocación NO va por calendario (una fecha inventada solo se pospone) sino
> por hito verificable, el primero que ocurra:
>
> 1. **La suite deja de necesitar el cluster de prod**: cuando exista un
>    Postgres 16 de test aparte (contenedor propio en goncloud o local)
>    donde corran `test_migracion_rechaza_en_vivo` y los tests de
>    veto/escritura que hoy crean roles LOGIN temporales. Es la condición
>    real: revocar antes deja la suite sin poder correr en vivo.
> 2. **Cualquier acceso de terceros al DSN de test** (otra persona, otra
>    máquina, un CI que use el túnel): ahí la mitigación vigente deja de
>    sostenerse y se revoca ESE MISMO DÍA, aunque la suite pierda cobertura
>    en vivo.
>
> Tarea abierta en el tracker: **"ORBIT — DB de test fuera del cluster de
> prod (revocar ADMIN OPTION de orbit_test)"**. Hasta que el hito ocurra
> esto es **deuda declarada, no olvido**: quien tenga el DSN de test puede
> `SET ROLE` a escritura sobre la base viva. Se revisa en cada cierre de
> fase.

## Rotación del token de escritura (ORBIT 04, sellado 18)

El token estático de los endpoints de escritura (veto + reversas) vive en
`secrets/api_write_token` y se rota con la ceremonia de APPLY.md §11b:

```bash
ssh goncloud   # la sesion entra como ROOT: escribir en secrets/ (dir 700,
               # dueno 10001) y el chown lo exigen (CodeRabbit PR #36)
cd /mnt/data/appdata/orbit
# 1. Generar el token NUEVO en el server (nunca en el repo ni en out/)
NEW=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)
# 2. Escribirlo en secrets/ con 0600 y el uid del contenedor (dir 700,
#    dueno 10001 desde 4.1; mount :ro)
printf '%s' "$NEW" > secrets/api_write_token
chmod 600 secrets/api_write_token
chown 10001:10001 secrets/api_write_token
# 3. Reiniciar la app (relee el archivo y lo registra con register_secret)
docker compose up -d --no-deps --force-recreate app
# 4. Verificar contra el endpoint de veto con un queue_id INEXISTENTE
#    (999999999): la verificacion NO debe mutar nada real. Un queue_id
#    real en pending_veto/released VETARIA una fila de produccion 30 dias.
#    sin token -> 401; con el NUEVO -> 404 (fila inexistente = token
#    valido); con el VIEJO -> 401.
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8010/api/ads-optimizer/veto \
  -H 'Content-Type: application/json' -d '{"queue_id":999999999,"actor":"rotacion"}'
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8010/api/ads-optimizer/veto \
  -H 'Content-Type: application/json' -H "x-orbit-token: $NEW" \
  -d '{"queue_id":999999999,"actor":"rotacion"}'
unset NEW   # el token no vive en el shell ni en el historial
```

- El endpoint sigue `compare_digest` y **solo header** (`x-orbit-token`): una
  rotación jamás habilita query string (con test).
- **Fail-closed**: si falta el archivo, está vacío o es ilegible, TODA la
  superficie de escritura responde 503 — jamás fail-open.

## Aplicar migraciones

**Cadena completa y EN ORDEN** (CodeRabbit PR #46: la sección saltaba de
`0001` a `0003` — sin `0002` la base queda sin las tablas/GRANTs del apply y
`0003` se aplicaría sobre una instalación incompleta):

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0001_initial.sql
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0002_apply.sql
# 0003: ver abajo — exige chequeo previo y backup del schema
# 0004: ver abajo — ADD VALUE 'product_ad'; la aplica el lead
```

- `-1` = transacción única: si algo falla a medias, se revierte entera.
- **`0001` NO es re-runnable** (los `CREATE TYPE`/`CREATE TABLE` revientan a
  la segunda): solo se aplica una vez por base nueva. Las futuras deben ser
  idempotentes o gestionarse con una tabla de versiones.
- Post-aplicación (verificación de la 0001): 19 tablas en `public`, roles
  `app_*` en `pg_roles`, `prohibir_mutacion` en `pg_proc`.

Migración `0003` (ORBIT 05 preflight 1.2) — **APLICADA en goncloud el
2026-08-29 04:10 UTC por el lead con GO del dueño** (evidencia
`out/orbit-05-preflight-1-2-lead-20260829.md`). Secuencia obligatoria:

**(a) Chequeo PREVIO** (CodeRabbit PR #46: un goal MXN creado ANTES de 0003
pudo nacer con el techo USD y esos valores NO son None, así que ningún
guard de código los corrige) — debe dar **cero filas**:

```sql
-- OR, no AND (Greptile PR #47): un goal MXN pudo quedar con UN SOLO bound
-- heredado — `goals set` mueve piso y techo por separado y 0003 conserva los
-- valores existentes; con AND esa fila se escapaba del chequeo.
SELECT id, bid_currency, bid_floor, bid_ceiling FROM ads_optimizer_goal
 WHERE bid_currency = 'MXN' AND (bid_floor = 0.10 OR bid_ceiling = 2.50);
```

Si devuelve alguna: corregirla ANTES de migrar con
`python -m app.cli goals set <id> --floor 1.00 --ceiling 45.00` (queda
auditado por `updated_at`; 0003 NO toca datos por diseño). Verificado
2026-08-29: cero filas (goal 4 ya en 1.00/45.00 MXN).

**(b) Backup del schema de la tabla** (además del backup diario), con
**staging + verificación** (Greptile PR #47: escribir directo al nombre
final deja un archivo vacío o a medias con pinta de evidencia de rollback si
`pg_dump` se interrumpe; mismo patrón que `backup.sh`):

```bash
ssh goncloud 'set -e; D=/mnt/data/appdata/orbit/backups; \
  STAMP=$(date -u +%Y%m%d-%H%M%S); TMP="$D/.pre0003_$STAMP.sql.tmp"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only \
    -t ads_optimizer_goal > "$TMP"; \
  [ -s "$TMP" ] && grep -q "CREATE TABLE public.ads_optimizer_goal" "$TMP" \
    && grep -q "bid_floor" "$TMP" \
    && tail -5 "$TMP" | grep -q "PostgreSQL database dump complete" \
    || { echo "DUMP INVALIDO"; rm -f "$TMP"; exit 1; }; \
  chmod 600 "$TMP"; mv "$TMP" "$D/pre0003_ads_optimizer_goal_$STAMP.sql"; \
  ls -l "$D/pre0003_ads_optimizer_goal_$STAMP.sql"'
```

El archivo final solo aparece si el dump trae el `CREATE TABLE` de la tabla,
sus columnas **y el marcador de cierre que `pg_dump` escribe al terminar**
(`-- PostgreSQL database dump complete` en las últimas líneas): un dump
interrumpido DESPUÉS del `CREATE TABLE` no lo tiene (Greptile PR #47). Si
falta cualquiera de los tres, se borra el temporal y el runbook se detiene
ahí. El backup real del 2026-08-29 04:10 se verificó a mano: 6,407 B, con el
`CREATE TABLE` completo y el marcador de cierre presente.

**(c) Aplicar**, mismo patrón de comando:

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0003_goal_bounds_explicit.sql
```

- **`0003`** quita el `DEFAULT 0.10/2.50` de `bid_floor`/`bid_ceiling` en
  `ads_optimizer_goal` (sellado 2 del plan ORBIT 05 preflight; spot-check
  4.4: el default único estaba pensado en USD y el goal MXN nació con el
  techo que aplastaba bids vivos). **NOT NULL se queda**: un INSERT de goal
  que omita piso/techo REVIENTA — los defaults viven solo en
  `DEFAULTS_POR_MONEDA` (app/optimizer/goals.py). NO toca datos ni GRANTs.
- **La aplica el LEAD**, con **backup previo del schema (runbook 4.1,
  sección "Backups")**. Verificación post-aplicación — ambas filas deben
  traer `column_default` NULL:

```sql
SELECT column_name, column_default FROM information_schema.columns
 WHERE table_name = 'ads_optimizer_goal'
   AND column_name IN ('bid_floor', 'bid_ceiling');
```

Migración `0004` (ORBIT 06 0.4) — **NO aplicada todavía en goncloud**. Solo
hace `ALTER TYPE ad_entity_kind ADD VALUE 'product_ad'`. No toca datos ni
GRANTs. **No es re-runnable.** El valor nuevo no se puede usar en la misma
transacción que lo agrega: aplicar, commitear, y recién después correr
`ingest structure` (pasos separados). La aplica el **lead**, con backup
previo del schema (runbook 4.1). Verificación post-aplicación:

```sql
SELECT enumlabel FROM pg_enum e
  JOIN pg_type t ON t.oid = e.enumtypid
 WHERE t.typname = 'ad_entity_kind'
 ORDER BY e.enumsortorder;
```

Debe listar `product_ad` al final. Comando:

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0004_ad_entity_kind_product_ad.sql
```

Migración `0005` (ORBIT 06 0.7 — hallazgo de qwen en la review 3.3, doble
conteo confirmado en vivo el 2026-08-31) — **NO aplicada todavía en
goncloud**. `CREATE OR REPLACE VIEW v_tacos`: filtra el CTE `gasto` a
`e.kind IN ('keyword', 'product_target')`, el mismo grano del motor de
decisión y del candado de cobertura. Sin el filtro, `ads_metric_observation`
guarda el mismo costo en la fila `kind='campaign'` Y en sus hijas
keyword/product_target, y `v_tacos` lo sumaba dos veces (gasto_ads inflado
~2x, tacos_pct inflado ~2x). **Re-runnable** (`CREATE OR REPLACE`); la lista
de columnas de `v_tacos` no cambia. La aplica el **lead**. Comando:

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0005_v_tacos_grano_unico.sql
```

Verificación post-aplicación (reviewer 2026-08-31: "cae a la mitad" no
distingue el filtro correcto de uno sobre-agresivo — tirar también los
targets también "baja"). Se verifica el GRANO desglosando el costo maduro
por kind, y de paso se publica el residuo campaign − (keyword+target):

```sql
-- (a) desglose por kind Y MES (sin el mes, el total jamas cuadra contra la
--     fila mensual de la vista):
SELECT e.platform, date_trunc('month', m.metric_date)::date AS mes, e.kind,
       round(sum(m.cost), 2) AS gasto
  FROM v_metric_mature m JOIN ad_entity e ON e.id = m.ad_entity_id
 WHERE m.cost IS NOT NULL
 GROUP BY 1, 2, 3 ORDER BY 1, 2 DESC, 3;

-- (b) la vista, tras el filtro:
SELECT platform, mes, gasto_ads, tacos_pct FROM v_tacos ORDER BY platform, mes DESC LIMIT 6;
```

Cómo cuadrar (reviewer r2): en `amazon_mx` (costo sellado en MXN),
`gasto_ads` del mes = suma keyword+product_target de (a) **1:1**. En
`amazon_us` el costo está sellado en USD y `gasto_ads` sale convertido a
MXN: NO cuadra en absoluto — se compara la RELACIÓN (campaign vs
keyword+target debe ser ~1:1 en (a), y `gasto_ads` ≈ suma×tasa). La brecha
campaign − (keyword+target) de (a) es el residuo declarado en la 0005. El
`tacos_pct` publicado cambia de un día para otro (~mitad) — avisar al dueño
ANTES de aplicar, no después.

Migración `0011` (ORBIT 06 · palanca de mapeo, 2026-09-02) — **corrección de
DATOS, la primera del repo**: siembra el costo histórico de
`NH-GAM-NEG-PESETA-PLA` (325.00 MXN desde 2026-02-20) y
`NH-NOG-VEN-PESETA-DOR` (458.00 MXN [2026-02-20, 2026-08-18), la vigente
459.29 no se toca). Sin esa historia, los dos SKU sólo tenían costo desde el
2026-08-18 y dejaban a los **seis grupos Arras MX** en `catalogo_parcial`
(3,325.79 MXN/90d sin medir). Importes sellados por el dueño el 2026-09-02,
derivados de los hermanos de familia con historia (`NH-GAM-NEG-MAX-PLA`,
`NH-NOG-VEN-CEN-DOR`).

**Lleva una excepción declarada.** El SKU de plata tiene el MISMO costo antes
y después, y `colapsar()` fusiona tramos contiguos de igual costo: si se
dejaran dos vigencias abutidas, cada `ingest costs` futuro rechazaría el SKU
completo («origen reabre vigencia ya publicada») y ese producto dejaría de
recibir actualizaciones para siempre. El estado correcto es UNA vigencia
desde 2026-02-20, y llegar ahí exige BORRAR la fila publicada — lo que el
trigger append-only prohíbe. 0011 lo apaga y lo re-enciende **dentro de la
misma transacción** (un fallo entre ambos hace ROLLBACK y deja la tabla
protegida). Es el único `DELETE` de `sku_cost` del repo y **no sienta
precedente**: corregir un costo sigue siendo insertar una fila nueva.

**NO es re-runnable** (guardas fail-closed: la segunda corrida aborta en vez
de duplicar historia; también aborta si el estado de partida no es el
medido). Las filas nuevas cuelgan de un `ingest_run` con
`source = 'manual_costo_historico_0011'`. Candados en
`tests/test_costo_historico_0011.py` (7 estáticos + 5 vivos; los estáticos
tumban las tres mutaciones probadas: quitar el `ENABLE TRIGGER`, cambiar el
importe histórico, soltar el `WHERE` del `DELETE`).

**La reversa existe y se prueba** (`migrations/0011_reversa_costo_historico_peseta.sql`):
la regla del repo es que ninguna acción irreversible se ejecuta sin su vuelta
atrás YA implementada — la misma que obligó a `reponer-anuncios` antes de
`archivar-anuncios` (hallazgo de CodeRabbit, PR #116). La reversa borra por
**procedencia** (el `ingest_run` de 0011, no por importe), repone la vigencia
de plata que 0011 borró, y deja el estado exacto previo: probado en vivo con un
test de ida y vuelta que compara columna por columna, y con otro que confirma
que tras revertir **0011 se puede volver a aplicar** (ese es el caso de uso:
un importe mal, se revierte, se corrige con el dueño, se re-aplica). Único
detalle declarado: la fila repuesta es una fila NUEVA, así que trae `id` y
`ingest_run_id` nuevos — el importe, la moneda, `includes_tax` y las fechas
(que es lo que leen la vista y el motor) vuelven idénticos, pero esas dos
columnas de identidad no. Nada en el esquema referencia `sku_cost.id` (no hay
FK hacia él), así que el cambio no arrastra nada; aun así el paso 0 de abajo
captura el estado previo COMPLETO —`id` incluido— porque es su único registro.

```bash
# 0) EVIDENCIA del estado previo. Incluye `id` e `ingest_run_id`: la reversa NO
#    los repone (ver más abajo), así que esta captura es su único registro.
#    OJO con el quoting: '' NO escapa una comilla dentro de comillas simples de
#    bash — la termina. Los SKU van en $'...' (comilla simple escapable) o, como
#    aquí, la SQL entera se pasa por stdin y no hay anidamiento que romper.
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit' <<'SQL'
SELECT p.odoo_sku, c.id, c.cost_amount, c.cost_currency, c.includes_tax,
       c.valid_from, c.valid_to, c.ingest_run_id
  FROM sku_cost c JOIN product p ON p.id = c.product_id
 WHERE p.odoo_sku IN ('NH-GAM-NEG-PESETA-PLA', 'NH-NOG-VEN-PESETA-DOR')
 ORDER BY 1, 6;
SQL
# 1) backup del esquema + datos de sku_cost ANTES (runbook 4.1)
ssh goncloud 'docker exec orbit-db-1 pg_dump -U orbit -d orbit \
  -t sku_cost --data-only -Fc > /mnt/data/appdata/orbit/backups/sku_cost-pre-0011.dump'
# 2) aplicar (transacción única; -1 sobra porque el SQL trae BEGIN/COMMIT)
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1' < migrations/0011_costo_historico_peseta.sql
```

Verificación post-aplicación (la migración ya se auto-verifica y revienta si
algo no cuadra; esto es la lectura independiente):

```sql
-- (a) las vigencias finales, con su procedencia
SELECT p.odoo_sku, c.cost_amount, c.valid_from, c.valid_to, r.source
  FROM sku_cost c JOIN product p ON p.id = c.product_id
  LEFT JOIN ingest_run r ON r.id = c.ingest_run_id
 WHERE p.odoo_sku IN ('NH-GAM-NEG-PESETA-PLA','NH-NOG-VEN-PESETA-DOR')
 ORDER BY 1, 3;
-- (b) el append-only quedó ARMADO (esto es lo que no puede fallar)
SELECT tgname, tgenabled FROM pg_trigger
 WHERE tgrelid = 'sku_cost'::regclass AND NOT tgisinternal;
```

Si hay que dar marcha atrás (importe equivocado detectado tras aplicar):

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit   -v ON_ERROR_STOP=1' < migrations/0011_reversa_costo_historico_peseta.sql
```

La reversa se auto-verifica y aborta sin escribir si el estado no es el que
dejó 0011 (o si 0011 nunca se aplicó). Los dos `ingest_run` —el de 0011 y el
de la reversa— se conservan: que se aplicó y se revirtió es historia real. Si
se revierte, hay que deshacer también el cambio espejo en contabilidad (abajo),
o el próximo `ingest costs` volverá a divergir.

**Contabilidad, en el mismo cambio.** Sin esto el siguiente `ingest costs`
rechaza los dos SKU por divergencia con el origen y deja de seguirlos. Lo hace
`tools/contabilidad_costo_historico_0011.py`, que corre en el **host** (la
SQLite vive en `/mnt/data/appdata/accounting/data/accounting.db`, fuera de todo
contenedor): al de plata le corre `valid_from` a `2026-02-20` (queda UNA fila
abierta, que es lo que produce el colapso) y al de oro le agrega la fila cerrada
458.00 `[2026-02-20, 2026-08-18)`. Respalda `sku_costs` antes de tocarla,
escribe en UNA transacción, valida el estado de partida y aborta sin escribir si
no es el esperado.

```bash
# ENSAYO (no escribe, no deja respaldo): muestra el plan exacto
ssh goncloud 'python3 -' < tools/contabilidad_costo_historico_0011.py --dry-run
# De verdad
ssh goncloud 'cat > /tmp/cch_0011.py' < tools/contabilidad_costo_historico_0011.py
ssh goncloud 'python3 /tmp/cch_0011.py'          # imprime el respaldo y 2708 -> 2709
# Reversa (espejo de 0011_reversa_*.sql): --revertir, con --dry-run primero
ssh goncloud 'python3 /tmp/cch_0011.py --revertir --dry-run'
ssh goncloud 'python3 /tmp/cch_0011.py --revertir'
```

La reversa de contabilidad borra la fila histórica por su **`source`**
(`orbit_0011_costo_historico`), nunca por su importe — mismo criterio que la
reversa de Orbit.

El sync horario de Odoo (`sync_cogs_odoo.py`) sólo mira
`WHERE sku=? AND valid_to IS NULL`: no ve la fila cerrada y no toca la abierta
si el costo no cambió.

**Verificación que cierra el ciclo** (es la prueba de que ambas fuentes cuentan
la misma historia): tras aplicar 0011 **y** el espejo, `ingest costs` debe salir
sin escrituras de costo — `rows_written=0`, `rows_skipped=0`, `insertadas=0`,
`cerradas=0`. Un `rows_skipped` mayor que cero con el motivo «vigencia publicada
desaparecio del origen» o «origen reabre vigencia ya publicada» significa que
sólo se aplicó una de las dos mitades.

Precisión sobre esos cuatro contadores (hallazgo de CodeRabbit, PR #118): sólo
cubren `sku_cost`. `sync_costos` puede además actualizar el NOMBRE de un
producto (`_SQL_UPSERT_PRODUCTO`) y seguir mostrando los cuatro en cero, así que
esto **no** es un no-op del pipeline entero — es exactamente lo que hace falta
aquí, que ninguna vigencia se escriba ni se rechace. Para un no-op completo hay
que mirar también `productos: nuevos=0 actualizados=0` en la misma salida.

### Migracion 0019: fabrica v2 por publicacion (ORBIT 19 A)

`0019_fabrica_grupo_publicacion_v2.sql` se aplica una sola vez, despues de
`0018_fabrica_campanas.sql`. Es expansiva: conserva lotes y grupos v1; solo
habilita snapshots por `listing_id` y objetivos manuales con margen real
`NULL`. No recrear ni borrar tablas para revertirla.

Preflight de produccion, antes de tocar el esquema. Debe existir el esquema
F1, `target_origen` debe ser falso y los tres conteos deben ser cero. Si
alguna condicion difiere, detener el despliegue y conciliar el estado antes
de aplicar una DDL no reejecutable.

```bash
ssh goncloud 'docker exec orbit-db-1 psql -U orbit -d orbit -P pager=off -F "|" -At -c "
SELECT to_regclass('"'"'public.campana_grupo'"'"'),
       EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema='"'"'public'"'"' AND table_name='"'"'campana_grupo'"'"'
                  AND column_name='"'"'target_origen'"'"'),
       (SELECT count(*) FROM fabrica_lote),
       (SELECT count(*) FROM campana_grupo),
       (SELECT count(*) FROM campana_grupo_producto);"'
```

Crear backup de schema con staging y validacion. Aunque los grupos estan
vacios en el preflight de ORBIT 19, se conserva su contrato previo y el
archivo final solo aparece despues de validar el dump completo.

```bash
ssh goncloud 'set -eu; D=/mnt/data/appdata/orbit/backups; \
  STAMP=$(date -u +%Y%m%d-%H%M%S); TMP="$D/.pre0019_$STAMP.sql.tmp"; \
  OUT="$D/pre0019_fabrica_grupo_$STAMP.sql"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only \
    -t public.campana_grupo -t public.campana_grupo_producto > "$TMP"; \
  [ -s "$TMP" ] \
    && grep -q "CREATE TABLE public.campana_grupo" "$TMP" \
    && grep -q "CREATE TABLE public.campana_grupo_producto" "$TMP" \
    && tail -5 "$TMP" | grep -q "PostgreSQL database dump complete" \
    || { echo "DUMP INVALIDO"; rm -f "$TMP"; exit 1; }; \
  chmod 600 "$TMP"; mv "$TMP" "$OUT"; ls -l "$OUT"'
```

Aplicar la DDL en transaccion unica y comprobar columnas, constraints y filas:

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0019_fabrica_grupo_publicacion_v2.sql

ssh goncloud 'docker exec orbit-db-1 psql -U orbit -d orbit -P pager=off -c "
SELECT column_name, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema='"'"'public'"'"' AND table_name IN ('"'"'campana_grupo'"'"','"'"'campana_grupo_producto'"'"')
   AND column_name IN ('"'"'target_origen'"'"','"'"'target_derivado_pct'"'"','"'"'fraccion'"'"','"'"'margen_neto_pct'"'"')
 ORDER BY table_name, column_name;
SELECT conname FROM pg_constraint
 WHERE conrelid IN ('"'"'public.campana_grupo'"'"'::regclass,
                    '"'"'public.campana_grupo_producto'"'"'::regclass)
   AND conname IN ('"'"'campana_grupo_objetivo_v2'"'"','"'"'campana_grupo_producto_seller_sku_unico'"'"')
 ORDER BY conname;
SELECT (SELECT count(*) FROM fabrica_lote) AS lotes,
       (SELECT count(*) FROM campana_grupo) AS grupos,
       (SELECT count(*) FROM campana_grupo_producto) AS publicaciones;"'
```

Desplegar la aplicacion solo desde `origin/master` ya integrado, sin tocar
`bridge`, `accounting` ni `orbit-db-1`:

```bash
git archive --format=tar origin/master app Dockerfile .dockerignore \
  pyproject.toml uv.lock tools/fabrica_campanas.py \
  | ssh goncloud 'cd /mnt/data/appdata/orbit && tar -xf -'
ssh goncloud 'cd /mnt/data/appdata/orbit && docker compose up -d --no-deps --build app'
ssh goncloud 'curl -fsS http://127.0.0.1:8010/health'
```

La configuracion vigente sin `fabrica.creacion` vale `v1`; se deja asi en este
corte. No insertar una configuracion `v2` ni llamar `/crear`: el smoke usa
solo `GET /api/fabrica/catalogo` y `POST /api/fabrica/plan`. El lector,
registro, reconciliacion y pausa de un lote v2 permanecen disponibles con
ese interruptor, por lo que la reversa operativa es conservar este binario y
mantener `fabrica.creacion=v1`, nunca volver a un binario que no lea v2.

El ensayo de lote v2 parcial
`test_ensayo_staging_lote_v2_parcial_se_recupera_con_v1_sin_post_de_creacion`
corre contra PostgreSQL temporal con las migraciones de dependencia de fabrica
hasta 0019 y `httpx.MockTransport`: parte de pasos `applied + failed`,
reconcilia, registra y pausa despues de `fabrica.creacion=v1`, sin POST de
creacion. No se usa una campana real como sonda de produccion.

### Migracion 0038: fase `hermanas_negadas` + trigger bid-solo (FABRICA 02 A.2)

`0038_fabrica_hermanas_biblioteca.sql` se aplica una sola vez, despues de
`0018_fabrica_campanas.sql`. **Aplicada en producción el 2026-09-15** (D.1,
`docs/evidencia/fabrica-02/D.1/evidencia.md`); el código quedó en `7384152`
tras un segundo rebuild el mismo día (el primero, `359f1f8`, era anterior al
PR #283). **No es puramente expansiva**: recrea el índice
parcial `harvest_job_en_vuelo` (gana la fase `hermanas_negadas`) y suelta el
CHECK `goal_harvest_completo` (entra el trigger bid-solo + simétrico de
grupo) — ambos dentro de la transacción. No recrear ni borrar tablas para
revertirla.

Backup del schema antes (patrón de la 0003: staging + verificación, el
archivo final solo aparece con el `CREATE TABLE` y el marcador de cierre):

```bash
ssh goncloud 'set -eu; D=/mnt/data/appdata/orbit/backups; \
  STAMP=$(date -u +%Y%m%d-%H%M%S); TMP="$D/.pre0038_$STAMP.sql.tmp"; \
  OUT="$D/pre0038_harvest_biblio_$STAMP.sql"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only \
    -t public.harvest_job -t public.apply_attempt -t public.ads_optimizer_goal \
    -t public.campana_grupo_rol -t public.keyword_biblioteca \
    -t public.negative_biblioteca > "$TMP"; \
  [ -s "$TMP" ] \
    && grep -q "CREATE TABLE public.harvest_job" "$TMP" \
    && tail -5 "$TMP" | grep -q "PostgreSQL database dump complete" \
    || { echo "DUMP INVALIDO"; rm -f "$TMP"; exit 1; }; \
  chmod 600 "$TMP"; mv "$TMP" "$OUT"; ls -l "$OUT"'
```

Aplicar la DDL en transacción única y comprobar lo que se suelta y lo que
entra:

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0038_fabrica_hermanas_biblioteca.sql

ssh goncloud 'docker exec orbit-db-1 psql -U orbit -d orbit -P pager=off -c "
SELECT conname FROM pg_constraint
 WHERE conrelid IN ('"'"'public.harvest_job'"'"'::regclass,
                    '"'"'public.apply_attempt'"'"'::regclass,
                    '"'"'public.ads_optimizer_goal'"'"'::regclass)
   AND conname IN ('"'"'harvest_job_fase_check'"'"','"'"'attempt_tipo_valido'"'"','"'"'goal_harvest_completo'"'"');
SELECT pg_get_expr(indpred, indrelid) FROM pg_index
 WHERE indexrelid = '"'"'public.harvest_job_en_vuelo'"'"'::regclass;
SELECT tgrelid::regclass, tgname FROM pg_trigger
 WHERE tgrelid IN ('"'"'public.ads_optimizer_goal'"'"'::regclass,
                   '"'"'public.campana_grupo_rol'"'"'::regclass)
   AND tgname IN ('"'"'ads_optimizer_goal_harvest_coherente'"'"',
                  '"'"'campana_grupo_rol_destino_protegido'"'"')
   AND tgenabled <> '"'"'D'"'"' AND NOT tgisinternal
 ORDER BY 1, 2;"'
```

Lo que se suelta: el CHECK `goal_harvest_completo` (la consulta de
`pg_constraint` debe traer EXACTAMENTE dos filas — `harvest_job_fase_check`
y `attempt_tipo_valido` — y ninguna de `goal_harvest_completo`; el trigger
nuevo admite sus dos estados viejos más el bid-solo). Lo que entra:
`hermanas_negadas` en el CHECK de fase y en el predicado del índice,
`hermana` en `attempt_tipo_valido`, y las dos parejas exactas
`(ads_optimizer_goal, ads_optimizer_goal_harvest_coherente)` y
`(campana_grupo_rol, campana_grupo_rol_destino_protegido)` habilitadas
(`tgenabled <> 'D'`). La tabla `apply_attempt` tiene ~30 filas: el escaneo
del ADD CONSTRAINT es instantáneo; si creció mucho, aplicar en ventana
controlada. Precondición de datos (D.1 la verifica al desplegar): ningún goal
puede estar en parcial distinto de los tres estados — 0001 lo impedía por
CHECK, así que en una base sana no hay nada que conciliar.

### Migración 0039: motor de precios (REPRICING 01 A.0)

`0039_precio.sql` crea las cinco tablas del motor (`precio_goal`,
`precio_decision`, `precio_cotizacion`, `precio_envio_muestra`,
`precio_cambio`), amplía `apply_cap_de_config` con los tres motores
`precio:*` y trae su bloque DO candado bajo `SET ROLE` (no deja filas).
**Esta fase NO la aplica a producción**: aplicarla es la fila D.1 del plan
(`plans/repricing-01.md`) y la corre el dueño con GO. Hasta entonces esta
sección es procedimiento sellado, no estado aplicado.

**No es puramente expansiva**: declara `UNIQUE (id, platform)` en `listing`
(lo único que toca de una tabla existente — la FK compuesta de `precio_goal`
lo exige) y reemplaza `apply_cap_de_config` por `CREATE OR REPLACE` (los ocho
mapeos de Ads idénticos + los tres de precio). Revertirla es restaurar el
dump (las tablas son append-only: no se borran filas para revertir).

Backup del schema antes: `--schema-only` COMPLETO (patrón de la 0003:
staging + verificación, el archivo final solo aparece con el marcador de
cierre). A propósito sin `-t`: un `pg_dump -t` solo saca tablas y **no
respalda `apply_cap_de_config`**, que esta migración reemplaza — «revertir es
restaurar el dump» no revertiría la función:

```bash
ssh goncloud 'set -eu; D=/mnt/data/appdata/orbit/backups; \
  STAMP=$(date -u +%Y%m%d-%H%M%S); TMP="$D/.pre0039_$STAMP.sql.tmp"; \
  OUT="$D/pre0039_precio_$STAMP.sql"; \
  docker exec orbit-db-1 pg_dump -U orbit -d orbit --schema-only > "$TMP"; \
  [ -s "$TMP" ] \
    && grep -q "CREATE TABLE public.listing" "$TMP" \
    && grep -q "CREATE OR REPLACE FUNCTION public.apply_cap_de_config" "$TMP" \
    && tail -5 "$TMP" | grep -q "PostgreSQL database dump complete" \
    || { echo "DUMP INVALIDO"; rm -f "$TMP"; exit 1; }; \
  chmod 600 "$TMP"; mv "$TMP" "$OUT"; ls -l "$OUT"'
```

Aplicar la DDL en transacción única y comprobar lo que entra **como lector**
(patrón `PSQL`/`PSQL_READ` de D.1: la verificación corre con el DSN de solo
lectura, no como superusuario):

```bash
PSQL='docker exec -i orbit-db-1 psql -U orbit -d orbit -X -P pager=off'
PSQL_READ='sh -c '"'"'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off'"'"''

ssh goncloud "$PSQL -v ON_ERROR_STOP=1 -1" < migrations/0039_precio.sql

ssh goncloud "$PSQL_READ \
  -c \"SELECT tablename FROM pg_tables WHERE schemaname = 'public' \
        AND tablename LIKE 'precio\\_%' ORDER BY 1;\" \
  -c \"SELECT conname FROM pg_constraint WHERE conrelid = 'public.listing'::regclass \
        AND conname = 'listing_id_platform_key';\" \
  -c \"SELECT pg_get_expr(indpred, indrelid) FROM pg_index \
        WHERE indexrelid IN ('public.precio_goal_un_vigente'::regclass, \
                             'public.precio_cambio_abierto_unico'::regclass);\" \
  -c \"SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint \
        WHERE conrelid = 'public.precio_goal'::regclass \
          AND conname = 'precio_goal_sin_solape';\" \
  -c \"SELECT tgrelid::regclass, tgname FROM pg_trigger \
        WHERE tgrelid IN ('public.precio_cotizacion'::regclass, \
                          'public.precio_decision'::regclass, \
                          'public.precio_cambio'::regclass) \
          AND tgfoid IN ('public.precio_cotizacion_coherente()'::regprocedure, \
                         'public.precio_decision_coherente()'::regprocedure, \
                         'public.precio_cambio_coherente()'::regprocedure, \
                         'public.precio_cotizacion_fecha_utc()'::regprocedure) \
          AND NOT tgisinternal AND tgenabled <> 'D' ORDER BY 1, 2;\" \
  -c \"SELECT apply_cap_de_config('precio:amazon_mx') AS cap_mx, \
             apply_cap_de_config('precio:amazon_us') AS cap_us, \
             apply_cap_de_config('precio:meli') AS cap_meli;\" \
  -c \"SELECT tgrelid::regclass, tgname FROM pg_trigger \
        WHERE tgrelid IN ('public.precio_goal'::regclass, 'public.precio_decision'::regclass, \
                          'public.precio_cotizacion'::regclass, \
                          'public.precio_envio_muestra'::regclass, \
                          'public.precio_cambio'::regclass) \
          AND NOT tgisinternal AND tgenabled <> 'D' ORDER BY 1, 2;\" \
  -c 'SELECT count(*) AS goal FROM precio_goal;' \
  -c 'SELECT count(*) AS decision FROM precio_decision;' \
  -c 'SELECT count(*) AS cambio FROM precio_cambio;\"'
```

Lo que entra: las cinco tablas, `listing_id_platform_key` en `listing`
(índice redundante con la PK que toma un lock breve sobre una tabla viva:
aplicar en ventana tranquila), los
dos índices parciales (un vigente / un abierto), los tres motores `precio:*`
en `apply_cap_de_config` (cada `SELECT` devuelve el cap de la config vigente;
verificado por `tests/test_schema.py`) y los triggers de las cinco tablas
habilitados. Los tres conteos deben dar **cero** (el bloque DO revierte lo
que inserta). Las tablas `precio_*` tienen ~0 filas: el `ADD CONSTRAINT` es
instantáneo; si crecieron mucho, aplicar en ventana controlada. Precondición
de datos (D.1 la verifica al desplegar): ningún duplicado de `(id, platform)`
en `listing` — `id` es PK, así que en una base sana no hay nada que
conciliar. El `EXCLUDE` con enum + `daterange` (primero del repo que combina
los dos) corrió en PostgreSQL 16 local; D.1 lo confirma en la versión de
producción antes de aplicar.

## Correr los tests desde la máquina dev (túnel SSH)

La suite de integración (`test_migracion_rechaza_en_vivo`) necesita un
Postgres real; en CI lo provee un service container, y desde la máquina dev
se usa la base viva por túnel. El test crea/borra una base temporal
`orbit_schema_test_*` y un rol centinela **con el rol `orbit_test`**
(CREATEDB/CREATEROLE, sin superusuario): una regresión del test no puede
tocar nada fuera de lo que ese rol alcanza. El DSN de `orbit_test` apunta a
la base `postgres` — **nunca a `orbit`** — y el test no toca la base
`orbit`.

```bash
# 1. túnel (en background / otra terminal); 5433 para no chocar con un
#    listener viejo en 5432
ssh -N -L 5433:127.0.0.1:5432 goncloud

# 2. suite con el DSN de orbit_test (la password sale del .env del server,
#    no se escribe en la línea de comando ni en ningún archivo local)
ORBIT_TEST_DSN="$(ssh goncloud 'echo "postgresql://orbit_test:$(sed -n \
  "s/^ORBIT_DSN_TEST=postgresql:\/\/orbit_test:\([^@]*\)@.*/\1/p" \
  /mnt/data/appdata/orbit/.env)@localhost:5433/postgres"')" \
  PYTHONPATH=. pytest -q      # meta: 30 passed, 0 skipped
```

**Gotcha del túnel muerto:** un `ssh` viejo puede quedar escuchando
`127.0.0.1:5432` sin conectar a nada (acepta TCP y no habla protocolo). El
probe del suite habla protocolo Postgres real con timeout corto, así que un
túnel muerto **skipea** el test en vez de colgarlo — si ves el test skipeado
"sin razón", manda el ssh viejo al diablo:

```bash
netstat -ano | grep ":5432.*LISTEN"     # anota el PID
taskkill //PID <pid> //F                # Windows; en Linux: kill <pid>
```

## Backups

- **Cron en goncloud (root):** `30 3 * * * /mnt/data/appdata/orbit/backup.sh`
  (log en `/mnt/data/appdata/orbit/backup.log`).
- Cada corrida publica un **directorio fechado** `backups/orbit_YYYY-MM-DD/`
  (dir `700`, archivos `600`) con la pareja completa:
  - `orbit_YYYY-MM-DD.dump` — datos+esquema (`pg_dump -Fc`).
  - `orbit_globals_YYYY-MM-DD.sql` — roles del cluster con sus passwords
    hasheadas (`pg_dumpall --globals-only`). **`pg_dump` NO dumpea roles**:
    sin este archivo, una recuperación de cluster revive el esquema pero no
    los usuarios.
- Propiedades del script (todas nacidas de hallazgos de revisión):
  - `flock`: una sola ejecución a la vez (un manual no pisa al cron).
  - staging con `mktemp -d` + `trap`: los temporales jamás quedan.
  - publicación que **conserva la versión previa**: la pareja se aparta a
    `.old_`, se publica la nueva con un rename y, si algo falla, rollback
    automático a la de ayer — nunca te quedas sin backup válido.
  - rotación de **14** directorios (la lección de `competitive.db`: la
    rotación de 3 se comió el histórico).
- Verificar que un dump sirve (rápido, solo catálogo):

```bash
ssh goncloud 'docker exec -i orbit-db-1 pg_restore --list < \
  /mnt/data/appdata/orbit/backups/orbit_YYYY-MM-DD/orbit_YYYY-MM-DD.dump | head'
```

- Verificar de verdad (restauración real a base descartable): conserva el
  código de salida de `pg_restore`, limpia SIEMPRE la base temporal (incluso
  si el restore falla, con `FORCE` por si algo quedó conectado) y sólo dice
  `VERIFY_OK` cuando TODO salió bien — el exit code viaja hasta tu shell:

```bash
ssh goncloud 'D=/mnt/data/appdata/orbit/backups/orbit_YYYY-MM-DD; \
  docker exec orbit-db-1 psql -U orbit -d postgres -qc "DROP DATABASE IF EXISTS orbit_verify_tmp"; \
  docker exec orbit-db-1 psql -U orbit -d postgres -qc "CREATE DATABASE orbit_verify_tmp"; \
  rc=0; docker exec -i orbit-db-1 pg_restore -U orbit -d orbit_verify_tmp --exit-on-error \
    < "$D/orbit_YYYY-MM-DD.dump" || rc=$?; \
  docker exec orbit-db-1 psql -U orbit -d postgres -qc "DROP DATABASE orbit_verify_tmp WITH (FORCE)"; \
  if [ "$rc" -eq 0 ]; then echo VERIFY_OK; else echo VERIFY_FAIL; exit "$rc"; fi'
```

**Contenido de `/mnt/data/appdata/orbit/backup.sh`** (versionado aquí
porque el runbook debe poder reconstruirlo):

```bash
#!/bin/bash
# Backup diario de orbit (v4, hallazgos CodeRabbit PR #5 y #6):
# - flock: una sola ejecucion a la vez (cron + manual nunca se pisan)
# - staging con mktemp -d y trap: basura temporal jamas queda
# - publicacion con CONSERVACION de la version previa: si algo falla en el
#   remplazo, la copia de ayer sigue intacta (rollback automatico)
# - rotacion de 14 sobre directorios
set -euo pipefail
DIR=/mnt/data/appdata/orbit/backups
STAMP=$(date +%F)
umask 077
exec 9>/mnt/data/appdata/orbit/.backup.lock
flock -n 9 || { echo "$(date -Is) otro backup ya corre; salgo"; exit 0; }
mkdir -p "$DIR"; chmod 700 "$DIR"
STAGE=$(mktemp -d "$DIR/.staging.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
docker exec orbit-db-1 pg_dump -U orbit -Fc orbit > "$STAGE/orbit_$STAMP.dump"
docker exec orbit-db-1 pg_dumpall -U orbit --globals-only > "$STAGE/orbit_globals_$STAMP.sql"
FINAL="$DIR/orbit_$STAMP"
OLD="$DIR/.old_$STAMP"
if [ -e "$FINAL" ]; then mv -T "$FINAL" "$OLD"; fi   # aparta la version previa
if mv -T "$STAGE" "$FINAL"; then                    # publica la nueva
  rm -rf "$OLD"; trap - EXIT
else
  [ -e "$OLD" ] && mv -T "$OLD" "$FINAL"            # rollback a la previa
  echo "$(date -Is) backup FALLO al publicar; se conserva la version previa" >&2
  exit 1
fi
ls -1dt "$DIR"/orbit_[0-9]*[0-9]/ | tail -n +15 | xargs -r rm -rf
echo "$(date -Is) backup OK: $FINAL ($(stat -c%s "$FINAL/orbit_$STAMP.dump") + $(stat -c%s "$FINAL/orbit_globals_$STAMP.sql") bytes)"
```

### Backup pre-cutover (ORBIT 04 4.4, 2026-08-28)

Snapshot NOMBRADO del estado justo antes del cutover a live, **fuera de la
rotación de 14** (su nombre no calza el patrón `orbit_NNNN-NN-NN/` que la
rotación borra): `backups/precutover_orbit04_2026-08-28/` (dir 700, archivos
600, root). Contenido:

- `orbit_precutover_orbit04_2026-08-28.dump` — pg_dump -Fc completo
  (368 entradas TOC, 22 TABLE DATA).
- `orbit_globals_precutover_orbit04_2026-08-28.sql` — roles del cluster.
- `ad_entity_state_2026-08-28.csv` — COPY CSV del cache de estado
  (5,899 filas + header).
- `listas_amazon/listas_por_plataforma.json` — snapshot de las listas v3 de
  Amazon (keywords/negativeKeywords/targets, 2 plataformas, agrupado por
  campaña), capturado con el cliente de LECTURA (POST list, cero
  mutaciones).

**Cómo se produce** (como root en goncloud, `umask 077`, `D=backups/precutover_<tag>/`):

1. Dump + globals: los mismos dos comandos de `backup.sh` (pg_dump -Fc y
   `pg_dumpall --globals-only` vía `docker exec orbit-db-1`), con el
   nombre `orbit_precutover_<tag>.dump` / `orbit_globals_precutover_<tag>.sql`.
2. CSV del cache: `docker exec orbit-db-1 psql -U orbit -d orbit -c
   "\copy ad_entity_state TO STDOUT CSV HEADER" > "$D/ad_entity_state_<fecha>.csv"`.
3. Listas de Amazon (SOLO lectura): el tool **`tools/snapshot_listas.py`**
   (ORBIT 05 preflight 1.3, decisión sellada 3 del preflight: el snapshot se
   produce con un tool del repo con test, jamás código inline) recorre
   `app.ads.structure.perfiles_aceptados` y lista `/sp/keywords/list`,
   `/sp/negativeKeywords/list` y `/sp/targets/list` con
   `AdsClient.list_objects` (paginación completa por nextToken, cero
   mutaciones), agrupa por `campaignId` y escribe
   `$D/listas_amazon/listas_por_plataforma.json` (el tool fuerza `umask
   077`: dir 700, archivo 600). Flags: `--out <dir>` (escribe el JSON) o
   `--solo-conteos` (imprime el resumen por stdout, no escribe archivo);
   `--platform amazon_us|amazon_mx` opcional. Receta de contenedor (patrón
   §11d; el tool no va en la imagen):
   `cat tools/snapshot_listas.py | ssh goncloud 'docker exec -i orbit-app-1
   sh -c "cat > /tmp/snapshot_listas.py"'` y correr con
   `PYTHONPATH=/app python /tmp/snapshot_listas.py --out /tmp/listas`
   (runbook completo en el docstring del tool, con el `docker cp` de salida
   y la limpieza). **Prerequisito de imagen**: la receta simple (solo el
   tool en `/tmp`, `PYTHONPATH=/app`) exige que la imagen incluya el commit
   que trae el tool (`app.ads.structure` con `listar_todo` pública y
   `PATH_NEGATIVE_KEYWORDS`). Si la imagen es anterior, montar el árbol del
   commit en `/tmp` y correrlo desde ahí — el bootstrap del tool pone su
   propio árbol primero en `sys.path`, sin mezclar módulos (variante
   verificada en la corrida real del 2026-08-28; receta completa en el
   docstring del tool). **Historia**: en 4.4 el snapshot del 2026-08-28 corrió
   como código inline dentro del contenedor (hueco declarado del runbook —
   hallazgo Greptile PR #40); ORBIT 05 preflight 1.3 lo aterrizó como tool
   del repo con test de sus partes puras.

**Cómo se verifica — los CUATRO artefactos, no solo el dump** (CodeRabbit
PR #40): `VERIFY_OK` solo se emite si pasan todos:

1. Dump: receta VERIFY_OK de arriba apuntando `$D` al directorio y el
   nombre del dump a `orbit_precutover_<tag>.dump` (ojo, NO es el
   `orbit_YYYY-MM-DD.dump` de la receta): restore real a `orbit_verify_tmp`
   con `--exit-on-error`, `pg_restore --list` (CON `docker exec -i`), y
   conteos de `apply_queue`/`apply_attempt`/`config_version`/`decision`/
   `ad_entity_state` idénticos a producción ANTES del `DROP ... WITH (FORCE)`.
2. Globals: `grep -c '^CREATE ROLE' "$D/orbit_globals_precutover_<tag>.sql"`
   = el número de roles del cluster (`SELECT count(*) FROM pg_roles WHERE
   rolname NOT LIKE 'pg_%'`).
3. CSV: `wc -l` = `SELECT count(*) FROM ad_entity_state` + 1 (header).
4. JSON: `python3 -c 'import json;json.load(open(...))'` sin error y los
   totales por plataforma/recurso = `SELECT platform, kind, count(*) FROM
   ad_entity WHERE kind IN ('keyword','product_target') GROUP BY 1,2`
   (incluye ARCHIVED: el LIST los devuelve); negativeKeywords sin
   referencia en cache — solo se registra el conteo.

Verificado 2026-08-28 (ensayo): dump VERIFY_OK con 4/29/9/977/5,899; CSV
5,900 líneas; JSON cargable con MX 2,645 kw / 861 targets y US 1,336 kw /
549 targets = `ad_entity` exacto (conciliado por el lead); globals 3,236 B
(conteo de roles NO verificado en el ensayo — se exige desde el real).

**Cómo se restaura**: ver "Recuperación desde backups" (mismo mecanismo;
los globals van ANTES del dump para revivir los roles).

**Vigencia (codex 4.4, hallazgo alto)**: el snapshot del 2026-08-28 es el
ENSAYO del runbook, no el punto de restauración del flip: la base y las
listas de Amazon cambian a diario y el flip es ~2026-09-07 o después. El
día del cutover se REPITE este mismo procedimiento (mismos 4 artefactos +
VERIFY_OK + conteos del día) en `backups/precutover_orbit05_<fecha>/`
ANTES del discard masivo (checklist APPLY.md §12 ítem 4). El directorio del
ensayo se conserva como referencia y como respaldo del estado pre-4.4.

Instalación del cron (idempotente):

```bash
ssh goncloud '( crontab -l 2>/dev/null | grep -v "/mnt/data/appdata/orbit/backup.sh" ; \
  echo "30 3 * * * /mnt/data/appdata/orbit/backup.sh >> /mnt/data/appdata/orbit/backup.log 2>&1" ) | crontab -'
```

## Smoke de candados (chequeo rápido de que el esquema defiende)

Sobre la base real, con transacciones que se revierten:

```sql
BEGIN;
INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate)
    VALUES ('2026-08-21', 'USD', 'MXN', 18.5);
UPDATE fx_rate SET rate = 19.0;   -- DEBE reventar: restrict_violation (append-only)
ROLLBACK;

TRUNCATE decision CASCADE;        -- DEBE reventar: restrict_violation (trigger)
SELECT count(*) FROM fx_resolve('2026-01-01','USD','MXN');  -- sin datos: 0 filas
```

Nota medida en vivo: `TRUNCATE decision` **sin** CASCADE lo frena la FK
(`harvest_job` referencia `decision`) ANTES de que dispare el trigger —
rechazo igual de duro por otro candado; con CASCADE el que reventa es el
trigger `prohibir_mutacion`. Y el `UPDATE` de `fx_rate` necesita una fila
presente (los triggers append-only son row-level: con la tabla vacía no
disparan), por eso el INSERT dentro de la transacción que se revierte.

## Reconstruir desde cero (app + base nuevas, sin backups)

1. En goncloud: `mkdir -p /mnt/data/appdata/orbit/{backups,archive,logs,secrets}`.
   Permisos: `secrets/` `700`, `backups/` `700`. Archivos de secretos `600`
   root — **jamás** `chmod` para acomodar un usuario del contenedor.
2. Copiar del repo al dir de deploy: `docker-compose.yml`, `Dockerfile`,
   `.dockerignore`, `pyproject.toml`, `uv.lock`, `app/` y `migrations/`
   (el paso 5 aplica `migrations/0001_initial.sql`: sin la carpeta, la
   base nueva no puede completar el esquema — hallazgo CodeRabbit).
3. Crear `.env` (600) con `POSTGRES_USER=orbit` y un
   `POSTGRES_PASSWORD` generado en el server (≥24 chars, nunca commiteado):
   ```bash
   ssh goncloud 'cd /mnt/data/appdata/orbit && umask 177 && \
     { echo "POSTGRES_USER=orbit"; echo "POSTGRES_PASSWORD=$(head -c 48 \
     /dev/urandom | base64 | tr -dc "A-Za-z0-9" | head -c 32)"; } > .env'
   ```
4. `docker compose up -d` y esperar `pg_isready` + `curl 127.0.0.1:8010/health`
   (ver "Levantar"). `ss -lntp` debe mostrar 5432 y 8010 **solo** en
   127.0.0.1.
5. Aplicar las migraciones EN ORDEN (`0001_initial.sql`, `0002_apply.sql`,
   `0003_goal_bounds_explicit.sql`, `0004_ad_entity_kind_product_ad.sql`,
   `0005_v_tacos_grano_unico.sql` — ver "Aplicar migraciones"). Omitir 0003
   re-crearia los DEFAULT USD 0.10/2.50 que el sellado 2 del preflight
   elimino: un goal MXN volveria a nacer con techo 2.50. Omitir 0004 deja el
   enum sin `product_ad` y la ingesta de estructura de la 0.4 revienta al
   insertar ese kind. Omitir 0005 reproduce el doble conteo de gasto en
   `v_tacos` (TACoS inflado ~2x) en la instalacion nueva.
6. Crear los usuarios LOGIN por servicio + `orbit_test` (ver "Usuarios y
   DSN": comandos exactos arriba).
7. Poblar `secrets/` (amazon_ads_config.json + amazon_ads_tokens.json,
   `api_write_token` — el token estático de los endpoints de escritura,
   ver "Rotación del token de escritura" — y `telegram.json` — OPCIONAL,
   canal de avisos de ORBIT 04 (3.3): `{"bot_token": "...", "chat_id": "..."}`
   con `600`; sin el archivo el canal queda deshabilitado SIN error (los
   avisos no salen y no generan nota) —, etc.; nombres verificados en el
   server, valores jamás al repo).
8. Instalar el backup: `backup.sh` + cron de **root** (ver "Backups").
9. Instalar los 3 crons de Orbit en el crontab de **gon** (ver "Crons de
   Orbit") — ADITIVO, no pisa accounting.
10. Verificar: suite completa por túnel + smoke de candados +
    `curl 127.0.0.1:8010/health` + una corrida manual de cada job del CLI.

## Recuperación desde backups (pérdida del volumen) — pasos verificados por separado, NO punta a punta

> **Honestidad primero:** este procedure NO fue ejecutado punta a punta
> contra un volumen realmente destruido. Cada paso se verificó por separado
> sobre el cluster activo (restore del dump a base vacía existente = exit 0
> con permisos idénticos; restore de globals = no-op tolerante con la
> verificación de roles de abajo). Destruir el volumen real para el ensayo
> completo queda como mantenimiento futuro con datos ya presentes.

`docker compose down -v` destruye datos — nunca hacerlo salvo
reconstrucción deliberada. Pasos:

1. Volumen nuevo + cluster nuevo: `docker compose up -d` (initdb crea el
   superusuario `orbit` con `POSTGRES_PASSWORD` del `.env`).
2. **Primero los globals** (los roles NO vienen en el `pg_dump` de datos, y
   las ACLs del dump referencian `app_*` — sin los roles, el restore
   revienta):
   ```bash
   ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d postgres \
     -v ON_ERROR_STOP=0 < /mnt/data/appdata/orbit/backups/orbit_FECHA/orbit_globals_FECHA.sql'
   ```
   Esperado y tolerable: `ERROR: role "orbit" already exists` (lo creó el
   initdb). `ON_ERROR_STOP=0` calla CUALQUIER error, no solo el esperado —
   por eso el restore **no cuenta como done** hasta pasar el gate, que
   además del conteo valida ATRIBUTOS y MEMBRESÍAS (un globals parcial
   crea roles sin grants y igual cuenta 9 — el gate v2 lo atrapa):
   ```bash
   ssh goncloud "N=\$(docker exec orbit-db-1 psql -U orbit -d postgres -tAc \\
     \"SELECT count(*) FROM pg_roles WHERE rolname IN ('app_ingest','app_decide',\\
'app_read','app_admin','orbit_ingest','orbit_decide','orbit_read','orbit_admin','orbit_test')\"); \\
     ATTR=\$(docker exec orbit-db-1 psql -U orbit -d postgres -tAc \\
     \"SELECT count(*) FROM pg_roles WHERE (rolname LIKE 'app\\\_%' AND NOT rolcanlogin) OR \\
      (rolname='orbit_test' AND rolcreatedb AND rolcreaterole AND NOT rolsuper)\"); \\
     MEM=\$(docker exec orbit-db-1 psql -U orbit -d postgres -tAc \\
     \"SELECT count(*) FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid \\
      JOIN pg_roles u ON u.oid=m.member WHERE g.rolname='app_'||substr(u.rolname,7) \\
      AND u.rolname LIKE 'orbit\\\_%'\"); \\
     if [ \"\$N\" = 9 ] && [ \"\$ATTR\" = 5 ] && [ \"\$MEM\" = 4 ]; then echo GATE_ROLES_OK; \\
     else echo \"GATE_ROLES_FAIL (N=\$N ATTR=\$ATTR MEM=\$MEM): NO restaurar datos\"; exit 1; fi"
   ```
   El gate **detiene el procedure** (exit 1) antes del restore de datos si
   falta cualquiera de los 9 roles, si los `app_*` no son NOLOGIN, si
   `orbit_test` no tiene sus atributos, o si los 4 usuarios no son miembros
   de su rol `app_*` correspondiente.
3. **Después los datos**: la base `orbit` YA EXISTE vacía (el compose la
   crea en el initdb vía `POSTGRES_DB=orbit` — un `CREATE DATABASE orbit`
   aquí revienta con "already exists"). Solo restaurar encima:
   ```bash
   ssh goncloud 'docker exec -i orbit-db-1 pg_restore -U orbit -d orbit \
     --exit-on-error < /mnt/data/appdata/orbit/backups/orbit_FECHA/orbit_FECHA.dump'
   ```
   Verificado en vivo: exit 0, 19 tablas, y los permisos quedan idénticos
   (`app_read`: SELECT sí / UPDATE no).
4. **No reaplicar `0001`** después de restaurar: los `CREATE TYPE`/
   `CREATE TABLE` ya existen y revientan (la migración solo va en bases
   nuevas). Y **no inferir el estado de migraciones por la fecha del dump**:
   hoy no existe una fuente autoritativa de versiones, así que si algún día
   hay migraciones posteriores a `0001`, la recuperación se DETIENE aquí
   para revisión manual de qué migraciones faltan sobre el dump — no se
   aplica nada en automático.
5. Verificar como siempre: suite por túnel + smoke de candados.

## Archivo manual de keywords inertes (BIDS 01)

Las hojas sin tráfico no se ajustan (guarda `entidad_inerte` del ciclo):
se diagnostican en `v_entidad_inerte` (página `/inertes`) y, con go del
dueño, se archivan por lote con `tools/archiva_inertes.py`. Archivar en
Amazon es ARCHIVED (POST `/sp/keywords/delete`) y no se deshace: la
vuelta atrás es volver a crear la keyword (`--reponer`, invariante 7).
Cada lote exige su go literal y queda en el ledger
`keyword_archivo_manual` (migración `0014`).

```sh
# 1) ENSAYO (default): imprime la tabla del plan, cero HTTP.
#    Filtros: --plataforma amazon_mx|amazon_us (default: las dos),
#    --clasificacion (default: peso_muerto), --min-dias-sin-impresiones
#    (default: 30), --limite N. Solo kind='keyword'; los product_target
#    salen como excluidos (solo se reportan). El tool entra por stdin
#    (la imagen solo trae app/, como reactiva_campanas).
docker exec -i orbit-app-1 python - < tools/archiva_inertes.py

# 2) De verdad. --esperado N debe igualar las candidatas del ensayo
#    (anti-typo: si el plan cambió, se re-autoriza) y --go lleva el
#    literal que el dueño autorizó viendo el ensayo.
docker exec -i orbit-app-1 python - --acepto-mutacion-real \
  --esperado 12 --go "<literal del dueño>" < tools/archiva_inertes.py
```

Por keyword: LIST previo (si ya no está ENABLED se salta con nota, no
se pisa decisión ajena) → fila `planeado` en el ledger con commit
(intención durable antes del HTTP) → DELETE → readback LIST: ARCHIVED
→ `applied`; distinto → `failed` y el lote SE DETIENE (se revisa antes
de seguir). Cada mutación imprime una línea JSON (scrub) y al final la
reconciliación. DSNs: `ORBIT_DSN_READ` para el plan, `ORBIT_DSN_ADMIN`
para el ledger (los dos viven en el contenedor, como reactiva_campanas).

### La reversa: `--reponer <lote>`

Recrea cada fila `applied` del lote con su identidad del ledger
(adGroupId, campaignId, texto, matchType, bid con su moneda) en ENABLED
y la sella `repuesto` con el external nuevo. CREA keywords: habilita
gasto (igual que el harvest).

```sh
# ENSAYO (default): lista lo que repondría, cero HTTP.
docker exec -i orbit-app-1 python - --reponer inertes-2026-09-05 \
  < tools/archiva_inertes.py

# De verdad.
docker exec -i orbit-app-1 python - --reponer inertes-2026-09-05 \
  --acepto-mutacion-real < tools/archiva_inertes.py
```

Sin bid en el ledger la fila no se repone (no se inventa: queda
`applied` y el lote se detiene declarándolo).

Dos notas de operación (cross-review, residuales declarados):

- El lote es la unidad: dos corridas autorizadas el mismo día comparten
  `lote = inertes-YYYY-MM-DD` y `--reponer` las revierte juntas. Un lote
  por día; si hace falta partir por plataforma, se opera un día cada una.
- `reponer` no es idempotente entre el CREATE y el sello: ante
  `lote_detenido` en reposición, verificar en consola lo ya creado (el
  log trae ack + id nuevo) antes de reintentar. Igual para un `failed`
  con sospecha de eventual-consistency: si la consola muestra ARCHIVED,
  sellar a mano `UPDATE keyword_archivo_manual SET estado = 'applied',
  readback_estado = 'ARCHIVED' WHERE id = <fila>;` y reintentar el resto
  en un lote nuevo (la fila ya archivada se salta con nota por el LIST
  previo).

## Limpieza de product ads muertos (ORBIT 06)

Un product ad "muerto" apunta a una publicación que ya no existe: no gasta
(sin publicación no hay impresión) pero ensucia toda medición de cobertura.
`archivar-anuncios` los archiva; **archivar NO tiene reversa**.

El comando NO decide cuáles están muertos: recibe una lista EXPLÍCITA de
adIds. Es a propósito — Orbit todavía no distingue un anuncio muerto de un
producto real sin mapear (los dos se ven `listing_id IS NULL`), así que la
evidencia la pone el operador.

```sh
# 1) ENSAYO (default): no sale ninguna mutación, imprime lo que haría.
docker exec -i orbit-app-1 python -m app.cli archivar-anuncios \
  --platform amazon_mx --ids-file /tmp/muertos.txt

# 2) De verdad. La igualdad es EXACTA: 'liv', 'si' o 'LIVE' siguen siendo ensayo.
docker exec -i orbit-app-1 python -m app.cli archivar-anuncios \
  --platform amazon_mx --ids-file /tmp/muertos.txt --confirmar live
```

Salida por anuncio: `archivado` / `ya_estaba` / `no_existe` / `sin_confirmar`
/ `fallo`. `sin_confirmar` NO es un fallo: el list de Amazon es eventualmente
consistente y a veces tarda en reflejar el archivado — se re-corre el ensayo
un rato después y los que ya estén `ya_estaba` quedaron bien.

Después de archivar conviene `ingest structure` para que la base refleje el
estado nuevo.

### La reversa: `reponer-anuncios`

Archivar no se deshace en Amazon, así que la vuelta atrás es **volver a crear
el anuncio** en su mismo ad group (invariante 7; decisión del dueño
2026-08-30, a sabiendas de que crear un anuncio habilita gasto).

`archivar-anuncios` imprime, por anuncio, una columna de **reversa** con todo
lo que hace falta (`adGroupId`, `campaignId`, `sku`, `state`). Se copian esas
líneas a un archivo y:

```sh
# ENSAYO (default): no crea nada.
docker exec -i orbit-app-1 python -m app.cli reponer-anuncios \
  --platform amazon_mx --reversa-file /tmp/reversa.txt

# De verdad. CREA anuncios: habilita gasto.
docker exec -i orbit-app-1 python -m app.cli reponer-anuncios \
  --platform amazon_mx --reversa-file /tmp/reversa.txt --confirmar live
```

Sin `state` en la línea, repone en **PAUSED**: un anuncio repuesto en ENABLED
empieza a gastar solo, y eso lo enciende un humano.

Se crea por **SKU**, no por ASIN: la guía de Sponsored Products pide ASIN para
vendors/KDP y SKU para *sellers*, y el gate de perfiles solo acepta seller.

## Reputación v1 (REPUTACION 01 / A.7, lead 2026-09-08)

Desplegado: migraciones 0024–0027, código master, smoke 200 en
`/reputacion` y `/api/reputacion/resumen`.

**Migraciones** (expansivas, NO re-runnables; aplicadas en serie con
`-v ON_ERROR_STOP=1 -1`; los warnings de doble BEGIN son benignos):

```bash
for m in 0024_reputacion 0025_reputacion_sin_fk 0026_reputacion_preguntas_reobservacion 0027_reputacion_reviews_reobservacion; do
  ssh goncloud "docker exec -i orbit-db-1 psql -U orbit -d orbit -v ON_ERROR_STOP=1 -1 -q" < migrations/$m.sql
done
```

Backup pre-A.7: `backups/preA7_schema_<STAMP>.sql` (staging +
verificado: no-vacío + `CREATE TABLE public.listing` + marcador de
cierre) además del diario `orbit_2026-09-08/`.

**Secretos** (`secrets/`, 600, dueño 10001):

- `meli_tokens.json`: access/refresh + `client_id`/`client_secret`
  (copiados de `accounting.env` con GO del dueño 2026-09-08; backup
  `meli_tokens.json.bak-<STAMP>`). El refresh rota y reescribe el
  archivo (tmp + rename atómico).
- `apify_token.json`: `{"token": ...}` desde `APIFY_TOKEN` de
  `third_party.env`.

**Mount rw**: `docker-compose.yml` monta `secrets/` en `:rw` (antes
`:ro`) para que el refresh OAuth MeLi persista. Cambio autorizado por
el dueño; respaldo `docker-compose.yml.bak-A7-<STAMP>`; reversa =
restaurar el respaldo + `up -d --no-deps app` (pero el cron MeLi
moriría al 2º día: MeLi rota el refresh_token).

**Crons** (crontab de `gon`, ADITIVOS; respaldo
`archive/crontab-gon.<STAMP>`; accounting byte-igual verificado):

| UTC | job_key | comando |
|-----|---------|---------|
| 30 9 * * * | `reputacion:meli` | `docker exec orbit-app-1 python -m app.cli reputacion snapshot --fuente meli` → `logs/reputacion-meli.log` |
| 30 10 * * * | `reputacion:alertas` | `docker exec orbit-app-1 python -m app.cli reputacion alertas` → `logs/reputacion-alertas.log` |
| 0 10 * * 1 | `reputacion:amazon` | **NO INSTALADO** (comentado en crontab): instalar tras manual sana con crédito Apify (ver pendiente). |

**Reversa v1** (acta §8.4): deshabilitar los jobs reputación del
crontab, conservar datos y pantalla (v1 solo-lectura: nada que
deshacer). Código: `app.bak-predeploy-A7-<STAMP>` + rebuild.
Schema: tablas nuevas, sin rollback (no se tocó nada existente).

**Pendiente dueño**: subir plan Apify (cuenta en $0.000017,
`not-enough-usage-to-run-paid-actor`) → manual
`reputacion snapshot --fuente amazon` en lunes + instalar cron
semanal (descomentar línea). Estreno MeLi 2026-09-08: run 117/118
(236 filas: 65 snapshots + 119 reviews + 1 seller + 51 questions),
4 alertas abiertas (1 aviso + 3 críticas).

## FABRICA 02 (F2) — harvest por grupo: D.1 despliegue, D.2 terna del grupo 1, D.3 primer harvest y reversa

Runbook de las tres filas de cierre de `plans/fabrica-02.md` (v1.9). **Todo lo
corre el dueño con `!` desde la raíz del checkout, con `origin/master` ya
mergeado y con CI verde sobre ese SHA; el lead lee cada salida y la anota en
`docs/evidencia/fabrica-02/D.1/`, `D.2/` y `D.3/`.** Nada de esto se corre
"a ver si funciona": cada paso con mutación lleva dry-run antes y un go
literal del dueño. Producción sigue igual hasta que D.1 termina: 0038 no
está aplicada y nada de F2 corre en vivo.

Contexto de esquema: 0038 recrea el índice parcial `harvest_job_en_vuelo`
(gana la fase `hermanas_negadas`), suelta el CHECK `goal_harvest_completo`
(entra el trigger bid-solo + simétrico de grupo) y da GRANTs de biblioteca a
`app_decide`. Detalle y backup del schema en §«Migración 0038» de arriba;
este runbook lo referencia y agrega lo que la fila D.1 exige.

Convenciones de los comandos: `PSQL` es la sesión de administración de la
base y `PSQL_READ` la de solo lectura. Defínelas una vez en la terminal:

```bash
PSQL='docker exec -i orbit-db-1 psql -U orbit -d orbit -X -P pager=off'
PSQL_READ='sh -c '"'"'DSN=$(docker exec orbit-app-1 printenv ORBIT_DSN_READ); docker exec -i orbit-db-1 psql "$DSN" -X -P pager=off'"'"''
```

### D.1.0 Precondiciones (solo lectura, sin go)

1. **SHA y ventana.** Fija en una variable el SHA **completo** que el lead
   aprobó al cerrar el momento de merge (CI de master verde sobre él) y
   comprueba que `origin/master` sigue ahí; todo lo que sigue usa
   `$APROBADO`, nunca `origin/master`, para que un push posterior no cambie
   lo que se despliega:

   ```bash
   APROBADO=<sha completo aprobado por el lead>
   git fetch -q origin && [ "$(git rev-parse origin/master)" = "$APROBADO" ] \
     && echo "ok: origin/master = $APROBADO" || echo "FALLO: origin/master avanzó; no despliegues"
   ```

   Ventana: lejos del ciclo de Ads (08:40
   UTC) y de las ingestas (05:00–07:20 UTC); entre 09:30 y 15:00 UTC o
   después de las 16:00 UTC.
2. **Cola sin harvest en vuelo** (patrón orbit-05 1.3). Las dos consultas
   deben dar `0`:

   ```bash
   ssh goncloud "$PSQL -c \"SELECT count(*) AS harvest_no_terminales FROM apply_queue
     WHERE kind = 'harvest' AND estado NOT IN ('applied','failed','vetoed','discarded');\" \
     -c \"SELECT count(*) AS jobs_en_vuelo FROM harvest_job
     WHERE fase IN ('pending','negative_created','exact_created');\""
   ```

   Si hay filas, se esperan (vencen solas) o se resuelven por `/cortes`
   antes; **no se despliega encima de un harvest a medias**.
3. **Goals del grupo 1 en sombra.** Ningún goal del grupo puede estar
   `live`; el resultado esperado es `shadow` u `off` en todas las filas:

   ```bash
   ssh goncloud "$PSQL_READ -c \"SELECT r.grupo_id, r.rol, g.id AS goal_id, g.mode, g.enabled,
       g.harvest_campaign_id, g.harvest_ad_group_id, g.harvest_default_bid
     FROM campana_grupo_rol r JOIN ads_optimizer_goal g ON g.ad_entity_id = r.ad_entity_id
     WHERE r.grupo_id = 1 ORDER BY r.rol;\""
   ```

   Anota la salida: es el estado «antes» de D.2 (terna presente o NULL por
   goal).
4. **Ningún goal en estado parcial** (precondición de datos del trigger
   nuevo; 0001 lo impedía por CHECK, debe dar `0`):

   ```bash
   ssh goncloud "$PSQL_READ -c \"SELECT count(*) AS goals_parciales FROM ads_optimizer_goal
     WHERE (harvest_campaign_id IS NULL) <> (harvest_ad_group_id IS NULL)
        OR ((harvest_campaign_id IS NULL) AND harvest_default_bid IS NOT NULL
            AND scope = 'platform');\""
   ```

   (En grupo, «bid-solo» —terna NULL con bid— es el estado que 0038
   legaliza; fuera de grupo no debería existir todavía.)
5. **Backup nocturno presente** (cron de root 03:30) y espacio:

   ```bash
   ssh goncloud 'ls -la /mnt/data/appdata/orbit/backups | tail -4; df -h /mnt/data | tail -1'
   ```
6. **Caps vigentes** (para decidir el nuevo con el número a la vista):

   ```bash
   ssh goncloud "$PSQL_READ -c \"SELECT id, label, created_at,
       settings->'ads_apply_cap_amazon_mx_harvest'  AS cap_harvest_mx,
       settings->'ads_apply_cap_amazon_us_harvest'  AS cap_harvest_us,
       settings->'ads_apply_cap_amazon_mx_negative' AS cap_negative_mx,
       settings->'ads_apply_cap_amazon_us_negative' AS cap_negative_us,
       settings->'ads_optimizer_mode' AS modo
     FROM config_version ORDER BY id DESC LIMIT 1;\""
   ```

   Fíjate en el **tipo JSON** con que están guardados los caps (número o
   texto): el nuevo se escribe con el mismo tipo.

### D.1.1 Decisión del cap y config nueva (go del dueño)

Regla de la fila D.1: los caps diarios de harvest se **bajan al arranque**.
Multiplicador a la vista: **una unidad de harvest son hasta cinco
mutaciones nuevas de ida** (el negativo en el origen, la keyword en la
exacta y hasta tres negativos en las hermanas). Referencia del plan: 2
harvest/día ≈ 10 escrituras máximas de ida por plataforma. **Los negativos
de hermanas no cuentan contra el cap de `negative`**: van al ledger como
tipo `hermana` con `quota_cobrada = false`; el cap de `negative` sigue
midiendo solo los negativos propios del ciclo.

Propuesta del lead (el número lo fija el dueño): `amazon_mx` = **2**,
`amazon_us` = **2** (US no tiene grupo todavía; sus harvests siguen
resolviendo por terna y el cap bajo solo acota). Se escribe como
`config_version` nueva, append-only, copiando la vigente (patrón APPLY §11c;
no existe camino del motor para cambiar caps):

```bash
# Dry-run: lo que quedaría, sin escribir.
ssh goncloud "$PSQL_READ -c \"SELECT settings || jsonb_build_object(
    'ads_apply_cap_amazon_mx_harvest', 2, 'ads_apply_cap_amazon_us_harvest', 2)
  FROM config_version ORDER BY id DESC LIMIT 1;\""

# Go del dueño (literal en el label). Si los caps vigentes están como TEXTO,
# usa '2' en vez de 2 en los dos valores.
ssh goncloud "$PSQL -c \"INSERT INTO config_version (label, settings)
  SELECT 'F2 D.1 caps harvest bajados: <literal del dueño>',
         settings || jsonb_build_object('ads_apply_cap_amazon_mx_harvest', 2,
                                        'ads_apply_cap_amazon_us_harvest', 2)
  FROM config_version ORDER BY id DESC LIMIT 1
  RETURNING id, label, settings->'ads_apply_cap_amazon_mx_harvest',
            settings->'ads_apply_cap_amazon_us_harvest';\""
```

Verificación: `GET /api/dashboard/salud` muestra la quota de `harvest` con
`cap = 2` y `fuente = config_vigente` (si la fila de quota de hoy ya nació
con el cap viejo, el cap nuevo rige desde la fila de mañana; hoy nada sale
en vivo, así que no importa):

```bash
ssh goncloud 'curl -fsS http://127.0.0.1:8010/api/dashboard/salud' | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(json.dumps({k: v.get("quota") for k, v in d["plataformas"].items()}, indent=1)[:1200])'
```

### D.1.2 Backup del schema

Corre el bloque de backup de §«Migración 0038» tal cual (dump `--schema-only`
de `harvest_job`, `apply_attempt`, `ads_optimizer_goal`, `campana_grupo_rol`,
`keyword_biblioteca`, `negative_biblioteca`, con verificación del `CREATE
TABLE` y del marcador de cierre, `chmod 600`). Anota el nombre del archivo
resultante en E/D.1. Los datos no se tocan: 0038 solo altera constraints,
índice, triggers y GRANTs (su bloque DO inserta y borra semillas dentro de
la transacción).

### D.1.3 Migración 0038 en una transacción y verificación como lector

Aplicar y comprobar lo que se suelta y lo que entra con los dos comandos de
§«Migración 0038» (`psql -v ON_ERROR_STOP=1 -1` con el archivo por stdin, y
la consulta de `pg_constraint` / `pg_index` / `pg_trigger`). Resultado
esperado, literal: `harvest_job_fase_check` y `attempt_tipo_valido`
presentes, `goal_harvest_completo` ausente; el predicado del índice incluye
`hermanas_negadas`; los dos triggers habilitados.

Después, **como `orbit_read`** (la fila D.1 pide verificar fases, índice,
tipo `hermana`, triggers, GRANTs en los dos sentidos y secuencias):

```bash
ssh goncloud "$PSQL_READ \
  -c \"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'harvest_job_fase_check';\" \
  -c \"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'attempt_tipo_valido';\" \
  -c \"SELECT indexdef FROM pg_indexes WHERE indexname = 'harvest_job_en_vuelo';\" \
  -c \"SELECT has_table_privilege('app_decide','keyword_biblioteca','INSERT') AS decide_inserta_kw,
              has_table_privilege('app_decide','negative_biblioteca','INSERT') AS decide_inserta_neg,
              has_column_privilege('app_decide','keyword_biblioteca','updated_at','UPDATE') AS decide_toca_updated_at,
              has_table_privilege('app_decide','keyword_biblioteca','DELETE') AS decide_borra_kw,
              has_table_privilege('app_decide','negative_biblioteca','UPDATE') AS decide_actualiza_neg,
              has_table_privilege('app_read','keyword_biblioteca','INSERT') AS read_inserta_kw,
              has_sequence_privilege('app_decide','keyword_biblioteca_id_seq','USAGE') AS decide_seq_kw,
              has_sequence_privilege('app_decide','negative_biblioteca_id_seq','USAGE') AS decide_seq_neg;\""
```

Esperado: las dos definiciones traen `hermanas_negadas` y `hermana`
respectivamente; `decide_inserta_kw = t`, `decide_inserta_neg = t`,
`decide_toca_updated_at = t`, `decide_borra_kw = f`,
`decide_actualiza_neg = f`, `read_inserta_kw = f`, `decide_seq_kw = t`,
`decide_seq_neg = t`. Un solo valor distinto detiene el despliegue: la
reversa del esquema es restaurar el dump de D.1.2 en una transacción, **no**
recrear tablas.

### D.1.4 Deploy del código y smoke de lectura

El deploy es el de siempre (`git archive` de `origin/master`, md5 contra el
remoto, `up -d --no-deps --build app`), con **dos archivos más** en el
archive: `tools/harvest_excepcion.py` y `tools/reversa_harvest.py` no van en
la imagen (entran por stdin en D.2 y D.3, como `archiva_inertes.py`), pero
el md5 del árbol del server debe incluirlos para que lo que se corre sea lo
que está en master. El respaldo, el hash y la reversa usan **el mismo
conjunto de archivos**: `app/`, `Dockerfile`, `.dockerignore`,
`pyproject.toml`, `uv.lock` y los tres tools. Desde la raíz del checkout:

Cada paso está encadenado con `&&` y **termina con estado distinto de cero
si falla** (`|| { echo FALLO…; false; }`), así que sirve igual línea por
línea o dentro de un `set -e`. No sigas a la línea siguiente después de un
`FALLO`; la reversa del código es restaurar `predeploy-$STAMP/` (mismo
conjunto de archivos que se copia y se verifica) y reconstruir.

```bash
STAMP=$(date -u +%Y%m%d-%H%M); TMP=$(mktemp -d)
ssh goncloud "cd /mnt/data/appdata/orbit && mkdir predeploy-$STAMP && cp -a app Dockerfile .dockerignore pyproject.toml uv.lock tools predeploy-$STAMP/ && echo respaldo predeploy-$STAMP" || { echo "FALLO respaldo"; false; }
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock \
  tools/fabrica_campanas.py tools/harvest_excepcion.py tools/reversa_harvest.py \
  | ssh goncloud 'cd /mnt/data/appdata/orbit && tar -xf -' && echo "copiado $APROBADO" || { echo "FALLO copia"; false; }
git archive --format=tar "$APROBADO" app Dockerfile .dockerignore pyproject.toml uv.lock tools/fabrica_campanas.py tools/harvest_excepcion.py tools/reversa_harvest.py \
  | tar -xf - -C "$TMP" \
  && ( cd "$TMP" && find app tools Dockerfile .dockerignore pyproject.toml uv.lock -type f | sort | xargs md5 -r ) | awk '{print $1, $2}' | sort -k2 > "$TMP/local.md5" \
  && ssh goncloud 'cd /mnt/data/appdata/orbit && find app tools/fabrica_campanas.py tools/harvest_excepcion.py tools/reversa_harvest.py Dockerfile .dockerignore pyproject.toml uv.lock -type f | sort | xargs md5sum' \
     | awk '{print $1, $2}' | sort -k2 > "$TMP/server.md5" \
  && diff -q "$TMP/local.md5" "$TMP/server.md5" >/dev/null \
  && echo "md5 OK: $(wc -l < "$TMP/local.md5") archivos idénticos a $APROBADO" \
  && ssh goncloud 'cd /mnt/data/appdata/orbit && docker compose up -d --no-deps --build app' \
  && echo "build+recreate OK" \
  || { echo "FALLO: md5 distinto o build fallido; NO sigas"; diff "$TMP/local.md5" "$TMP/server.md5" | head; false; }
ssh goncloud 'curl -fsS http://127.0.0.1:8010/health' && echo && ssh goncloud 'docker ps --format "{{.Names}} {{.Status}}" | grep -q "orbit-app-1 Up" && docker ps --format "{{.Names}} {{.Status}}" | grep orbit-app' && echo "health OK" || { echo "FALLO health o contenedor"; false; }
```

Smoke de lectura (F2 visible, sin escribir nada): `/salud` trae, **dentro de
`plataformas.<plataforma>`** (no en la raíz del JSON), el bloque
`harvest_destino` con `resueltos` por procedencia y `saltos_grupo`, y
`/cortes` responde 200:

```bash
ssh goncloud 'curl -fsS http://127.0.0.1:8010/api/dashboard/salud' | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(json.dumps({k: v.get("harvest_destino") for k, v in d["plataformas"].items()}, indent=1, ensure_ascii=False))'
ssh goncloud 'curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/cortes'
```

Antes de D.2, `resueltos` debe mostrar al grupo 1 por `terna` (su terna
sigue puesta) o por `grupo` si ya era NULL; anótalo: es el «antes» de D.2.

### D.1.5 Verificación de apagado (no es la reversa)

Con los goals del grupo en `shadow`, un ciclo completo **no debe emitir
ninguna mutación de F2 para el grupo 1** (hoy el único grupo; si mañana
hay otro en `live`, el filtro `r.grupo_id = 1` sigue midiendo solo al que
se verifica): ningún job de harvest para sus campañas, ningún intento
`normal` o `hermana` ligado a una decisión suya, ninguna fila nueva en las
bibliotecas (las bibliotecas no llevan grupo en la fila de conteo: si otro
grupo estuviera en vivo, se comparan por `origen`). Este gate mide **mutaciones de
campañas de grupo**, no todo el HTTP del ciclo: la envolvente ya es `live`
para el resto de la cuenta, así que el ciclo sigue haciendo su `GET` de
perfiles y los applies de los goals que ya están en vivo (bids, negativos y
harvests por terna de otras campañas). Conteos antes, un ciclo, conteos
después, los mismos:

```bash
ssh goncloud "$PSQL_READ -c \"SELECT
   (SELECT count(*) FROM harvest_job h JOIN campana_grupo_rol r ON r.ad_entity_id = h.ad_entity_id
     WHERE r.grupo_id = 1) AS jobs_grupo1,
   (SELECT count(*) FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
      JOIN campana_grupo_rol r ON r.ad_entity_id = d.ad_entity_id
     WHERE r.grupo_id = 1 AND a.tipo IN ('normal','hermana')) AS intentos_grupo1,
   (SELECT count(*) FROM apply_attempt a JOIN decision d ON d.id = a.decision_id
      JOIN campana_grupo_rol r ON r.ad_entity_id = d.ad_entity_id
     WHERE r.grupo_id = 1 AND a.tipo = 'hermana') AS hermanas_grupo1,
   (SELECT count(*) FROM keyword_biblioteca) AS kw_biblio,
   (SELECT count(*) FROM negative_biblioteca) AS neg_biblio,
   (SELECT max(id) FROM optimizer_cycle) AS ultimo_ciclo;\""
ssh goncloud 'docker exec orbit-app-1 python -m app.cli cycle --platform amazon_mx 2>&1 | tail -5'
# repetir el SELECT: jobs_grupo1, intentos_grupo1, hermanas_grupo1, kw_biblio y neg_biblio IDÉNTICOS; ultimo_ciclo +1
```

Y como segunda lectura, en `/salud` el ciclo nuevo debe listar las campañas
del grupo 1 saltadas por modo `shadow` (no por `destino_inconsistente` ni
`sin_destino_de_harvest`). Si algún conteo subió, se detiene todo y se lee
el ciclo en `/salud` (motivo del skip) antes de seguir. Lo que sí puede
aparecer: filas `shadow` en `apply_queue` y notas `harvest_destino` en el
ciclo — eso es lectura, no HTTP.

Con esto D.1 cierra: E/D.1 lleva SHA, respaldo, salidas de D.1.3, md5 y
`Recreated`, el cap decidido con su `config_version.id`, y los dos conteos
de D.1.5.

### D.2 Limpiar la terna del grupo 1 (go del dueño)

Objetivo: que el grupo 1 resuelva su destino **por grupo** (rol
`category_exact`) y no por la terna copiada en cada goal. La herramienta es
`tools/harvest_excepcion.py --limpiar-terna`: solo `app_admin`, cero Amazon,
va goal por goal por `goals_write.edita_goal(harvest_limpia_destino=True)`
(bid intacto), y es **reanudable**: un fallo a mitad aborta con el
`goal_id` y lo limpio queda limpio. Entra por stdin al contenedor, como
`archiva_inertes.py`.

```bash
# 1) Dry-run: candidatas, resolución de hoy y de después, huella. Cero escrituras.
ssh goncloud 'docker exec -i orbit-app-1 python - --limpiar-terna --grupo 1' \
  < tools/harvest_excepcion.py

# 2) Go del dueño: --esperado = número de candidatas del dry-run, --huella la del dry-run.
ssh goncloud 'docker exec -i orbit-app-1 python - --limpiar-terna --grupo 1 \
  --acepto-mutacion-real --esperado <N> --huella <H> --go "<literal del dueño>"' \
  < tools/harvest_excepcion.py
```

Verificación (las mismas consultas de D.1.0 paso 3 y del smoke de D.1.4):
los goals `scope = 'campaign'` del grupo 1 con `harvest_campaign_id` y
`harvest_ad_group_id` en NULL y `harvest_default_bid` igual que antes; el
readback del propio tool debe listarlos en bid-solo; `/salud` →
`harvest_destino.resueltos.grupo` incluye al grupo 1 y `saltos_grupo` sin
`destino_inconsistente` para sus campañas. Si vuelves a correr el dry-run,
debe decir «ya limpia» para todas.

**Migración a `harvest_excepcion`: no es masiva.** 241 campañas resuelven
hoy por el goal de plataforma y solo 4 han cosechado alguna vez; el resto
sigue por la terna vigente con `migracion_pendiente`, que es un estado
legítimo. Se migran solo las que el dueño decida (candidatas naturales: esas
4). Si decide migrar alguna, es una corrida por campaña, con su propio go:

```bash
# Dry-run
ssh goncloud 'docker exec -i orbit-app-1 python - --migrar --plataforma amazon_mx \
  --campana <external_id origen> --destino-campana <external_id> --destino-ad-group <external_id>' \
  < tools/harvest_excepcion.py
# Go (misma ceremonia: --acepto-mutacion-real --esperado 1 --huella <H> --go "<literal>")
```

E/D.2 declara: goals limpiados con ids, `config`/`salud` antes y después,
filas de `harvest_excepcion` creadas (si alguna) con su `go_literal`, y
cuántas campañas quedan en `migracion_pendiente` a propósito.

### D.3 Primer harvest de grupo en vivo y ensayo de reversa

Dos gos separados del dueño, y **una precondición que no se puede
forzar**: hace falta un harvest de grupo con hermanas, y las cinco
campañas del grupo 1 (`kit_arras | Personalizado`, nacidas el 2026-09-09)
no reciben decisiones antes de diez días de datos (regla 6; fabrica-01
Tarea 11 paso 4, 2026-09-19). Además `cortes-ui-01` 1.2 es precondición
de la fila. Hasta entonces D.3 no arranca; se declara, no se adelanta.

Orden cuando llegue el día:

1. **Encender el grupo a `live`** (go 1) con `tools/goals_modo_grupo.py`
   (PR #283: `mode` entra a `goals_write.edita_goal` con validación pura y
   regla post-lectura; el tool va goal por goal, reanudable, solo
   `app_admin`, cero Amazon; entra por stdin como los demás). Precondición:
   el PR #283 mergeado y desplegado (rebuild del contenedor). La envolvente
   `ads_optimizer_mode` solo se muestra: el modo efectivo es el meet.

   ```bash
   # Dry-run: candidatas (los goals scope=campaign del grupo que no están ya en live),
   # envolvente vigente y huella del conjunto. Cero escrituras.
   ssh goncloud 'docker exec -i orbit-app-1 python - --grupo 1 --mode live' \
     < tools/goals_modo_grupo.py
   # Go 1 del dueño: --esperado = candidatas del dry-run, --huella la del dry-run.
   ssh goncloud 'docker exec -i orbit-app-1 python - --grupo 1 --mode live \
     --acepto-mutacion-real --esperado <N> --huella <H> --go "<literal del dueño>"' \
     < tools/goals_modo_grupo.py
   ```

   Readback: el propio tool imprime el `mode` **leído** por goal y el modo
   efectivo; además la consulta de D.1.0 paso 3 debe mostrar `live` en los
   cinco. **Kill switch** (sin ceremonia, goal por goal, funciona también en
   bid-solo post-D.2):

   ```bash
   ssh goncloud 'docker exec orbit-app-1 python -m app.cli goals set <goal_id> --mode shadow'
   ```

2. **Seguir el primer harvest natural hasta `done`** (sin forzar `/run`:
   espera el ciclo del cron). Evidencia: `harvest_job` con `fase` pasando
   por `hermanas_negadas`, `external_ids.hermanas_objetivo` con las
   hermanas del roster, keyword en la exacta por readback, negativos en las
   hermanas por LIST, fila en `keyword_biblioteca`, ledger `normal` +
   `hermana` sellado con `quota_cobrada = false` en las hermanas, y
   `/cortes` mostrando `destino` y `hermanas` en el renglón antes de vencer
   el veto.
3. **Ensayo de reversa sobre ese job** (go 2, aparte): dry-run primero,
   cero HTTP; luego la mutación real con la ceremonia completa. Orden
   canónico keyword → hermanas propias → origen, readback entre deletes,
   stop al primer fallo, reanudación sin repetir.

   ```bash
   # Dry-run: plan + huella sobre los pasos pendientes, cero HTTP.
   ssh goncloud 'docker exec -i orbit-app-1 python - --job <id harvest_job done>' \
     < tools/reversa_harvest.py
   # Go 2 del dueño.
   ssh goncloud 'docker exec -i orbit-app-1 python - --job <id> \
     --acepto-mutacion-real --esperado <N> --huella <H> --go "<literal del dueño>"' \
     < tools/reversa_harvest.py
   ```

   Verificar con ids reales: readback `ARCHIVED`/ausente de la keyword y de
   cada negativo propio, ledger `reversa` con `quota_cobrada = false`, y que
   nada ajeno se tocó (negativos adoptados —`creada = false`— intactos).

E/D.3 lleva los dos go literales, ids reales, ledger y readbacks de la
reversa y del primer harvest. Con D.3 cierran en el tracker `AUTO-02` y
`ORBIT 17 — Harvest por grupo (FABRICA 02)`.
