# R13-F: pruebas que matan los mutantes de borde de busquedas

Script del revisor: m03_mutantes_r11.sh con m03_tabla.py, corrido
sobre una copia del HEAD (git archive), nunca sobre el worktree.

## Antes (HEAD eb832ed)

La base pasa (A00 SOBREVIVE sin mutar, 296 passed) y los 22 ids de
la fila SOBREVIVE: L6, G3b, G3c, L1, W10, W11, P3, W5, W6, W13, U2,
U3, U4, U7, P4, P5, P6, P7, R4, R5, L3, G1c.

## Despues (HEAD d1d1342)

20 de 22 MUERE, cada uno por su prueba nueva:

- L6 MUERE: test_r13f_moneda_de_us_es_usd
- G3b MUERE: test_r13f_un_dia_con_clics_nulos_anula_los_clics
- G3c MUERE: test_r13f_un_dia_con_pedidos_nulos_anula_los_pedidos
- L1 MUERE: test_r13f_settings_vigentes_son_los_ultimos
- W10 MUERE: test_r13f_pagina_pinta_los_conteos_de_medio_camino
- W11 MUERE: test_r13f_pagina_pinta_el_gasto_de_la_palabra
- P3 MUERE: test_r13f_ejemplos_son_tres_y_no_cuatro
- W5 MUERE: test_r13f_pagina_escapa_las_campanas
- W6 MUERE: test_r13f_pagina_escapa_la_palabra
- W13 MUERE: test_r13f_pagina_escapa_el_asin
- U2 MUERE: test_r13f_palabra_en_el_minimo_exacto_entra
- U3 MUERE: test_r13f_asin_bajo_la_mitad_no_es_medio_camino
- U4 MUERE: test_r13f_palabra_bajo_la_mitad_no_es_medio_camino
- U7 MUERE: test_r13f_palabra_bajo_la_mitad_no_es_medio_camino
- P5 MUERE: test_r13f_asins_ordenados_por_gasto_mayor
- P6 MUERE: test_r13f_palabras_ordenadas_por_gasto_mayor
- P7 MUERE: test_r13f_ejemplos_ordenados_por_gasto_mayor
- R4 MUERE: test_r13f_referencia_redondea_mitad_hacia_arriba
- R5 MUERE: test_r13f_referencia_incluye_los_asin
- L3 MUERE: test_r13f_asin_propio_con_listing_en_minusculas

## G1c: muere por guarda de texto, con motivo

El empate de observed_at dentro de un grupo DISTINCT ON no se puede
sembrar: la PK de la 0001 es (platform, ad_entity_id, search_term,
metric_date, observed_at), asi que dos observaciones del grupo nunca
comparten observed_at y el desempate nunca llega a ejercerse con
datos. La prueba guarda la clausula en el texto de la consulta
(test_r13f_el_desempate_por_reporte_queda_en_el_sql) y el mutante
muere por ella.

## P4: equivalente, con motivo

El mutante suma una clave "n" en el acumulador de cada entrada, pero
nadie la lee: busquedas sigue saliendo de len(v["por_texto"]) y los
ejemplos de v["por_texto"].items(). Inspeccion del codigo mutado:
la clave "n" aparece una sola vez, en la linea que la escribe. La
semantica que el mutante queria romper (contar textos distintos y
no filas) ya la cubre test_misma_busqueda_en_dos_ad_groups.
