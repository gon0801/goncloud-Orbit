# R07-D: el rejuego ignora la tercera rama de v_cambio_bid

La tercera rama de la vista (ajuste_de_campana) trae bid_antes y
bid_despues iguales al bid de hoy, asi que un ajuste de campana
confirmado despues del ciclo pisaba el bid del dia al caminar hacia
atras. La consulta de cambios del rejuego ahora excluye ese origen.

Prueba primero: tests/test_rejuega_niveles.py::test_r07d_cambios_del_rejuego_ignoran_el_ajuste_de_campana
sembraba un ajuste confirmado y la consulta lo devolvia. Mutante:
quitar el filtro deja la prueba en rojo. Archivo: 17 passed.
