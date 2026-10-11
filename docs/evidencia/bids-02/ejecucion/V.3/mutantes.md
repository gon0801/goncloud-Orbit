# Mutantes de V.3 (migracion 0067)

Un mutante por prueba nueva de `tests/test_migracion_0067.py`: cada cambio es
de una linea y pone su prueba en rojo. Verificados el 2026-10-11 corriendo
cada prueba contra su mutante (los cuatro dieron `1 failed`) y restaurando
despues (la suite quedo en `4 passed`).

| Prueba | Mutante (1 linea) | Efecto |
| --- | --- | --- |
| `test_0067_update_solo_confirmado_el` | En `migrations/0067_bids02_campana_ajuste.sql`: `OR OLD.confirmado_el IS NOT NULL THEN` por `OR OLD.confirmado_el IS NULL THEN` | El sello legitimo (NULL -> valor) revienta: `1 failed`. Dos intentos previos no abrieron nada (las lineas 36 y 40 del trigger se cubren entre si) y se descartaron. |
| `test_0067_delete_y_truncate_rechazados` | En `migrations/0067_bids02_campana_ajuste.sql`: el trigger DELETE con `prohibir_mutacion()` por `campana_ajuste_0067_solo_confirmado()` | El DELETE revienta con otro mensaje (no `APPEND-ONLY`): `1 failed`. |
| `test_0067_rama_ajuste_ubicacion_y_presupuesto_fuera` | En `migrations/0067_bids02_campana_ajuste.sql`: `a.clase = 'ajuste_ubicacion'` por `a.clase = 'presupuesto'` | Vivo al inicio: ambas clases producen la misma tupla y la prueba no distinguia que ajuste la produjo. Se endurecio la prueba (presupuesto solo -> 0 filas; luego ubicacion -> 1 fila) y el mutante muere: `1 failed`. |
| `test_0067_reversa_deja_vista_de_dos_ramas_y_esquema_igual` | En `migrations/0067_reversa_bids02_campana_ajuste.sql`: `DROP FUNCTION campana_ajuste_0067_solo_confirmado();` comentado | La funcion sobrevive a la reversa: la foto del catalogo difiere, `1 failed`. |
| `test_0067_reversa_deja_vista_de_dos_ramas_y_esquema_igual` (grants; hallazgo del ensayo D.3) | En `migrations/0067_reversa_bids02_campana_ajuste.sql`: `REVOKE INSERT ON ads_campana_config_observation FROM app_admin;` borrado | El INSERT residual sobrevive: `has_table_privilege` da t, `1 failed`. El ensayo D.3 encontro el hueco (diff de ACLs); la reversa y el test se endurecieron en el commit de arreglo V.3. |

## Obligatorios del paso (verificados 2026-10-11, todos MUEREN)

- M1 (adaptado: el diseno no trae umbral de confianza — el dueno pide el
  ajuste y `planea_ajuste` es determinista —; frontera analoga): `<= 0`
  por `< 0` en `app/campana_ajustes.py` ->
  test_presupuesto_cero_o_negativo_levanta en rojo (el 0 pasa).
- M2: `_errores_put` ignora `code` (`if codigo[:1] in ("4", "5"):` por
  `if False:`) -> test_ajustar_campana_207_con_errores_anidados_lanza en
  rojo (el 207 con code 400 ya no lanza).
- M3: confirma sin releer (`if leida != destino:` por `if False:` en
  `_aplica_y_confirma`) -> test_readback_divergente_deja_sin_confirmar en
  rojo (la fila divergente queda confirmada).
- M4: regresables sin el filtro (`AND a.clase <> 'fuera_de_amazon'`
  borrado) -> test_pg_regresables_y_aviso_propio_en_fila_campana en rojo
  (fuera_de_amazon sale con boton).

## Planeador (verificados 2026-10-11, todos MUEREN)

- `_PCT_MAX` 900 por 901 -> test_porcentaje_fuera_de_rango_levanta en rojo.
- `==` por `!=` en el no-cambia de presupuesto ->
  test_ajuste_que_no_cambia_nada_levanta en rojo.
- Huella sin `despues` -> test_huella_cambia_campo_por_campo en rojo.
- Sin el check de clase sellada (`if False:`) ->
  test_clase_sin_sonda_sellada_levanta_antes_de_todo en rojo.
- `despues = vigente` (sin aplicar el presupuesto) ->
  test_presupuesto_cambia_solo_el_presupuesto en rojo.
- `_ya_limitado` devuelve False siempre ->
  test_ajuste_que_no_cambia_nada_levanta en rojo.
- `CLASES_SELLADAS` sin `presupuesto` ->
  test_clases_selladas_son_las_tres_sondas en rojo.
