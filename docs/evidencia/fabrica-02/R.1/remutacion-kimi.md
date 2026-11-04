# E/R.1 — Re-mutación ejecutada de kimi y auditoría del lead (2026-09-16)

**Qué es esto.** La mitad del revisor de la fila R.1 sobre el catálogo del
implementador (`mutantes.md`, PR #278). Kimi (`kimi-code` 0.39.1, prompt no
interactivo, worktree propio en el SHA del PR, Postgres local) re-ejecutó
los 27 mutantes M01–M27 aplicando el diff de cada fila, corrió el archivo
focal del test asesino, comparó el rojo con el literal del catálogo y
revirtió; ejecutó M28; verificó los cinco cierres (H2, H3, H4, H5, M14) en
ambas direcciones; probó tres mutantes propios fuera del catálogo; y revisó
el diff de tests. `git status` de su worktree quedó vacío. El lead ejecutó
por su cuenta una muestra de ocho comprobaciones en otro worktree, con la
misma base, antes de leer el informe de kimi.

El informe de kimi va tal cual abajo, sin editar. La auditoría del lead y la
ronda de correcciones están al final.

---

# Re-mutación R.1 — kimi — SHA 224d5d9

## 1. Baseline ejecutado

```
305 passed, 1 warning in 45.41s
```

0 skipped. El warning es el preexistente `StarletteDeprecationWarning`. Reproducido dos veces: al inicio y tras la purga de `__pycache__` (ver nota metodológica en §8): `305 passed, 1 warning in 44.35s`.

## 2. Tabla M01–M27

| # | Veredicto | Rojo observado (primera línea) | Nota |
|---|---|---|---|
| M01 | CONFIRMADO | `E KeyError: 'auto_discovery'` | literal |
| M02 | CONFIRMADO | `E AssertionError: filtro en cada pagina` | literal |
| M03 | CONFIRMADO | `E AssertionError: ARCHIVED es operativamente AUSENTE: la identidad devuelve la fila VIVA` | literal; el bloque existe 2 veces (607 y 647), apliqué solo en `_identidad` desambiguando con la línea siguiente |
| M04 | CONFIRMADO | `E AssertionError: ResumenReconciliacion(jobs_done=1, jobs_failed=0, ...)` sobre `assert resumen.jobs_failed == 1` | literal; la línea existe 2 veces (1487/1597), apliqué en 1597 con contexto |
| M05 | CONFIRMADO | `E AssertionError: adoptada fuera, propias en orden canonico, origen ultimo` | literal |
| M06 | CONFIRMADO | `E At index 2 diff: True != False` | literal |
| M07 | CONFIRMADO | `E AssertionError: assert 'cost' not in 'INSERT INTO...RETURNING id'` | literal |
| M08 | CONFIRMADO | `E AssertionError: assert '8001' == '6104'` | literal |
| M09 | CONFIRMADO | `E AssertionError: assert DestinoHarvest(campaign_external='8001', ...) == SaltoHarvest(motivo='destino_inconsistente')` | literal |
| M10 | CONFIRMADO | `E psycopg.errors.InsufficientPrivilege: permission denied for sequence keyword_biblioteca_id_seq` | literal |
| M11 | CONFIRMADO | `E Failed: DID NOT RAISE UniqueViolation` | literal |
| M12 | CONFIRMADO | `E assert 0 == 1` (`jobs_done`) | literal; la línea existe 2 veces (54/92), apliqué en 54 con la línea previa |
| M13 | CONFIRMADO | `E AssertionError: post-D.2 el harvest sale del destino, no de la terna` | literal |
| M14 | CONFIRMADO | `E AssertionError: el duplicado no se aplica` / `assert 1 == 0` | muere con el test nuevo de R.1; verde sin el mutante (ver §4) |
| M15 | CONFIRMADO | `E AssertionError: assert '6100' == '6104'` | literal |
| M16 | CONFIRMADO | `E AssertionError: la misma racha no re-avisa` / `assert 2 == 1` | literal exacto. Primera corrida dio `assert 4 == 1` por contaminación de `.pyc` rancio (ver §8); re-ejecutado limpio reproduce el literal del catálogo |
| M17 | CONFIRMADO | `E AssertionError: la exacta no se harvestea a si misma` | literal |
| M18 | CONFIRMADO | `E assert 'done' == 'hermanas_negadas'` | literal. Primera corrida dio `TypeError` espurio por el mismo `.pyc` rancio; re-ejecutado limpio reproduce el literal |
| M19 | CONFIRMADO | `E assert 'hermanas_negadas' == 'done'` | literal (la constante aparece también en un comentario; muté solo la asignación) |
| M20 | CONFIRMADO | `E psycopg.errors.CheckViolation: apply_queue 1: transicion vetoed -> applied fuera de la maquina de estados...` | literal; lo mata el trigger, como declara el catálogo |
| M21 | CONFIRMADO | `E psycopg.errors.UndefinedTable: relation "no_existe" does not exist` | literal |
| M22 | CONFIRMADO | `E AssertionError: apply usa el congelado aunque la terna ya sea NULL` / `assert 0 == 1` | literal |
| M23 | CONFIRMADO | `E psycopg.errors.RaiseException: 0038: UPDATE(origen) en keyword_biblioteca NO fue rechazado` | literal |
| M24 | CONFIRMADO | `E AssertionError: assert None == 'migracion_pendiente'` | literal |
| M25 | CONFIRMADO | `E AssertionError: no se ensena como exclusion` | literal |
| M26 | CONFIRMADO | `E AssertionError: el bid congelado viaja clampeado` / `assert 1.0 == 11.62` | literal |
| M27 | CONFIRMADO | `E assert 2 == 1` sobre `assert used == 1` | literal |

