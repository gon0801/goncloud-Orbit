# Veredicto sobre `78888f3e`

SHA revisado: `78888f3e554c581b17b226ef249823ca1d95c593`.
SHA de la corrección: se agrega en `sha-final.txt` al cerrar.

## Bloqueantes

- `antes.txt`: `PYTHONPATH=/tmp:tests:. uv run --frozen python
  /tmp/orbit_review_78888f3e_blockers.py` sobre `78888f3e`. El as-of de ahora+1h
  envenenaba la marca (`latest 5.0000`) y el reloj que retrocede dejaba la
  corrección anterior (`latest 5.0000`).
- `despues.txt`: el mismo comando sobre el head. La corrección actual gana
  (`6.0000`) y el reloj lógico mantiene la última (`6.5000`).
  `BLOQUEANTES_REPRODUCIDOS [False, False]`.

## Gates

- `pruebas.txt`: focales 106 passed, 43 del módulo.
- `suite.txt`: suite completa con `ORBIT_TEST_DSN`.
- `gates.txt`: Ruff, formato, `bash -n`, `git diff --check`, pre-commit.
- `ui-perf-http.txt`: 30 requests a `/` con uvicorn real. Base 31ba12a
  p95=4.86 ms; head p95=4.96 ms; reinicio del servicio head p95=4.70 ms.
  Mismo payload de 24,959 bytes. Regla de +10 %: PASS.
- `perf-100k.txt`: 3 ingestas frescas de 100,000 filas en 6.97–7.74 s.
- `scripts/`: fixture Amazon, lanzador de Postgres 5433, perf HTTP y
  TestClient, más los scripts de perf/conciliación del directorio anterior.

## Pendientes

Lane 1 Amazon (credenciales AWS y fuente viva) y los diez lanes live en VM con
capturas. No hay infraestructura para esos dos en este checkout.
