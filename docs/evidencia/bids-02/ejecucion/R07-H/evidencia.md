# R07-H: no ofrecer Regresar si la campana ya esta como antes

La pantalla pintaba el boton Regresar de un ajuste confirmado aunque
la configuracion vigente ya hubiera vuelto al valor de antes; la ruta
contestaba 409. Ahora la pantalla compara el vigente contra el antes
de cada ajuste (igualdad completa o coincidencia en la clase) y no lo
ofrece en ese caso.

Prueba primero: tests/test_pantalla_dinero.py::test_r07h_no_ofrece_regresar_si_ya_esta_como_antes
caia porque el ajuste salia en regresables. Mutante: apagar el filtro
deja la prueba en rojo. Archivo: 41 passed.