## 3. M28

Apliqué `with conn.transaction():  # MUTANTE R.1: enviar DENTRO de tx` envolviendo la llamada `_fase_notifica(...)` en `app/cycle.py` (indentando el bloque completo, verificado por el assert de unicidad del parche). Resultado ejecutado: **80 passed, 1 warning** en `tests/test_notifica.py` + `tests/test_fabrica_f2_visibilidad.py` con el mutante aplicado. **No discriminable**, tal cual declara el catálogo: ningún test observa el estado de transacción en el envío de `_fase_notifica` (los espías de timing de H4 son de biblioteca, otro camino). Confirmo el estado `RES / no discriminable` por ejecución, no por lectura.

## 4. Tests nuevos y fortalecidos

| Test | verde limpio | rojo con mutante | nota |
|---|---|---|---|
| M14: `test_r1_revalida_duplicado_en_exacta_resuelta_descarta` | sí (baseline 305) | sí: `E AssertionError: el duplicado no se aplica` / `assert 1 == 0` | mutante = diff exacto de la fila M14 |
| H2: `test_r1_plan_cola_no_applied_falla_cerrado` | sí | sí: `E Failed: DID NOT RAISE ValueError` | mutante = quitar guardas 1150-1152 y 1156-1157 (ambas a la vez) |
| H2: `test_r1_plan_sin_ids_falla_cerrado` | sí | sí: `E Failed: DID NOT RAISE ValueError` | mismo mutante |
| H3: `test_a6_campana_suelta_salta_en_skips_pero_no_en_saltos_grupo` | sí (1 passed explícito) | sí: `E AssertionError: assert None == 1` (`sin_destino_de_harvest` ausente; quedó contado como `harvest_sin_config`) | mutante mío: quitar la sustitución `motivo = motivo_salto` en `app/cycle.py:1611-1612` |
| H4: `test_fallo_inyectado_keyword_sello_intacto` | sí (baseline) | sí: `E AssertionError: A.4r1: el aviso sale DESPUES del commit del sello` / `assert <TransactionStatus.INTRANS: 2> == <TransactionStatus.IDLE: 0>` | mutante = `avisa_si_fallo` dentro del `with conn.transaction():` del sello en `app/apply_harvest.py` |
| H4: `test_fallo_inyectado_negative_veredicto_intacto` | sí (baseline) | sí: mismo rojo literal (`INTRANS == IDLE`) | mutante = `avisa_si_fallo` dentro del sello en `app/apply_cola.py:823` |
| H5: `_dsn_decide_de` + 5 CLI | sí: 5 passed | n/a | `_dsn_decide_de` (tests/test_reversa_harvest.py:705-709) deriva de `_test_dsn().rsplit('/', 1)[0]`; grep confirma 0 DSN fijos restantes (solo el comentario y el skip-reason) |

## 5. Mutantes extra

