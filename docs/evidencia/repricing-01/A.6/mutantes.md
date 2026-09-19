# A.6 — catalogo de mutantes, bloque AVISOS (`notifica_precio`)

Siembra a mano en `app/notifica.py` (solo codigo nuevo del bloque), un
mutante por regla y borde, corrido con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`,
Telegram falso) y `PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco por mutante,
revertido sin commit (restore verificado por `cmp` contra el backup
pre-siembra). Cero sobrevivientes. Otros bloques de A.6 anexan sus filas
debajo.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| P1 | Tope SKUs 5 -> 6 (`[:TOPE_SKUS_PRECIO]` + 1) | `test_precio_aviso_tope_5_skus` (SKU-006 aparece) | MUERTO |
| P2 | Flanco umbral `racha == umbral` -> `>=` (sigue reavisaria) | `test_precio_aviso_flanco_sigue_no_reavisa` | MUERTO |
| P3 | Flanco umbral: guarda `hoy not in` invertida (vuelve no avisaria) | `test_precio_aviso_flanco_corta_y_vuelve` | MUERTO |
| P4 | Cota dias 14 -> 15 (umbral 15 admitido) | `test_precio_aviso_umbral_dias` (15 debe dar `ValueError`) | MUERTO |
| P5 | `precio_no_cubre_costo` en palabras con "costo" | `test_precio_aviso_builders_sin_prohibidos` | MUERTO |
| P6 | Linea `y N mas` eliminada (resto sin contar) | `test_precio_aviso_tope_5_skus` (exige `y 2 mas`) | MUERTO |
| P7 | Canal apagado devuelve `False` en vez de `True` | `test_precio_aviso_apagado_devuelve_true` | MUERTO |
| P8 | Tipo desconocido devuelve `True` en vez de `False` | `test_precio_aviso_sender_jamas_levanta` | MUERTO |
| P9 | `no_evaluado` sin flanco de umbral (avisa con racha 2 < 3) | `test_precio_aviso_umbral_flanco_en_base` (exige 0 envios) | MUERTO |
| P10 | Huerfana: `> 0` -> `> 5` (2 huerfanas no avisarian) | `test_precio_aviso_huerfana_flanco_en_base` | MUERTO |
| P11 | Fraseo `goal_inalcanzable` con "goal" literal | `test_precio_aviso_builders_sin_prohibidos` | MUERTO |
| P12 | `flanco_nuevos` sin excluir ayer (todo es "nuevo") | `test_precio_aviso_flanco_corta_y_vuelve` | MUERTO |

---

# A.6 — bloque PANTALLA (`GET /api/dashboard/precios`, `app/api_dashboard.py`)

Siembra a mano en el codigo nuevo del bloque (solo `app/api_dashboard.py`),
un mutante por regla y borde, corrido con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`) y
`PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco por mutante, revertido sin commit
(restore verificado por `cmp` contra el backup pre-siembra). Cero
sobrevivientes. Cada mutante se corrio contra su test killer (el archivo
completo queda en verde: 20 passed).

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| Q1 | `@router.get("/precios")` -> `@router.post` (la ruta deja de ser GET) | `test_precio_pantalla_ruta_get_contrato` (405, sin `hoy`/`plataformas`) | MUERTO |
| Q2 | `fuentes.leer_publicaciones(...)` -> `[]` (recuadro sin lectura en vivo) | `test_precio_pantalla_recuadro_en_vivo_sin_consultas_duplicadas` (el espia no registra `amazon_mx`) | MUERTO |
| Q3 | `decision_date = %s` -> `decision_date <> %s` (decisiones de otros dias) | `test_precio_pantalla_decisiones_exactas_vs_select` (`{}` vs SELECT del dia) | MUERTO |
| Q4 | `"evaluadas": rec.evaluadas` -> `rec.activas` (ecuacion inflada) | `test_precio_pantalla_ecuacion_cuadra_en_vivo` (evaluadas 2, espera 1) | MUERTO |
| Q5 | Huerfanas `estado = 'pendiente'` -> `'confirmado'` (cuenta otro estado) | `test_precio_pantalla_huerfanas_exactas_vs_select` (0 vs 1 del SELECT) | MUERTO |
| Q6 | Linea `"precios": _precios_de(...)` eliminada de /salud | `test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves` (set sin `precios`) | MUERTO |
| Q7 | `"huerfanas": _huerfanas_precio(...)` -> `0` (huerfanas fijas) | `test_precio_pantalla_salud_precios_exactos_vs_select` (0 vs 1 del SELECT) | MUERTO |
| Q8 | `_ERROR_SIN_0039` sin `0039` (mensaje que no nombra la migracion) | `test_precio_pantalla_sin_0039_none_warning_y_error_claro` (warning y detail sin `0039`) | MUERTO |

