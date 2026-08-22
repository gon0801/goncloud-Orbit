# DEPLOY — Base de datos Orbit (Postgres 16 en goncloud)

> Runbook de operación de la base viva. Responde la pregunta de
> reconstrucción: **¿cómo levanto esto desde cero?** (ver "Reconstruir
> desde cero" y "Recuperación desde backups" al final — ambos procedures
> fueron probados en vivo, no adivinados).

## Dónde vive

- **Servidor:** `goncloud` (acceso por `ssh goncloud`), junto a `bridge` y
  `accounting`, como manda `docs/CONTEXTO.md`.
- **Dir de deploy:** `/mnt/data/appdata/orbit/`
  - `docker-compose.yml` — copia del repo (fuente de verdad: el repo).
  - `.env` — `POSTGRES_USER=orbit` + `POSTGRES_PASSWORD` + los DSN por
    servicio (incluido `ORBIT_DSN_TEST`). Permisos `600`, **nunca se
    commitea** (está en `.gitignore`).
  - `backups/` — dumps diarios (`700`; dumps `600`).
  - `backup.sh` + cron — ver "Backups".
- **Contenedor:** `orbit-db-1` (imagen `postgres:16`, volumen `orbit_pgdata`).
- **Red:** bind `127.0.0.1:5432` SOLO en loopback del server (`ss -lntp` lo
  confirma). No hay puerto abierto al exterior: el acceso remoto es por
  túnel SSH.

## Levantar / parar

```bash
ssh goncloud
cd /mnt/data/appdata/orbit
docker compose up -d        # levanta (o nada si ya está)
docker compose ps           # debe decir Up y 127.0.0.1:5432->5432
docker exec orbit-db-1 pg_isready -U orbit   # accepting connections
```

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
echo "ORBIT_DSN_TEST=postgresql://orbit_test:${PT}@127.0.0.1:5432/postgres" >> "$ENVF"
chmod 600 "$ENVF"
SCRIPT
```

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

## Reconstruir desde cero (base nueva, sin backups)

1. En goncloud: `mkdir -p /mnt/data/appdata/orbit`.
2. Copiar `docker-compose.yml` del repo al dir de deploy.
3. Crear `.env` (600) con `POSTGRES_USER=orbit` y un
   `POSTGRES_PASSWORD` generado en el server (≥24 chars, nunca commiteado):
   ```bash
   ssh goncloud 'cd /mnt/data/appdata/orbit && umask 177 && \
     { echo "POSTGRES_USER=orbit"; echo "POSTGRES_PASSWORD=$(head -c 48 \
     /dev/urandom | base64 | tr -dc "A-Za-z0-9" | head -c 32)"; } > .env'
   ```
4. `docker compose up -d` y esperar `pg_isready` (ver "Levantar").
5. Aplicar `migrations/0001_initial.sql` (ver "Aplicar migraciones").
6. Crear los usuarios LOGIN por servicio + `orbit_test` (ver "Usuarios y
   DSN": comandos exactos arriba).
7. Instalar el backup: `backup.sh` + cron (ver "Backups").
8. Verificar: suite completa por túnel (30 passed, 0 skipped) + smoke de
   candados.

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
