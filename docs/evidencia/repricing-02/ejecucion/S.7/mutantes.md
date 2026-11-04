# Mutantes de S.7

Un mutante por prueba nueva o reescrita (34), cada uno corrido solo
contra su prueba con `ORBIT_TEST_DSN` y `PYTHONPATH=.`, purgando
`app/__pycache__` antes de cada corrida. Script: `/tmp/mutantes_s7.py`
(no se commitea). 34/34 en rojo. La prueba del cron es un pin de doc
(sin codigo productivo que mutar).

## Tipos (`app/precio/tipos.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_tipos_senal_evidencia_solo_fuera_de_sin_dato | guarda de evidencia → `if False:` | ROJO |
| test_tipos_submotivos_de_unidad_reemplazan_los_de_fba | `"venta_sin_publicacion"` → `"venta_sin_publicacion_x"` | ROJO |
| test_tipos_decision_verificacion_lineal_o_cotizada | guarda de verificacion → `if False:` | ROJO |

## Senal (`app/precio/ventas.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_ventas_borde_90_no_dispara_89_si | `not u15 < esperado` → `u15 < esperado` | ROJO |
| test_ventas_ventanas_inclusive_ayer_y_anteayer | `range(16, 76)` → `range(16, 75)` | ROJO |
| test_ventas_hueco_ledger_sin_cobertura | `> 3` → `> 30` | ROJO |
| test_ventas_u60_bajo_minimo | guarda `u60_min` → `elif False:` | ROJO |
| test_ventas_n15_insuficiente_con_excluidas | guarda `n15` → `elif False:` | ROJO |
| test_ventas_historia_corta_74_dias | `< 75` → `< 7` | ROJO |
| test_ventas_dia_no_disponible_y_sin_observacion | `!= "disponible"` → `if False:` | ROJO |
| test_ventas_racha_dos_de_tres_es_sin_dato | `>= senal_dias` → `if True:` | ROJO |
| test_ventas_sin_caida_resetea_racha | racha `0` → `racha_previa` | ROJO |
| test_ventas_sin_atribuir_da_venta_sin_publicacion | guarda `sin_atribuir` → `if False:` | ROJO |
| test_r3_k5_senal_solo_mira_disponibilidad | submotivos `dia_*` cruzados | ROJO |
| test_r1_c_v4_u60_en_el_minimo_si_evalua | `< u60_min` → `<= u60_min` | ROJO |
| test_r1_c_v9_exclusiones_asimetricas_cambian_veredicto | contados15 ignora excluidas | ROJO |
| test_r1_b4_dia_sin_observacion_tiene_submotivo_propio | `disp.get(dia)` → default `"disponible"` | ROJO |
| test_r1_b4_ventana_60_toda_excluida | `n60 == 0` → `if False:` | ROJO |
| test_r1_b1_cubierto_hace_3_pasa_con_13_dias | contados15 ignora cubierto | ROJO |
| test_r1_b1_cubierto_hace_4_es_hueco | hueco solo si `cubierto is None` | ROJO |
| test_ventas_excluidas_escalan_el_promedio | contados60 ignora excluidas | ROJO |

## Trafico y cohorte (`app/precio/ventas.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_trafico_sin_subida_propia_da_sin_dato | `if ultima_subida is None:` → `if False:` | ROJO |
| test_trafico_caida_con_z_da_perdiendo | `num > 4 * den` → `num > 400 * den` | ROJO |
| test_trafico_sin_caida_da_no_perdiendo | retorno trafico → `"perdiendo"` | ROJO |
| test_trafico_corto_da_evidencia_insuficiente | minimo de exposiciones → `if False:` | ROJO |
| test_cohorte_alarma_si_subidos_caen_mas_que_control | alarma → `False` | ROJO |
| test_cohorte_conjunto_chico_no_alarma | guarda `min_unidades` → `if False:` | ROJO |
| test_cohorte_estable_no_alarma | `1 - tasa_sub/tasa_ctrl` → `tasa_sub/tasa_ctrl` | ROJO |

## Lineal y cableado (`reglas.py`, `precio_mercado.py`, `corrida.py`)

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_lineal_cierra_con_formula_sin_pedir_cotizacion | `lineal=lineal` → `lineal=False` en `_subir` | ROJO |
| test_senal_por_unidad_no_ve_las_ventas_de_otra_publicacion | ventas de `ledger_event` (sin `listing_id`) | ROJO |
| test_senal_usa_ultima_subida_y_trafico | `ultima_subida=...` → `None` | ROJO |
| test_senal_sin_clave_de_trafico_falla_fuerte | lectura de clave → `trafico_min = 200` | ROJO |
| test_ultima_subida_ignora_lo_que_no_movio_precio | estados + `"error"` | ROJO |
| test_persistir_uno_guarda_verificacion_lineal | `verificacion or "cotizada"` → `"cotizada"` | ROJO |
