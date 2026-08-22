# DEPLOY — Base de datos Orbit (Postgres 16 en goncloud)

> Runbook de operación de la base viva. Responde la pregunta de
> reconstrucción: **¿cómo levanto esto desde cero?** (ver "Reconstruir
> desde cero" al final).

## Dónde vive

- **Servidor:** `goncloud` (acceso por `ssh goncloud`), junto a `bridge` y
  `accounting`, como manda `docs/CONTEXTO.md`.
- **Dir de deploy:** `/mnt/data/appdata/orbit/`
  - `docker-compose.yml` — copia del repo (fuente de verdad: el repo).
  - `.env` — `POSTGRES_USER=orbit` + `POSTGRES_PASSWORD` + los DSN por
    servicio. Permisos `600`, **nunca se commitea** (está en `.gitignore`).
  - `backups/` — dumps `pg_dump -Fc` con rotación de 14.
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
servicio, miembro del rol que le corresponde (creados una sola vez, con
password generada en el server — los valores viven SOLO en el `.env` del
server, jamás en el repo):

```
ORBIT_DSN_INGEST=postgresql://orbit_ingest:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_DECIDE=postgresql://orbit_decide:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_READ=postgresql://orbit_read:<pass>@127.0.0.1:5432/orbit
ORBIT_DSN_ADMIN=postgresql://orbit_admin:<pass>@127.0.0.1:5432/orbit
```

- `orbit_read`: SELECT sí, UPDATE/INSERT no (verificado en vivo).
- `orbit_ingest`: INSERT en tablas de ingesta sí (`ingest_run`, métricas…).
- El superusuario del contenedor es `orbit` (para migraciones y admin).

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
`orbit_schema_test_*` y un rol centinela en el cluster — NO toca la base
`orbit`.

```bash
# 1. túnel (en background / otra terminal)
ssh -N -L 5432:127.0.0.1:5432 goncloud

# 2. suite con el DSN del superusuario (la password sale del .env del server,
#    no se escribe en la línea de comando ni en ningún archivo local)
ORBIT_TEST_DSN="$(ssh goncloud 'echo "postgresql://orbit:$(sed -n \
  "s/^POSTGRES_PASSWORD=//p" /mnt/data/appdata/orbit/.env)@localhost:5432/postgres"')" \
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
- Cada corrida hace `pg_dump -Fc orbit` a `backups/orbit_YYYY-MM-DD.dump`
  (primero `.tmp`, luego `mv` — nunca queda un dump a medias con nombre
  bueno) y rota dejando las **14** más recientes (la lección de
  `competitive.db`: la rotación de 3 se comió el histórico).
- Verificar que un dump sirve:

```bash
ssh goncloud 'docker exec -i orbit-db-1 pg_restore --list < \
  /mnt/data/appdata/orbit/backups/orbit_YYYY-MM-DD.dump | head'
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

## Reconstruir desde cero

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
6. Crear los usuarios LOGIN por servicio con `GRANT app_*` y guardar sus
   DSN en el `.env` del server (ver "Usuarios y DSN"; misma generación de
   password en el server, sin imprimirla).
7. Instalar el backup: script + cron (ver "Backups").
8. Verificar: suite completa por túnel (30 passed, 0 skipped) + smoke de
   candados.

Si se pierde el volumen (`docker compose down -v` destruye datos — nunca
hacerlo salvo reconstrucción deliberada): restaurar el último dump con
`pg_restore` y reaplicar los pasos 5–6 si cambió el esquema.
