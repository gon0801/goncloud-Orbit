# R07-09C: reintento de H#N pendiente

Al reaplicar un plan cuya fila base ya estaba confirmada (tras un
regreso), un readback divergente dejaba la fila H#2 pendiente y el
siguiente reintento abria la H#3, dejando la H#2 huerfana para
siempre. Ahora el reintento busca primero una H#N pendiente de la
misma campana y clase y la reusa: la confirma con el PUT, o la sella
sin PUT si el vigente ya esta en destino.

Prueba primero: tests/test_aplica_ajuste.py::test_r07_09c_reintento_reusa_la_pendiente_hn
caia con el id nuevo (4) en vez del de la H#2 (3). Mutante: buscar la
pendiente entre las confirmadas en vez de las pendientes deja la
prueba en rojo. Archivo completo tras el arreglo: 48 passed.

Correccion del panel poteto: la ruta de produccion no siempre entra
por aplica_ajuste_campana. Si el vigente ya esta en destino, el
replanteo de la ruta levanta AjusteSinCambio y deriva a
reconcilia_ajuste_campana, que solo buscaba la fila base por su
huella y devolvia esa fila ya sellada, dejando la H#2 pendiente para
siempre con su efecto vivo (reproducido con datos reales: tras
reconciliar, la H#2 seguia sin confirmar). La regresion
test_r07_09c_reconcilia_sella_la_pendiente_hn nacio en rojo
(devolvia la base, id 1, en vez de la H#2, id 3). Ahora la
reconciliacion busca la H#N pendiente de la huella y la sella cuando
el vigente ya es su destino, y devuelve esa fila. Salida: 50 passed
en test_aplica_ajuste y 61 passed en test_api_write y test_api.

Correccion de interrogate (r2): la reconciliacion sellaba solo la
primera pendiente de la huella (LIMIT 1); con dos pendientes del
residuo previo, la segunda quedaba huerfana. La regresion
test_r07_09c_reconcilia_sella_todas_las_pendientes nacio en rojo
(quedaba una fila sin sellar). Ahora las dos puertas leen todas las
pendientes con un solo ayudante y sellan cada una cuyo destino ya es
el vigente. De paso cayo el envoltorio _config_por_id de apply, que
solo pasaba la llamada a campana_config.
