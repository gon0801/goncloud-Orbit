# ORBIT 19 A.5 - correcciones de revision

Fecha: 2026-09-07 UTC. Alcance: corregir los cuatro hallazgos major y dos
observaciones de la revision sobre `aa92611..911aaa2`. Todo el ejercicio usa
Postgres/fakes locales y `httpx.MockTransport`; no hubo POST a Amazon, llamada
`/crear`, campana real ni cambio de la fase B.

## Regresiones RED contra 911aaa2

`regresiones-red-911aaa2.txt` contiene la ejecucion focal con las pruebas
nuevas sobre un worktree aislado de `911aaa2`, sin red ni Amazon. Falla por
dos motivos observables: la mezcla CLI abre conexion antes de rechazar y falta
el advisory lock del lote durable. Es la evidencia RED de los hallazgos P1/P2
que originaron [PR 187](https://github.com/gon0801/goncloud-Orbit/pull/187).

El ensayo AC9 es una cobertura de aceptacion que no existia, no una regresion
funcional observada. Las pruebas nuevas fijan que un lote v2 con pasos
`applied + failed` se recupera aun con `fabrica.creacion=v1`. Tambien fijan
que settings no objeto resuelven el interruptor de creacion en v1 y que un
margen `NULL` con 70 dias no se etiqueta como `Muestra limitada`.

## Correcciones y prueba focal

- La CLI usa `cli-<huella>` y un advisory lock transaccional antes de LWA. Si
  el lote ya existe, no crea y dirige a `--reconciliar`, `--registrar` o
  `--desarmar`.
- `--target-acos` solo se acepta con `--listing-ids`.
- El ensayo v2 parcial reconcilia el ultimo `product_ad` failed, registra el
  grupo y pausa las cinco campanas. Se ejecuta contra una base PostgreSQL
  temporal con las migraciones de dependencia de fabrica hasta 0019 y
  `httpx.MockTransport`; verifica
  estado final, ledger, grupo, dos publicaciones y cinco goals apagados, sin
  POST de creacion.
- Settings que no son objeto vuelven a `v1`; la etiqueta de muestra limitada
  depende solo de menos de 30 dias con venta.
- La revision automatica de PR 187 pidio que el rechazo CLI demuestre cero
  conexiones y que el ensayo de pausa lea `PAUSED` solo despues del PUT. Ambas
  comprobaciones estan en `test_fabrica_campanas.py`.

```text
uv run python -m pytest -q tests/test_fabrica_campanas.py  80 passed
uv run python -m pytest -q tests/test_fabrica_plan.py tests/test_ui_fabrica.py  44 passed
uv run ruff check --fix .                                  All checks passed
uv run ruff format .                                       reformateo 1 plan ajeno; restaurado
node --check app/static/js/fabrica.js                       passed
PATH=".venv/bin:$PATH" pre-commit run --all-files           todos los candados passed
```

La suite completa se ejecuta en CI del PR de esta correccion. El resultado,
SHA desplegado, backup y smoke se agregan al cerrar A.5 despues de esa
ejecucion y del despliegue.

## Cierre

La [CI final de PR 187](https://github.com/gon0801/goncloud-Orbit/actions/runs/34088493597)
termino en `success`. El despliegue, resguardo, smoke sin mutacion y capturas
productivas estan archivados en `despliegue-final.md`.
