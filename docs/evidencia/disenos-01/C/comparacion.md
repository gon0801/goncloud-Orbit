# Comparacion de Contribucion (DISENOS 01, bloque C)

## Antes

Se guardaron de produccion (`http://10.13.13.1:8010`) antes de tocar codigo:

- `antes/contribucion.html` (53,364 bytes)
- `antes/api-contribucion.json` (13,561 bytes)

El JSON es la fuente que alimenta la pagina y no cambia con este bloque.

## Metodo

`compara.py` renderiza el template nuevo con el JSON de antes y, para cada una
de las 38 filas (Amazon US 11, Amazon MX 27), exige:

- nombre, rango `sin halo .. con halo` (o `— (motivo)`), fechas, chip de
  `precio min multilisting`, chip de tipo de cambio aproximado y motivo parcial;
- los mismos importes, fechas, chip y motivo que la fila de la pagina anterior;
- la clase de signo y su barra (linea en 0, rango o punto);
- el eje de la plataforma (minimo, 0 y maximo) y el recorte con
  `▸ valor real` solo donde la escala se sale;
- los conteos de la tarjeta por clase y los segmentos de la barra.

## Resultado con los datos de antes

```text
origen del payload: docs/evidencia/disenos-01/C/antes/api-contribucion.json
filas comprobadas: 421
sin diferencias
```

- Total: 38 filas, las mismas 38 de la pagina anterior.
- Amazon US: 11 campanas; Positiva 4, Cruza 0 5, Negativa 2, Sin dato 0.
  Eje -1,000 .. 2,000 con el 0 en 33%.
- Amazon MX: 27 campanas; Positiva 9, Cruza 0 7, Negativa 6, Sin dato 5.
  Eje -2,000 .. 50,000 con el 0 en 4%; el minimo no se rotula porque el cero
  queda a menos del 12% del borde.
- Punto (minimo igual al maximo): 8 filas (`Wedding Coin - Asin Targeting`,
  `ASEM - Product Targeting - MX`, `ASEM - Category Exact - MX`,
  `AGMX - Category Exact - MX`, `AGMX - Auto Discovery - MX` y las tres
  `kit_arras | Personalizado | ...`).
- Sin dato: 5 filas (`Exact_arras_de_boda`, tres `AD_READY` con catalogo
  incompleto y `AD_READY: B0D9F4V1CC` sin ventas en el libro para repartir).
  Ninguna dibuja barra.
- Escala recortada: `AC - Category Exact - MX` (122,380.55 MXN) termina en el
  borde de 50,000 y muestra `▸ valor real: 122,380.55`.

## Movil

`mide_390.py --html docs/evidencia/disenos-01/C/despues/contribucion.html`:

```json
{"ancho": 390, "scrollWidth": 390, "bodyScrollWidth": 390, "bodyClientWidth": 390, "bodyScroll": false, "tablaScroll": true}
sin desborde a 390 px; la tabla scrollea dentro de su caja
```

Las capturas quedan en `despues/contribucion-1440.png` y
`despues/contribucion-390.png`.

## Despues del despliegue

La misma comparacion contra produccion se corre con:

```text
python docs/evidencia/disenos-01/C/compara.py --base http://10.13.13.1:8010
python docs/evidencia/disenos-01/C/mide_390.py --base http://10.13.13.1:8010
```

El recibo del despliegue del supervisor respalda esa corrida; el JSON no cambio
y la comparacion local ya cubre los 421 valores.
