# Cross-review rebase2 C.4 (revisor independiente)

- **Objeto:** `origin/feat/ads-proteccion-c4` @ `36266fd` (draft PR #342, ADS PROTECCION C.4, proposal-only), rebasado sobre `origin/master` @ `b3b2c8e` (#340 A.3). Punta previa: `9aaedff` (base `25bded0`). `git ls-remote` confirma que los dos refs remotos apuntan a esos SHAs.
- **Metodo:** claude opus, revisor independiente (no es autor del cambio). Alcance: (1) la resolucion de conflictos contra #340 y (2) la validez de la punta. No re-revise todo C.4; la referencia es R4 APROBADO sobre `74f49b0` y la confirmacion de rebase APROBADO sobre `fcfd318`.
  - Git en solo lectura sobre `/Users/dn/dev/goncloud-Orbit`: `merge-tree`, diff de las lineas `+/-` de cada parche antes y despues del rebase, archivo por archivo, y `range-diff`.
  - `/tmp/rv-c4-rb2` no es un repo git. Lo compare con `git archive 36266fd`: coincide byte a byte (solo difiere `.claude/`, que no esta versionado). No lo modifique.
  - Focalizados y `ruff` con el `.venv` de `/Users/dn/dev/wt-orbit-ads-c4`. Su HEAD es `36266fd` y el arbol queda limpio antes y despues (`git status --porcelain` = 0).
  - `gh` solo en lectura. **No se corrio la bateria completa local**, porque esta corrida lo prohibe.
- **Fecha:** 2026-09-25

## Bloqueantes

Ninguno.

## Observaciones (no bloqueantes)

1. **El run CI 36103443731 NO incluye la bateria completa, y los carriles no se saltan por draft sino por tipo de evento.** En `quality.yml`, `rapido` corre con `pull_request`/`workflow_dispatch`, `completa` (el `pytest -q` entero) con `push` a main/master/`workflow_dispatch` y `pesada` con `schedule`/`workflow_dispatch`. En un evento `pull_request`, `completa` y `pesada` quedan `skipped` aunque el PR no sea draft. Lo unico que se salta por draft es el workflow `AI review` (run 36103443679, `if: !github.event.pull_request.draft`). Consecuencia: el arbol combinado A.3+C.4 (`36266fd`) no ha pasado la bateria entera en ningun lado. Las rondas anteriores la corrieron sobre el arbol sin A.3. Hoy la evidencia son los 351 focalizados verdes, que cubren los modulos de test de los 4 archivos en conflicto, mas `rapido`. No bloquea: el rebase es solo de contexto (ver abajo), 0041 y 0042 crean objetos disjuntos (`ads_ingest_incident` y `ads_campaign_proposal`) y el diseno del repo corre `completa` en el push a master. Sugerencia barata antes de quitar el draft: `gh workflow run Quality --ref feat/ads-proteccion-c4` (con `workflow_dispatch` corren `rapido`, `completa` y `pesada` sobre `36266fd`). Asi la bateria se ve verde antes del merge y no despues.
2. **Hay que invocar pytest con `PYTHONPATH=.`**, igual que CI. `.venv/bin/pytest` a secas falla en la coleccion con `ModuleNotFoundError: No module named 'app'`, y fallarian los 10 archivos. Es un tema de entorno, no del cambio. Los focalizados de abajo se corrieron con `PYTHONPATH=.`.
3. **`ruff format --check` con el ruff del venv (0.16.4) marca 2 archivos.** Son `plans/brief-sp-api-01-a5-ronda-review.md` y `plans/fabrica-01.md`, ambos markdown. Ninguno lo toca C.4 y los mismos 2 ya aparecen en la base `b3b2c8e`. Con el ruff pineado en pre-commit (v0.15.20), `ruff check` pasa y `ruff format --check` da "241 files already formatted". Son preexistentes y no vienen de este PR. Queda anotado por la diferencia de version entre el venv y el pin.

## Lo verificado OK

1. **Sin marcas de conflicto** en el arbol `36266fd`: `git grep -nE '^(<<<<<<<|>>>>>>>)( |$)|^=======$' 36266fd` no devuelve nada (la base `b3b2c8e` tampoco).
2. **Choque #340 vs C.4, reproducido y resuelto.** `git merge-tree --write-tree 9aaedff b3b2c8e` reproduce el choque original: CONFLICT (content) en `app/api_dashboard.py` y `tests/test_precio_pantalla.py`, y auto-merge en `app/templates/salud.html` y `tests/test_ui.py`. Son exactamente los 4 archivos que tocan los dos lados.
   - Para cada uno de los 4 archivos, las lineas `+/-` del **parche C.4 son IDENTICAS** antes (`25bded0..9aaedff`) y despues (`b3b2c8e..36266fd`). Las lineas `+/-` del **parche A.3 son IDENTICAS** en `25bded0..b3b2c8e` y en lo que A.3 aporta sobre la punta (`9aaedff..36266fd`). Ningun lado borra ni duplica lineas del otro.
   - `app/api_dashboard.py`: estan los dos imports, `from app.ads.salud import bloque_salud as bloque_ads_ingest` (A.3) y `from app.api import KINDS_DECISION, ConexionLectura, propuestas_campana_visibles` (C.4). En `salud()` estan las dos claves, `"avisos_propuesta": _avisos_propuesta_de(...)` (C.4) y `"ads_ingest": _ads_ingest_de(...)` (A.3). Estan las dos funciones: `_ads_ingest_de` (A.3), seguida de `_SQL_AVISOS_PROPUESTA_PENDIENTES` y `_avisos_propuesta_de` (C.4). `cortes()` conserva `propuestas_campana` (C.4).
   - `tests/test_precio_pantalla.py`: `_CLAVES_SALUD_PREVIAS` trae `"ads_ingest"` (A.3) y `"avisos_propuesta"` (C.4).
   - `app/templates/salud.html`: estan las dos secciones, "Ingesta Ads principal" (`datos.ads_ingest`, A.3) y "Avisos de propuesta" (`datos.avisos_propuesta`, C.4).
   - `tests/test_ui.py`: estan `test_ui_salud_ads_ingest_muestra_fecha_y_entrega_pendiente` (A.3), `test_ui_cortes_pinta_propuestas_campana_y_escapa_nombre` (C.4, con "Campañas") y `test_ui_salud_muestra_avisos_propuesta_pendientes_y_fallo` (C.4).
   - El conjunto de archivos que toca C.4 es el mismo antes y despues del rebase.
3. **`git range-diff 25bded0..9aaedff b3b2c8e..36266fd`** da 9 contra 9 commits. 7 salen `=` y 2 salen `!`: `15309a4` (fix c4 B1/B2/B3) y `a83b1ad` (fix c4-r4 BN2). En esos 2 solo cambian lineas de contexto y cabeceras de hunk: el import A.3 y la clave `"ads_ingest"` como contexto en `api_dashboard.py`, y `"ads_ingest"` en lugar de `"quota"` como contexto en `test_precio_pantalla.py`. Ninguna linea `+/-` propia de C.4 cambia.
4. **La punta es valida.**
   - Los focalizados sobre el worktree en `36266fd` (`PYTHONPATH=. .venv/bin/pytest -p no:cacheprovider -q -rs` sobre test_ads_salud, test_propuestas_campana, test_api_dashboard, test_api, test_notifica, test_ui, test_precio_pantalla, test_ui_copy_campana, test_optimizer_goals y test_cycle_pause_cooldown) dan **351 passed, 0 skipped**, exit 0, en 16.7 s. Postgres local en 127.0.0.1:5432 responde (`pg_isready`), asi que los tests con base de datos si corrieron.
   - `ruff check --no-cache .` da "All checks passed!", tanto con 0.16.4 del venv como con 0.15.20 del pin de pre-commit.
   - Las migraciones no chocan: A.3 trae `0041_ads_ingest_alert.sql` y C.4 `0042_ads_campaign_proposal.sql`. Los numeros duplicados 0011/0031 son pares de reversa que ya estaban en la base.
5. **CI, run Quality `pull_request` 36103443731** sobre `headSha` `36266fd`: conclusion `success`.
   - Jobs: `rapido` success y `gate` success. Todos los pasos de `rapido` salen success: el candado de frescura del chat-context, pre-commit con higiene + ruff + contexto, y las guardas test_architecture, test_precommit_hooks y test_chat_context_guard.
   - `completa` y `pesada` salen `skipped` por tipo de evento, no por draft (ver obs. 1). El `AI review` 36103443679 sale `skipped` por draft.
6. **`gh pr view 342`**: `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, `isDraft: true`, `headRefOid` `36266fd`, `baseRefOid` `b3b2c8e`. Coincide con lo local: `git merge-tree --write-tree b3b2c8e 36266fd` produce el arbol `8af2a31`, que es el mismo arbol de `36266fd` (fast-forward limpio).

VEREDICTO: APROBADO