---

# A.6 — bloque UI (`GET /precios`, `app/ui.py` + plantillas)

Siembra a mano en el codigo nuevo del bloque (ruta en `app/ui.py`,
`app/templates/precios.html` nueva, enlace en `base.html`, bloque en
`salud.html`), un mutante por regla y borde, corrido con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`) y
`PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco por mutante, revertido sin commit
(restore verificado por `cmp` contra el backup pre-siembra). Cero
sobrevivientes. R6 sobrevivio una vez (el motivo en palabras tambien sale
en el bloque 4) y se mato precisando el test a la seccion divergente
(`bloque-divergente-amazon_mx`).

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R1 | `@router.get("/precios")` -> `@router.post` (la ruta deja de ser GET) | `test_precio_ui_ruta_get_cinco_bloques_en_orden` (405, sin 200) | MUERTO |
| R2 | `"aviso_puente"` forzado a `None` (puente sin aviso del 5%) | `test_precio_ui_cobertura_cuadra_puente_y_aviso_5` (sin `5%`) | MUERTO |
| R3 | Seccion `bloque-con-goal` eliminada de la plantilla | `test_precio_ui_ruta_get_cinco_bloques_en_orden` (falta un bloque) | MUERTO |
| R4 | Seccion `bloque-hecho` eliminada de la plantilla | `test_precio_ui_hecho_habria_hecho_sombra` (sin `habr`) | MUERTO |
| R5 | `item.motivo \| motivo_precio_es` -> `item.motivo` (id crudo) | `test_precio_ui_no_evaluados_en_palabras` (`precio_sin_observar` visible) | MUERTO |
| R6 | Filtro divergente `["precio_divergente", ...]` -> `[]` | `test_precio_ui_divergente_con_y_sin` (seccion sin `distinto del publicado`) | MUERTO |
| R7 | `rec.activas == 0` -> `rec.activas < 0` (sin-datos inalcanzable) | `test_precio_ui_sin_datos_estado_visible` (sin `sin publicaciones activas`) | MUERTO |
| R8 | `status_code=exc.status_code` -> `status_code=200` (error como exito) | `test_precio_ui_sin_0039_error_claro_nunca_500` (200 en vez de 503) | MUERTO |
| R9 | Bloque precios eliminado de `salud.html` | `test_precio_ui_salud_html_bloque_precios` (sin `bloque-precios`) | MUERTO |
| R10 | `href="/precios"` -> `href="/precio"` (enlace roto) | `test_precio_ui_nav_enlace_y_skus` (sin `href="/precios"`) | MUERTO |

---

# A.6 — bloque CIERRE (`precio` CLI pasa `avisar`, `app/cli.py`)

Siembra a mano en el codigo nuevo del bloque (solo las 2 lineas de
`_precio`: import tardio + `avisar=avisar_precio`), un mutante por regla
y borde, corrido con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`) y
`PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco por mutante, revertido sin commit
(restore verificado por `cmp` contra el backup pre-siembra). Cero
sobrevivientes. S3 sobrevivio una vez (el test del import tardio solo
exigia la palabra `import`, que tambien sale en un comentario del bloque)
y se mato precisando el test a la sentencia exacta.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| S1 | Linea `avisar=avisar_precio,` eliminada (el CLI corre sin gancho) | `test_precio_cierre_cli_pasa_avisar_a_correr` (kw sin `avisar`) | MUERTO |
| S2 | `avisar=avisar_precio` -> `avisar=None` (gancho nulo explicito) | `test_precio_cierre_cli_pasa_avisar_a_correr` (`None is not avisar_precio`) | MUERTO |
| S3 | Import movido a cabecera del modulo (rompe el import tardio) | `test_precio_cierre_import_tardio_en_precio` (sentencia fuera de `_precio`) | MUERTO |
| S4 | `avisar=avisar_precio` -> `avisar=lambda *a: None` (gancho mudo) | `test_precio_cierre_cli_pasa_avisar_a_correr` (lambda no es el gancho real) | MUERTO |

---