| Mutante (sitio + diff) | Resultado | Test que lo mató |
|---|---|---|
| X1 dinero — `app/apply_harvest.py:407`: `- return min(max(base, floor), ceiling)` / `+ return max(base, floor)` (bid sin techo) | MUERE | `test_apply_harvest.py::test_clampeo_sugerido_sobre_ceiling_y_bajo_floor`: `E AssertionError: sugerido > ceiling → ceiling` / `assert Decimal('15') == Decimal('2.50')` |
| X2 quota — `app/apply.py:325`: `- WHERE apply_quota_state.used < apply_quota_state.cap` / `+ ... used <= ...` (cobra cap+1) | MUERE | `test_preflight_1_4.py::test_consume_quota_y_sello_transicion_de_saturacion` (+2 más del mismo archivo): `E psycopg.errors.CheckViolation: ... violates check constraint "quota_no_excedida"` — lo mata el CHECK del esquema, defensa en capas como M20 |
| X3 reversa — `app/apply_harvest.py:1289-1290`: quitar el guard `- if _reversa_confirmada(...): continue` (re-DELETE de lo ya confirmado) | MUERE | `test_reversa_harvest.py::test_crash_despues_de_keyword_reanuda_sin_repetir`: `E AssertionError: keyword confirmada: se salta, no se repite` |

Cero sobrevivientes entre los extra.

## 6. Diff de tests

`git diff 41bc6aa..HEAD -- tests/`: 4 archivos, +469/−33. Revisado línea por línea:

- `test_harvest_destino.py`: +42, solo el test nuevo de M14. Sin toques a existentes.
- `test_reversa_harvest.py`: +2 tests H2, helper `_dsn_decide_de` y 5 sitios H5 (DSN fijo → derivado). Ningún assert existente tocado.
- `test_fabrica_f2_visibilidad.py`: H3 fortalecido — el único assert eliminado es `assert ag is not None` (relleno vacío), reemplazado por siembra + `skips.termino == 1` + `saltos_grupo == {}` + cero avisos. Estrictamente más fuerte (verificado con mutante en §4).
- `test_fabrica_f2_biblioteca.py`: H4 — el assert de timing sale del espía (donde el fail-silent lo tragaba) y se ejecuta fuera; los asserts de contenido se conservan sobre la tupla registrada. Más fuerte, no más débil (verificado con mutante en §4).
- Ningún `skip`/`xfail`/`noqa` nuevo sobre tests existentes (los `+@_skip_db` del diff son el gate de DB estándar en tests NUEVOS). Ningún test borrado. `git diff 41bc6aa..HEAD --stat -- app/ tools/ migrations/ ops/`: **vacío** — el PR no toca código, como declara el catálogo.

**Sin hallazgos.**

## 7. Veredicto

**APPROVE sobre 224d5d9.**

Los 27 mutantes del catálogo mueren con el rojo literal declarado (0 DISCREPA, 0 SOBREVIVE, 0 NO REPRODUCIBLE tras la re-ejecución limpia de M16/M18), M28 se confirma no discriminable por ejecución, los cierres H2/H3/H4/H5/M14 discriminan en ambas direcciones, los 3 mutantes extra mueren, y el diff de tests es solo endurecimiento.

```
$ git status --short
(vacío)
```

## 8. Lo que no pude verificar

- **`docs/evidencia/fabrica-02/R.1/revision-kimi.md` no existe** en este worktree (el directorio solo contiene `mutantes.md`). Trabajé del catálogo y de las citas que él hace de mi pre-pase; no pude re-leer mi propio informe ni la auditoría del lead contra el entregable.
- **Nota metodológica (afecta a quien re-ejecute mutantes en este repo):** a mitad del pase, corridas en árbol limpio empezaron a fallar (22 rojos en hermanas, 8 en biblioteca). Causa raíz demostrada por `dis`: mutantes de igual longitud aplicados y revertidos dentro del mismo segundo dejan un `.pyc` válido con el bytecode DEL MUTANTE (criterio mtime+tamaño), y los tests siguientes ejecutan el mutante ya revertido. M16 (`!=`→`==`) y M17 (`==`→`!=`) son de ese tipo. Tras `rm -rf **/__pycache__` y `PYTHONDONTWRITEBYTECODE=1`, baseline 305 verde y M16/M18 reprodujeron el literal exacto del catálogo. Los veredictos M01–M15 y M17 no estaban contaminados (cada corrida de mutante compiló su propia fuente; lo verifiqué por secuencia de tamaños/mtimes y por el rojo observado). Recomendación: registrar en el runbook de mutación que se corra con `PYTHONDONTWRITEBYTECODE=1` o purgar `__pycache__` tras cada revert — sin eso, los falsos SOBREVIVE/DISCREPA son sistemáticos en mutantes de igual longitud.
- M29 no se re-ejecutó (el brief lo excluye de la re-mutación; estructural, sin código que mutar).
- Los rojos "también mueren" de las notas del catálogo (tests secundarios) no se re-verificaron uno a uno; verifiqué el test asesino declarado por fila.