- `paginas_de_producto` mapeado a `ajuste_top_pct` ->
  test_ubicacion_cambia_solo_su_campo en rojo.
- `_FUERA_LIMITADO` con otra estrategia ->
  test_fuera_de_amazon_fija_minimize_spend en rojo.
- Huella con `id(self)` -> test_huella_estable_ante_mismo_plan en rojo.

## PUT de campana (verificados 2026-10-11, todos MUEREN)

- `LLAVES_CUERPO_PUT` con `"state"` de mas ->
  test_ajustar_campana_rechaza_llave_desconocida_sin_http en rojo.
- `cuerpo_put_campana` manda `"state": "ENABLED"` ->
  test_cuerpo_put_lleva_config_completa_sin_state en rojo.
- `cuerpo_put_campana` no omite fuera_de_amazon ausente (`if True:`) ->
  test_cuerpo_put_omite_lo_ausente en rojo (TypeError). El primer intento
  (mutar el `if` del presupuesto) no murio: esa prueba trae presupuesto
  presente; se muto el campo que la prueba trae ausente.

## Apply y regreso (verificados 2026-10-11, todos MUEREN)

- Sin check del literal (`if False:`) ->
  test_literal_distinto_no_toca_nada en rojo.
- Sin idempotencia por huella (`if False:`) ->
  test_segunda_vez_misma_huella_no_repite_put en rojo.
- El regreso aplica `vigente` en vez de `antes` ->
  test_regresa_aplica_antes_y_segundo_regreso_rechazado en rojo (PUTs
  [120.0, 120.0] en vez de [120.0, 100.0]).
- Segundo regreso permitido (`if regresos:` por `if False:`) ->
  test_regresa_aplica_antes_y_segundo_regreso_rechazado en rojo (revienta
  UNIQUE, no AjusteYaRegresado).
- Regreso de fuera_de_amazon permitido (`if False:`) -> vivo al inicio:
  `_ajuste_hacia` levanta AjusteSinRegreso igual (segunda capa). Se
  endurecio el test con `match="no tiene regreso sellado"` y el mutante
  muere: test_regresa_fuera_de_amazon_rechazado_sin_http en rojo. La
  segunda capa (`_ajuste_hacia`, linea 2263) no tiene mutante aislado de
  1 linea: el check de clase la precede y sin quitarlo no se ejerce.
- Confirmado sin `guarda_config` -> test_aplica_presupuesto_confirma_y_guarda_vigente en rojo.
- Sin check de huella (`if False:`) ->
  test_huella_vieja_devuelve_plan_nuevo_sin_http en rojo.

## Rutas (verificados 2026-10-11, todos MUEREN)

- Huella vieja mapeada a 500 -> test_aplicar_huella_vieja_da_409_con_plan_nuevo en rojo.
- AjusteInexistente mapeado a 500 -> test_regresar_inexistente_da_404 en rojo.
- ConfirmacionInvalida mapeada a 500 por HTTP: NO APLICA — pydantic
  (`Literal["APLICAR AJUSTE"]`) veta el literal antes y el 422 sale del
  schema (verificado: el test HTTP pasa aun con el mapeo en 500). El
  mapeo se fija con test_mapeo_errores_ajuste_trae_sus_codigos (puro) y
  ahi el mutante 422->500 muere.
- AjusteYaRegresado mapeado a 500 -> test_regresar_confirma_y_segundo_da_409 en rojo.
- Plan con `Depends(exige_token)` agregado ->
  test_plan_sin_token_devuelve_frase_y_huella en rojo (401).
- Aplicar sin su `Depends(exige_token)` ->
  test_aplicar_sin_token_da_401 en rojo.
- Aplicar responde `{}` -> test_aplicar_con_token_confirma en rojo.
- Ruta regresar comentada -> test_superficie_ads_optimizer_sellada_get_mas_escrituras_autenticadas en rojo.

## Pantalla, avisos, arquitectura, trayectoria (verificados 2026-10-11, todos MUEREN)

- Aviso sin filtro de clase (`if False:`) ->
  test_aviso_propio_fuera_de_ventana_u_otra_clase_da_none en rojo.
- Aviso sin tope de ventana (`dias > 7` quitado) ->
  test_aviso_propio_fuera_de_ventana_u_otra_clase_da_none en rojo.
- `_aviso_ajuste_de` devuelve None siempre ->
  test_pg_regresables_y_aviso_propio_en_fila_campana en rojo.
- `import httpx` en `app/campana_ajustes.py` (fuga temporal, revertida) ->
  test_campana_ajustes_es_puro en rojo.