# A.6 — bloque R1 (A1, A2, B2, B4, B6, B7, T1, T2 + C)

Siembra a mano en el codigo nuevo/cambiado de r1 (solo `app/notifica.py` +
el import de `app/cli.py`), un mutante por regla, corrido con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`,
Telegram falso) y `PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco **por mutante**,
revertido sin commit (restore verificado por `cmp` contra el backup
pre-siembra; guion en `/tmp/r1_mutantes.py`). Cero sobrevivientes. R1T2 se
corrio de nuevo a mano con los dos killers (el primer pase llevaba un
node-id mal escrito en el segundo killer y su veredicto no valia).

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R1T1 | `notifica_precio`: warning del `except` (`fallo armando...`) -> `pass` | `test_precio_aviso_excepcion_warning_scrub_y_false` (exige el record WARNING antes del scrub) | MUERTO |
| R1T2 | `flanco_umbral`: `return racha == umbral` -> `return len(presentes) == umbral` | `test_precio_aviso_flanco_corta_y_vuelve` (huecos puros) + `test_r1_t2_hueco_en_base_no_avisa` (`{hoy, hoy-2, hoy-3}` en base) | MUERTO |
| R1A1 | `_avisar_noconf_precio`: el flanco de hoy lee ayer (vuelve el callado) | `test_r1_a1_no_confirmado_avisa_en_corrida_real` (0 avisos `no_confirmado`) | MUERTO |
| R1A2 | `_avisar_huerfana_precio`: `huerfanas > 0` -> `> 1` (una huerfana no avisa) | `test_r1_a2_huerfana_es_evento_en_corrida_real` (0 avisos huerfana) | MUERTO |
| R1B2 | `avisar_precio`: guarda `resumen.decisiones > 0` -> `True` (reenvia) | `test_r1_b2_reejecucion_mismo_dia_no_reenvia` (la 2a manda 1+) | MUERTO |
| R1B4 | umbral invalido: `umbral = None` (sigue) -> `return 0` (calla todo) | `test_r1_b4_umbral_invalido_solo_apaga_no_evaluado` (0 enviados) | MUERTO |
| R1B6 | `_avisar_grupos_precio` sin su `try` (el fallo se propaga y calla todo) | `test_r1_b6_un_tipo_roto_no_calla_los_otros` (0 enviados) | MUERTO |
| R1C1 | `notifica_precio`: `payload.tipo != tipo` -> `... and False` (no checa) | `test_r1_c_payload_con_otro_tipo_no_envia` (sale `True` apagado) | MUERTO |
| R1C2 | `app/cli.py`: import de avisos a la cabecera de `_precio` (antes de `--reporte`) | `test_precio_cierre_import_tardio_en_precio` (import antes que `args.reporte`) | MUERTO |

---

# A.6 — bloque R1 (A3, A4, A5, B1, B9, T3 + C y B12 de pantalla)

