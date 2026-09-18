# A.1 — Catálogo de mutantes (REPRICING 01, carril A)

Un mutante por regla de la DoD (fila A.1 del plan v1.3 + (a)–(j) del
runbook). Corridos con base real (`ORBIT_TEST_DSN` local) y
`-p no:cacheprovider`, uno por uno con restauración después de cada uno.
**Cero sobrevivientes.** La regla (j) *es* este catálogo, no lleva mutante.

Comando por mutante (ejemplo M-plan1):

```
MUT_OLD='...' MUT_NEW='...' python3 /tmp/muta.py tools/precio_goal.py \
  tests/test_precio_goals.py::test_dry_run_imprime_referencia_y_no_escribe
```

(`/tmp/muta.py`: aplica el cambio, corre el test, restaura; andamio
desechable, no va al repo.) "MUERTO (exit 1)" = el test falló con el
mutante puesto = el test discrimina.

## Fila del plan

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| M-plan1 | Dry-run no escribe | `tools/precio_goal.py`: `if not args.acepto_mutacion_real:` → `if False:` (el dry-run cae a pedir huella y mutar) | `test_dry_run_imprime_referencia_y_no_escribe` | MUERTO (exit 1), `AssertionError` en `returncode == 0` |
| M-plan2 | `--go` escribe exactamente N filas | `tools/precio_goal.py` (`_go_con_ceremonia`): `for fila in plan:` → `for fila in plan[:-1]:` | `test_csv_go_escribe_exactamente_n_filas` | MUERTO (exit 1): cuenta 1, esperaba 2 |
| M-plan3 | Huella distinta aborta | `tools/precio_goal.py`: `if args.huella != huella:` → `if False:` | `test_huella_distinta_aborta_y_no_escribe` | MUERTO (exit 1): el go escribe (cuenta 1, esperaba 0) |
| M-plan4 | CSV con fila repetida aborta antes de la huella | `tools/precio_goal.py`: `if clave in vistos:` → `if False:` (sin dedup) | `test_csv_repetido_aborta_antes_de_la_huella_con_linea` | MUERTO (exit 1): dry-run imprime huella y sale 0 |
| M-plan5 | `--goal-pct 0.30` rechazado | `app/precio/goals_write.py`: `if valor < 1:` → `if valor < 0:` | `test_goal_pct_en_fraccion_rechazado` | MUERTO (exit 1): aborta por banda, sin el mensaje "por ciento" |
| M-plan6 | Goal fuera de banda rechazado en Python y en la base | `app/precio/goals_write.py` (`_validar_fila`): `if not minimo <= fraccion <= maximo:` → `if fraccion is None:` | `test_goal_fuera_de_banda_rechazado_en_python_y_en_base` | MUERTO (exit 1): `sembrar_goal` inserta 0.05 y la base lo tumba con `CheckViolation(precio_goal_banda)` en vez de `PrecioGoalInvalido` — el mismo fallo prueba el lado base |
| M-plan7 | `live` sin go rechazado | `app/precio/goals_write.py`: `if go_literal is None or not go_literal.strip():` → `if False:` | `test_sembrar_live_exige_go_en_python` | MUERTO (exit 1): el mensaje ya no es "live sin go" sino "la base rechazo el goal (precio_goal_live_exige_go)" — el lado Python discrimina y el mensaje prueba el lado base |
| M-plan8 | Salto > 25 % sin confirmación aborta | `tools/precio_goal.py`: `UMBRAL_SALTO = Decimal("0.25")` → `Decimal("0.50")` | `test_salto_mayor_25_aborta_sin_confirmacion_y_pasa_con_ella` | MUERTO (exit 1): el salto de 41 % pasa y sale 0 |
| M-plan8b | El 25 % es relativo, no absoluto | `tools/precio_goal.py`: `> UMBRAL_SALTO * fila.p_actual` → `> Decimal("25")` | `test_dry_run_imprime_referencia_y_no_escribe` | MUERTO (exit 1): \|89.88−116\| = 26.12 > 25 aborta un salto relativo de 22.5 % |
| M-plan9 | Candado `_IDENT_PRECIO_GOAL` pasa y falla con UPDATE crudo en `tools/` | `tools/precio_goal.py` + `_FUGA_MUTANTE = "UPDATE precio_goal SET x = 1"` | `test_escritura_precio_goal_vive_solo_en_goals_write` | MUERTO (exit 1) |

