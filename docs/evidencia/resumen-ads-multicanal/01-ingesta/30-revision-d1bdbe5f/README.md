# Revisión integral de `d1bdbe5f`

SHA revisado y rechazado: `d1bdbe5f4d69578e6d9951ca144be700cc2d9218`.
SHA final: se agrega en `sha-final.txt` al cerrar la corrección.

## Antes

- `antes-1.txt`: `PYTHONPATH=/tmp:tests:. uv run --frozen python -c
  'import orbit_review_d1bdbe5 as r; r.case_mark_interleaving();
  r.case_clock_collision(); r.case_future_clock()'`.
  Muestra la marca leída por id (10:00), el snapshot de 11:00 aceptado, la
  colisión de PK disfrazada de duplicado y el as-of 2099 envenenando la marca.
- `antes-2.txt`: `PYTHONPATH=tests:. uv run --frozen python
  /tmp/orbit_review_inputs.py`. Cuatro fallos: `updated_at` inválido ignorado,
  dinero redondeado, claves canónicas duplicadas y fallo de lectura sin
  `ingest_run`.

## Después

- `despues-1.txt`: el comando 1 se detiene en el snapshot atrasado, con la
  marca 12:00 y cero escrituras.
- `despues-2.txt`: el comando 2 rechaza `_dinero("1.23456")`.
- `despues-casos.txt`: cada caso del revisor capturado por separado. La
  concurrencia rechaza el atrasado, la colisión de PK aborta con
  `UniqueViolation`, el futuro se rechaza, el dinero y los duplicados se
  rechazan, y la corrida ausente deja `ingest_run`.

- `regresion-d1bdbe5f.txt`: mapeo de cada regresion a su reproduccion en el
  SHA rechazado; el modulo nuevo no colecciona en `d1bdbe5f` y la revision con
  `|` se aceptaba con la version truncada.

## Gates y evidencia operativa

- `pruebas.txt`: focales 99 passed, 36 del módulo.
- `gates.txt`: Ruff, formato, `bash -n`, `git diff --check`, pre-commit.
- `suite.txt`: suite completa con `ORBIT_TEST_DSN`.
- `perf-100k.txt`: 3 ingestas frescas de 100,000 filas, 10,498,048 bytes,
  6.9–7.7 s cada una.
- `operativo-carga.txt`, `operativo-correccion-y-atrasado.txt`,
  `operativo-rechazos.txt`, `operativo-sin-snapshot-dsn.txt`,
  `conciliacion.txt`, `reinicio.txt`.

## Pendientes de infraestructura

Lane 1 Amazon, latencia de `/`, lanes live en VM y reinicio del servicio HTTP.
Detalle en `../99-cierre.md`, sección "Pendiente".
