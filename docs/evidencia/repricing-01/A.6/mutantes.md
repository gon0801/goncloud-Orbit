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
| Q1 | `@router.get("/precios")` -> `@router.post` (la ruta deja de ser GET) | `test_precio_pantalla_ruta_get_contrato` (404, sin `hoy`/`plataformas`) | MUERTO |
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
en el bloque 4) y se mato precisando U6 a la seccion divergente.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| R1 | `@router.get("/precios")` -> `@router.post` (la ruta deja de ser GET) | `test_precio_ui_ruta_get_cinco_bloques_en_orden` (404, sin 200) | MUERTO |
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
sobrevivientes. S3 sobrevivio una vez (C2 solo exigia la palabra
`import`, que tambien sale en el comentario D9) y se mato precisando C2
a la sentencia exacta.

| ID | Mutante (que se cambio) | Test que lo mata | Estado |
|----|-------------------------|------------------|--------|
| S1 | Linea `avisar=avisar_precio,` eliminada (el CLI corre sin gancho) | `test_precio_cierre_cli_pasa_avisar_a_correr` (kw sin `avisar`) | MUERTO |
| S2 | `avisar=avisar_precio` -> `avisar=None` (gancho nulo explicito) | `test_precio_cierre_cli_pasa_avisar_a_correr` (`None is not avisar_precio`) | MUERTO |
| S3 | Import movido a cabecera del modulo (rompe D9: import tardio) | `test_precio_cierre_import_tardio_en_precio` (sentencia fuera de `_precio`) | MUERTO |
| S4 | `avisar=avisar_precio` -> `avisar=lambda *a: None` (gancho mudo) | `test_precio_cierre_cli_pasa_avisar_a_correr` (lambda no es el gancho real) | MUERTO |
