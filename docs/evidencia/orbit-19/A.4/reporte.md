# ORBIT 19 A.4 - selector por publicacion y objetivo explicito

Fecha: 2026-09-06

## Cobertura

- La pantalla selecciona `listing_ids`, no productos internos.
- Cada publicacion muestra ASIN, SKU de Amazon, margen y enlace seguro.
- ASIN o SKU Amazon ausente impiden seleccionarla. Margen nulo, cero o negativo y
  `historial_ads` ausente se muestran como aviso y siguen seleccionables.
- El objetivo es obligatorio: manual requiere ACoS explicito; el derivado declara
  que exige margen medido positivo. No se completa ningun valor de forma implicita.
- La solicitud de preview v2 envia `listing_ids` y `objetivo`; cambiar cualquier
  campo invalida el preview antes de crear.
- La interfaz conserva la confirmacion, el token solo en encabezado, la recuperacion
  de lote y no llama a la ruta de creacion durante esta comprobacion.

## Verificacion visual

Se uso una instancia local con el template, CSS y JavaScript reales y datos de
fixture. No se uso Amazon ni se crearon campanas.

- [selector-publicaciones.png](selector-publicaciones.png): tres publicaciones
  visibles; las de margen nulo y negativo permanecen disponibles; los avisos y los
  datos de identidad se leen por publicacion.
- [preview-manual.png](preview-manual.png): seleccion de publicaciones y preview
  con objetivo manual explicito de 25.00 por ciento.

El flujo de teclado queda cubierto por el formulario nativo: los controles tienen
etiquetas asociadas, foco visible y los campos obligatorios se validan antes del
preview. El CSS mantiene campos con ancho minimo cero, tarjetas publicacion que
se ajustan y tablas con desplazamiento horizontal en pantallas estrechas.

## Pruebas focales

```text
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_ui_fabrica.py
9 passed, 1 warning

ruff check app/fabrica_web.py app/static/js/fabrica.js app/static/css/fabrica.css \\
  app/templates/fabrica.html tests/test_api_fabrica.py tests/test_ui_fabrica.py
All checks passed!

node --check app/static/js/fabrica.js
```

La advertencia procede de la deprecacion de `httpx` expuesta por `TestClient`.
La suite completa se reserva para el unico pase de CI del PR.
