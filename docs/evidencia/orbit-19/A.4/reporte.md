# ORBIT 19 A.4 - selector por publicacion y objetivo explicito

Fecha: 2026-09-06 UTC.

## Cobertura

- La pantalla selecciona `listing_ids`, no productos internos.
- Cada publicacion muestra ASIN, SKU de Amazon, margen, muestra de dias con
  venta, ventana y enlace seguro.
- ASIN o SKU Amazon ausente o invalido impiden seleccionarla. Margen nulo,
  cero o negativo e historial Ads ausente son avisos: siguen seleccionables
  con objetivo manual.
- El objetivo es obligatorio: manual requiere ACoS explicito; el derivado
  declara que exige margen medido positivo. No se completa valor alguno de
  forma implicita.
- La solicitud de preview v2 envia `listing_ids` y `objetivo`; cambiar un
  campo invalida el preview antes de crear.
- La interfaz conserva confirmacion, token solo en encabezado y recuperacion
  de lote. Esta comprobacion no llamo a la ruta de creacion.

## Regresion

`regresion-red.txt` es la ejecucion de
`test_selector_v2_exige_objetivo_y_envia_listings_no_productos` contra
`d6ff849`, previo a A.4. Falla porque no existen los controles de objetivo.
La prueba actual pasa y verifica los IDs de los controles y el cuerpo v2 sin
`productos`.

## Verificacion visual

Se uso una instancia local con template, CSS y JavaScript reales y datos de
fixture. No se uso Amazon ni se crearon campanas.

- [selector-publicaciones.png](selector-publicaciones.png): escritorio con
  ASIN, SKU, margen, muestra/ventana, avisos y enlace por publicacion.
- [preview-manual.png](preview-manual.png): preview con las tres publicaciones
  y objetivo manual explicito de 25.00 por ciento.
- [selector-publicaciones-movil.png](selector-publicaciones-movil.png): el
  mismo selector a 390 x 844; mantiene la seleccion, avisos y tarjetas sin
  recorte horizontal.

El recorrido de teclado dio foco al checkbox de la publicacion 101, uso
`Space` para marcarlo y `Tab` para avanzar; el snapshot confirmo el estado
marcado y los labels asociados. El flujo de preview uso solo `/catalogo` y
`/plan`; nunca `/crear`.

## Pruebas focales

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \\
  tests/test_fabrica_plan.py tests/test_fabrica_campanas.py \\
  tests/test_api_fabrica.py tests/test_ui_fabrica.py
154 passed, 1 warning

ruff check y ruff format --check: passed
node --check app/static/js/fabrica.js: passed
```

La advertencia procede de la deprecacion de `httpx` expuesta por `TestClient`.
La suite completa se reserva para el unico pase de CI del PR.
