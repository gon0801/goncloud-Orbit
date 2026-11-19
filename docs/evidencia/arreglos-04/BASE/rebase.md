# BASE.0 Rebase, contratos vigentes y diferencias de entorno

Fecha: 2026-11-18. Bloque `BASE` de `plans/arreglos-04.md` (fila `BASE.0`), ejecutado en Mac mini (`goncloud-orbit-7`).

## Rebase y firmas de arranque

- Rama de trabajo: `hermes/arreglos-04-base-reinicio` sobre `origin/master`.
- SHA de `origin/master` tras `git fetch origin`: `313225fb7be2b5b09c056407d2bc8a3764f59c8c` (`Merge pull request #655 from gon0801/preparacion/poteto-arreglos-04`).
- HEAD de arranque antes del commit de evidencia: `313225fb7be2b5b09c056407d2bc8a3764f59c8c` (`git merge-base --is-ancestor origin/master HEAD` devuelve `0`).
- Plan activo en `plans/manifest.json`: `"active": "arreglos-04"`.
- Precondición de archivos en `origin/master`: comprobados con `git cat-file -e "origin/master:$ruta"` (salida `0` en los 16 archivos exigidos por `BASE.0`):
  - Plan y orden: `plans/arreglos-04.md`, `plans/ORDEN-IMPLEMENTACION.md`.
  - Diseños del plan: `docs/evidencia/arreglos-04/DISENO.md`, `docs/evidencia/arreglos-04/DISENO-bloqueos.md`.
  - Hallazgos del 2026-11-12: `docs/evidencia/arreglos-2026-11-12/web.md`, `docs/evidencia/arreglos-2026-11-12/fabrica.md`, `docs/evidencia/arreglos-2026-11-12/apply.md`.
  - Contrato común y review portado: `docs/evidencia/arreglos-05/ACEPTACION.md`, `docs/evidencia/arreglos-05/comprobar.sh`, `docs/evidencia/arreglos-05/revision/informe.txt`, `docs/evidencia/arreglos-05/revision/hallazgos.json`, `docs/evidencia/arreglos-05/revision/matriz.tsv`, `docs/evidencia/arreglos-05/revision/criterio-cierre.txt`, `docs/evidencia/arreglos-05/revision/reproducir.txt`, `docs/evidencia/arreglos-05/revision/test_accept_writes.py`, `docs/evidencia/arreglos-05/revision/test_accept_ui.py`.
- Igualdad de base ejecutable con `819fe1ff` (cierre desplegado de `ARREGLOS 03`): `git diff --stat 819fe1ff..313225fb` toca únicamente 9 archivos documentales (`AGENTS.md`, `CLAUDE.md`, `DISENO-bloqueos.md`, `DISENO.md`, `ACEPTACION.md`, `ORDEN-IMPLEMENTACION.md`, `ROADMAP.md`, `arreglos-04.md`, `manifest.json`) y cero archivos de `app/`, `tools/`, `migrations/`, `tests/` o `docs/evidencia/arreglos-05/revision/test_accept_*.py`. La referencia auditada `ff03ed61` existe en el historial local y el rojo TN registrado en `819fe1ff` sigue vigente sin cambio ejecutable.

## Contratos conservados y base ya integrada en `master`

