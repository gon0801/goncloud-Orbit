# Launch — como lanzar el Drive

El Drive NO inicia uvicorn ni usa el puerto 8000: `test_drive.py` importa
`app.main:app` y lo ejercita con `fastapi.testclient.TestClient` (en proceso).

1. Postgres 16 desechable, mismo usuario/password/db que quality.yml:
   pg_ctl -D "$PGDATA" -o "-p 5433 -k $SOCK" start
2. Variables:
   export ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres
   export ORBIT_DSN_READ=$ORBIT_TEST_DSN
   export ORBIT_SECRETS_DIR=$(mktemp -d)   # vacio: canal notifica apagado
   export LC_ALL=en_US.UTF-8               # si el Postgres local no arranca
3. python -m pytest verify/
4. Limpieza: ver Cleanup.md.
