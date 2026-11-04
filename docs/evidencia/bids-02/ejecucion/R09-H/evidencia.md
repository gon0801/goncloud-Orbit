# R09-H: el lote sigue ante un fallo por producto

Decision de diseno ya tomada por el dueno: si un producto falla al
lanzar, `lanza` continua con los demas y devuelve el resultado por
producto, con los fallos adjuntos, en el mismo formato parcial que ya
usa la fabrica. El docstring de `lanza` ya lo prometia; el codigo
abortaba el lote cuando un producto chocaba con un impulso vivo.

## Pruebas primero

- Nueva: `test_r09h_lote_de_tres_con_el_segundo_en_conflicto_lanza_los_otros`.
- Actualizadas por el cambio de contrato declarado en la fila:
  `test_lanza_rechaza_vivo_y_acepta_ids_vacios_con_422` y
  `test_lanza_409_por_vivo_adjunta_lo_lanzado` (el 409 que abortaba
  pasa a fallo del parcial) y la asercion final de
  `test_r09g_tope_por_producto_previa_marca_y_lanza_rechaza_409`.

Comando:

```bash
ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:5433/postgres \
  uv run --frozen python -m pytest -q -rs tests/test_impulso_io.py tests/test_api_fabrica.py
```

- Antes: 4 failed (`assert 409 == 502`: el conflicto abortaba el lote).
- Despues: 212 passed, 0 saltadas. Lote de tres con el segundo en
  conflicto: el primero y el tercero se lanzan y el parcial trae el
  fallo del segundo.

## Cambios

`app/impulso_io.py`: el conflicto con un impulso vivo (del mismo
listing con otro plan, o de otro listing del mismo producto) entra a
`fallos` por producto y el ciclo continua. Los no lanzables de la
vista previa cuyo producto tiene un vivo tambien entran al parcial
(`_fallos_por_vivo`). Se elimino el chequeo global que abortaba la
solicitud entera (venia de R09-G; su candado queda en la vista previa
y en el chequeo por producto bajo el advisory lock).

## Mutante

Volver el conflicto bajo lock a `raise ErrorImpulso(409, ...)`:
las tres pruebas del parcial pasan a rojo (3 failed). Revertido:
4 passed en el subconjunto de la fila.

## Ajuste de relectura

`_fallos_por_vivo` solo convierte a fallo cuando el vivo es de OTRO
listing: si el vivo es del mismo listing y el producto quedo fuera
por otra causa (sin margen, sin subfamilia), el motivo real manda y
el "nada lanzable" sigue en 422.
