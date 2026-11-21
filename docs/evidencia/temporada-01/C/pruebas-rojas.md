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

## Cierre con el detalle del coordinador (caso D del diseno y frenos de temporada)

1. `test_temporada01_c_recupera_con_evidencia` ahora siembra el CASO D exacto
   (12 pedidos pesados en el tramo reciente, 100 clics pesados, cpc 1.90,
   ticket 450, previa de cuenta CVR 3 % con la hoja de relleno): la decision
   del ciclo real es `Regresar(7.10)` (2.16 x 3.2904), el replay del caso
   congelado rejuega igual y la MISMA evidencia al precio nuevo no repite el
   salto. Techo de baja compartido aparte:
   `test_temporada01_c_techo_de_baja_comparte_el_caso_d` (dinero): la cadena
   20 -> 10 -> 7 con la evidencia del caso D sostiene 23.03 y el techo de baja
   la topa en `Regresar(14.00)`. Rojas contra c670287 (pre-C: la maquinaria de
   R6r no existe — ImportError del modulo de pruebas).
2. `test_temporada01_c_sin_frenos_tras_cambio_de_temporada` (pura): el ultimo
   cambio de temporada no pasa por `_freno_trayectoria` (decision 7 del plan):
   el salto de recuperacion y la subida normal de R6 salen sin R14/R15.
   Roja contra fae87b30ee9ea100332febbd7b8d7c210b7d31c5: el salto quedaba en
   Mantener(esperando_precio_medido).
3. Puerta del presupuesto: la lectura solo corre cuando temporada o
   recuperacion estan en modo `live` (con shadow la plataforma sigue byte a
   byte lo de hoy; sin flags, identico).

## Revision del dueno (PRRT_kwDOUANqbs66bJPw: el freno BUD ignoraba el corte bitemporal)

`_presupuesto_campanas` leia el gasto de campana de `v_metric_latest` y el
presupuesto de `v_campana_config_vigente` SIN `visto_el`: una correccion
append-only del costo o un cambio de presupuesto alteraban `dias_topados` y
cambiaban BUD y el veredicto al rejugar un ciclo con `metrics_as_of`.

Fix: `visto_el` propaga a `_presupuesto_campanas` — el gasto sale de
`metrics_as_of(%s)` (el mismo fragmento `_fuente` de tramos y precio) y el
presupuesto de la ultima observacion de `ads_campana_config_observation` con
`observed_at <= visto_el`.

Prueba `test_temporada01_c_presupuesto_rejuega_con_visto_el` (dinero): con
corte en D_E1, una correccion del costo del dia a 2000 y un cambio de
presupuesto a 100 DESPUES del corte dejan el insumo IDENTICO al rejugar
(1000.00, 7 dias, 0 topados); sin corte el freno si cambia (100.00, 1
topado). Roja contra e891f5b0d427dc71e40624770d4a0241a22b58cc: la firma ni
siquiera aceptaba `visto_el`.
