# A.7 — Catálogo de mutantes (REPRICING 01, carril B)

Un mutante por regla de la DoD (fila A.7 del plan v1.3 + (a)–(h) del
runbook). Corridos con base real (`ORBIT_TEST_DSN` local), uno por uno
con restauración, `-p no:cacheprovider` y caché de bytecode fresca por
corrida (`PYTHONPYCACHEPREFIX`). **Cero sobrevivientes.** La regla (h)
*es* este catálogo, no lleva mutante.

## Fila del plan

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| A7-1 | Canal desconocido → `canal_sin_dato`, nunca un default | `cobertura._motivo_no_evaluada`: `return "canal_sin_dato"` → `return None` | `test_canal_desconocido_es_canal_sin_dato_sin_default` | MUERTO (exit 1): la pub sin canal sale `evaluada` |
| A7-2 | Sin reportar > 3 días → `catalogo_desactualizado` | `dias_sin_reportar > max_dias` → `>= max_dias` | `test_stale_mas_de_max_dias_es_catalogo_desactualizado` | MUERTO (exit 1): la pub con 3 días exactos cae en `catalogo_desactualizado` |
| A7-3 | El recuadro cuadra exacto (12 pubs, cuatro estados) | `armar_recuadro`: `if fase is not None:` → `if False:` (las FBM caen a otros buckets) | `test_recuadro_cuadra_exacto_con_doce` | MUERTO (exit 1): `fase_E_envio_fbm` vacío, no cuadra |
| A7-4 | Sin goal aparece listada, no oculta | `DetalleSinGoal`: `sku=fila.seller_sku` → `sku=str(fila.listing_id)` | `test_sin_goal_aparece_listada_con_precio_y_canal` | MUERTO (exit 1): los SKU listados no son los sembrados |
| A7-5 | `fuera_de_alcance` nombra la fase, no un genérico | `_fase_fuera_de_alcance`: `return FASE_MELI` → `return FASE_FBM` | `test_fuera_de_alcance_nombra_la_fase` | MUERTO (exit 1): MeLi sale como `fase_E_envio_fbm` |
| — | Readback en producción (264 MX, 106 US; puente 284/109) | Sin mutante de código: lo corre el lead (regla 9). Cubierto en local por A7-8 + `test_tool_imprime_recuadro_con_puente_al_lado` | — | — |

## Runbook (a)–(g)

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| (a) | Sin migración, sin tocar `app/listings.py` | Sin mutante de código: es una ausencia; se verifica con `git diff --name-only` (ni `migrations/` ni `app/listings.py` aparecen) | — | — |
| A7-6 | Doble ASIN: dos listings con el mismo SKU cuentan uno | `fuentes._SQL_CANO`: `SELECT DISTINCT ON (e.seller_sku, e.platform)` → `SELECT` (el join se abanica) | `test_fuentes_dos_listings_mismo_sku_cuentan_uno` | MUERTO (exit 1): `len(filas) == 2`, activas = 2 |
| A7-7 | `max_dias` de la config con cota 1–14, sin defaults | `max_dias_desde_settings`: `minimo=1, maximo=14` → `minimo=0, maximo=99` | `test_max_dias_de_config_con_cota` | MUERTO (exit 1): `"0"` y `"15"` se aceptan |
| (d) | Cubierto por A7-5 (la fase viaja en el mismo `return`) | — | `test_fuera_de_alcance_nombra_la_fase` | — |
| A7-8 | Puente al lado; diferencia > 5 % se avisa | `aviso_puente`: `> 5` → `> 50` | `test_aviso_puente_mas_de_5_por_ciento` | MUERTO (exit 1): 16.7 % sin aviso |
| A7-f1 | `fuentes.py` solo importa lo permitido | `fuentes.py` + `import httpx` | `test_fuentes_solo_importa_permitido` (post-Q1, commiteado) | MUERTO (exit 1) |
| A7-f2 | `fuentes.py` solo lee (cero escritura) | `fuentes.py` + `_X = "INSERT INTO precio_goal ..."` | `test_fuentes_solo_select` (post-Q1, commiteado) | MUERTO (exit 1) |
| A7-9 | `consultas/*.sql` son las que ejecuta `fuentes.py` | `01_canonicas.sql`: `LIKE '%BUYABLE%'` → `= 'BUYABLE'` | `test_consultas_iguales_a_las_que_ejecuta_fuentes` | MUERTO (exit 1) |
| A7-10 | `recuadro_desde_salidas.py` alimenta el mismo recuadro | `canal_por_listing[...] = (canal, precio, moneda)` → `(precio, canal, moneda)` | `test_recuadro_desde_salidas_roundtrip` | MUERTO (exit 1): `rec == esperado` falla |

## Notas

- A7-10 sobrevivió una vez: el roundtrip solo comparaba conteos y SKUs
  (el canal `Decimal("116")` no cambiaba de bucket). Se endureció el test
  a `assert rec == esperado` y el mutante murió. Sin sobrevivientes.
- A7-f1/A7-f2 murieron primero contra el cuerpo exacto del candado
  post-Q1 validado en `/tmp/lock_fuentes.py`, y de nuevo tras el merge
  contra los tests ya commiteados en `tests/test_architecture.py`
  (`test_fuentes_solo_importa_permitido`,
  `test_fuentes_solo_select`).
