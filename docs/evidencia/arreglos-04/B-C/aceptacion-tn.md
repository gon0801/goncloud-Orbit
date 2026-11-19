# B-C / TN02: lector unico estricto y paginacion completa de listas SP

Contrato: `docs/evidencia/arreglos-04/DISENO.md` (seccion `B-C/TN02`, que sustituye a `F10` de `DISENO-bloqueos.md`) y `docs/evidencia/arreglos-05/ACEPTACION.md`. Owner unico de `TN02`: bloque `B-C`.

## Medicion previa (B-C.0) y diferencias reales verificadas

Se ejecuto el comando de aceptacion `bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-C/mide_paginas.py > docs/evidencia/arreglos-04/B-C/paginas.txt` el `2026-11-19` con codigo de salida `0`. `mide_paginas.py` usa unicamente `AdsClient` (guard de solo lectura) y `perfiles_aceptados`, validando cada pagina con `_valida_pagina_list` y `_valida_token_list`.

Resultado versionado en `docs/evidencia/arreglos-04/B-C/paginas.txt`:
- `amazon_mx|/sp/keywords/list|3|2719|1000|fin`
- `amazon_mx|/sp/negativeKeywords/list|3|2643|1000|fin`
- `amazon_us|/sp/keywords/list|2|1340|1000|fin`
- `amazon_us|/sp/negativeKeywords/list|2|1563|1000|fin`

Dimensionamiento confirmado sobre ambos endpoints (`/sp/keywords/list` y `/sp/negativeKeywords/list`) en MX y US:
- Las 4 listas llegaron a `fin` (cero `sin_fin`, cero `corte_callado`) y entregaron `1000` elementos en su primera pagina.
- La lista mas larga ocupa `3` paginas (`amazon_mx` en `/sp/keywords/list` y en `/sp/negativeKeywords/list`).
- `max(20, 4 * 3) = 20`, redondeado a la decena superior: `TOPE_PAGINAS_LIST = 20` se mantiene con margen de `6.6x` sobre el maximo real observado.

Diferencias reales documentadas al verificar `B-C.0`:
1. En el pase inicial del PR (`12e253f0`, rebasado en `3e7164b` sobre `origin/master` `08e178bc`), `en_prod.sh` fallo en `goncloud` por permisos sobre `/var/run/docker.sock` y no habia medicion viva de `/sp/negativeKeywords/list`; en esta verificacion `en_prod.sh` corre con exito (`rc=0`) y deja `paginas.txt` completo para las 4 listas. El acceso SSH para despliegue permanece en cola hasta decision de David.
2. Conforme a `plans/arreglos-04.md:311` (subordinar la receta de `F10` al contrato estricto `B-C/TN02` de `DISENO.md:1018`), `mide_paginas.py` no corta ante una pagina vacia con `nextToken` valido (`DISENO-bloqueos.md:384` tenia `if not pagina`) y captura `ValueError` de `.json()` como `sin_fin`. En produccion las 4 listas devolvieron paginas pobladas y cerraron con `fin`, por lo que no aplica parada ni `diferencia.md` de bloqueo.

## Inventario de callers migrados y bucles retirados (B-C.2)

Se retiraron `_lista_todos` y `_lista_filtrada` de `app/apply_harvest.py`. El unico bucle de paginacion vigente es `app.apply_harvest._lista_completa(cliente, path, *, grupos=None) -> list[dict]` sobre `cliente.list_sellado`, que lanza `ListaIncompleta(AdsApiError)` con `causa` en `{"ambiguo", "truncado"}` ante JSON ilegible, contenedor ausente o no-lista, elemento incompleto o fuera de enum, elemento fuera de `adGroupIdFilter`, `nextToken` ilegible o repetido, o `nextToken` vivo al agotar `TOPE_PAGINAS_LIST`.

