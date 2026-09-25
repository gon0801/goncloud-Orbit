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
