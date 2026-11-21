# Ronda de revision 1 (Devin sobre 3d8c71b: 4 hallazgos, los 4 corregidos)

Todos demostrados FALLANDO contra 3d8c71b0305ef455919f2d512a8c77265012f37b
con la prueba nueva correspondiente:

1. **Pujas reales en modo Practica** (cycle.py): con
   `ads_bid_recuperacion_<plat> = shadow` el caso real llevaba el marcador y
   el salto se enviaba a Amazon. Fix: el marcador solo viaja con modo `live`
   (igual que la temporada; la comparacion de sombra llega con el bloque D).
   Prueba `test_temporada01_c_practica_no_envia_pujas` (dinero): roja = el
   ciclo live decidia `recupera_nivel` 5.04 y la aplicaba; verde = solo el
   paso de R6 de hoy.
2. **Ciclo abortado por gasto desconocido** (lecturas_caso.py): una fila de
   campana con impresiones y costo NULL (la ingesta y el esquema lo admiten)
   reventaba `None >= Decimal` en `_presupuesto_campanas` y tumbaba el ciclo
   entero. Fix: el dia sin costo se desconoce -> `dias_topados = None` (BUD
   no sube: dato faltante, no inventado). Prueba
   `test_temporada01_c_costo_null_no_tumba_el_ciclo` (dinero): roja =
   TypeError; verde = dias_topados None.
3. **Recuperacion por debajo de la puja minima** (recuperacion.py): si el
   dueno elevo el piso del goal por encima del nivel sostenido por la
   evidencia, el salto salia por debajo del piso. Fix: destino < piso ->
   None (lo decide el R6 de hoy, que aplica su ajuste de rango). Prueba
   `test_temporada01_c_piso_del_goal_sin_evidencia_para_cruzarlo` (pura):
   roja = destino 4.45 con piso 6.00; verde = None.
4. **Recuperacion con pujas fuera de memoria** (recuperacion.py): con un
   episodio abierto mas viejo que la memoria, la historia extendida metia en
   `techo_conocido` pujas fuera de la memoria elegida y autorizaba saltos
   mas altos. Fix: el techo solo considera manos y cambios dentro de
   `memoria_dias` (hoy = ventana_hasta + DIAS_MADUREZ). Prueba
   `test_temporada01_c_memoria_acota_el_techo` (pura): roja = techo 30.00
   con memoria 365; verde = 8.00 (y 30.00 con memoria 1000).