- **ARREGLOS 01, bloque X (`01X`, commit `976df4dc`), conservado.**
  - `TECHOS_HUMANOS_POR_MONEDA`, `exige_bajo_techo` y `exige_banda_meta_manual` existen en `app/optimizer/goals.py` (`:132`, `:138`, `:660`). `PYTHONPATH=. .venv/bin/python docs/evidencia/arreglos-01/X/prueba.py` devuelve `X OK` (salida `0`) y los 4 controles `arreglos01_x_` de `tests/test_optimizer_goals.py` y `tests/test_config_write.py` pasan.
  - La banda manual de Ajustes (`web1`, `app/config_write.py:390,457` `_exige_banda_manual`) ya está corregida por `01X`. `test_rev_settings.py::test_target_manual_fuera_de_banda_se_rechaza[0.2]` y `[2000]` pasan en `313225fb`. Se conserva como control sin reabrir Ajustes en este plan.
  - Las 4 llamadas a `exige_bajo_techo("presupuesto_diario", ...)` para cifras tecleadas a mano coinciden con `W-N.0` en `app/api_fabrica.py:241`, `app/campana_ajustes.py:152`, `app/impulso_io.py:313` y `tools/fabrica_campanas.py:580` (`_exige_techos_humanos`, llamada en `:595` antes del desvío a `_arma_plan_v2` y repetida en `:621` dentro de la rama v1; cuando `W-B` retire v1, `:595` mantiene cubierto el camino v2 hasta que `W-N` reemplace el rechazo por aviso y confirmación).
  - En `app/goals_write.py:229-237` (`_valida_pre_editar`), `01X` ya valida `bid_ceiling` y `harvest_default_bid` con `exige_bajo_techo("bid", ...)` (`test_rev_settings.py::test_techo_de_puja_de_goal_sin_tope_se_rechaza` pasa en `313225fb`), pero aún no valida `target_acos_pct` (`exige_banda_meta_manual`) ni `bid_floor` (`exige_bajo_techo`). `W-I` unificará el bucle sobre `cambios` sin duplicar las validaciones ya presentes.
- **Bloque `W-O` conservado como Salud.**
  - `W-O` mantiene su alcance exclusivo sobre etiquetas de Salud en `app/etiquetas.py` (`MOTIVO_SALUD` en `:110` y candado de vocabularios cerrados en `:918-940`). Los cuatro motivos (`politica.MOTIVO_SUBIDA_EN_ESPERA`, `proteccion.MOTIVO_PROTEGIDA`, `proteccion.MOTIVO_INDETERMINADA` y `ciclo.MOTIVO_PAUSA_PRODUCTO_PENDIENTE`) siguen sin entrada en `MOTIVO_SALUD` sobre `313225fb`.
  - `TN04` tiene como único dueño a `W-P` (`app/static/js/productos.js`, dependiente de `W-M` y `W-N`). `W-O` no comparte propiedad con `TN04`.
- **ARREGLOS 02, bloque B (`5b73e2ed`), revalidado.**
  - `app/ads/write.py:188-203` conserva `_bid_payload` con `ROUND_HALF_EVEN` explícito. `tests/test_ads_write.py` pasa completa en `313225fb`. `B-A` solo eliminará `archivar_product_ad` (`:516-550`) y el par `("POST", "/sp/productAds/delete")` de `MUTATION_REQUEST_TYPES` (`:114`), sin tocar `_bid_payload`.
- **ARREGLOS 03 (`819fe1ff`), integrado.**
  - `app/dia_cerrado.py` publica `ZONAS`, `dia_local`, `ultimo_dia_cerrado` y `fin_del_dia`.
  - `app/dashboard_resumen.py:200-220` define `hoy_por_canal` y `hoy_resumen(canales, instante)` sobre `app.dia_cerrado.dia_local`, con la firma que consumen `W-C`, `W-D` y `W-M`.
  - `app/cli.py` incluye los cambios de `03` (`reputacion` en `:156-172`, `:697`, `:817-825`) junto a `archivar-anuncios` (`:237`, `:674`, `:827`), `reponer-anuncios` (`:291`, `:719`, `:812`) y `_parse_decided_at` (`:68`). `B-A` y `W-H` editarán por contenido conservando los cambios de `03`.
- **DISEÑOS 01 y PR #584.**
  - `DISEÑOS 01` A/B/C (`19c2c7b3`, `f15ffc77`, `c6cb744f`) está integrado en `master`.
  - Verificado con `gh pr view 584`, el PR #584 está en estado `CLOSED` desde `2026-11-13T06:15:04Z` (la mención en `DISENO.md:76` de que seguía abierto es anterior al cierre y ya quedó superada por `plans/arreglos-04.md:23,65`). No hay otros PRs abiertos al iniciar este bloque.
