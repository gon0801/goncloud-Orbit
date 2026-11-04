# Cross-review rebase3 C.4 (revisor independiente)

- **Objeto:** `origin/feat/ads-proteccion-c4` @ `cb193a1` (PR #342, ADS PROTECCION C.4, proposal-only), rebasado sobre `origin/master` @ `024c1f7` (merge de #341 C.3). Punta previa: `09ff20d` (base `b3b2c8e`). `git ls-remote` confirma que la rama apunta a `cb193a1`.
- **Estado del PR al revisar:** **#342 ya está MERGED.** Lo mergeó gon0801 a las 06:52:11Z con el merge commit `787869a` (padres `024c1f7` y `cb193a1`). El árbol de `787869a` es `225076d`, el mismo que el de `cb193a1`. Según el timeline, salió de draft a las 06:49:39Z (`ready_for_review`) y el force-push de `cb193a1` fue a las 06:50:48Z. Esta revisión es entonces post-merge, pero el árbol revisado es exactamente el que quedó en master.
- **Método:** claude opus, revisor independiente (no es autor del cambio). Alcance: (1) la resolución del conflicto contra C.3 y (2) la validez de la punta. No re-revisé todo C.4; la referencia es R4 APROBADO sobre `74f49b0` y las confirmaciones de rebase APROBADO.
  - Git en solo lectura sobre `/Users/dn/dev/goncloud-Orbit`: `merge-tree`, comparación de las líneas `+/-` de cada parche por archivo antes y después del rebase, y `range-diff`. El único cambio al repo fue `git fetch origin`, que actualiza refs remotas y `FETCH_HEAD`.
  - `/tmp/rv-c4-rb3` no es un repo git. Lo comparé con `git archive cb193a1` usando `diff -rq`: coincide, salvo los `.claude/` (ver obs. 5).
  - Focalizados y `ruff` con el `.venv` de `/Users/dn/dev/wt-orbit-ads-c4`. Su HEAD es `cb193a1` y `git status --porcelain` da 0 antes y después.
  - `gh` solo en lectura. **No corrí la batería completa local**, porque esta corrida lo prohíbe.
- **Fecha:** 2026-09-25

## Bloqueantes

Ninguno.

## Observaciones (no bloqueantes)

1. **El PR se mergeó antes de cerrar esta revisión y con checks abiertos.** Al momento del merge (06:52:11Z):
   - `AI review` (run 36104734617) seguía `in_progress`. Se disparó cuando el PR salió de draft.
   - CodeRabbit seguía `PENDING`.
   - Este cross-review no se había entregado.
   - El merge llegó 45 s después de que terminara el run `pull_request` de Quality (06:51:26Z), y ese run no incluye la batería completa (ver obs. 2).

   No encontré defecto, así que no bloquea, pero el orden del proceso quedó invertido: la regla pide la batería completa sobre el commit final antes de mergear, de preferencia en CI por PR. → fila del plan / decide el operador.
2. **Batería completa sobre el árbol combinado C.3+C.4.**
   - El run `pull_request` 36104734587 salta `completa` y `pesada` por tipo de evento (`quality.yml:41` y `:79`), no por draft.
   - El run `push` a master sobre `024c1f7` (36104600524, solo C.3) quedó **`cancelled`**: la concurrencia (`quality-${{ ... || github.ref }}`) lo tumbó cuando llegó `787869a`. Ahí `Run Python tests` = cancelled y `gate` = failure. O sea que C.3 por sí solo nunca terminó la batería en master.
   - La primera batería completa sobre C.3+C.4 es el run `push` 36104837829 sobre `787869a`, que tiene el mismo árbol que `cb193a1`. **Salió verde: 3394 passed** (ver "Lo verificado OK", punto 5). Pero corrió después del merge, no antes.
   - `pesada` (`verify/`) sale `skipped` en `push`. Corre en el `schedule` nocturno (04:00 UTC) o por `workflow_dispatch`.
3. **`gh pr view 342 --json mergeable,mergeStateStatus` da `UNKNOWN`/`UNKNOWN`** porque el PR ya está cerrado por merge; GitHub no calcula mergeabilidad para PRs cerrados. La REST da `mergeable=null` y `mergeable_state=unknown`. El equivalente local es limpio: `024c1f7` es ancestro de `cb193a1` (10 commits encima) y `git merge-tree --write-tree 024c1f7 cb193a1` da `225076d`, que es el árbol de `cb193a1` (fast-forward). Además, el árbol de `787869a` en master es idéntico.
4. **Hay que invocar pytest con `PYTHONPATH=.`**, igual que CI. Es tema del entorno, no del cambio.
5. **Transparencia:**
   - Los `.claude/` que hay en `/tmp/rv-c4-rb3/` y en `/tmp/rv-c4-rb3/.saikit/scratch/ads-proteccion-1/` son estado de hooks de la sesión (`sessions/`, `state/`). No están versionados y no son contenido del objeto; se pueden borrar.
   - Temporales míos, también borrables: `/tmp/rb3-arch.TcVE` (export de `cb193a1`), `/tmp/rb3-*.txt` y `/tmp/rb3-watch.log`.

## Lo verificado OK

1. **Sin marcas de conflicto.** `git grep -nE '^(<<<<<<<|>>>>>>>)( |$)|^=======$' cb193a1` no devuelve nada.
2. **`app/optimizer/goals.py`: los dos flags están intactos.**
   - `git merge-tree --write-tree 09ff20d 024c1f7` reproduce el choque: `CONFLICT (content)` solo en `app/optimizer/goals.py`. `app/cycle.py`, `tests/test_cycle.py` y `tests/test_cycle_apply.py` se mezclan solos. Esos 4 archivos son exactamente la intersección C.3∩C.4.
   - En `cb193a1`, las constantes están en `goals.py:114-117`, en este orden: `CLAVE_SETTING_MODO`, `CLAVE_SETTING_PAUSE_SIN_COOLDOWN_BID`, **`CLAVE_SETTING_PAUSE_ECONOMICA = "ads_pause_economica"`** (C.3) y **`CLAVE_SETTING_PROPUESTAS_CAMPANA = "ads_propuestas_campana"`** (C.4). Las funciones: `pause_sin_cooldown_bid_desde_settings` (:424), **`pause_economica_desde_settings`** (:440, C.3) y **`propuestas_campana_desde_settings`** (:448, C.4). Las claves son distintas y las dos son fail-closed (`is True`).
   - `diff 024c1f7 cb193a1 -- goals.py` da +9/-0, exactamente las líneas de C.4, las mismas que `b3b2c8e..09ff20d`. `diff 09ff20d cb193a1 -- goals.py` da +9/-0, exactamente las líneas de C.3, las mismas que `b3b2c8e..024c1f7`. Ningún lado pierde ni duplica líneas del otro.
3. **Parches antes y después, y `range-diff`.**
   - En los **22 archivos de C.4**, las líneas `+/-` son **idénticas** en `b3b2c8e..09ff20d` y en `024c1f7..cb193a1`. El conjunto de archivos es el mismo.
   - En los **13 archivos de C.3**, las líneas `+/-` son **idénticas** en `b3b2c8e..024c1f7` y en `09ff20d..cb193a1`. Los archivos que cambian en `09ff20d..cb193a1` son exactamente los de C.3.
   - `git range-diff b3b2c8e..09ff20d 024c1f7..cb193a1` da 10 contra 10: 9 `=` y 1 `!`. El `!` es `15309a4 → 9bb7150` (fix c4 B1/B2/B3) y solo cambia contexto en `goals.py`: aparece `CLAVE_SETTING_PAUSE_ECONOMICA` como línea de contexto, y la cabecera de hunk ahora es `pause_economica_desde_settings` en lugar de `pause_sin_cooldown_bid_desde_settings`. Ninguna línea `+/-` de C.4 cambia.
   - **`app/cycle.py` (auto-merge), revisado en semántica.** C.4 usa estos puntos, todos detrás de `propuestas_campana_desde_settings`: `lee_evaluaciones` en `_recorre_plataforma` (:1878), `guarda_evaluaciones` en TX3 (:2378), avisos (:2409) y `telegram["aviso_propuesta"]` (:2457). C.3 agrega el parámetro `pause_economica` (:1907/:1930) y llama a `_mezcla_evidencias_persistidas` (:2462) antes de `_sella_apply`. Escriben claves distintas del cuerpo (`propuestas_campana` y `telegram.aviso_propuesta` contra `apply.revalidaciones_economicas`), así que no se pisan. El orden también es coherente: el aviso C.4 marca `telegram` y eso lleva al bloque de sello, que corre la mezcla C.3 y después sella.
4. **La punta es válida.** Todo esto se corrió en el worktree en `cb193a1`, con Postgres 127.0.0.1:5432 respondiendo (`pg_isready`: accepting connections).
   - `PYTHONPATH=. .venv/bin/pytest -p no:cacheprovider -q -rs` sobre los 12 pedidos (test_pause_economica, test_apply_cola, test_ads_salud, test_propuestas_campana, test_api_dashboard, test_api, test_notifica, test_ui, test_precio_pantalla, test_ui_copy_campana, test_optimizer_goals, test_cycle_pause_cooldown) da **419 passed, 0 skipped**, exit 0, en 18.2 s.
   - Extra, por los archivos auto-mezclados o que toca C.4: test_cycle, test_cycle_apply, test_api_write y test_fabrica_f2 dan **98 passed**, exit 0.
   - Los dos flags tienen su prueba fail-closed y las dos corrieron verdes: `tests/test_optimizer_goals.py:924-929` (C.3) y `tests/test_propuestas_campana.py:392-397` (C.4).
   - `ruff check --no-cache .` (0.16.4) da "All checks passed!". El ruff pineado de pre-commit también pasó en CI (`rapido`).
5. **CI.**
   - **Run Quality `pull_request` 36104734587**: `headSha` `cb193a1`, conclusion **`success`**. Los jobs `rapido` y `gate` salen success. Todos los pasos de `rapido` salen success: candado de frescura del contexto de chat, pre-commit (higiene + ruff + contexto) y guardas (test_architecture, test_precommit_hooks, test_chat_context_guard). `completa` y `pesada` salen `skipped` por evento.
   - **Batería completa, run Quality `push` 36104837829** sobre `787869a`, cuyo árbol `225076d` es idéntico al de `cb193a1`: conclusion **`success`**.
     - `completa` success, de 06:52:38Z a 06:58:25Z.
     - `Run pre-commit (todos los candados locales, sobre todo el repo)` success: ruff check, ruff format y el presupuesto de contexto salen Passed.
     - `Run Python tests`: **`3394 passed, 1 warning in 318.62s`**, sin failed ni skipped.
     - `gate` success.
     - `rapido` y `pesada` salen `skipped` por evento `push`.
     - Las líneas `ERROR`/`Failing row` del log son del contenedor Postgres: vienen de las pruebas negativas de constraints, no son fallos de pytest.
     - Contra la referencia: en `74f49b0` (C.4 sin A.3 ni C.3) eran 3321. El total sube; no se perdió ningún test.
   - `AI review` 36104734617 seguía `in_progress` al cerrar este reporte.
6. **`gh pr view 342`**: `state=MERGED`, `isDraft=false`, `headRefOid=cb193a1`, `baseRefOid=024c1f7`, `mergeable=UNKNOWN` y `mergeStateStatus=UNKNOWN`, estos dos por estar mergeado (ver obs. 3).

VEREDICTO: APROBADO
