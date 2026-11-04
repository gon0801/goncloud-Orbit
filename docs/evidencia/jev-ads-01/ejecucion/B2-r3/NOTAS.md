# E/B2-r3 — correccion del bloqueante B3 (VEREDICTO-B2-r2)

Fecha UTC: 2026-10-04T00:46:05Z (ACK) a cierre. Rama `docs/jev-ads-b2`,
partiendo limpia de `04281aa0e5c425818e5abcdae9c1f034bd9da301` (verificado
por el veredicto; ahead 6 de origin/master `afc1f3b`). Un solo escritor;
SIN push (lo hace Claw tras VEREDICTO: APROBADO).

## SHA

| Commit | Contenido |
| --- | --- |
| `315ff5191f61ae51a05bb7596467b975020d0dcc` | B3: missing_state SOLO para miembros con anuncios; la repro pasa a regresion con mutante |

HEAD final de CODIGO (40): `315ff5191f61ae51a05bb7596467b975020d0dcc`
(los commits posteriores, si los hay, son SOLO evidencia documental).

## B3: rojo -> verde

- ROJO: la repro del veredicto corrida contra `04281aa`
  (`jev_b2_r3_rojo.txt`): `AssertionError: esperado NingunoCompatible,
  salio Indeterminado(motivos=frozenset({'missing_state'}))` — salida
  identica a la observada por el revisor.
- CAUSA: `_con_estado_ausente` devolvia True con `estados == ()`, asi que
  todo miembro sin anuncios aportaba `missing_state`.
- ARREGLO (`app/jev_ads.py`): `_con_estado_ausente(miembro)` ahora
  devuelve False si `anuncio_ids` esta vacio (un miembro sin anuncios no
  tiene estado que falte); con anuncios, falta estado si `estados` esta
  vacio o algun `status` es None. Un miembro sin anuncios tampoco es
  `no_anunciado` (con `estados == ()`, `_solo_no_activo` ya devolvia
  False). Docstring de `componer` actualizado con la regla afinada.
- VERDE: la repro imprime `NingunoCompatible(miembros_totales=2)` sin
  error; focalizadas `40 passed`
  (`jev_b2_r3_verde_focalizadas.txt`: 21 de jev_ads + 19 de catalogo).
- REGRESION integrada:
  `tests/test_jev_ads.py::test_plan_sin_anuncios_con_todo_no_satisface_da_ninguno_compatible`
  (universo exhaustivo del plan de fabrica, miembros con `anuncio_ids=()`,
  fichas de todos y `no_satisface` en cada par → `NingunoCompatible`).
- MUTANTE demostrado en vivo: guard desactivado (`if False:` = conducta
  r2, estados vacios vuelven a contar como missing_state) → la nueva
  prueba `1 failed`; guard restaurado → `1 passed`. Diff verificado sin
  residuos del mutante.

## Residual NO bloqueante (anotado, NO corregido aqui)

`jev_revision.created_at` conserva `DEFAULT now()` (inicio de la
transaccion). Con captura de censo dentro de la misma tx, una fila VALIDA
puede quedar con `captured_at > created_at` (el trigger ya no la rechaza
desde B2-r2: compara contra `clock_timestamp()`, pero la columna guardada
muestra el orden invertido). Pendiente de fila de plan ANTES del
despliegue de 2.3 / cierre B5: cambiar el DEFAULT a `clock_timestamp()` en
una migracion propia (0049 ya no se edita una vez desplegada). Este
bloque NO toco `migrations/0049_jev_ads.sql` ni `app/jev_catalogo.py`.

## Comandos

```
PYTHONPATH=. uv run --frozen python /Users/dn/.local/state/jev-ads-01-loop/repro-B2-r2/repro_plan_sin_anuncios.py   # rojo, luego verde
uv run --frozen python -m pytest tests/test_jev_ads.py tests/test_jev_catalogo.py -q   # 40 passed
uv run --frozen python -m pytest tests/test_jev_ads.py::test_plan_sin_anuncios_con_todo_no_satisface_da_ninguno_compatible -q
  con mutante: 1 failed | sin mutante: 1 passed
uv run --frozen python -m pytest -q   # 3,787 passed, 1 warning in 215.80s (bateria, una vez sobre 315ff51)
uv run ruff check .                   # All checks passed!
pre-commit run --all-files            # 9 hooks Passed
git commit sin --no-verify            # hooks en verde
```

## Limites respetados

- Archivos tocados: SOLO `app/jev_ads.py`, `tests/test_jev_ads.py` y esta
  evidencia (los permitidos por el encargo). 0049 y `jev_catalogo.py`
  intactos; `cycle.py`/`apply_cola.py`/`apply_harvest.py` intactos.
- Cero llamadas a TypeSafe/Jev; cero claves; sin secretos.
- Sin push ni PR: commit local `315ff51`; arbol limpio.
  El push lo hace Claw tras VEREDICTO: APROBADO.

## Skills/agentes/modelos

- Sesion principal opencode: `zai-coding-plan/glm-5.3-flash`, un solo
  escritor, dueno del diff.
- Skills activas en la sesion (cargadas en B2-r1 y vigentes):
  poteto-mode (playbook Feature, skips razonados), unslop, tdd,
  typesafe-ai + guia pstack-opencode. B3 es arreglo pautado: sin
  delegacion (contrato de un-escritor del loop) y sin paneles (no hay
  diseño contestado).
- TDD aplicado: rojo de la repro registrado, verde con regresion
  definitiva, mutante matado y demostrado.
