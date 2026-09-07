# ORBIT 19 A.5 - correcciones de revision

Fecha: 2026-09-07 UTC. Alcance: corregir los cuatro hallazgos major y dos
observaciones de la revision sobre `aa92611..911aaa2`. Todo el ejercicio usa
Postgres/fakes locales y `httpx.MockTransport`; no hubo POST a Amazon, llamada
`/crear`, campana real ni cambio de la fase B.

## Regresiones RED

- La CLI aceptaba `--productos` junto con `--target-acos` y despues intentaba
  construir el plan v1: la regresion esperaba rechazo por `target-acos` y
  recibia el intento de abrir `ORBIT_DSN_READ`.
- Dos ejecuciones CLI v2 con la misma huella creaban dos lotes timestamp y
  duplicaban las llamadas simuladas de creacion. La regresion espera un lote
  durable y cero solicitudes nuevas en el segundo intento.
- El nuevo ensayo de recuperacion no existia. Ahora fija un snapshot v2 con
  pasos `applied + failed` y setting `fabrica.creacion=v1`.
- Un `settings` JSONB escalar causaba `AttributeError`; margen `NULL` con 70
  dias se mostraba como `Muestra limitada`.

## Correcciones y prueba focal

- La CLI usa `cli-<huella>` y un advisory lock transaccional antes de LWA. Si
  el lote ya existe, no crea y dirige a `--reconciliar`, `--registrar` o
  `--desarmar`.
- `--target-acos` solo se acepta con `--listing-ids`.
- El ensayo v2 parcial reconcilia el ultimo `product_ad` failed, registra el
  grupo y pausa las cinco campanas. Se ejecuta contra una base PostgreSQL
  temporal con las migraciones 0001--0019 y `httpx.MockTransport`; verifica
  estado final, ledger, grupo, dos publicaciones y cinco goals apagados, sin
  POST de creacion.
- Settings que no son objeto vuelven a `v1`; la etiqueta de muestra limitada
  depende solo de menos de 30 dias con venta.

```text
uv run python -m pytest -q tests/test_fabrica_campanas.py  80 passed
uv run python -m pytest -q tests/test_fabrica_plan.py tests/test_ui_fabrica.py  44 passed
uv run ruff check ...                                      All checks passed
uv run ruff format --check ...                             5 files already formatted
node --check app/static/js/fabrica.js                       passed
```

La suite completa queda reservada para una unica ejecucion CI del PR de esta
correccion. El resultado, SHA desplegado, backup y smoke se agregan al cerrar
A.5 despues de esa ejecucion y del despliegue.
