# Doctor — como saber que el entorno esta sano

Precondiciones del Drive (pytest verify/):

1. Python 3.12+ con `fastapi`, `psycopg[binary]`, `httpx`, `jinja2`, `pytest`
   instalados (es la misma lista de dependencias de pyproject.toml). Sin uno
   de esos, TestClient no importa y pytest.reporta un `importorskip`.
2. Un Postgres 16 desechable al que `ORBIT_TEST_DSN` apunte, con el esquema
   ya migrado (`migrations/*.sql` en orden, salvo 0011 y las migrations
   `_reversa_*` que no aplican sobre una base vacia) — ver LEEME.md.
3. `ORBIT_DSN_READ` igual a `ORBIT_TEST_DSN` (la API de lectura usa esta
   variable para conectar; sin ella los endpoints devuelven 503).
4. `ORBIT_SECRETS_DIR` apuntando a un directorio VACIO: el canal de
   notifica queda deshabilitado, sin token, sin cargas de red reales.

Check rapido (el Drive NO abre puerto HTTP: la app vive en TestClient,
asi que no hay curl que hacer - lo que se testea son el Postgres y los
imports):

```bash
pg_isready -h 127.0.0.1 -p 5433                  # "accepting connections"
TABS="$(psql "$ORBIT_TEST_DSN" -tAc "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")"
test "$TABS" = 47 || { echo "ESQUEMA INCOMPLETO: $TABS/47 tablas BASE" >&2; exit 1; }
# Igualdad estricta con corte de flujo: un cluster parcial imprime el numero
# chico y sale con error en vez de seguir. Sin table_type='BASE TABLE' daria 59
# (incluye las 12 vistas v_*). NO usar `\dt | wc -l`: cuenta lineas, no tablas.
python -c "import app.main"                      # importa sin error
python -c "from fastapi.testclient import TestClient; import app.main as m; c=TestClient(m.app); print(c.get('/health').status_code)"
# el ultimo devuelve 200 si la app vive
```
