# ORBIT 19 A.2 - Normalizacion comun CLI/API/motor

Fecha: 2026-09-06 UTC.

## Alcance realizado

- `POST /api/fabrica/plan` conserva v1 con `productos` y acepta v2 solo con
  `listing_ids` y un `objetivo` explicito. Una mezcla de formas se rechaza en
  la frontera HTTP antes de consultar o mutar.
- API y CLI convierten sus entradas en los mismos argumentos de
  `tools/fabrica_campanas._arma_plan`; el motor construye el plan v2 canonico.
  El CLI v2 exige `--listing-ids` y `--target-acos`; no transforma
  `--productos` en listings.
- La ruta medida v2 exige todos los margenes positivos y que el ACoS aplicado
  no supere el menor. Si falta margen, es cero, negativo o el clamp lo supera,
  rechaza con `manual_lanzamiento`; v1 conserva su regla y huella.
- La identidad v2 exige listing de la plataforma, ASIN valido y `seller_sku`
  unico antes de cualquier POST. Margen `NULL` y varios listings del mismo
  producto llegan al preview manual y se guardan como `null` en el snapshot.
- El dry-run v2 muestra el origen, la procedencia y los conteos reales de
  semillas por rol. No declara una simulacion vacia si el plan contiene datos.
- El interruptor `fabrica.creacion` es fail-closed: ausente o invalido equivale
  a `v1` y bloquea solo altas v2. El preview y las rutas de recuperacion no
  dependen de ese interruptor.

## Regresion y pruebas focales

`regresiones-red.txt` conserva la salida contra `e6c462d`, antes de estas
correcciones. El mismo conjunto falla porque acepta ASIN vacio, margen medido
cero/negativo/inferior al ACoS y reporta `semillas=0` aun con semillas reales.
No hubo HTTP ni escrituras durante la reproduccion.

Tras el cambio:

```text
PYTHONPATH=. .venv/bin/python -m pytest -q \\
  tests/test_fabrica_campanas.py tests/test_fabrica_plan.py
107 passed
```

Las pruebas de API con PostgreSQL real cubren preview v2 con
NULL/multilisting, rechazo de mezcla y SKU duplicado sin escribir lote, el
contrato decimal del catalogo y bloqueo de alta v2 con el interruptor ausente.
Se ejecutan en la bateria completa unica del PR; no se duplica localmente.

## Limite deliberado

No se llamo a Amazon ni a `/crear`. A.3 implementa el registro y la
recuperacion v2; A.5 ensaya la reversa con lote parcial y solo hace smoke
productivo de GET/preview.