## Runbook (a)–(i)

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| M-a | Ceremonia: `shadow` sin go literal, `--go` en shadow se rechaza | `tools/precio_goal.py`: `if args.go is not None:` (rama shadow/cerrar) → `if False:` | `test_shadow_go_sin_go_literal_y_rechaza_go` | MUERTO (exit 1): el go con `--go` escribe (cuenta 1, esperaba 0) |
| M-b | Goal como fracción de dos decimales (`30.00` → `0.3000`) | `app/precio/goals_write.py`: `(valor / 100).quantize(...)` → `(valor / 1000).quantize(...)` | `test_porcentaje_a_fraccion_dos_decimales` | MUERTO (exit 1): `0.0300 != 0.3000` |
| M-c | Banda de la config vigente en `goals_write.py`; sin claves aborta, nunca default | `app/precio/goals_write.py` (`_numero`): `raise PrecioGoalInvalido(f"config sin {clave}")` → `return Decimal("0.10")` | `test_sin_claves_de_config_aborta` | MUERTO (exit 1): ya no aparece "config sin precio_goal_min_pct" |
| M-d | `m_actual` y `P*` del escenario `disponible` más reciente + su `fee_observation` | `tools/precio_goal.py`: `ORDER BY observed_at DESC LIMIT 1` → `ASC` | `test_escenario_mas_reciente_manda` | MUERTO (exit 1): `m_actual=0.2400` en vez de `0.4100` |
| M-e | `--cerrar` solo fija `valid_to` del vigente; sin vigente aborta | `app/precio/goals_write.py` (`cerrar_goal`): `if fila is None:` → `if False:` | `test_cerrar_sin_vigente_aborta` | MUERTO (exit 1): `TypeError` en vez de `PrecioGoalAusente` |
| M-f | `live` exige go literal no vacío (lado tool) | `tools/precio_goal.py`: `if not args.go or not args.go.strip():` → `if False:` | `test_live_go_completo_escribe_go_literal` | MUERTO (exit 1): el go sin `--go` ya no menciona `--go` en stderr |
| M-g | Lote CSV duplicado aborta antes de la huella, con línea | `tools/precio_goal.py`: `en linea {numero}` → `en linea {numero + 1}` | `test_csv_repetido_aborta_antes_de_la_huella_con_linea` | MUERTO (exit 1): dice línea 5, el test exige "nea 4" |
| M-h | Todo dinero en `Decimal`, cero `float` | `app/precio/goals_write.py`: `(valor / 100).quantize(...)` → `float(valor / 100)` | `test_porcentaje_a_fraccion_dos_decimales` | MUERTO (exit 1): `isinstance(..., Decimal)` falla |
| M-i | Pureza: excepción por nombre + `rglob` + candado propio de imports | `tests/test_architecture.py`: `EXCEPCIONES_PURAS_PRECIO = ("goals_write.py",)` → `("goals_write.py", "reglas.py")` | `test_precio_excepcion_por_nombre` | MUERTO (exit 1): la tupla ya no es exactamente `("goals_write.py",)` |

## Notas declaradas

- M-plan6 y M-plan7 prueban los dos lados en un solo fallo: el mutante
  quita el rechazo Python y la base lo tumba (`precio_goal_banda`,
  `precio_goal_live_exige_go`); el test muere porque esperaba el error
  Python. Los tests de INSERT crudo (`test_live_sin_go_rechazado_por_la_base`,
  banda en `test_goal_fuera_de_banda_...`) prueban el lado base directo.
- `sin_escenario` cubre también fee no derivable (`fee_error`,
  `margen_imposible`): se imprime `m_actual` real con `P*=sin_escenario`,
  se avisa y se sigue; solo la falta de escenario aborta `live` (DoD (d)
  literal). Residual declarado.
