# Re-review C.4, ronda 4 (revisor independiente)

- **Objeto:** `origin/feat/ads-proteccion-c4` @ `74f49b0` (draft PR #342, ADS PROTECCION C.4, proposal-only).
  Delta: `eaf45fb..74f49b0` = `1980832` (docs: review ronda 3) + `74f49b0` (fix BN2: 3 archivos, 7+/5-).
- **Método:** leí el diff (`git diff eaf45fb 74f49b0`). Comprobé que la copia `/tmp/rv-c4` es idéntica a `git archive 74f49b0` (con `diff -rq`, solo le sobra `.claude/`). Corrí los 2 tests de BN2 en `74f49b0` y en `eaf45fb` (árbol exportado a un tmp, para ver si discriminan). Corrí la suite C.4 focalizada y `ruff check`. Revisé el run de CI con `gh`, solo lectura.
- **Fecha:** 2026-09-25

## Bloqueantes

Ninguno.

## Observaciones (no bloqueantes)

1. **Fallo local que es del entorno, no del código:** `tests/test_ui.py::test_uv_lock_commiteado_y_jinja2_pinneada` falla en `/tmp/rv-c4` porque la copia no tiene `.git`. `git ls-files` sale con `returncode=128`, `fatal: not a git repository`.
   En el objeto el archivo sí está trackeado (`git ls-tree 74f49b0 uv.lock` → `100644 blob 50f0f59…`) y en CI (checkout real) pasa. No es regresión del delta.
2. **La rama va atrás de master y mezclarla da conflicto (fuera del delta):** merge-base = `f98b30a`, pero `origin/master` ya está en `25bded0` (`f31058d` + #343).
   Una mezcla de prueba (`git merge-tree <mb> origin/master 74f49b0`) da **2 conflictos en `app/optimizer/goals.py`**: la constante y la función del flag B.2a (#339, `CLAVE_SETTING_PAUSE_SIN_COOLDOWN_BID` / `pause_sin_cooldown_bid_desde_settings`) chocan con las de C.4 (`CLAVE_SETTING_PROPUESTAS_CAMPANA` / `propuestas_campana_desde_settings`). Son adiciones contiguas y se resuelven dejando ambas. `app/cycle.py` cambió en los dos lados pero se mezcla solo.
   El verde de `74f49b0` **no certifica** el resultado mezclado. Antes de sacar el PR de draft hay que actualizar la rama y volver a correr Quality sobre el SHA mezclado. → fila del plan.
3. **Transparencia:** para ver el delta hice `git fetch origin feat/ads-proteccion-c4 master` en `/Users/dn/dev/goncloud-Orbit`, que actualiza refs remotas y `FETCH_HEAD`. Exporté árboles a `/tmp/rv4-tree.2Alj` y `/tmp/rv4-eaf.l1hM`, que puedes borrar. En `/tmp/rv-c4` no edité nada; pytest y ruff corrieron sin cache (`-p no:cacheprovider`, `--no-cache`).

## Lo verificado OK

**BN2 (a): `_CLAVES_SALUD_PREVIAS` incluye `avisos_propuesta`.** Está en `tests/test_precio_pantalla.py:555`, y lo emite `app/api_dashboard.py:933`. El test sigue comparando **igualdad exacta de conjuntos** (`tests/test_precio_pantalla.py:807-811`), así que una clave de más o de menos lo sigue tumbando. El fix no lo debilitó.

**BN2 (b): la copia visible de `cortes.html` usa `ñ`.**
- `app/templates/cortes.html:100` `Campañas`
- `:101` `campañas`
- `:106` `<th>Campaña</th>`

El único `campana` en ASCII que queda está en la `:96`, dentro de un comentario Jinja `{# … #}`, y el candado #178 lo excluye a propósito (`tests/test_ui_copy_campana.py:9,17`). La aserción de `tests/test_ui.py:223` (`"Campañas" in html`) quedó alineada.

**Los 2 tests de BN2, antes y después del fix (sí discriminan):**
```
# 74f49b0 (/tmp/rv-c4)
$ python -m pytest -q tests/test_precio_pantalla.py::test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves tests/test_ui_copy_campana.py
PASSED ...::test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves
PASSED ...::test_templates_sin_campana_ascii_en_copia_visible
PASSED ...::test_motivos_es_sin_campana_ascii
3 passed, 1 warning in 0.54s

# eaf45fb (árbol exportado, sin el fix)
FAILED ...::test_precio_pantalla_salud_agrega_precios_sin_cambiar_claves  (Extra items: 'avisos_propuesta')
FAILED ...::test_templates_sin_campana_ascii_en_copia_visible            (['cortes.html…html:Campana'] != [])
2 failed, 1 passed
```

**Suite C.4 focalizada (Postgres 127.0.0.1:5432, DSN default):**
```
$ python -m pytest -q tests/test_propuestas_campana.py tests/test_api_dashboard.py tests/test_api.py \
    tests/test_notifica.py tests/test_ui.py tests/test_precio_pantalla.py tests/test_ui_copy_campana.py
1 failed, 292 passed, 1 warning in 15.79s
```
El único fallo es el de entorno de la observación 1 (sin `.git`).
```
$ ruff check --no-cache .
All checks passed!
```

**CI: Quality por dispatch sobre `74f49b0`, verde total.**
- `gh run view 36095608356`: `workflow_dispatch`, `headSha=74f49b0e6e7c377e9e45e9282cd5fbacb7566e28`, `conclusion=success`.
- Los 4 jobs en success: `rapido`, `completa`, `pesada`, **`gate`** (`needs: [rapido, completa, pesada]`).
- `completa`: `pytest -q` → **`3321 passed, 1 warning in 433.34s`**, cero skipped. En el run anterior sobre `eaf45fb` (`36094417038`) fueron `2 failed, 3319 passed`, con los mismos 2 tests de BN2. El total es el mismo (3321), así que no se quitó ni se saltó ningún test.
- `rapido`: `107 passed`. `pesada`: `verify/` `20 passed`.
- pre-commit (`pre-commit/action`) corrió en `rapido` y también en `completa`, sobre todo el repo, y salió verde: ningún candado roto.

**Alcance del fix:** solo toca `cortes.html` (copia), la tupla esperada de un test y una aserción y un docstring de `test_ui.py`. No cambia lógica de `app/`, ni los umbrales, ni el comportamiento proposal-only. No hay regresión atribuible al delta.

VEREDICTO: APROBADO

---

# Confirmacion rebase C.4 (revisor independiente)

- **Objeto:** `origin/feat/ads-proteccion-c4` @ `fcfd318` (draft PR #342, ADS PROTECCION C.4, proposal-only), rebasado sobre `origin/master` @ `25bded0`. Punta previa: `74f49b0` (ronda 4 APROBADO).
- **Metodo:** claude opus, revisor independiente (no es autor del cambio). Alcance limitado a (1) la resolucion del conflicto y (2) la validez de la punta; no se re-reviso todo C.4. Trabaje solo en lectura: el unico cambio al repo fue `git fetch origin master feat/ads-proteccion-c4` en `/Users/dn/dev/goncloud-Orbit`. No toque `/tmp/rv-c4-rb` y el worktree C.4 quedo limpio despues de las pruebas (`git status --porcelain` = 0).
- **Fecha:** 2026-09-25

## Bloqueantes

Ninguno.

## Observaciones (no bloqueantes)

1. **El run `pull_request` 36097024815 (mismo SHA `fcfd318`) no corre la bateria.** Como el PR es draft, se saltan `completa` y `pesada` (`skipped`) y aun asi `gate` sale `success`. El rollup del PR muestra `rapido=SUCCESS, completa=SKIPPED, pesada=SKIPPED, gate=SUCCESS, review=SKIPPED, CodeRabbit=SUCCESS`. La evidencia de la bateria completa sobre `fcfd318` viene solo del `workflow_dispatch` 36097030041. Sirve mientras el SHA no cambie; si se mueve antes del merge, hay que volver a despachar.
[Errata 2026-09-26: los `skipped` son por tipo de evento (`quality.yml`: un `pull_request` nunca corre `completa`/`pesada`), no por draft; ver `C.4/review-c4-rb2-opus.md:17`.]
2. **`review=SKIPPED`:** el revisor automatico con IA que entro con master #343 no corrio porque el PR es draft. No afecta la validez de la punta.

## Lo verificado OK

### 1. Resolucion del conflicto (`app/optimizer/goals.py`)

- **Sin marcas de conflicto:** `git show fcfd318:app/optimizer/goals.py | grep -E '^(<<<<<<<|=======|>>>>>>>)'` no devuelve nada. `git grep -E '^(<<<<<<<|>>>>>>>)( |$)' fcfd318` en todo el arbol tampoco.
- **Contra master `25bded0`:** `git diff 25bded0 fcfd318 -- app/optimizer/goals.py` = **+9 −0**. Solo agrega `CLAVE_SETTING_PROPUESTAS_CAMPANA = "ads_propuestas_campana"` (linea 116, debajo de `CLAVE_SETTING_PAUSE_SIN_COOLDOWN_BID`) y `propuestas_campana_desde_settings()` (despues de `pause_sin_cooldown_bid_desde_settings`). Nada de master se borra ni se altera.
- **Flag B.2a de master intacto:** `CLAVE_SETTING_PAUSE_SIN_COOLDOWN_BID = "ads_pause_sin_cooldown_bid"`, `pause_sin_cooldown_bid_desde_settings()` (fail-closed `is True`, docstring con el INSERT de H5) y `en_cooldown(..., kind=None)` con el SQL `confirmed_at <= %s` / `d.kind = %s::decision_kind` quedan tal cual estan en master.
- **Contra `74f49b0`:** `git diff 74f49b0 fcfd318 -- goals.py` = **+27 −3**, y es exactamente el delta que master mete en ese archivo. Lo comprobe asi: `diff <(git diff f98b30a 25bded0 -- goals.py | grep '^[+-]') <(git diff 74f49b0 fcfd318 -- goals.py | grep '^[+-]')` no muestra diferencias. La superficie C.4 (`CLAVE_SETTING_PROPUESTAS_CAMPANA` + `propuestas_campana_desde_settings`, fail-closed `is True`) queda identica.
- **`git range-diff f98b30a..74f49b0 25bded0..fcfd318`:**
  - 6 de 7 commits salen `=`, sin cambios.
  - Solo `3e97a54` (fix B1/B2/B3) sale `!`, y lo unico que cambia es el contexto del hunk en goals.py: ahora la clave C.4 queda despues de la clave B.2a y la funcion C.4 despues de `pause_sin_cooldown_bid_desde_settings`.
  - `fcfd318` es un commit nuevo que solo toca `.saikit/scratch/ads-proteccion-1/review-c4-opus.md`.
- **El otro archivo tocado por los dos lados (`app/cycle.py`)** se automergeo sin mezclar lados. El parche C.4 antes y despues del rebase es identico (`diff` de `f98b30a..74f49b0` contra `25bded0..252ec3c` vacio). El delta de master antes y despues del rebase tambien es identico.
- **En todo el arbol**, el parche C.4 antes y despues del rebase solo difiere en las lineas de contexto de goals.py de arriba.
- **Migraciones:** `0042_ads_campaign_proposal.sql` (C.4, ya estaba en `74f49b0`) no choca con `0040_ads_report_result.sql` de master. Los prefijos duplicados `0011` y `0031` ya existian en master.
- **Copias de revision:** comparando el hash de cada blob, `/tmp/rv-c4-rb` coincide con `fcfd318` (845 archivos, 0 diferencias) y `/tmp/rv-c4-r4base` coincide con `74f49b0` (828 archivos, 0 diferencias).

### 2. La punta es valida (local)

Cwd `/Users/dn/dev/wt-orbit-ads-c4`, HEAD = `fcfd31874b66d479995d82c144b13c6499f4176f`, arbol limpio. `.venv` del worktree. Postgres `127.0.0.1:5432 - accepting connections`.

- **Focalizados C.4:**
  - `.venv/bin/python -m pytest -p no:cacheprovider -q -rs tests/test_propuestas_campana.py tests/test_api_dashboard.py tests/test_api.py tests/test_notifica.py tests/test_ui.py tests/test_precio_pantalla.py tests/test_ui_copy_campana.py`
  - Resultado: **293 passed**, 0 skipped (con `-rs` no aparece ningun skip, asi que las pruebas con Postgres si corrieron), 1 warning (deprecacion httpx/starlette, ajena al cambio).
- **B.2a:**
  - `.venv/bin/python -m pytest -p no:cacheprovider -q -rs tests/test_optimizer_goals.py tests/test_cycle_pause_cooldown.py`
  - Resultado: **47 passed**.
- **Ruff 0.16.4:**
  - `.venv/bin/ruff check --no-cache .` → `All checks passed!`
  - `.venv/bin/ruff format --no-cache --check app/optimizer/goals.py` → `1 file already formatted`.

### 3. CI (gh en solo lectura)

- **Run Quality `workflow_dispatch` 36097030041:** headSha `fcfd318`, `completed/success`. Los 4 jobs salen `success`: `rapido`, `pesada` (verify/ `20 passed`), `completa` y `gate`.
- **Conteo de `completa`:** **3340 passed, 0 failed** (`3340 passed, 1 warning in 312.53s`; ninguna linea `FAILED`/`ERROR` en el log).
- **Cuadre del conteo:** el esperado es 3321 + el delta de master.
  - `74f49b0` (dispatch 36095608356): 3321.
  - Master `25bded0` (push 36095114172): 3312.
  - Base previa `f98b30a` (push 36020342391): 3293.
  - Delta de master = 3312 − 3293 = **19**, asi que 3321 + 19 = **3340**. Tambien cuadra al reves: 3312 (master) + 28 (C.4) = 3340. No se perdio ni se duplico ninguna prueba.
- **Run `pull_request` 36097024815 (mismo SHA):** termino `success`, pero con `completa`/`pesada` en `skipped` (draft). Solo cuenta como evidencia extra de `rapido`+`gate`; ver Observacion 1.

### 4. Mergeabilidad

`gh pr view 342 --json mergeable,mergeStateStatus` → `"mergeable":"MERGEABLE"`, `"mergeStateStatus":"CLEAN"`. `headRefOid` = `fcfd318`, base `master`, `isDraft: true`, `state: OPEN`.

VEREDICTO: APROBADO
