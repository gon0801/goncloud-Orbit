# D-M — Cobertura del catálogo y resultado transaccional (TN06/TN14)

Diseño: `docs/evidencia/arreglos-03/DISENO.md`, sección «D-M».
Plan: `plans/arreglos-03.md`, filas D-M.1 a D-M.3.

- SHA base (antes del cambio, origin/master con D-L integrado): `1d197a950f1f8a8686782cdee60114026b49e5ca`
- SHA entregado (código + tests): `003df8344efcb1b43088681a312a858f572c0f7d`
- Sin migraciones. La interfaz `(ids, truncado)` de `ClienteMeli.items_seller` se
  conserva (la semántica de `truncado` pasa a ser «cobertura desconocida»), así que no
  hubo que migrar catálogo ni reputación.

## Rojo previo (SHA `1d197a9`, código sin el arreglo)

### Arnés TN (PG desechable 127.0.0.1:55449, bajo el candado `locks/orbit-tn`, `PYTHONUTF8=1`)

`CI=1 uv run --frozen --python 3.12 python -B -m pytest -p no:cacheprovider -q -rs docs/evidencia/arreglos-05/revision/test_accept_ingest.py -k "test_06 or test_14"` → **2 failed**, salida 1 (2026-11-18 20:08 UTC):

- `test_06_scan_incompleto_no_desactiva_ausentes` (test_accept_ingest.py:28):
  `miembros: [('MLM1001', True), ('MLM1002', False)]` — el scan de 1 ítem sobre un total
  de 2 (sin scroll) desactivó al ausente `MLM1002`; y las variantes del scan declararon
  completo: `([('MLM1'], False)` (página vacía antes del total) y `(['MLM1', 'MLM1'], False)`
  (duplicados contados dos veces).
- `test_14_contencion_no_declara_escrituras_revertidas` (test_accept_ingest.py:34):
  `observadas: 1, observaciones_reales: 0, sello: (True, 1, 1)` — el contador publicó
  éxito por una observación revertida con el savepoint del ítem.

### Pruebas nuevas (escritas antes del cambio, corridas contra `1d197a9`)

`PYTHONUTF8=1 uv run --frozen --python 3.12 python -m pytest -q tests/test_reputacion.py -k "arreglos03_dm or scan_truncado_cuenta or scan_pagina_doble"` → **7 failed, 2 passed**:

| Prueba | Fallo en `1d197a9` |
| --- | --- |
| `test_cliente_meli_scan_truncado_cuenta` (promovida) | `len(ids) == 3`: contaba la misma página repetida tres veces |
| `test_arreglos03_dm_scroll_ausente_no_certifica_completitud` | devolvía `(['MLM1'], False)`: completo por omisión de scroll |
| `test_arreglos03_dm_fin_precozo_pagina_vacia_no_certifica` | devolvía `(['MLM1'], False)` con página vacía antes del total |
| `test_arreglos03_dm_total_inconsistente_no_certifica` | certificaba con total cambiante, únicos>total y total booleano |
| `test_arreglos03_dm_duplicados_no_acreditan_completitud` | `['MLM1','MLM1']` con total 2 daba completo |
| `test_arreglos03_dm_solapamiento_con_avance_alcanza_el_total_unico` | devolvía `['MLM1','MLM0','MLM0','MLM2']` (duplicado sin deduplicar) |
| `test_arreglos03_dm_paginas_malformadas_son_parciales` | `results: "MLM1"` (string) se iteraba por carácter y daba `(['M','L','M','1'], False)` |

Pasaron en rojo (controles, ver abajo): `test_cliente_meli_scan_pagina_doble`,
`test_arreglos03_dm_catalogo_vacio_certificado`.

`PYTHONUTF8=1 uv run --frozen --python 3.12 python -m pytest -q tests/test_precio02_meli_catalogo.py -k arreglos03_dm` → **2 failed, 2 passed**:

| Prueba | Fallo en `1d197a9` |
| --- | --- |
| `test_arreglos03_dm_scan_parcial_no_da_bajas_ni_exito_ficticio` | `skips["meli: scan truncado"] == 0`: el scan incompleto se tomaba por completo (daba bajas) |
| `test_arreglos03_dm_fallo_tras_observar_no_cuenta_la_escritura_revertida` | `observadas=2` contra 1 fila real y sello `rows_written=1`: contaba la escritura revertida |

