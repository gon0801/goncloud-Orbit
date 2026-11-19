# B-C / TN02: lector unico estricto y paginacion completa de listas SP

Contrato: `docs/evidencia/arreglos-04/DISENO.md` (seccion `B-C/TN02`, que sustituye a `F10` de `DISENO-bloqueos.md`) y `docs/evidencia/arreglos-05/ACEPTACION.md`. Owner unico de `TN02`: bloque `B-C`.

## Medicion previa (B-C.0)

Se escribio `docs/evidencia/arreglos-04/B-C/mide_paginas.py` usando solo `AdsClient` (guard de solo lectura) y `perfiles_aceptados`, validando cada pagina con `_valida_pagina_list` y `_valida_token_list`.

Al ejecutar `bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-C/mide_paginas.py > docs/evidencia/arreglos-04/B-C/paginas.txt` desde esta Mac mini, el comando devolvio codigo `1` con `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock` en `goncloud`. Conforme a las reglas de seguridad y a `AGENTS.md`, no se intento escalar permisos ni eludir el control de acceso; se elimino el archivo vacio y `mide_paginas.py` queda listo para que Hermes ejecute la medicion desde el entorno con acceso a Docker antes de desplegar.

Dimensionamiento vigente con las mediciones reales versionadas en el repositorio (`DISENO.md` §Medicion, pruebas y cierre):
- En `app/ads/structure.py:25-26,33-35` (corrida real del 2026-08-22), la pagina 1 de `/sp/keywords/list` entrego `1000` elementos por omision.
- En `docs/evidencia/arreglos-04/DISENO-bloqueos.md:42,74-80` (consulta de solo lectura del 2026-11-12 sobre `a4644aad`), `amazon_mx` tiene `2719` keywords totales (`3` paginas a `1000` por pagina) y `amazon_us` tiene `1340` (`2` paginas).
- `max(20, 4 * 3) = 20`, redondeado a la decena superior: `TOPE_PAGINAS_LIST = 20` se mantiene.

## Inventario de callers migrados y bucles retirados (B-C.2)

Se retiraron `_lista_todos` y `_lista_filtrada` de `app/apply_harvest.py`. El unico bucle de paginacion vigente es `app.apply_harvest._lista_completa(cliente, path, *, grupos=None) -> list[dict]` sobre `cliente.list_sellado`, que lanza `ListaIncompleta(AdsApiError)` con `causa` en `{"ambiguo", "truncado"}` ante JSON ilegible, contenedor ausente o no-lista, elemento incompleto o fuera de enum, elemento fuera de `adGroupIdFilter`, `nextToken` ilegible o repetido, o `nextToken` vivo al agotar `TOPE_PAGINAS_LIST`.

| Caller | Antes | Destino en B-C | Efecto ante lectura desconocida |
| --- | --- | --- | --- |
| `app/apply_harvest.py::_preexiste_provisional` | `_lista_completa` (tupla) y `_lista_filtrada` | `_lista_completa` sin filtro (keywords) o con `grupos=(str(paso.ad_group_ext),)` (negatives); captura `ListaIncompleta` -> `"stop-unknown"` | Cero `POST .../delete`; detiene la reversa manual con `"readback no concluyente"` |
| `app/apply_harvest.py::ejecuta_reversa_harvest` | `_lista_completa` (tupla) y `_lista_filtrada` | `_lista_completa` sin filtro o con `grupos=(str(paso.ad_group_ext),)`; captura `ListaIncompleta` -> `(False, "... readback no concluyente")` | El intento abierto no se sella `ok` ni `fallo:sigue_vivo`; no avanza al siguiente delete |
| `app/apply_harvest.py::_reversa_automatica` | `_lista_todos` (keywords y negatives) | `_lista_completa` sin filtro; `ListaIncompleta` sube a `except AdsClientError` | Cero `POST .../delete` por identidad inferida; devuelve `"reversa: fallo (...)"` |
| `app/apply_harvest.py::_paso_negative` | `_lista_todos` (`/sp/negativeKeywords/list`) | `_lista_completa(cliente, "/sp/negativeKeywords/list")` | Propaga `ListaIncompleta(AdsApiError)` antes de grabar ledger ni emitir `POST /sp/negativeKeywords` |
| `app/apply_harvest.py::_paso_keyword` | `_lista_todos` (negatives y keywords) | `_lista_completa` en ambos endpoints | Propaga `ListaIncompleta(AdsApiError)`; cero `POST /sp/keywords`, cero `/delete`, sin `failed` |
| `app/apply_harvest.py::_paso_readback` | `_lista_todos` (`/sp/keywords/list`) | `_lista_completa(cliente, "/sp/keywords/list")` | Propaga `ListaIncompleta(AdsApiError)`; cero `/delete`, conserva `harvest_job` en `exact_created` y cola en `applying` |
| `app/apply_harvest.py::_paso_hermanas` (previo y posterior) | `_lista_filtrada` | `_lista_completa(..., grupos=...)`; captura `ListaIncompleta` y registra `list_truncado` o `list_ambiguo` | Cero `POST /sp/negativeKeywords` en hermanas; el harvest confirmado sigue vivo o cierra con pendiente declarada |
| `app/apply_harvest_reconciliacion.py::_reconcilia_negativas` | `_ejecucion._lista_todos` | `_ejecucion._lista_completa(cliente, "/sp/negativeKeywords/list")` bajo `except AdsApiError: continue` | Cero re-POST de negative, cero confirmacion `verify_ok`, cola permanece `applying` para el siguiente ciclo |
| `app/bloqueo_dueno_efecto.py::_lista_o` | `_lista_filtrada` | `_lista_completa(..., grupos=(str(grupo_ext),))`; traduce `ListaIncompleta` a `([], exc.causa)` | Cero POST/delete en bloqueos del dueno (`lista_ambiguo` / `lista_truncado` / `pendiente`) |

