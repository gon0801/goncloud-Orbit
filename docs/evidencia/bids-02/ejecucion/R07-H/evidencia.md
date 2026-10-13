# R07-H: no ofrecer Regresar si la campana ya esta como antes

La pantalla pintaba el boton Regresar de un ajuste confirmado aunque
la configuracion vigente ya hubiera vuelto al valor de antes; la ruta
contestaba 409. Ahora la pantalla compara el vigente contra el antes
de cada ajuste (igualdad completa o coincidencia en la clase) y no lo
ofrece en ese caso.

Prueba primero: tests/test_pantalla_dinero.py::test_r07h_no_ofrece_regresar_si_ya_esta_como_antes
caia porque el ajuste salia en regresables. Mutante: apagar el filtro
deja la prueba en rojo. Archivo: 41 passed.

Correccion posterior (d4dd968): la primera version importaba
`_config_por_id` desde `app.apply` y rompia
`test_pantallas_no_importan_escritura`. La lectura por id vive ahora
en `app/ads/campana_config.config_por_id`, `apply` delega en ella y
la pantalla la importa desde ahi. Salida: 141 passed en
test_arq_bids_p, test_pantalla_dinero, test_aplica_ajuste y
test_campana_config.