- `--cerrar` lleva ceremonia estilo shadow (`--acepto-mutacion-real
  --huella`, `--go` rechazado) y no mira `--mode`: cerrar no enciende
  nada, no hay go que dar. Decisión del carril, con test.
- Un intento de mutante M-plan6 (`if False:` sin cuerpo) murió por
  `IndentationError`: se descartó por infiel y se repitió con `if fraccion
  is None:` (el de la tabla).
- Un sobreviviente real apareció en el camino (M-plan7 con match laxo
  `live.*go`): el `except CheckViolation` de `sembrar_goal` convertía el
  rechazo de la base en el mismo `PrecioGoalInvalido`. Se endureció el test
  a `match="live sin go"` y el mutante murió. Sin sobrevivientes al cierre.

## Ronda 1 (auditoría del lead, 2026-09-18 UTC)

Mutantes que sobrevivían sobre `b18e0a4`, corridos igual (base real, uno
por uno con restauración, `-p no:cacheprovider`, caché de bytecode fresca
por corrida vía `PYTHONPYCACHEPREFIX`). **Cero sobrevivientes.**

| # | Regla | Cambio exacto | Test que lo mata | Salida |
|---|---|---|---|---|
| L4 | Borde de banda inclusivo en `goals_write` | `_validar_fila`: `if not minimo <= fraccion <= maximo:` → `if not minimo < fraccion < maximo:` | `test_banda_bordes_inclusivos_se_siembran` | MUERTO (exit 1): `PrecioGoalInvalido` al sembrar 10.00 |
| L4b | Borde de banda inclusivo en el tool | `_construir_plan`, misma línea, `<=` → `<` | `test_banda_bordes_inclusivos_en_el_tool` | MUERTO (exit 1): el dry-run al 10.00 aborta "fuera de banda" |
| L5 | La huella ata el modo | `_huella`: `...:{fila.fraccion}:{mode}` → sin `:{mode}` | `test_huella_ata_el_modo` | MUERTO (exit 1): el go live con huella shadow escribe (cuenta 1) |
| L5b | La huella ata el goal | `_huella`: sin `:{fila.fraccion}` | `test_huella_ata_el_goal` | MUERTO (exit 1): el go al 50.00 con huella del 30.00 escribe |
| L10 | El salto es `abs(P* − P_actual)` (también hacia abajo) | `_guardas_del_plan`: `abs(fila.p_estrella - fila.p_actual)` → `(fila.p_estrella - fila.p_actual)` | `test_salto_hacia_abajo_tambien_aborta` | MUERTO (exit 1): P*=66.07 (−43 %) sale 0 en vez de abortar |
| L11 | `--cerrar` cierra el vigente entre goals viejos | `cerrar_goal`: `... AND valid_to IS NULL` → `... ORDER BY id LIMIT 1` | `test_cerrar_elige_el_vigente_entre_cerrados` | MUERTO (exit 1): intenta recerrar el goal viejo y el trigger lo tumba |
| L12 | `--huella` sin `--acepto-mutacion-real` aborta | `_go_con_ceremonia`: `if args.go is not None or args.huella is not None:` → `if args.go is not None:` | `test_huella_sin_acepto_tambien_aborta` | MUERTO (exit 1): el dry-run con `--huella` sale 0 |

Notas r1:

- L11 requirió dos intentos: el primero murió por `SyntaxError` (comilla
  rota por el shell al aplicar el mutante), infiel; se repitió limpio (el
  de la tabla, `AssertionError`).
- R1 endureció además `sembrar_goal` (pre-chequeo de vigente antes del
  INSERT): sin él, resembrar el mismo día UTC pegaba primero en
  `precio_goal_unico_por_fecha` y el mensaje de R2 mentía diciendo que
  había vigente. Detectado por `test_segundo_vigente_rechazado` en verde.
- G1 cambió el formato impreso (`goal=30.00% m_actual=36.64%`): los
  asserts viejos (`m_actual=0.2400`) se actualizaron en el mismo cambio.
