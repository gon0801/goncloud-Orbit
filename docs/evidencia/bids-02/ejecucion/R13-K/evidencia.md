# R13-K: gasto de la pantalla de busquedas con dos decimales

La pagina pintaba el gasto tal cual (511.8600 MXN). La presentacion
del endpoint que la pagina consume ahora formatea el gasto de cada
seccion y el de cada fila (aqui e historial) con dos decimales.

Prueba primero: tests/test_api_dashboard.py::test_r13k_pagina_gasto_sin_venta_dos_decimales
pide la pagina y exige 12.50 MXN sin 12.5000; caia con el valor de
cuatro decimales. Las afirmaciones del endpoint que esperaban
"5.0000"/"12.5000" pasaron a "5.00"/"12.50". Mutante: no formatear
el total de la seccion deja la prueba en rojo.
