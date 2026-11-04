# R13-K: gasto de la pantalla de busquedas con dos decimales

La pagina pintaba el gasto tal cual (511.8600 MXN). La presentacion
del endpoint que la pagina consume ahora formatea el gasto de cada
seccion y el de cada fila de la parte de aqui con dos decimales.
El contrato crudo no trae gasto en historial, asi que ahi no hay
nada que formatear.

Prueba primero: tests/test_api_dashboard.py::test_r13k_pagina_gasto_sin_venta_dos_decimales
pide la pagina y exige 12.50 MXN sin 12.5000; caia con el valor de
cuatro decimales. Las afirmaciones del endpoint que esperaban
"5.0000"/"12.5000" pasaron a "5.00"/"12.50". Mutante: no formatear
el total de la seccion deja la prueba en rojo.

Correccion posterior: el primer arreglo formateo el endpoint de
gasto-sin-venta (dashboard), pero la pagina de busquedas seguia
pintando cuatro decimales: su endpoint devolvia el gasto tal cual
("400.5000 MXN" en la pagina, medido contra la app local). La fila
cubre las dos pantallas. La segunda prueba,
test_r13k_busquedas_gasto_con_dos_decimales, nacio en rojo contra el
codigo anterior ('511.8600' contra '511.86') y el endpoint
busquedas_sin_venta ahora formatea el gasto de asins y palabras con
_dinero_2. Verificado en vivo: la pagina pinta "400.50 MXN" y el
JSON trae "gasto":"400.50".
