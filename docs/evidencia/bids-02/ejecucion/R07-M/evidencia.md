# R07-M: pruebas que matan los mutantes sobrevivientes

Se corrieron los tres scripts del revisor (05_mutantes de vistas,
08_mutantes de apply y mutantes_p2b) sobre una copia del SHA, nunca
sobre el worktree. Cada id de la fila tiene su prueba nueva:

- V04, V12 y V13 mueren con
  tests/test_migracion_0067.py::test_r07m_rama3_fuera_regreso_y_fecha_de_confirmado
  (fuera_de_amazon fuera de la rama 3, el regreso de ubicacion dentro
  y la fecha de la fila es confirmado_el, no creado_el).
- M07 muere con tests/test_aplica_ajuste.py::test_r07m_huella_existente_de_otra_clase_no_pasa.
- J5 muere con tests/test_ui.py::test_r07m_dinero_js_error_de_api_se_pinta_como_texto
  (el error de la API se pinta como texto, jamas innerHTML).
- H2 a H7 mueren con tests/test_ui.py::test_r07m_plantilla_dinero_formularios_ocultos_y_escape
  (formulario hidden, token password y nunca texto, sin method=get, y
  nombre, frase y avisos escapados).
- P1 muere con tests/test_pantalla_dinero.py::test_r07m_regreso_pendiente_no_quita_el_boton_ni_cuenta_sin_confirmar.
- P5 muere con tests/test_avisos_campana.py::test_r07m_aviso_propio_en_el_dia_8_da_none.

Dos ids no mueren y quedan nombrados:

- P2 es un mutante equivalente. La consulta del aviso agrega con
  max(confirmado_el), y max() ignora los NULL de los ajustes sin
  confirmar; cuando el grupo solo trae sin confirmar, el maximo es
  NULL y el lector lo salta. Quitar el filtro IS NOT NULL no cambia
  ninguna salida, y la corrida del script sobre el SHA lo confirma
  (58 passed con la prueba de P1 y P2 sembrando un ajuste sin
  confirmar).
- M09 ya no aplica: el texto que muta (guardar el destino tras
  confirmar) ya no existe en apply.py, porque hoy se guarda la mezcla
  leida; el script lo reporta NO-APLICA.
