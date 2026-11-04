# ORBIT 19 / B.1 — Reporte de pruebas

Fecha: 2026-09-06. Rama `feat/orbit19-fase-b`. Tests nuevos:
`tests/test_ads_producto_ingesta.py` (10 tests: 8 unitarios/estaticos + 2 de
integracion contra Postgres temporal con 0001+0020 aplicadas fresh).

## pytest_focal

```
$ PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_ads_producto_ingesta.py
..........                                                                [100%]
10 passed in 0.33s
```

Casos cubiertos (DoD B.1):

- campana multiproducto NO reparte agregados (A cost 3 y B cost 7 en la misma
  campana quedan 3 y 7; nada se divide ni promedia);
- dos campanas del mismo ASIN/SKU SUMAN (cost 5.0, clicks 6, impressions 110)
  con envenenamiento a NULL de lo que un aporte no trajo;
- columna faltante -> NULL, jamas 0;
- append-only: mismo report id re-corrido = dedupe (0 escritas, tabla
  intacta); report id NUEVO del mismo dia = fila nueva con observed_at
  distinto, la vieja conserva su valor (regla 5);
- US no verificado (400 en POST): corrida fail-closed, run sellada ok=false,
  cero filas inventadas;
- currency por perfil: MX=MXN, US=USD, y el trigger rechaza moneda cruzada
  (CheckViolation);
- body del reporte afirmado campo por campo (spAdvertisedProduct, groupBy
  ["advertiser"], columns strings, sin filters, sin salesSameSku30d);
- migracion 0020: parsea (pglast), PK bitemporal, dedupe por reporte, sin
  GRANT de UPDATE/DELETE, sin tocar tablas previas;
- cero escritura a ads_metric_observation y a Amazon (todo con MockTransport).

## Lint / formato

```
$ .venv/bin/python -m ruff check app/ads/reports.py tests/test_ads_producto_ingesta.py
All checks passed!
$ .venv/bin/python -m ruff format --check app/ads/reports.py tests/test_ads_producto_ingesta.py
2 files already formatted
```

## Regresiones vecinas

```
$ .venv/bin/python -m pytest -q tests/test_reports_pipeline.py tests/test_schema.py
100 passed in 0.63s
```

El orquestador existente no cambia de comportamiento: `sync_metrics` gana el
parametro opcional `reportes` (default `REPORTES_CFG`, los 4 de siempre) y el
dispatch por tabla una rama nueva para `ads_product_metric_observation`.

## Pendiente para el lead

- Conciliacion VIVA de la tabla persistida vs gzip (misma ventana/moneda/
  campos) con credenciales del servidor: la primera corrida con
  `--productos` sobre produccion; si amazon_us da 400/403, declarar US
  no_verificada (el sistema ya lo trata fail-closed, no inventa filas).
- Actualizar el cron del servidor para la tirada diaria D-31..D-1 con
  `--productos` (docs/DEPLOY.md).
- No se corrio la suite completa ni pre-commit (alcance: pytest_focal +
  ruff sobre lo nuevo; la suite completa va en el PR de la fase).
