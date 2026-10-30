# Scripts de la revisión `78888f3e`

Se versionan los scripts que produjeron la evidencia. No llevan credenciales:
el DSN sale de `_test_dsn()` o se pasa como argumento.

## Reproducciones del veredicto

```bash
PYTHONPATH=/tmp:tests:. uv run --frozen python \
  /tmp/orbit_review_78888f3e_blockers.py
```

(El script del revisor está en `scripts/revision/` del directorio anterior y usa
`tests/test_meli_ads_ingest.py`.)

## Perf de `/` y reinicio del servicio

```bash
bash scripts/launch_pg_5433.sh
psql -h 127.0.0.1 -p 5433 -U orbit -d postgres -f scripts/fixture_amazon.sql
ORBIT_DSN_READ=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
ORBIT_DSN_ADMIN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
ORBIT_SECRETS_DIR=$(mktemp -d) \
uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8765 &
python scripts/ui_perf_http.py http://127.0.0.1:8765/
```

`ui_perf_testclient.py` es la variante en proceso, sin uvicorn.
