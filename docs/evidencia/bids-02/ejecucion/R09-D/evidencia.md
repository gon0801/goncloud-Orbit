# R09-D: productos.js muestra el mensaje y los fallos, no [object Object]

## Prueba primero

`tests/test_ui_productos.py::test_r09d_error_objeto_muestra_mensaje_y_fallos_sin_object_object`
(ejecuta el JS real con Node, DOM y fetch simulados).

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs tests/test_ui_productos.py
```

- Antes: el estado pintaba `Error: [object Object].` ante un 502
  parcial con `detail` objeto.
- Despues: 1 passed. Vecinos: `tests/test_ui_productos.py` y
  `tests/test_pantalla_productos.py` 26 passed, 0 saltadas.

## Cambio

`app/static/js/productos.js`: nueva `mensajeError(data)`, espejo de la
de `fabrica.js`, con los fallos por producto del parcial adjuntos al
mensaje. Las cinco lineas que pintaban `(r.data.detail || r.data)`
ahora usan `mensajeError(r.data)`.

El mensaje del parcial suma el conteo de lanzados cuando el detalle
los trae. La prueba afirma ademas que los cinco sitios usan
`mensajeError(r.data)` y que ninguno conserva el patron viejo.

## Mutante

Devolver la linea de `lanzar` a `(r.data.detail || r.data)` repone el
`[object Object]`: la prueba nueva pasa a rojo (1 failed). Revertido:
1 passed.
