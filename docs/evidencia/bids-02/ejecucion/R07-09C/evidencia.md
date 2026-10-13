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