---

# Auditoría del lead (ejecutada, sobre `224d5d9`)

Worktree propio, `ORBIT_TEST_DSN` a Postgres local, intérprete del venv del
repo, `-p no:cacheprovider`. Cada mutante aplicado a mano, focales del
archivo relevante, revertido con `git checkout --`, `git status` vacío.

| Comprobación | Resultado del lead | Coincide con el catálogo |
|---|---|---|
| Baseline focal (8 archivos) | 305 passed, 0 skipped | sí |
| M06 `quota_cobrada=True` en hermana | muere: `At index 2 diff: True != False` | sí |
| M13 `completa` derivada de la terna (solo `_goal_json`) | muere 1/24: `post-D.2 el harvest sale del destino, no de la terna` | sí |
| M20 `vetoed` fuera de filas muertas | muere por el trigger `apply_queue_sella_transiciones` | sí |
| M21 `raise` en el catch de `registra_keyword` | muere: `UndefinedTable: relation "no_existe"` | sí |
| M28 `_fase_notifica` dentro de la transacción | 80 passed: no discriminable | sí |
| H2 sin las dos guardas de `plan_reversa_harvest` | los dos tests nuevos caen (`DID NOT RAISE`); el viejo no ve nada | sí |
| M14 (variante del lead: dedupe contra la terna del goal de plataforma) | el test nuevo cae: `el duplicado no se aplica` / `assert 1 == 0` | sí |
| H4 `avisa_si_fallo` dentro de la transacción del sello | el espía fijado lo atrapa: `INTRANS == IDLE` | sí |

Diff de tests revisado línea por línea: solo agrega o fortalece; el único
assert eliminado (`assert ag is not None`) era relleno vacío y lo reemplazan
cuatro asserts con siembra real. `git diff 41bc6aa..224d5d9 -- app/ tools/
migrations/ ops/` vacío.

## Ronda de correcciones (lead → Muse, mismo PR)

Tres comentarios menores de CodeRabbit sobre los tests nuevos, uno de ellos
un hueco real de discriminación, enviados a Muse como ronda del loop:

1. `test_r1_plan_sin_ids_falla_cerrado` quita `keyword_id` y luego
   `negative_id` de forma acumulada: un mutante que vigile solo
   `keyword_id` pasa. Se pide probar la ausencia de `negative_id` de forma
   independiente, rojo-primero contra ese mutante.
2. `_dsn_decide_de` reconstruye el DSN con `rsplit('/', 1)`: rompe con
   opciones en la URI. Se pide `psycopg.conninfo`.
3. Espías de H4: `db_f2` es `autocommit=True`, así que `IDLE` prueba que no
   hay transacción explícita abierta al enviar, no que el sello está
   persistido. Se pide leer el sello desde una segunda conexión en el
   momento del aviso, o declararlo como residual si el fixture no lo
   permite.

Kimi vuelve a ver la corrección; el APPROVE del lead se da sobre el SHA
nuevo y se registra como review en el PR #278.

## Nota metodológica que va al runbook de mutación

Hallazgo de kimi, reproducido: mutantes de igual longitud aplicados y
revertidos dentro del mismo segundo dejan un `.pyc` con el bytecode del
mutante (criterio mtime + tamaño), y las corridas siguientes ejecutan el
mutante ya revertido. Regla: mutar con `PYTHONDONTWRITEBYTECODE=1` o purgar
`__pycache__` tras cada revert. Va al brief de cualquier catálogo futuro.

---

# Ronda de correcciones verificada (SHA `d291dd4`)