- **TEMPORADA 01.**
  - Permanece después de `ARREGLOS 04` en la cola serial (`04 -> TEMPORADA 01 -> 05`) y no ha entrado en `master`.
  - `app/apply.py` conserva `_cliente_ajuste` y `app/apply_cola.py` conserva `_SQL_EXTERNALES_GRUPO` (usados por `app/bloqueo_dueno_efecto.py:380,438`). `app/static/js/dinero.js` aún no contiene el diálogo «Revisar» de `TEMPORADA 01 G`; `W-N` entrará antes y `TEMPORADA 01 G` consumirá su contrato.

## Contrato `B-C` / `TN02` (sustituye `F10`) e inventario de callers

- El contrato `B-C/TN02` de `docs/evidencia/arreglos-04/DISENO.md:1011-1071` sustituye íntegramente a `F10` de `docs/evidencia/arreglos-04/DISENO-bloqueos.md:321-421`, incluido su pseudocódigo permisivo anterior; `DISENO-bloqueos.md` conserva únicamente la receta de medición previa (`B-C.0`) y los bloques `B-A`, `B-B` y `B-D`.
- `B-C` no agrega un cuarto lector. Consolida los tres bucles existentes de `app/apply_harvest.py` (`_lista_todos` en `:418`, `_lista_completa` en `:517` y `_lista_filtrada` en `:554`) en un único `_lista_completa(cliente, path, *, grupos=None)` que valida páginas y tokens con `_valida_pagina_list` (`:500`) y `_valida_token_list` (`:440`) sobre `cliente.list_sellado` y lanza `ListaIncompleta(AdsApiError)` ante cualquier lectura truncada o ambigua.
- **Inventario completo de callers en `313225fb` que `B-C` debe migrar:**
  1. `app/apply_harvest.py:1398` y `:1402`: `_preexiste_provisional` (`_lista_completa` para keywords y `_lista_filtrada` para negatives).
  2. `app/apply_harvest.py:1459` y `:1463`: `ejecuta_reversa_harvest` (`_lista_completa` para keywords y `_lista_filtrada` para negatives); consumida también por `tools/reversa_harvest.py:37`, que no pagina por su cuenta.
  3. `app/apply_harvest.py:1510` y `:1521`: `_reversa_automatica` (dos llamadas a `_lista_todos`).
  4. `app/apply_harvest.py:1570`: `_paso_negative` (`_lista_todos` en `/sp/negativeKeywords/list`).
  5. `app/apply_harvest.py:1650` y `:1673`: `_paso_keyword` (dos llamadas a `_lista_todos`, negatives y keywords).
  6. `app/apply_harvest.py:1802`: `_paso_readback` (`_lista_todos` en `/sp/keywords/list`).
  7. `app/apply_harvest.py:2077` y `:2137`: higiene de hermanas, lecturas previa y posterior (`_lista_filtrada`).
  8. `app/apply_harvest_reconciliacion.py:393`: `_reconcilia_negativas` (`_ejecucion._lista_todos`).
  9. `app/bloqueo_dueno_efecto.py:372`: `_lista_o` (`_lista_filtrada`; `B-B` y `B-D` integran después de `B-C` consumiendo `_lista_o` ya migrado).
- **Revisión de `app/apply.py`, `app/apply_cola.py` y `tools/`:**
  - `app/apply_cola.py` no contiene llamadas directas a lectores `list_sellado` ni `list_objects`; delega en `apply_harvest` y `apply_harvest_reconciliacion`.
  - `app/apply.py` no llama a `_lista_todos`, `_lista_filtrada` ni `_lista_completa`. Solo contiene `_fila_de_readback` (`:1006-1024`, búsqueda puntual por `keywordId`/`targetId` con parada al hallar el ID y `AdsApiError` ante respuesta malformada) y `_config_leida` (`:2061-2085`, readback de campaña en `/sp/campaigns/list`). Ambos quedan fuera de `B-C` conforme a `DISENO-bloqueos.md:249-251` sin cambio de política.
  - `tools/archiva_inertes.py` pertenece a `ARREGLOS 05 C` (`TN11`); `tools/smoke_apply.py` (sonda manual cuyo `_paso_mutacion` se ajusta en `B-B.5`), `tools/snapshot_listas.py` y `tools/reactiva_campanas.py` son utilidades manuales independientes fuera de `B-C`.

