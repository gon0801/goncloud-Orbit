# Launch - como lanzar el Drive

El Drive NO inicia uvicorn ni abre un puerto HTTP: `test_drive.py` importa
`app.main:app` y lo ejercita con `fastapi.testclient.TestClient` (en proceso).
Lo unico que se lanza de verdad es el Postgres 16 desechable. Todos los paths
que crea este procedimiento salen de `mktemp -d` - es lo que despues permite
que Cleanup.md borre sin riesgo (ver alla).

1. Dirs y cluster temporal, TODO adentro de /tmp:

   ```bash
   PGDATA=$(mktemp -d /tmp/orbit-pgdata.XXXXXX)
   SOCK=$(mktemp -d /tmp/orbit-pgsock.XXXXXX)
   initdb -D "$PGDATA" -U orbit --auth=trust -E UTF8
   pg_ctl -D "$PGDATA" \
          -o "-p 5433 -k $SOCK -c listen_addresses=127.0.0.1" \
          -l /tmp/orbit-pg.log start
   pg_isready -h 127.0.0.1 -p 5433   # "accepting connections"
   ```

   Trust en local: el DSN lleva orbit:orbit pero auth=trust ignora el
   password; mismos usuario/db que quality.yml y docker-compose (orbit/orbit,
   db postgres).

2. Migrar el esquema (en orden, salvo `0011_*` y las `_reversa_*`, que no
   aplican sobre una base vacia - igual que en CI):

   ```bash
   for f in migrations/*.sql; do
     case "$f" in *0011_*|*_reversa_*) continue ;; esac
     psql -h 127.0.0.1 -p 5433 -U orbit -d postgres -v ON_ERROR_STOP=1 -f "$f"
   done
   psql -h 127.0.0.1 -p 5433 -U orbit -d postgres -c "\dt" | wc -l   # >= 47 tablas
   ```

3. Variables del Drive:

   ```bash
   export ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres
   export ORBIT_DSN_READ=$ORBIT_TEST_DSN
   export ORBIT_SECRETS_DIR=$(mktemp -d /tmp/orbit-secrets.XXXXXX)  # vacio: canal notifica apagado
   export LC_ALL=en_US.UTF-8   # si initdb o el Postgres local se quejan de locale
   ```

   NO apuntes ORBIT_SECRETS_DIR al default de produccion
   (`/mnt/data/appdata/orbit/secrets`): el Drive lo necesita vacio.

4. `python -m pytest verify/`
5. Limpieza: ver Cleanup.md. NO exportes PGDATA/SOCK/ORBIT_SECRETS_DIR con
   otros valores antes de Cleanup - los guards de alla verifican que cada
   path sea un mktemp propio antes de borrar.
