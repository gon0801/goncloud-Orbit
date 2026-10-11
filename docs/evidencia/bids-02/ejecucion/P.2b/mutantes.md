# Mutantes de P.2b (botones, regresar, avisos, dinero.js)

Un mutante por prueba nueva o comportamiento nuevo de P.2b: cada cambio
es de una linea y pone su prueba en rojo. Verificados el 2026-10-11
corriendo cada prueba contra su mutante (todos dieron `1 failed`) y
restaurando despues.

## Plantilla (verificados 2026-10-11, todos MUEREN)

- Boton sin `data-ajuste-clase` (`data-clase` en vez) ->
  test_pantalla_dinero_boton_por_clase_sellada_en_cada_campana en rojo.
- Botones hardcodeados (las 3 clases en vez de `clases_ajuste`) ->
  test_pantalla_dinero_clase_sin_sellar_sin_boton_y_dato_pintado en rojo
  (pinta 3 con 2 selladas).
- Regresar sin `data-regresar-ajuste` (`data-regresar` en vez) ->
  test_pantalla_dinero_regresar_y_avisos_en_fila_campana en rojo.
- Fila sin el loop de `avisos` ->
  test_pantalla_dinero_regresar_y_avisos_en_fila_campana en rojo.
- Fila sin el `if` de `aviso_ajuste` ->
  test_pantalla_dinero_regresar_y_avisos_en_fila_campana en rojo.

## Dict y frase (verificados 2026-10-11, todos MUEREN)

- Dict sin filtrar selladas (`list(ORDEN_CLASES)`) ->
  test_clases_ajuste_del_dict_filtra_sin_sellar en rojo.
- Frase sin formato (`texto` pelado) ->
  test_pg_regresables_y_aviso_propio_en_fila_campana en rojo.
- `ORDEN_CLASES` sin `fuera_de_amazon` ->
  test_donde_poner_el_dinero_api_trae_clases_y_regresables en rojo.

## dinero.js con Node (verificados 2026-10-11, todos MUEREN)

- Aplicar sin el check del literal (`if (false)`) ->
  test_ui_dinero_js_plan_sin_post_y_aplicar_exige_literal en rojo (manda
  el POST sin literal).
- Plan por POST (`{method: "POST"}` agregado) ->
  test_ui_dinero_js_plan_sin_post_y_aplicar_exige_literal en rojo.
- Regreso a otra ruta (`/revertir` en vez de `/regresar`) ->
  test_ui_dinero_js_plan_sin_post_y_aplicar_exige_literal en rojo.