## Fixtures y diferencias de entorno registradas

1. **Recolección de aceptación TN y reproducciones del 2026-11-12.**
   - `bash docs/evidencia/arreglos-05/comprobar.sh --help` responde `0`. Con Python 3.12.15 del `.venv`, `pytest --collect-only` recoge los 9 casos de `test_accept_writes.py` (incluidos `test_02_lista_incompleta_no_autoriza_repost[missing]` y `[truncated]`) y los 6 casos de `test_accept_ui.py` (`test_04` retiro e impulso, `test_15`, `test_16` y los 2 controles).
   - En `docs/evidencia/arreglos-2026-11-12/pruebas/`, las reproducciones sin Postgres fallan hoy por las fallas previstas en `W-D`, `W-E`, `W-F`, `W-G`, `W-H`, `W-I` (meta de goal), `B-A` y `B-B.5`. Los casos `test_cat_productos.py` y `test_fab_desarme.py` residen en `docs/evidencia/arreglos-2026-11-12/pruebas/fabrica/`.
2. **Fallo preexistente en `origin/master` dentro de `tests/test_architecture.py`.**
   - En `313225fb`, `tests/test_architecture.py::test_presupuesto_de_tamano_por_modulo` ya falla con `AssertionError: modulos sobre el presupuesto de 900 lineas sin entrada en la allowlist ...: {'app/ledger.py': 1000}` (crecimiento de `app/ledger.py` hasta `c838fa1` en `ARREGLOS 03`; las otras 144 pruebas de `test_ads_write.py` + `test_architecture.py` pasan). Ningún bloque de `ARREGLOS 04` edita `app/ledger.py`; aplica la regla de `AGENTS.md` sobre fallos preexistentes en `master` cuando corran los bloques que invocan `tests/test_architecture.py`.
3. **Herramientas y servicios en esta Mac mini (`darwin` arm64).**
   - Disponibles: Python 3.12.15 (`.venv/bin/python` y `/Users/gon/.local/bin/python3`), `uv` (`/Users/gon/.local/bin/uv`), `gh` (`/Users/gon/.local/bin/gh`; `origin` CLI no está instalado, por lo que los PRs usan `gh`), `node` (`/Users/gon/.local/bin/node`), `md5sum` (`/sbin/md5sum`) y Google Chrome 157.0.8103.79 (`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, utilizable vía `ORBIT_CHROMIUM` en los bloques UI).
   - No instalados o apagados hoy en esta máquina: `rg`, `pre-commit`, `gitleaks`, `docker`, `psql`, `pg_dump`, `flock`, y sin instancia PostgreSQL escuchando en `127.0.0.1` (`5432`, `55432`, `55433`, `55449`). `BASE.0` no requiere ninguno de ellos. Los bloques posteriores que usen `rg` en su `Acepta` (`W-A`, `W-B`, `W-C`, `B-A`) o PostgreSQL desechable (`CI=1` / puerto `55449` del arnés TN) requerirán tener esa herramienta o el túnel/contenedor de PG16 disponible antes de correr su verificación con base.
   - Workflows activos en `.github/workflows/` sobre `origin/master`: únicamente `ai-review-publish.yml` y `ai-review-worker.yml` (`Quality` sigue desactivado y no se reactiva).
4. **Graphify.**
   - No existen `graphify-out/graph.json` ni el ejecutable `graphify` en el entorno, por lo que los subcomandos de Graphify no aplican en este estado.
