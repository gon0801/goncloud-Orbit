# Mutantes de 0.a

Un mutante por prueba nueva: el cambio de una linea, la prueba en rojo y el
codigo restaurado. Cada linea se corrio con

	PATH="/opt/homebrew/bin:/usr/bin:/bin" ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
		PYTHONPATH=. .venv/bin/python -m pytest -q <prueba>

contra el codigo mutado; al terminar, `git checkout -- <archivo>`.

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_importe_suma_resta_escala_y_compara_en_la_misma_moneda | `__sub__` suma en vez de restar | ROJO |
| test_importe_con_monedas_distintas_revierta | `__add__` sin `_misma_moneda` | ROJO |
| test_convertir_es_la_unica_puerta_entre_monedas | chequeo `importe.moneda != tasa.base` desactivado | ROJO |
| test_cuenta_no_se_construye_con_monedas_mezcladas | `len(monedas) != 1` por `< 1` | ROJO |
| test_cuenta_margen_es_ingreso_menos_todo_sobre_ingreso | margen sin restar el envio | ROJO |
| test_envio_proprio_exige_muestra_propia | post_init de `EnvioPropio` sin chequeo | ROJO |
| test_envio_imputado_rechaza_muestra_propia_y_sin_donantes | post_init de `EnvioImputado` sin chequeo | ROJO |
| test_miembro_que_manda_toma_el_de_precio_estrella_mas_alto | `estrella > mejor_p` invertido | ROJO |
| test_miembro_que_manda_desempata_por_sku_ascendente | `sorted(..., reverse=True)` | ROJO |
| test_puerto_expone_vitrina_y_mercado | protocolo sin `cotizar` | ROJO |
| test_registro_de_universos_trae_las_cuatro_entradas | amazon_us/fbm con `moneda_venta="MXN"` | ROJO |
| test_lector_de_config_falla_y_nombra_la_clave_que_falta | `_modo` devuelve "shadow" si falta la clave | ROJO |
| test_config_motor_rechaza_claves_fuera_de_cota | cota inferior de `precio_fusible_frac` a 0.001 | ROJO |
| test_config_motor_rechaza_modo_invalido_y_mapeo_basura | "turbo" aceptado como modo | ROJO |
| test_modo_de_da_off_para_universo_sin_entrada | default del universo sin entrada a "shadow" | ROJO |
| test_abrir_meli_levanta_config_invalida_con_adaptador_vacio | mensaje sin "meli" | ROJO |
| test_abrir_desconocida_levanta_config_invalida | `_mercado_de` devuelve None en vez de reventar | ROJO |
| test_atribuidor_es_un_protocolo_con_atribucion_concreta | `atribuidor_amazon.atribuir` devuelve `object()` | ROJO |
| test_todo_literal_de_motivo_de_la_estimacion_esta_en_motivos_estimacion | motivo nuevo `universo_no_soportado_zz` en `_motivo_universo` | ROJO |
| test_motivos_no_evaluado_es_estimacion_mas_los_propios_del_motor | `envio_sin_historia` fuera del vocabulario | ROJO |
| test_la_0054_deja_entrar_el_insert_de_decision_que_hace_hoy_el_codigo | OBLIGATORIO: la 0054 con el CHECK que exige `l_origen` (el de la 0058) | ROJO |
| test_la_0054_deja_entrar_el_insert_de_escenario_disponible_que_hace_hoy_el_codigo | OBLIGATORIO: la 0054 con el CHECK que exige `logistica_origen` (el de la 0055) | ROJO |
| test_rechaza_muestra_familia_sin_donantes | OBLIGATORIO: `precio_muestra_origen_coherente` con `CHECK (origen = origen)` | ROJO |
| test_rechaza_decision_con_goal_id_ya_cerrado | OBLIGATORIO: rama `NEW.goal_id` desactivada (`AND FALSE`) | ROJO |
| test_rechaza_liberacion_para_corrida_que_no_retuvo | trigger `precio_liberacion_exige_retencion` borrado | ROJO |
| test_rechaza_update_de_origen_sobre_goal_ya_sembrado | `origen`, `lote` y `m_referencia` fuera del ROW inmutable | ROJO |
| test_sigue_rechazando_live_bajo_goal_shadow_y_acepta_shadow_bajo_goal_live | OBLIGATORIO: predicado de modo vuelve a `g.mode = NEW.mode` | ROJO |
| test_acepta_goal_reeditado_el_mismo_dia | indice parcial sin `WHERE valid_to IS DISTINCT FROM valid_from` | ROJO |
| test_la_0054_siembra_config_con_modo_global_shadow_y_conserva_lo_previo | clave sembrada `precio_modo_zz` en vez de `precio_modo_global` | ROJO |
| test_atribuye_venta_nueva_y_rellena_la_historia_sin_duplicar | INSERT de atribucion sin `ON CONFLICT DO NOTHING` | ROJO |
| test_venta_ya_ingerida_sin_atribuidor_se_atribuye_en_la_siguiente_corrida | `_id_de_evento` siempre None (no rellena historia) | ROJO |
| test_atribuidor_que_no_puede_no_escribe_y_no_suma_rows_skipped | `_rows_skipped` sin excluir `venta_sin_atribuir` | ROJO |
| test_precio_mercados_es_el_unico_importador_de_los_adaptadores | `import app.meli.precio_mercado` sembrado en `app/ui.py` | ROJO |

Los cinco mutantes obligatorios del plan que tocan esta seccion estan
marcados OBLIGATORIO y todos mueren.
