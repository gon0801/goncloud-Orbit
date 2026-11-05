# P13: revision de costos y medidas de PEZ

Revision del 5-nov-2026 UTC autorizada por el usuario («ok hazlo»), con GPT Sol.
[Plan PEZ 02](../../../../plans/pez-02.md). PEZ 01 y sus nueve correspondencias
siguen cerrados; esta revision no cambio datos productivos.

## Resultado

| Frente | Resultado comprobado | Estado y siguiente paso |
|---|---|---|
| Costo publicado frente a componentes | Las diferencias de 4.68 MXN dorado y 4.20 MXN plateado concilian 9/9 con el cambio del cofre: de 48.74 a 44.06/44.54 tras una recepcion del 24-ago. Los SET retienen sus valores publicados del 18-ago. | Investigacion cerrada. Conservar la fuente publicada; no se demostro error de ingesta ni una obligacion de recalcularla como suma material actual. |
| Diametro de arras PEZ | Odoo registra familia nominal 22; MeLi dice 16/19 mm en descripciones comunes a seis modelos. No hay medida fisica confirmada en las fuentes revisadas. | Pendiente confirmar el diametro real. Borradores guardados, sin publicar. No sustituir globalmente por 22 mm: Italia incluye recetas de familia 19 y 22. |

## Costos: lo que cambio y lo que no puede inferirse

La propiedad del costo de los nueve SET conserva su escritura del 18-ago.
Los cofres cambiaron el 24-ago al recibirse mercancia bajo costo promedio
(AVCO): sus capas de valoracion y propiedades tienen el mismo instante.
Los nueve SET son kits `phantom`, sin capas propias encontradas. No se atribuye
su costo retenido a un promedio de movimientos de los SET.

La suma del BOM actual valorada con precios historicos reproduce los nueve
valores publicados. Esto concilia las diferencias, pero no demuestra la
composicion de cada receta el 18-ago ni el autor/metodo de aquel calculo.
`write_date` de producto no se usa como fecha de cambio de costo.

El codigo instalado distingue valoracion del producto y recosteo por BOM.
El primero es la fuente actual de Orbit; el segundo es una operacion que
puede escribir en Odoo. No se invoco. Adoptar suma material para otra
proyeccion seria una decision de fuente, no arreglar una sincronizacion rota.

- [Informe y tabla de nueve costos](costos/README.md).
- [Historial independiente de accounting](accounting/historial.json),
  [conciliacion Decimal](accounting/conciliacion.json) y
  [script reproducible](accounting/conciliar.py).

## Medidas: propuesta concreta y dato faltante

Las nueve descripciones se comparten entre 54 variantes comerciales. Se
preparo un borrador minimo por item que retira sus tres afirmaciones de
un diametro universal, conservando literalmente el resto. No se publico
ninguno y no se editaron las otras 45 variantes por inferencia.

Si el usuario confirma fisicamente 22 mm para PEZ, la nota propuesta es:
«Variante Sello cristiano (PEZ): arras de 22 mm de diametro». Esa medida no se
transfiere a los demas modelos, al cofre ni al estuche. Si no esta confirmada,
el borrador neutro queda sin asignar una cifra fisica a PEZ.

Se solicito al usuario confirmar 22 mm / 16 o 19 mm / no confirmado. La
pregunta sigue sin respuesta al corte de esta revision. La falta de respuesta
no confirma una medida ni autoriza publicar una descripcion.

- [Informe de medidas](medidas/README.md), [matriz](medidas/matriz.md) y
  [borradores por item con fragmentos exactos](medidas/borradores.json).
- Las fotografias con cotas son de los estuches; no se usaron para deducir
  el diametro de las arras mediante proporciones de pixeles.

## Verificacion y limites

Revision independiente GPT Sol: APPROVE de la investigacion y borradores.
Conciliacion Decimal reproducida 9/9 contra historia externa; lecturas frescas
confirman 19 productos/costos/unidades, nueve BOM y 27 lineas. Los 27 retiros
de texto propuestos coinciden literalmente con la fuente y no alteran el
resto de las descripciones. Scripts comprobados con Ruff, compilacion/AST,
JSON y revision de secretos. No se agregaron pruebas de texto.

Fuentes externas: Odoo XML-RPC solo lectura, codigo instalado leido sin
invocarlo, SQLite `mode=ro/query_only` con transaccion y timeout 15 s, y GET
MeLi sin refresh de token. Secretos solo en memoria. No se escribieron Odoo,
Orbit, MeLi, accounting o bridge. Las capturas tienen hora UTC y no constituyen
una transaccion atomica entre todos los sistemas.
