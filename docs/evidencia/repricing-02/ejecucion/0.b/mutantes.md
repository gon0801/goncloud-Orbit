# Mutantes de 0.b

Un mutante por prueba nueva: el cambio de una linea, la prueba en rojo y el
codigo restaurado. Cada linea se corrio con

	PATH="/opt/homebrew/bin:/usr/bin:/bin" ORBIT_TEST_DSN="postgresql://orbit:orbit@localhost:5432/postgres" \
		PYTHONPATH=. .venv/bin/python -m pytest -q <prueba>

contra el codigo mutado; al terminar, el archivo se restauro de su respaldo.

| Prueba | Mutante (una linea) | Salida |
| --- | --- | --- |
| test_corrida_con_mercado_falso_sombra_inserta_virtual | `_fase3_uno`: el virtual solo si `mode == "live"` (invertido) | ROJO |
| test_corrida_con_mercado_falso_live_aplica_y_sella | `escritas_1 = 0` (no cuenta el cambio real) | ROJO |
| test_aplicar_dos_veces_escribe_una_vez | `aplicar` devuelve `enviado` en vez de `saltado` en la rama `ya_aplicado` | ROJO |
| test_catalogo_coincide_con_sql_cano_y_el_orden_importa | `_SQL_CANO` con `ORDER BY observed_at ASC` (filtrar despues del DISTINCT ON roto) | ROJO |
| test_leer_cuenta_da_los_mismos_importes_que_la_entrada_del_adaptador | `insumos` arma la entrada con `canal=None` | ROJO |
| test_toda_decision_guarda_goal_origen_y_verificacion | `l_origen="herramienta"` / `verificacion=None` en la rama compite | ROJO |
| test_decision_no_evaluada_tambien_guarda_goal_y_origen | `l_origen="herramienta"` en la rama generica | ROJO |
| test_sin_referral_unico_en_tolerancia_mantiene_y_bajo_el_goal_no_evalua | `_subir` sin el corto `isinstance(fees_escenario, str)` (lazy roto) | ROJO |

Nota: tres mutantes intentados NO enrojecieron y se documentan en vez de
esconderse: `iva_divisor` con `costo` en `leer_cuenta` (el test 4 compara
`leer_cuenta` contra `insumos`, que usa `leer_cuenta`: tautologico en esos
campos; su valor es fijar que `insumos` no arma la cuenta a mano), `ref`
contra `p_actual` en `_subir` y `goal_id=None` en la rama compite (el
fixture trae `p_actual == precio_cotizado == 116`; el test 1 no mira
`goal_id`, lo cubre el test 5).
