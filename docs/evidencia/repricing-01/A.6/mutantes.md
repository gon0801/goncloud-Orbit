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