- Orden de buckets declarado y con test: `fuera_de_alcance` estructural
  antes que `catalogo_desactualizado` (`test_fuera_estructural_antes_que_stale`).
- `listing` no tiene `updated_at`: el contraste del puente muestra solo
  la cuenta (el spec pedía `updated_at`, no existe esa columna).

## Ronda 1 (auditoría del lead + cruzada, 2026-09-18 UTC)

Mutantes F1–B13 corridos con la suite **sin** la prueba de igualdad de
texto (`--deselect test_consultas_iguales_a_las_que_ejecuta_fuentes` no
hizo falta: cada corrida apunta a un solo test de comportamiento), con
base real, uno por uno con restauración y bytecode fresca por corrida.
**Cero sobrevivientes.**

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| F1 | Canal desconocido en Amazon → `canal_sin_dato` tenga o no goal | `cobertura._motivo_no_evaluada`: `return "canal_sin_dato"` → `return None` | `test_canal_desconocido_con_goal_tambien_es_sin_dato` | MUERTO (exit 1) |
| F2 | La identidad se muestra sin aviso; el aviso solo con `--puente-activas` | tool: `if args.puente_activas is None:` → `if False:` (cae a la rama con bandera en `None` y revienta) | `test_tool_identidad_al_lado_sin_aviso_y_puente_con_bandera` | MUERTO (exit 1) |
| F3 | Activa sin `listing` cuenta (`LEFT JOIN`) | `fuentes._SQL_CANO`: `LEFT JOIN listing` → `JOIN listing` | `test_activa_sin_listing_cuenta_y_cuadra` | MUERTO (exit 1): la huérfana desaparece |
| F5 | MeLi sin fuente canónica: `unknown`, sin recuadro | tool: `if args.platform == "meli":` → `if False:` | `test_tool_meli_es_unknown_sin_recuadro` | MUERTO (exit 1): imprime `activas=0` cuadrado |
| B6 | Goal cerrado no cuenta | `fuentes._SQL_GOALS`: `... OR valid_to > %s)` → `... OR valid_to > %s OR valid_to IS NOT NULL)` (tautología con aridad intacta) | `test_goal_cerrado_no_cuenta_como_goal` | MUERTO (exit 1) |
| B7 | Decisión de ayer no es la de hoy (`=` exacto) | `fuentes._SQL_DECISIONES`: `decision_date = %s` → `decision_date <= %s` | `test_decision_de_ayer_no_es_la_de_hoy` | MUERTO (exit 1) |
| B8 | El canal manda la oferta más reciente | `fuentes._SQL_CANAL`: `observed_at DESC` → `ASC` | `test_canal_manda_la_oferta_mas_reciente` | MUERTO (exit 1): sale `fba` |
| B13 | Resultado desconocido se nombra | `cobertura`: `return f"resultado_desconocido:..."` → `return None` | `test_resultado_desconocido_se_nombra` | MUERTO (exit 1): la pub sale `evaluada` |
| K1 | Vigente exige `valid_from <= hoy` | `fuentes._SQL_GOALS`: `valid_from <= %s` → `(valid_from <= %s OR valid_from IS NOT NULL)` | `test_goal_futuro_no_cuenta_como_vigente` | MUERTO (exit 1) |
| G1 | La excepción no ampara reloj en `fuentes.py` | `fuentes.py` + `_hora = date.today()` | `test_fuentes_sin_reloj_ni_entorno_ni_dinamico` | MUERTO (exit 1) |
| G2 | Escritura multilínea y todos los verbos | patrón: `INSERT\s+INTO` → `INSERT INTO` (espacio literal) | `test_candado_select_fuentes_caza_fugas_sembradas` | MUERTO (exit 1): el `INSERT` partido no dispara |
| G3 | De `app.estimacion_insumos` solo `mapear_canal` | candado: `elif nodo.module == "app.estimacion_insumos":` → `... and False:` | `test_candado_imports_fuentes_solo_mapear_canal` | MUERTO (exit 1) |
| G4 | Los relativos se marcan | candado: `if nodo.level:` → `if False:` | `test_candado_imports_fuentes_caza_relativo` | MUERTO (exit 1): sale `hermano` en vez del marcador |

Notas r1:

- B6 requirió dos intentos: el primero rompía la aridad (`ProgrammingError`,
  infiel); se repitió como tautología (`OR valid_to IS NOT NULL`) con los
  3 parámetros intactos (el de la tabla).
- G2 tuvo un intento infiel (quitar `CREATE`, que ningún seed usa y
  sobrevivió): el mutante que cuenta es el que rompe lo multilínea.
- `sin_listing` va después de `catalogo_desactualizado` y antes de
  `canal_sin_dato`: refina el orden de F1 (una fila sin `listing` no
  tiene canal que mirar). Sin mutante asignado; lo cubre F3.
- G1 (r1) no toca `goals_write.py`: auditado, el hueco estructural
  existe ahí también (está en la tupla de excepción), pero el archivo no
  usa reloj/entorno/dinámico de Python (solo `now()` SQL en dos
  consultas); anotado y sin tocar, como ordena el brief.
