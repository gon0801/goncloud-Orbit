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
# copiar del repo: Dockerfile .dockerignore pyproject.toml uv.lock app/
docker compose up -d --no-deps --build app
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
> fuera del cluster de prod (sin fecha; cuando eso exista):
>
> ```sql
> REVOKE app_read, app_ingest, app_decide, app_admin FROM orbit_test;
> ```

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

```bash
ssh goncloud 'docker exec -i orbit-db-1 psql -U orbit -d orbit \
  -v ON_ERROR_STOP=1 -1' < migrations/0001_initial.sql
```

- `-1` = transacción única: si algo falla a medias, se revierte entera.
- **`0001` NO es re-runnable** (los `CREATE TYPE`/`CREATE TABLE` revientan a
  la segunda): solo se aplica una vez por base nueva. Las futuras deben ser
  idempotentes o gestionarse con una tabla de versiones.
- Post-aplicación (verificación de la 0001): 19 tablas en `public`, roles
  `app_*` en `pg_roles`, `prohibir_mutacion` en `pg_proc`.

Migración `0003` (ORBIT 05 preflight 1.2), mismo patrón de comando:

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
3. Listas de Amazon (SOLO lectura): por cada perfil de
   `app.ads.structure.perfiles_aceptados`, `AdsClient.list_objects` sobre
   `/sp/keywords/list`, `/sp/negativeKeywords/list` y `/sp/targets/list`
   (paginando), agrupado por `campaignId` → `$D/listas_amazon/listas_por_plataforma.json`.
   **En 4.4 esto corrió como código inline dentro del contenedor (no está
   en el repo)**: antes del flip, el ítem 4 del checklist exige aterrizarlo
   como `tools/snapshot_listas.py` (con test de sus partes puras) para que
   el operador lo repita sin reescribirlo — hallazgo Greptile PR #40.

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
   `0003_goal_bounds_explicit.sql` — ver "Aplicar migraciones"). Omitir 0003
   re-crearia los DEFAULT USD 0.10/2.50 que el sellado 2 del preflight
   elimino: un goal MXN volveria a nacer con techo 2.50.
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
