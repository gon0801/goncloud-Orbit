# Mutantes de M.1

Un mutante por prueba nueva (20), cada uno corrido solo contra su
prueba con `ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres"`
y `PYTHONPATH=.`, restaurando el archivo despues de cada uno (sin
purga de `__pycache__`: cada aplicacion cambia el mtime y el verde
final confirma que nada quedo pegado). Script: `/tmp/m1_aplica_mutante.py`
(no se commitea). 20/20 en rojo, 20/20 en verde al restaurar.

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_publicacion_con_tres_variantes_mapeadas_da_una_fila_y_tres_miembros | ancla `min` → `max` | ROJO (ancla C) |
| test_pide_cada_item_con_include_attributes_all | params `{"include_attributes": "all"}` → `None` | ROJO |
| test_variante_sin_producto_queda_con_product_id_nulo | `mapa.get(...)` → `None` | ROJO |
| test_publicacion_sin_miembros_mapeados_no_entra_a_listing | `sin_ninguno += 1` → `con_alguno += 1` | ROJO |
| test_publicacion_cerrada_no_crea_listing_pero_deja_observacion | `if plan.estado != "active":` → `if False:` | ROJO (crea fila) |
| test_publicacion_sin_variantes_usa_el_sku_de_la_raiz | `variante_ext ""` → `"0"` | ROJO |
| test_segunda_corrida_actualiza_sin_duplicar | `if previo is None:` → `if True:` | ROJO (nuevos=1) |
| test_variacion_desaparecida_queda_inactiva | baja `activo = false` → `true` | ROJO |
| test_visitas_caidas_dejan_null_y_cuentan_sin_tumbar_la_publicacion | `return None` → `return 0` en `_visitas` | ROJO |
| test_main_sin_dsn_sale_2 | `return 2` → `return 0` sin DSN | ROJO |
| test_main_con_cliente_inyectado_corre_la_ingesta | `resultado.ok = ...` → `False` | ROJO (sale 1) |
| test_sku_no_mapeado_primero_no_rompe_el_ancla | filtro `if m.sku in mapa` fuera | ROJO (KeyError→skip) |
| test_item_que_falla_no_tira_a_los_demas_y_el_run_queda_sellado | `observadas += 1` → `+= 2` | ROJO |
| test_cinco_fallos_seguidos_abortan_honesto_sin_escribir | guarda del breaker → `if False:` | ROJO (no levanta) |
| test_main_con_todo_fallado_sella_mal_y_sale_1 | `if not resultado.ok:` → `if False:` | ROJO (sale 0) |
| test_scan_truncado_cuenta_y_no_reconcilia | `if not truncado:` → `if True:` | ROJO (da de baja) |
| test_ausente_del_scan_da_de_baja_sin_borrar_la_fila | `if externo in vistos:` → `if True:` | ROJO (sigue activo) |
| test_cierre_da_de_baja_miembros_y_deja_observacion | `if previo is not None:` → `is None:` (rama closed) | ROJO |
| test_error_de_escritura_no_tira_lo_demas | `except psycopg.Error:` → `except KeyError:` | ROJO (aborta todo) |
| test_vendedor_sin_id_entero_aborta | `raise ReputacionError(...)` → `pass` | ROJO (no levanta) |

Nota: el primer mutante de `test_publicacion_sin_miembros_mapeados`
(quitar `mapeados == 0` de la guarda) sobrevivio porque el `min()` sobre
vacio levanta y el item cae en skip con el mismo resultado visible.
Se agrego `test_sku_no_mapeado_primero_no_rompe_el_ancla` para fijar el
filtro, con su mutante propio (m12).