Revision de modulos vecinos (registrada en `BASE/rebase.md`):
- `app/apply_cola.py` no invoca lectores de listas directamente.
- `app/apply.py` solo conserva `_fila_de_readback` (busqueda puntual por ID de bid/pausa) y `_config_leida` (`/sp/campaigns/list`), fuera del alcance de `B-C` segun `DISENO-bloqueos.md`.
- `tools/reversa_harvest.py` delega en `ejecuta_reversa_harvest`; `tools/archiva_inertes.py` pertenece a `ARREGLOS 05 C` (`TN11`).

## Evidencia en rojo (antes del arreglo)

1. **Arnes TN (`test_02` en `819fe1ff` / `313225fb`):** registrado en `plans/arreglos-04.md:32` y `docs/evidencia/arreglos-04/BASE/rebase.md:16`, donde `test_02_lista_incompleta_no_autoriza_repost[missing]` y `[truncated]` fallaron porque `_reconcilia_negativas` trataba `{}` y `{"negativeKeywords": [], "nextToken": "otra-pagina"}` como ausencia y reenviaba `POST /sp/negativeKeywords`.
2. **Pruebas `test_arreglos04_bc_*` en `tests/test_apply_harvest.py` contra el codigo previo (`c56cdfe3` / `8c58d56b`, commit de reproduccion `207433e6`):** `15 failed, 2 passed, 4 skipped` por comportamiento real (no por error de importacion):
   - Conciliacion (`missing`, `truncated`, `contenedor_no_lista`, `token_ilegible`, `vacio_con_next_token`, `tope_agotado`): emitio `POST /sp/negativeKeywords` duplicado (`AssertionError: Left contains one more item: <Request('POST', 'https://advertising-api.amazon.com/sp/negativeKeywords')>`).
   - Conciliacion (`item_invalido`, `token_repetido`): confirmo en falso (`assert (1, 0) == (0, 0)`).
   - Creacion (`creacion_negative` y `creacion_keyword`, en `missing` y `tope_agotado`): emitio `POST /sp/negativeKeywords` y `POST /sp/keywords`.
   - Readback (`missing` y `tope_agotado`): revirtio una keyword ya creada emitiendo `POST /sp/keywords/delete`.
   - Reversa automatica (`tope_agotado`): devolvio `'reversa: nada que borrar (sin keyword ni negativo)'` en lugar de tratar la lectura como fallida.
3. **Sonda `docs/evidencia/arreglos-04/B-C/prueba.py` contra el paquete `app/` de `8c58d56b`:** fallo por comportamiento con `AssertionError: creacion negative posteada con missing: 1`.

## Evidencia en verde (con el arreglo)

- `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_apply_harvest.py -k arreglos04_bc_` -> `17 passed, 4 skipped, 50 deselected` (salida `0`; los 4 `skipped` son las variantes `@_skip_db` por ausencia de PostgreSQL local en esta Mac mini, documentada en `docs/evidencia/arreglos-04/BASE/rebase.md:69`).
- `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_fabrica_f2_hermanas.py` (Acepta `B-C.3`) -> `80 passed, 36 skipped` (salida `0`).
- `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_apply_harvest.py tests/test_d3_adversary.py tests/test_cycle_apply.py tests/test_apply_cola.py tests/test_fabrica_f2_hermanas.py` -> `124 passed, 136 skipped` (salida `0`).
- `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_bloqueo_dueno_efecto.py tests/test_bloqueo_dueno.py tests/test_reversa_harvest.py` -> `13 passed, 55 skipped` (salida `0`).
- `PYTHONPATH=. uv run --frozen --python 3.12 python docs/evidencia/arreglos-04/B-C/prueba.py` -> `B-C OK` (salida `0`).
- En esta Mac mini no hay servicio PostgreSQL 16 desechable en `127.0.0.1:55449` ni `5432` (`BASE/rebase.md:69`), por lo que `comprobar.sh T writes "test_02"` informa `ORBIT_TEST_DSN: Falta DSN del PG16 desechable` (la coleccion `ORBIT_TEST_DSN=postgresql://orbit:orbit@127.0.0.1:55449/postgres ... --collect-only` selecciona las 2 variantes `missing` y `truncated`, `2/9 tests collected`). Queda listo para su corrida con PostgreSQL 16 desechable por Hermes.
