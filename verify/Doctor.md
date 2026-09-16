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

Check rapido:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:$(echo $PORT)/health"  # 200 si la app vive
psql "$ORBIT_TEST_DSN" -c "\\dt" | wc -l    # >= 47 tablas si el enredo subio
```
