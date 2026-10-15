# Mutantes de F.4

Un mutante por prueba nueva (H3 lleva dos: ventas y `sin_atribuir`),
cada uno corrido solo contra su prueba con
`ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres"`
y `PYTHONPATH=.`, purgando `__pycache__` antes de cada corrida.
Script: `/tmp/mutantes_f4.py` (no se commitea). 11/11 en rojo.
El candado H11 se autocontrola con imports sembrados (patron F.1).

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_historia_fbm_disponibilidad_sale_del_estado_e_ignora_inventario | rama FBM `disponible/no_disponible` invertida | ROJO |
| test_historia_fba_exige_stock_y_buyable | prueba FBA sin exigir stocks | ROJO |
| test_historia_ventas_salen_de_la_vista_y_cuenta_sin_atribuir | `unidades[dia] = qty` (no acumula) | ROJO |
| test_historia_ventas_salen_de_la_vista_y_cuenta_sin_atribuir | `sin_atribuir` con `listing_id IS NOT NULL` | ROJO |
| test_historia_trafico_y_buy_box_del_dia | trafico: gana la primera, no la ultima (+ `ORDER BY` descubierto por este vivo) | ROJO |
| test_historia_ventana_76_dias_primera_y_cubierto | ventana `days=75` → `days=30` | ROJO |
| test_insumos_adjunta_historia_a_la_lectura | `historia = None` | ROJO |
| test_cli_precio_dos_platforms_corren_en_serie | `plataformas[:1]` | ROJO |
| test_sonda_sin_flag_imprime_plan_y_no_escribe | guarda del flag → `if False:` | ROJO |
| test_sonda_con_go_mueve_un_centavo_y_revierte | `+ CENTAVO` → `- CENTAVO` | ROJO |
| test_sonda_listing_inexistente_o_de_otra_plataforma_aborta | `if fila is None:` → `if False:` | ROJO |