Pasaron en rojo (controles): `test_arreglos03_dm_scan_completo_certificado_da_baja_real`,
`test_arreglos03_dm_rollback_exterior_no_publica_exito`.

## Verde (SHA `003df83`)

- `tests/test_reputacion.py -k "arreglos03_dm or scan_truncado_cuenta or scan_pagina_doble"` → 9 passed.
- `tests/test_precio02_meli_catalogo.py` completa → 28 passed.
- `tests/test_reputacion.py` completa → 40 passed.
- Arnés TN: `test_06` → 1 passed (salida 0); `test_14` → 1 passed (salida 0);
  `test_06 or test_14` → 2 passed (salida 0). Sin skips (`-rs`).

### Controles que ya pasaban antes (y siguen verdes)

Según ACEPTACION («si un caso ya pasa tras el rebase, es control resuelto»):

- `test_cliente_meli_scan_pagina_doble`: el scan de dos páginas válidas ya certificaba.
- `test_arreglos03_dm_catalogo_vacio_certificado`: total 0 con contenedor vacío ya daba
  `([], False)`; protege que el arreglo no rompa el catálogo vacío demostrado.
- `test_arreglos03_dm_scan_completo_certificado_da_baja_real`: con el total único
  alcanzado en dos páginas válidas (solapamiento con avance) la baja por ausencia SÍ
  corre; protege contra la sobre-corrección (un scan completo sigue reconciliando).
- `test_arreglos03_dm_rollback_exterior_no_publica_exito`: el sello ya se escribía en la
  misma TX que las filas, así que un fallo al sellar revierte filas y sello juntos y
  `sincronizar` levanta (nada de éxito ficticio por esa vía). El rojo de TN14 está en el
  savepoint interior, que es lo que D-M.2 cambia.

## Revisión de consumidores (D-M.3)

Quién consume `items_seller` y cómo queda con el certificado nuevo:

| Consumidor | Antes | Ahora |
| --- | --- | --- |
| `app/meli/catalogo.py::sincronizar` | `truncado` solo contaba el tope de 50 páginas; `_reconcilia_ausentes` corría con cualquier `truncado=False` | `truncado=False` es cobertura certificada por el cliente; solo entonces `_reconcilia_ausentes`; el parcial conserva ausentes y suma `meli: scan truncado` |
| `app/reputacion.py::fetch_meli` | skip «scan truncado a 50 paginas (items pendientes)» | mismo consumo de `(ids, truncado)`; el skip dice «scan de items incompleto (cobertura desconocida)» (promovido en `test_corre_meli_fallo_conserva_skips_acumulados`) |
| `app/reputacion.py::_ensayo` | `paginado_truncado: truncadas or scan_truncado` | igual: la señal nueva sigue siendo un bool |
| `tests/test_precio02_meli_catalogo.py::_ClienteFalso.items_seller` | devolvía `(ids, truncado)` fijo | igual: la interfaz no cambió |

- Variantes/unidades: `_fetch_item` y `_aplica_plan` no cambian la regla vigente (la
  variante más cara fija el precio aunque no tenga existencias; `variantes` con su stock
  por variante sigue igual). El delta solo agrupa contadores, no toca los datos escritos.
- Modos: sin cambios; nada de precios se escribe ni se modifica `shadow`.
- Contadores: `observadas`/`skips` de éxito/`existentes` se fusionan solo tras salir del
  savepoint sin error; el sello y las filas viven en la misma TX exterior; `ok` ya no
  puede depender de contadores revertidos (`test_14` lo demuestra con contención real).
- Candado de arquitectura: los ayudantes `_id_item_valido`/`_total_valido` están a nivel
  de módulo para no agregar métodos a `ClienteMeli`
  (`tests/test_arq_precio_m.py::test_cliente_meli_solo_expone_get` sigue en verde).
- Suites vecinas corridas: `test_arq_precio_m.py`, `test_reputacion_alertas.py`,
  `test_ui_reputacion.py`, `test_reputacion_migracion.py`, `test_cli.py` → 137 passed.
