# Veredicto sobre `10266d91`

SHA revisado: `10266d9123da73152ebb458ef790b4cd364dfdf0`.
SHA de la corrección: se agrega en `sha-final.txt` al cerrar.

## Bloqueantes

- `antes.txt`: la recopia real perdía su as-of (`latest 7.0000`) y la marca
  futura bloqueaba una corrección cercana (`latest 6.0000`).
- `despues.txt`: recopia real con `as_of=...; futuro=...`, intermedio
  rechazado (`latest 6.0000`), y frontera futura que acepta la corrección
  (`latest 7.0000`). Ambos `BLOQUEANTE_REPRODUCIDO False`.

## Gates

- `pruebas.txt`: focales 109 passed, 46 del módulo.
- `suite.txt`: suite completa con `ORBIT_TEST_DSN`.
- `gates.txt`: Ruff, formato, `bash -n`, `git diff --check`, pre-commit.
- `ui-perf-http.txt`: 5 rondas de 30 pares intercalados base/head. Mediana de
  p95: base 6.50 ms, head 6.05 ms, -6.92 %. PASS.
- `lane10-reinicio-conciliacion.txt`: reinicio real de uvicorn, historia y
  latest estables, conciliación 788/788 y 197/197 con exit 0.
- `scripts/`: fixture Amazon, lanzador PG 5433, perf intercalado y TestClient,
  conciliación corregida, perf 100k y reproducciones del revisor.

## Pendientes

Lane 1 Amazon (credenciales AWS y fuente viva) y los diez lanes live en VM con
capturas. No hay infraestructura para esos dos en este checkout.