Siembra a mano en el codigo nuevo/cambiado de r1-b (`app/api_dashboard.py`,
`app/ui.py`, `app/templates/base.html`), un mutante por regla, corrido con
base real (`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`)
y `PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco **por mutante**, revertido sin
commit (restore verificado por `cmp` contra el codigo en el arbol; guion en
`/tmp/r1b_mutantes.py`). Cero sobrevivientes. El test de la seccion
divergente se adapto a la semantica A5 (la racha de 3 dias): su seed de 1
dia ya no entra al bloque (e) por diseno; el mutante equivalente al viejo
R6 es R1B-A5b.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R1B-A3a | `movidos`: `mode == "live"` -> `"shadow"` (cuenta sombra con cambio) | `test_r1b_a3_salud_precios_clave_por_clave_vs_select` (0 vs 3 del SELECT) | MUERTO |
| R1B-A3b | `sombra`: `mode == "shadow"` -> `"live"` (cuenta live que sube/baja) | `test_r1b_a3_salud_precios_clave_por_clave_vs_select` (3 vs 2 del SELECT) | MUERTO |
| R1B-A3c | `cuota.cap`: `validar_cap(...)` -> `... - 1` (cap corrido en uno) | `test_r1b_a3_salud_precios_clave_por_clave_vs_select` (6 vs 7) | MUERTO |
| R1B-A3d | linea `"recuadro" = cobertura` eliminada (alias roto) | `test_r1b_a3_salud_precios_clave_por_clave_vs_select` (`KeyError: recuadro`) | MUERTO |
| R1B-A4a | frase `subir`: siempre `"subió"` (la sombra pierde el condicional) | `test_r1b_a4_con_goal_y_acciones_frases_exactas` (frase sin «habría») | MUERTO |
| R1B-A4b | frase `bajar`: siempre `"habría bajado"` (la live gana condicional) | `test_r1b_a4_con_goal_y_acciones_frases_exactas` (frase con «habría») | MUERTO |
| R1B-A4c | frase subir/bajar: `de {antes} a {despues}` invertido | `test_r1b_a4_con_goal_y_acciones_frases_exactas` (frases exactas) | MUERTO |
| R1B-A5a | ventana divergente `range(umbral)` -> `range(umbral - 1)` (2 dias bastan) | `test_r1b_a5_divergente_racha_de_tres` (`[A, B]` vs `[A]`) | MUERTO |
| R1B-A5b | racha divergente admite `moneda_divergente` (va al (d), no aqui) | `test_r1b_a5_divergente_racha_de_tres` (`[A, M]` vs `[A]`) | MUERTO |
| R1B-B1a | filas (d): `== "no_evaluado"` -> `!=` (filas de los evaluados) | `test_r1b_b1_no_evaluados_filas_del_dia_en_palabras` (set de SKUs) | MUERTO |
| R1B-B9a | `_ERROR_SIN_0039` -> `"precios no disponibles"` (mensaje generico) | `test_r1b_b9_errores_pantalla_tres_casos` (detail exacto) | MUERTO |
| R1B-B9b | rama `ValueError` de /precios sin `warning` (no se registra) | `test_r1b_b9_errores_pantalla_tres_casos` (clave en el log) | MUERTO |
| R1B-T3a | `pagina_precios`: `aviso_puente(...)` -> frase fija del 5 % siempre | `test_r1b_t3_puente_bajo_5_sin_aviso_y_frase_por_fila` (`aviso: puente` con diff 0) | MUERTO |
| R1B-Ca | barra `>Precios</a>` -> `>Repricing</a>` (titulo y barra difieren) | `test_r1b_c_barra_dice_precios_como_el_titulo` (sin `>Precios</a>`) | MUERTO |

---

# A.6 — bloque R1 (B3, B5, B10, B11 + C)

Siembra a mano en el codigo nuevo/cambiado de r1-c (solo `app/notifica.py`),
un mutante por regla, corrido con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`,
Telegram falso) y `PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco **por mutante**,
revertido sin commit (restore verificado por `cmp` contra el backup
pre-siembra). Cero sobrevivientes. B10/B11 son solo tests (sin codigo de
produccion: sin mutante). C: Q1/R1 ya dicen la causa real (405, no 404);
las menciones a identificadores sin definir se quitaron.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R1B3a | `TOPE_PRODUCTO_PRECIO` 5 -> 6 (7 buy box mandan 7) | `test_r1_b3_buybox_7_avisos_5_mas_agrupado` (`7 == 6`) | MUERTO |
| R1B3b | resto agrupado vaciado (el resto se pierde: 5 enviados) | `test_r1_b3_buybox_7_avisos_5_mas_agrupado` (`5 == 6`) | MUERTO |
| R1B5a | `ESTADO_PRECIO_ES["goal_inalcanzable"]` en crudo | `test_r1_b5_estado_en_palabras_y_asin_solo_amazon` + `test_precio_aviso_builders_sin_prohibidos` | MUERTO |
| R1B5b | puerta Amazon -> `True` (la etiqueta `asin` sale en `meli`) | `test_r1_b5_estado_en_palabras_y_asin_solo_amazon` (`asin:` en meli) | MUERTO |

---

# A.6 — bloque R2 (L1, L2, L14, L15, R2, R3, R4 + A1, L8, B1, B2, B3, B4, R1)

Siembra a mano sobre el HEAD nuevo con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`,
Telegram falso) y `PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco **por
mutante**, revertido sin commit (restore verificado por `cmp` contra el
backup pre-siembra; primera ola corrida con `/tmp/r2_mutantes.py`,
segunda con `/tmp/r2b_mutantes.py`). L1/L2/L14/L15/L8 son los mutantes
del lead sobre `230e114` (el codigo ya era correcto: el test nuevo es
lo que los mata); R2R2/R2R3/R2R4 y R2A1/R2B1/R2B2/R2B3/R2B4a/R2B4b
cubren el codigo nuevo de r2. R1 es solo-test (sin mutante, como
B10/B11). Cero sobrevivientes.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R2L1 | `_SQL_PRECIO_FRENADO_NOCONF_DIA` sin `AND d.motivo = 'no_confirmado'` | `test_r2_l1_frenado_api_error_no_da_aviso_no_confirmado` (`2 == 1`: sale el `no_confirmado` del `frenado(api_error)`) | MUERTO |
| R2L2 | `_avisar_noconf_precio`: `flanco_nuevos(...)` -> `sorted(nc_hoy)` | `test_r2_l2_frenado_noconf_ayer_y_hoy_cero_avisos` (`1 == 0`) | MUERTO |
| R2L14 | `_avisar_grupos_precio`: `elif (resultado, motivo) not in nuevos:` -> `elif False:` | `test_r2_l14_grupo_presente_ayer_y_hoy_no_reavisa` (`2 == 0`) | MUERTO |
| R2L15 | `_SQL_PRECIO_ULTIMO_NOCONF`: `ORDER BY c.id DESC` -> `ASC` | `test_r2_l15_aviso_lleva_precios_del_no_confirmado_nuevo` (aviso con `100.0000 -> 110.0000` en vez de `200/210`) | MUERTO |
| R2R2 | `_bloque_precios`: `validar_precio_aviso_dias(settings)` -> `3` fijo | `test_r2_r2_umbral_de_config_ausente_nombra_la_clave` (`200 == 503`) | MUERTO |
| R2R3 | `_avisar_buybox_precio` sin `try` por pieza (armado directo) | `test_r2_r3_buybox_una_pieza_rota_no_calla_las_otras` (`0 == 1`: la pieza rota calla todo) | MUERTO |
| R2R4 | docstring de `avisar_precio` sin la frase de la segunda corrida | `test_r2_r4_avisar_precio_docstring_declara_residuo_segunda_corrida` (sin `segunda corrida`) | MUERTO |
| R2A1 | `_frase_accion`: `live` sin cambio dice «subió» (sin «decidió ...; sin cambio aplicado») | `test_r2b_a1_live_sin_cambio_no_dice_subio` (frase con «subió») | MUERTO |
| R2L8 | `sombra`: `in ("subir", "bajar")` -> `in ("subir",)` | `test_r2b_l8_shadow_que_baja_cuenta_en_sombra` (`0 == 1`) | MUERTO |
| R2B1 | plantilla con-goal: `porcentaje_ui` -> `dinero_ui` en «Margen hoy» | `test_r2b_b1_margen_y_goal_en_porcentaje_en_html` (sin «24.00 %») | MUERTO |
| R2B2 | `movidos` sin exigir estado `enviado`/`confirmado`/`no_confirmado` | `test_r2b_b2_movidos_solo_con_patch_aceptado` (`3 == 1`) | MUERTO |
| R2B3 | plantilla Estado: `estado_precio_es` -> id crudo | `test_r2b_b3_estado_en_palabras_con_mismo_mapa` (sin «objetivo inalcanzable») | MUERTO |
| R2B4a | `_divergentes_precio`: `"dias": racha` -> `"dias": umbral` | `test_r2b_b4_divergente_racha_real_y_umbral_en_texto` (`3 == 5`) | MUERTO |
| R2B4b | plantilla divergente: texto con el umbral -> «N días seguidos» literal | `test_r2b_b4_divergente_racha_real_y_umbral_en_texto` (sin «3 días seguidos») | MUERTO |

---

# A.6 — bloque R3 (N2 + kimi r3-3, solo tests)

Siembra a mano sobre el HEAD nuevo con base real
(`ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`,
Telegram falso) y `PYTHONPYCACHEPREFIX=$(mktemp -d)` fresco **por
mutante**, revertido sin commit (restore con `git checkout --` del
archivo sembrado, verificado con `git status`). El codigo ya era
correcto: el test nuevo es lo que mata. Cero sobrevivientes.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R3N2 | `_frase_accion`: rama `bajar` de «`live` sin cambio real» (`if mode == "live" and cambio_estado is None:`, segunda ocurrencia) -> `if False:` | `test_r3_n2_live_bajar_sin_cambio_no_dice_bajo` (frase con «bajó») | MUERTO |
| R3R33 | `base.html`: vuelve `<span class="nav-proximo">Repricing <span class="chip-proximo">pronto</span></span>` | `test_sidebar_reputacion_enlace_y_sin_chip_reviews` (chip en el marcado) | MUERTO |
