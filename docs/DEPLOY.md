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
  - `Dockerfile`, `pyproject.toml`, `uv.lock`, `app/` — contexto de build
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
ni relajar permisos).

**Env por servicio (4.1):** ni `db` ni `app` declaran `env_file: .env`
(heredaban TODO). `db` recibe solo `POSTGRES_USER` / `POSTGRES_PASSWORD` /
`POSTGRES_DB` y `app` recibe solo los 4 DSN de servicio
(`INGEST`/`DECIDE`/`READ`/`ADMIN`), todo por interpolación del `.env`
(compose lo lee al parsear; el archivo sigue `600` root). `ORBIT_DSN_TEST`
NO entra a ningún contenedor: su rol tiene `ADMIN OPTION` sobre `app_*`
(escritura en prod) y solo lo usa la suite local por túnel.

**Qué se monta:** SOLO `secrets/` (read-only). Ni backups, ni `.env`
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
#        pyproject.toml uv.lock | ssh goncloud "cd /mnt/data/appdata/orbit && tar -xf -"
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
30 7 * * * /mnt/data/appdata/orbit/refresh_costos.sh >> /mnt/data/appdata/orbit/logs/costos.log 2>&1
```

Las **07:30 UTC** caen entre la ingesta de métricas (07:10) y los ciclos
(08:40/08:41): los costos llegan frescos antes de que el motor decida.

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

Cadencia: **manual** por ahora (la fuente ya es diaria en contabilidad; los
huecos medidos caben en el `nearest_prior` de 7 días). Re-correr es no-op
por PK. Cron diario se propone cuando la 0.7 lo pida.

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
docker cp /tmp/accounting-snapshot.db orbit-app-1:/tmp/accounting-snapshot.db
docker exec orbit-app-1 python -m app.cli ingest ledger --sqlite /tmp/accounting-snapshot.db
# 3) limpieza
rm /tmp/accounting-snapshot.db
docker exec -u 0 orbit-app-1 rm /tmp/accounting-snapshot.db
```

Cadencia: **manual** por ahora. Re-correr es no-op por los tres índices de
dedupe (`rows_written=0`, conflictos contados en `rows_skipped`). Si se
corre desde `docker exec`, el DSN del contenedor apunta a `127.0.0.1` y
falla: reescribir el host a `db` (mismo truco que costos) o correr desde
el host contra el puerto publicado.

## Crons de Orbit (crontab de `gon`, ADITIVO)

Tres jobs NUEVOS en el crontab de `gon`. Los de accounting (y el resto:
EHV, heartbeat, etc.) **no se tocan**. El backup de Postgres sigue en el
crontab de **root** (`30 3 * * * backup.sh`) — tampoco se toca.

El server está en UTC: estas horas SON UTC.

| UTC   | job_key | comando |
|-------|---------|---------|
| 06:45 | `ingest:structure` | `python -m app.cli ingest structure` |
| 07:10 | `ingest:metrics` | `python -m app.cli ingest metrics --fecha D-31 --fecha-fin D-1` |
| 08:40 | `ads_optimizer:amazon_us` + `ads_optimizer:amazon_mx` | `python -m app.cli cycle --platform …` (los dos, en serie) |

`job_key` del ciclo es `app.cycle.job_key_de` (`ads_optimizer:<platform>`),
la misma fuente que el CLI. Los de ingesta quedan como comentario en el
crontab y como `ingest_run.source` (`amazon_ads_structure_v2` /
`amazon_ads_reports_v3`).

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
   `0003_goal_bounds_explicit.sql`, `0004_ad_entity_kind_product_ad.sql` —
   ver "Aplicar migraciones"). Omitir 0003 re-crearia los DEFAULT USD
   0.10/2.50 que el sellado 2 del preflight elimino: un goal MXN volveria a
   nacer con techo 2.50. Omitir 0004 deja el enum sin `product_ad` y la
   ingesta de estructura de la 0.4 revienta al insertar ese kind.
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
