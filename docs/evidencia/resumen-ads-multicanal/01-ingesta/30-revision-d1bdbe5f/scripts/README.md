# Scripts de la revisión

Se versionan los scripts que produjeron la evidencia. No llevan credenciales:
el DSN sale de `_test_dsn()` o se pasa como argumento.

## Reproducciones del revisor

```bash
cd <repo>
PYTHONPATH=docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-d1bdbe5f/scripts/revision:tests:. \
  uv run --frozen python -c \
  'import orbit_review_78888f3e_blockers as r; print(r.futuro_dentro_de_tolerancia(), r.reloj_retrocede())'
```

`orbit_review_78888f3e_blockers.py` importa `orbit_review_d1bdbe5.py`, que a su
vez usa `tests/test_meli_ads_ingest.py` y `tests/test_schema.py`.

## Perf de 100,000 filas

```bash
PYTHONPATH=tests:. uv run --frozen python \
  docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-d1bdbe5f/scripts/perf_100k.py
```

Genera el snapshot, crea tres bases desechables, mide cada ingesta y las borra.

## Conciliación

```bash
export CONCILIAR_DSN="postgresql://.../base_desechable"
PYTHONPATH=. uv run --frozen python \
  docs/evidencia/resumen-ads-multicanal/01-ingesta/30-revision-d1bdbe5f/scripts/conciliar.py \
  <snapshot.db> --desde 2026-04-16 --hasta 2026-10-29
```

El DSN va por entorno (nunca en `argv`) y la ventana es explícita. Compara
campaña/día y el total diario contra `meli_ads_metrics_daily`; un NULL no
concilia aunque coincida.
