# R09-A: pausar repetido, desarmado o sin campanas responde 409

## Prueba primero

`tests/test_api_fabrica.py::test_r09a_pausar_repetido_desarmado_y_sin_campanas_409`

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs \
  tests/test_api_fabrica.py::test_r09a_pausar_repetido_desarmado_y_sin_campanas_409
```

- Antes del arreglo: `assert 502 == 409` en la segunda pausa (la ruta
  traducia todo `fc.Abortar` a 502).
- Despues: 1 passed. Vecinos (G9 502 a media pausa, A05, I.5, A04):
  25 passed, 0 saltadas.

## Cambio

`app/impulso_io.py`: `PausadorFabrica.pausa` atrapa `fc.Abortar` y, si
el estado del lote explica el rechazo (no esta `applied` o no tiene
campanas con external), lo traduce a `ErrorImpulso(409, mensaje)`. El
rechazo de Amazon con el lote `applied` se re-levanta y la ruta sigue
respondiendo 502.

## Mutante

En `_lote_no_pausable`, quitar `or fila[0] != "applied"` de la
condicion deja la doble pausa en 502: la prueba nueva pasa a rojo
(1 failed). Revertido: 1 passed.