- Rama de la vista con `clase = 'presupuesto'` (mismo mutante que 0067-3) ->
  test_ajuste_ubicacion_confirmado_entra_a_la_trayectoria en rojo.

## Tardios del paso (verificados 2026-10-11, todos MUEREN)

- Rechazo que no lanza (`if errores:` por `if False:` en
  `ajustar_campana`) -> test_put_rechazado_deja_fila_pendiente_y_manda_una_campana
  en rojo (espera AdsApiErrorMutacion). La unicidad del PUT (una sola
  campana) vive en `_mutate`, codigo previo y probado: el test la fija
  para el PUT de ajuste sin mutante aislado en codigo V.3. La
  pre-existencia de la fila (pre-HTTP) vive en el orden de la secuencia
  de `aplica_ajuste_campana` (reordenarla son N lineas): el test la fija
  sin mutante de 1 linea.
- Regresar sin su `Depends(exige_token)` ->
  test_regresar_sin_token_da_401 en rojo.
- Fuga sembrada: un INSERT en la ruta del plan ->
  test_plan_sin_token_devuelve_frase_y_huella en rojo (el conteo deja de
  ser 0). Fuga revertida.
- El candado nuevo trae su detector con fuga sembrada (regla 14):
  test_detector_ajustes_caza_import_sembrado.

## Panel interrogate (verificados 2026-10-11, todos MUEREN)

- Sin quantize (`monto` sin `quantize`) ->
  test_presupuesto_se_cuantiza_a_centavos en rojo.
- Sin `is_finite` -> test_presupuesto_nan_o_infinito_levanta en rojo.
- Ubicacion sin `dynamicBidding` pasa (`if False:`) ->
  test_ubicacion_sin_dynamicBidding_previo_se_rechaza en rojo.
- Sin moneda estrenada -> test_presupuesto_sin_previo_estrena_moneda_del_perfil en rojo.
- `coincide_clase` compara el registro completo ->
  test_coincide_clase_mira_solo_lo_que_la_clase_mueve en rojo.
- Early-return ciego (`if True:`) ->
  test_reaplica_tras_regreso_hace_put_nuevo en rojo (no sale el PUT 3).
- Sin reconciliacion (`elif False:`) ->
  test_pendiente_con_destino_observado_se_reconcilia_sin_put en rojo
  (sale un PUT que el test prohibe).
- Sin reuso de regreso pendiente (`if False:`) ->
  test_regreso_fallido_se_puede_reintentar en rojo (UNIQUE, no reintento).
- Count de regresos con pendientes ->
  test_regreso_fallido_se_puede_reintentar en rojo (409 prematuro).
- Sin cruce por id en `_config_leida` ->
  test_config_leida_elige_por_id_entre_varias en rojo (elige la ajena).
  El test de integracion no discrimina esta capa sola: `coincide_clase`
  tambien cruza por externa (doble capa intencional).
- `_clave_orden_cambio` sin NULL ->
  test_clave_orden_cambio_empate_con_null_no_revienta en rojo (TypeError).
- Sin `ON CONFLICT (huella)` ->
  test_doble_aplica_simultaneo_no_explota_ni_duplica_acto en rojo 3/3
  (UniqueViolation escapada).
- Sin sufijo `H#N` -> test_reaplica_tras_regreso_hace_put_nuevo en rojo
  (UNIQUE en vez de acto nuevo).
- Sin check de huella ajena (`if False:`) ->
  test_huella_ajena_no_confirma_otra_campana en rojo (sale a la red).
- Sin code de tope en `_errores_put` (`if False:`) ->
  test_ajustar_campana_falla_en_cerrado_ante_basura en rojo.
- Item no-dict saltado (sin `append`) ->
  test_ajustar_campana_falla_en_cerrado_ante_basura en rojo.
- Aplicar sin `except ValueError` -> test_aplicar_actor_blanco_da_422 en
  rojo (500 en vez de 422).
- `AdsApiErrorMutacion` a 500 -> test_aplicar_put_rechazado_da_502 en rojo.
- Sin `max_length` en actor -> test_aplicar_actor_largo_da_422 en rojo
  (el 422 sale de apply, no de pydantic).
- Regresables con `antes` vacio ->
  test_pg_regresables_y_aviso_propio_en_fila_campana en rojo (sale h-vacio).
- `config_vigente_con_id` con orden invertido ->
  test_config_vigente_con_id_trae_id_y_config_en_un_select en rojo.
- Ruido sin `AND c.origen <> 'ajuste_de_campana'` ->
  test_encogimiento_ignora_ajuste_de_campana_antiguo en rojo (la hoja
  desaparece del tablero: el ajuste corrompia la base).