**Lead, ejecutado:** reversa + biblioteca 51 passed en limpio; mutante «solo `keyword_id`» → `test_r1_plan_sin_ids_falla_cerrado` cae (`DID NOT RAISE ValueError`), cosa que antes de la ronda no pasaba; mutante «aviso dentro del sello» → el espía reforzado cae (`INTRANS == IDLE`). Worktree limpio.

**Kimi, informe tal cual:**

# Ronda R.1 — kimi — SHA d291dd4

## 1. Punto 1 (H2 independiente)
- Verde limpio: `test_r1_plan_sin_ids_falla_cerrado` pasa (8/8 con el resto de la tanda).
- Rojo con el mutante keyword-only (`app/apply_harvest.py:1156` → `if keyword_id is None:`), que antes de la ronda pasaba:
  `E Failed: DID NOT RAISE ValueError` en `tests/test_reversa_harvest.py:319` — el segundo caso (negative ausente **con** keyword_id presente, restaurado desde `ext_orig`). El test ahora prueba cada ausencia de forma independiente; el mutante cae donde debe. Revertido, `__pycache__` purgado, re-verificado verde.

## 2. Punto 2 (DSN)
- Los 5 CLI que usan `_dsn_decide_de` (`test_cli_dry_run_imprime_huella_sin_http`, `test_cli_mutacion_exige_ceremonia_completa`, `test_cli_readback_ambiguo_aborta_limpio`, `test_cli_cliente_sin_credenciales_aborta_limpio`, `test_cli_auth_durante_delete_aborta_limpio`): **5/5 verdes**.
- Llamada directa:
  - `make_conninfo('postgresql://u:p@h/db?sslmode=disable', dbname='otra')` → `user=u password=p dbname=otra host=h sslmode=disable` — la opción `?sslmode=disable` sobrevive al reemplazo de dbname.
  - Forma `keyword=value` con `connect_timeout=5`: también conservada. Correcto; `rsplit('/')` habría roto ambos casos.

## 3. Punto 3 (H4 segunda conexión)
- Verde limpio: ambos tests pasan.
- Rojo con mutante «aviso dentro del sello» harvest (`app/apply_harvest.py`, aviso indentado dentro del `with conn.transaction():`): `E AssertionError: ronda H4: el rastro del sello ya es visible al avisar` / `assert ((False,) is not None and False is True)`.
- Rojo con mutante negative (`app/apply_cola.py:823` dentro del `with conn.transaction():`): `E AssertionError: ronda H4: el veredicto del sello ya es visible al avisar` / `'applying' == 'applied'`.
- Opinión sobre la segunda conexión: el patrón es seguro. `psycopg.connect(...)` va fuera del try (si falla la apertura no hay nada que cerrar), y el `finally: otra.close()` cierra incluso si el SELECT revienta — no quedan conexiones abiertas. Es `autocommit=True` y solo lee datos ya commiteados, así que nunca espera locks ni compite con la tx del fixture; no puede colgarlo. Refuerzo legítimo: con autocommit, `IDLE` solo prueba ausencia de tx explícita, y la lectura desde otra conexión pinea la persistencia real.

## 4. Diff de la ronda
`git diff --stat 224d5d9..d291dd4`: solo `docs/evidencia/fabrica-02/R.1/mutantes.md`, `tests/test_fabrica_f2_biblioteca.py`, `tests/test_reversa_harvest.py`. No toca `app/`, `tools/`, `migrations/` ni `ops/`. Ningún test borrado ni relajado: solo se añaden asserts y el caso heredado de H2 se vuelve independiente (fortalece). El ledger de mutantes quedó actualizado y coincide con lo observado al ejecutar. Sin hallazgos.

## 5. Veredicto
**APPROVE sobre d291dd4.** Los tres puntos verifican ejecutando: mutante H2 ahora cae, make_conninfo conserva opciones, mutantes H4 caen con los mensajes nuevos, y la segunda conexión no deja recursos abiertos.

`git status --short` final: vacío.



# Veredicto del lead

**APPROVE sobre `d291dd4`**, publicado como comentario en el PR #278 (GitHub no permite aprobar un PR de la misma cuenta). El PR queda en cola hasta el momento de merge del bloque 3; la fila R.1 se cierra en el PR de cierres con este SHA.
