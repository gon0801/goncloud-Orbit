# Mutantes de M.1: uno por regla, todos muertos

Método: aplicar el mutante al código, correr su prueba focalizada, observar el
rojo con el diff predicho, revertir. 24/24 muertos, 0 sobrevivientes. R17
necesita quitar sus dos capas (chequeo en `decide` + backstop en `_paso`):
con una sola capa el veredicto no cambia por diseño.

## Tabla

| # | Mutante | Prueba que lo mata | Rojo observado |
|---|---|---|---|
| R0 | `decide` ignora el pause (`return Pausar` → `pass`) | `test_r0_pausa_por_umbral` | `Mantener(sin_gasto)` en vez de `Pausar(pause_umbral)` |
| R1 | regresa sin filtro de volumen (`if volumen is False` → `if False`) | `test_r1_no_regresa_sin_filtro_de_volumen` | `Regresar(12.00)` en vez de `Mantener(recorte_costo_trafico)` |
| R1 | regresa con razón 0.30 (`<` → `<=`) | `test_r1_no_regresa_con_razon_sobre_el_umbral` | `Regresar(12.00)` en vez de `Mantener(recorte_costo_trafico)` |
| R2 | no espera (`< DIAS_EFECTO` → `>`) | `test_r2_espera_antes_de_7_dias` | `Mantener(recorte_costo_trafico)` en vez de `Mantener(esperando_efecto)` |
| R3 | fuerte da -12 % (`PASO_BAJA_FUERTE` → `SUAVE`) | `test_r3_fuerte_recorta_25` | `Mover(-0.12, …)` en vez de `Mover(-0.25, 7.5000, …)` |
| R4 | hereda -25 % (`SUAVE` → `FUERTE`) | `test_r4_hereda_12_y_nunca_25` | `Mover(-0.25, …)` en vez de `Mover(-0.12, 8.8000, …)` |
| R5 | motivo cambiado a `azar_lo_explica` | `test_r5_vende_dentro_del_margen` | motivo `azar_lo_explica` en vez de `vende_dentro_del_margen` |
| R6 | subida da -12 % (`SUBIDA` → `BAJA_SUAVE`) | `test_r6_sube_con_evidencia` | `Mover(-0.12, …)` en vez de `Mover(0.15, 11.5000, …)` |
| R7 | motivo cambiado a `sin_evidencia` | `test_r7_un_pedido_no_concluye` | motivo `sin_evidencia` en vez de `azar_lo_explica` |
| R8 | `inmaduros >= 1` → `> 1` | `test_r8_venta_reciente_gana_al_gasto` | `Mover(-0.25, 7.5000, gasto_sin_venta_doble)` en vez de `Mantener(venta_reciente)` |
| R9 | recorta con cualquier gasto (`>= concluir` → `>= 0`) | `test_r9_simple_y_doble` | `Mover(-0.12, gasto_sin_venta)` con gasto 349.99 en vez de `Mantener(sin_evidencia)` |
| R10 | `gasto == 0` → `== 1` | `test_r10_sin_gasto` | `Mantener(sin_evidencia)` en vez de `Mantener(sin_gasto)` |
| R11 | sin materialidad (`>= 0.25×` → `>= 0×`) | `test_r11b_hoja_delgada_no_mueve_dinero` | `Mover(-0.12, grupo_sangra)` con gasto 50 en vez de `Mantener(hoja_delgada…)` |
| R11b | delgada recorta (devuelve candidato) | `test_r11b_hoja_delgada_no_mueve_dinero` | `Mover(-0.12, grupo_sangra)` en vez de `Mantener(hoja_delgada…)` |
| R12 | motivo cambiado a `sin_evidencia` | `test_r12_grupo_cumple` | motivo `sin_evidencia` en vez de `grupo_cumple` |
| R13 | motivo cambiado a `grupo_cumple` | `test_r13_sin_veredicto_del_grupo` | motivo `grupo_cumple` en vez de `sin_evidencia` |
| R14 | sin salida por tiempo (quita `dias < 14`) | `test_dueno_recorte_al_dia_14_sin_20_clics` | `Mantener(esperando_precio_medido)` al día 14 en vez de `Mover(-0.12, 4.4000, …)` |
| R15 | repite sin 20 clics (quita el freno) | `test_r15_repite_solo_con_20_clics` | `Mantener(recorte_costo_trafico)` en vez de `Mantener(sin_clics_nuevos)` |
| R16 | segundo recorte con razón 0.50 (`< 0.70` → `< 0.10`) | `test_r16_recorte_que_costo_trafico` | `Mover(-0.12, gasto_sin_venta)` en vez de `Mantener(recorte_costo_trafico)` |
| R17 | recorta bajo el piso (quita ambas capas) | `test_r17_no_baja_del_piso_aprendido` | `Mover(-0.12, 3.5200, …)` con piso 3.52 en vez de `Mantener(piso_aprendido)` |
| R18 | ignora inmaduros None (quita el freno) | `test_r18_dato_faltante` | `Mantener(sin_gasto)` en vez de `Mantener(dato_faltante)` |
| R19 | ignora rango (`if not (…)` → `if False`) | `test_r19_rango_bloquea_ajuste` | `Mover(-0.12, 50.00, …)` con techo 50 y bid 100 en vez de `Mantener(rango_bloquea_ajuste)` |
| JSON | `desde_json` acepta claves faltantes | `test_desde_json_rechaza_clave_faltante` | `KeyError: 'hoja_id'` en vez de `ValueError` |
| global | `decide` usa target global 20.72 | `test_decide_usa_el_target_del_caso` | `Mover(0.15, bajo_target)` con target 5.00 en vez de `Mantener(azar_lo_explica)` |

Comando por mutante (ejemplo R9): aplicar el cambio, correr
`pytest -q tests/test_optimizer_politica.py::test_r9_simple_y_doble`,
observar `1 failed`, revertir. Todos revertidos; el árbol queda con el
código sano y la suite en verde.