| Caller | Antes | Destino en B-C | Efecto ante lectura desconocida |
| --- | --- | --- | --- |
| `app/apply_harvest.py::_preexiste_provisional` | `_lista_completa` (tupla) y `_lista_filtrada` | `_lista_completa` sin filtro (keywords) o con `grupos=(str(paso.ad_group_ext),)` (negatives); captura `ListaIncompleta` -> `"stop-unknown"` | Cero `POST .../delete`; detiene la reversa manual con `"readback no concluyente"` |
| `app/apply_harvest.py::ejecuta_reversa_harvest` | `_lista_completa` (tupla) y `_lista_filtrada` | `_lista_completa` sin filtro o con `grupos=(str(paso.ad_group_ext),)`; captura `ListaIncompleta` -> `(False, "... readback no concluyente")` | El intento abierto no se sella `ok` ni `fallo:sigue_vivo`; no avanza al siguiente delete |
| `app/apply_harvest.py::_reversa_automatica` | `_lista_todos` (keywords y negatives, intercalando delete de keyword antes de leer negatives y atrapando `AdsClientError`) | Resuelve ambas lecturas con `_lista_completa` antes del primer `_reversa_delete` y propaga cualquier `AdsApiError`, `ListaIncompleta` o `AdsAuthError` de lectura sin entrar a `_falla_job` | Cero `POST .../delete` (sin reversa parcial); conserva `harvest_job` en su fase en vuelo y `apply_queue` en `applying` para reintentar en el siguiente ciclo |
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
2. **Pruebas `test_arreglos04_bc_*` en `tests/test_apply_harvest.py` contra el codigo previo (`08e178bc`, commit de reproduccion `c7ea92f`):** `15 failed, 2 passed, 4 skipped` por comportamiento real (re-POST de negative en conciliacion y creacion, confirmacion falsa en `item_invalido`/`token_repetido` y `POST /sp/keywords/delete` en readback).
3. **Sonda `docs/evidencia/arreglos-04/B-C/prueba.py` contra `app/` de `8c58d56b`:** `AssertionError: creacion negative posteada con missing: 1`.
4. **Hallazgos de revision de Astra (`app/apply_harvest.py:1480` y `:1484`, reproducidos en `0d3ee58` sobre `3e7164b` y en `7934e60` sobre `9d2745f`, aprobados en `40c0ee27`):** con `keyword_creada=True` e ID conocido (`"k-7"`) y `negative_creada=True` sin `negative_id`, una lista `/sp/negativeKeywords/list` truncada ya habia enviado `POST /sp/keywords/delete` y cerraba el job en `failed` (`AssertionError: cero DELETE ante lista truncada: ['/sp/keywords/delete']`), y un `AdsAuthError` al renovar credenciales LWA durante esa lectura caia en `except AdsClientError` y cerraba el job en `failed` sin completar la reversa (`assert (0, 1) == (0, 0)` en `test_apply_harvest.py` y `AssertionError: reversa cerro job con auth_en_lectura: ['keyword_ausente']` en `prueba.py`).

## Evidencia en verde (con el arreglo)

- `B-C.0`: `bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-04/B-C/mide_paginas.py > docs/evidencia/arreglos-04/B-C/paginas.txt` -> salida `0` (`4` filas con `fin`, cero `corte_callado`).
- `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_apply_harvest.py -k arreglos04_bc_` -> `18 passed, 4 skipped, 50 deselected` (salida `0`; en `test_arreglos04_bc_reversa_automatica_lista_incompleta_cero_delete_y_job_pendiente`, tanto `tope_agotado` como `auth_en_lectura` dejan `0` DELETEs y `harvest_job` en `exact_created` / `apply_queue` en `applying`, y el siguiente barrido con lista completa borra `k-7` y `id-propio` en orden y cierra el job).
- `B-C.2`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q -rs tests/test_apply_harvest.py tests/test_d3_adversary.py tests/test_cycle_apply.py tests/test_apply_cola.py tests/test_fabrica_f2_hermanas.py` -> `125 passed, 136 skipped` (salida `0`; los `136 skipped` son `@_skip_db` por ausencia de PostgreSQL local en esta Mac mini, documentada en `BASE/rebase.md:69`; con `CI=1` el guard fail-closed reclama ese servicio).
- `B-C.3`: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_fabrica_f2_hermanas.py` -> `80 passed, 36 skipped` (salida `0`).
- Vecinos: `PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_bloqueo_dueno_efecto.py tests/test_bloqueo_dueno.py tests/test_reversa_harvest.py` -> `13 passed, 55 skipped` (salida `0`).
- Sonda `B-C.4`: `PYTHONPATH=. uv run --frozen --python 3.12 python docs/evidencia/arreglos-04/B-C/prueba.py` y `test -s docs/evidencia/arreglos-04/B-C/aceptacion-tn.md` -> `B-C OK` (salida `0`).
- En esta Mac mini sigue ausente el PostgreSQL 16 desechable de laboratorio en `127.0.0.1:55449` (`BASE/rebase.md:69`), por lo que `comprobar.sh T writes "test_02"` informa `ORBIT_TEST_DSN: Falta DSN del PG16 desechable` (con `--collect-only` selecciona `2/9 tests collected`: `missing` y `truncated`). Queda listo para su corrida con PostgreSQL 16 desechable en la PC de Hermes.
