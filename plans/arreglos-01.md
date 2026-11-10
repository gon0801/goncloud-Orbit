# ARREGLOS 01: los 4 altos y 23 medios de la revisión del 2026-11-09
autopiloto: motor
plan: arreglos-01
repo: git@github.com:gon0801/goncloud-Orbit.git
implementa: deepseek_flash
revisa: opus

Base: rama desde `origin/master` (`3c841f4` o posterior). Migraciones nuevas: llevan la etiqueta `_arreglos01_` en el nombre (`0083_arreglos01_<tema>.sql`), su `_reversa_`, la cabecera `-- sonda-aplicada: <select que da t solo con la migración aplicada>` en sus primeras 10 líneas y van sin BEGIN/COMMIT (`tools/despliegue/comun.sh`). Números: F usa `0083`, Q `0084`, Y `0085`; si el número ya existe en master al abrir el PR, toma el siguiente libre. Donde el diseño nombra una migración con otro número o sin la etiqueta, vale el nombre de este plan.
Precondición: este plan, `docs/evidencia/arreglos-2026-11-09/` y `docs/evidencia/arreglos-01/` llegan juntos a master en el PR del plan; se entrega al motor después de ese merge (fila A.0).
Origen: revisión de toda la app del 2026-11-09 (`docs/evidencia/arreglos-2026-11-09/HALLAZGOS.md`, 4 altos y 23 medios) y su diseño de arreglos (`docs/evidencia/arreglos-2026-11-09/DISENO.md`, 24 unidades).
Decisiones: las 18 decisiones del dueño están tomadas (2026-11-09) en la sección 3 de `DISENO.md`. Ningún bloque espera permiso: este plan es la autorización completa, incluidas las lecturas, los despliegues y las escrituras a producción que nombran A, C, N y U.
Diseño: cada bloque implementa una unidad. La forma decidida está en `docs/evidencia/arreglos-2026-11-09/<frente>/SINTESIS.md` y manda sobre los candidatos; las firmas y el seudocódigo están en la sección del candidato que cita la primera fila del bloque. Lee las dos antes de tocar código. Si el código real contradice el diseño, para el bloque y escribe la diferencia en `docs/evidencia/arreglos-01/<bloque>/diferencia.md`; no inventes una forma nueva.
Para quién: el dueño de la cuenta. Hoy el motor puede apagar una keyword propia, soltar solo el freno de precios, congelar costos y callar fallas; cada bloque quita una de esas.
Ayudas ya probadas contra producción (solo lectura), en `docs/evidencia/arreglos-01/`: `lee_prod.sh` (un SELECT por la entrada estándar, rol `orbit_read`, transacción de solo lectura), `desplegado.sh [--no] texto...` (el código en producción trae o ya no trae un texto), `migracion_aplicada.sh <tema>` (corre la sonda de la migración), `en_prod.sh archivo.py` (corre en el contenedor un script de funciones puras, sin base ni red). `escribe_api.sh <ruta> <json>` llama un endpoint de escritura desde dentro del contenedor, donde vive el token; no se ha usado todavía contra producción, así que su primera salida se guarda completa en la evidencia.
Llaves: ninguna llave, token ni contraseña entra al repo ni a la evidencia. Si una salida del servidor trae una, se guarda sin ella.
Regla de replay: un cambio a una función de decisión del motor de Ads lleva marcador congelado o una consulta previa al despliegue que dé 0 filas (`app/optimizer/replay.py:17-22`). Las consultas están en la sección del diseño de cada unidad.
Pruebas nuevas solo en los 4 casos de `AGENTS.md`; el campo `pruebas:` de cada bloque nombra su caso. Cada prueba nueva se llama `test_arreglos01_<letra del bloque>_<qué comprueba>` y debe fallar en `3c841f4`. Lo demás se comprueba corriendo lo real. `Acepta` sale 0 en el SHA entregado con `uv run --frozen --python 3.12`. `prueba-real` es solo lectura.
En todo bloque: `graphify query` antes de explorar (en un checkout limpio, correr antes `graphify update .`) y `graphify update .` después de cambiar código. Si cambian selectores o textos de una pantalla, actualiza su ficha en `.cursor/skills/verify-orbit/features/` (regla de `CLAUDE.md`). Los textos que ve la persona van en español llano, sin códigos internos.
Orden: primero lo que hoy daña dinero o puede tumbar el motor (B a G), después los medios por frente. Los bloques de Ads van en cadena porque comparten `app/cycle.py`, `app/apply.py` y `app/apply_cola.py`.

## Bloque A — Base: la propuesta 2869 no se aplica y las migraciones del plan sí entran
lane: fast
depende: -
pruebas: instala
aviso: La propuesta 2869, que iba a bloquear «wedding arras» en su propio ad group de Estados Unidos, ya no se aplica. Las migraciones de este plan ya entran al despliegue.
prueba-real: curl -fsS http://10.13.13.1:8010/api/dashboard/cortes | python3 -c "import json,sys;d=json.load(sys.stdin);assert not [i for i in d['items'] if i['id']==26 and i['estado']=='pending_veto' and i['modo']=='live']"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.0 | Precondición: el diseño y las ayudas están versionados en master. | git ls-files --error-unmatch docs/evidencia/arreglos-2026-11-09/DISENO.md docs/evidencia/arreglos-2026-11-09/HALLAZGOS.md docs/evidencia/arreglos-01/lee_prod.sh docs/evidencia/arreglos-01/desplegado.sh docs/evidencia/arreglos-01/migracion_aplicada.sh docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/escribe_api.sh |
| A.1 | Cola 26 (decisión 2869, amazon_us, negative EXACT «wedding arras» sobre una keyword EXACT propia; vence el 2026-11-10 08:40 UTC). Leer `select id, estado, modo from apply_queue where id = 26` con `lee_prod.sh`. Si sigue `pending_veto`, rechazarla: `bash docs/evidencia/arreglos-01/escribe_api.sh /api/ads-optimizer/veto '{"queue_id": 26, "actor": "arreglos-01", "dias": 30}'` y volver a leer. Si ya está `vetoed`, no hacer nada. Si está `applied`, anotarlo: la revierte el bloque C. Guardar las lecturas y la respuesta en `docs/evidencia/arreglos-01/A/cola-26.txt`. | test -s docs/evidencia/arreglos-01/A/cola-26.txt |
| A.2 | `tools/despliegue/comun.sh` `migraciones_de` (`:141`): el patrón pasa a `_(auto03\|orbit07\|claridad01\|arreglos01)_`. Sin esto el despliegue no aplica las migraciones de F, Q y Y y no avisa. Prueba (instalar en las máquinas), en `tests/test_despliegue_motor.py`: una migración `0083_arreglos01_x.sql` con su cabecera y su reversa sale en la lista de `migraciones_de`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py -k arreglos01_a_ |

## Bloque B — El motor no bloquea ni cosecha un término que ya es keyword propia del ad group
lane: release
depende: A
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El motor ya no propone un negative ni un harvest sobre un término que es keyword EXACT de ese mismo ad group. Antes podía apagar una keyword propia.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/B/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | Unidad AD-U1 (hallazgo E-S1, ALTO). Leer `docs/evidencia/arreglos-2026-11-09/ads-decision/SINTESIS.md` y la sección `## E-S1` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Prueba (dinero), en `tests/test_optimizer_hygiene.py`: el término «Arras de Boda » con `keywords_propias={"arras de boda"}` da `kind None` y motivo `termino_es_keyword_propia`, tanto para negative como para harvest. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_hygiene.py -k arreglos01_b_ |
| B.2 | `app/optimizer/hygiene.py`: `decide_hygiene(..., *, keywords_propias: frozenset[str])`, obligatorio y sin valor por omisión (un llamador olvidado debe fallar, no volver al bug). El salto `termino_es_keyword_propia` va antes de separar negative y harvest. Lector por lote `hygiene.keywords_exactas_por_grupo(conn, platform, *, solo_grupo=None)`: una consulta para todos los ad groups, con el mismo normalizado de texto que usa el término. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_hygiene.py |
| B.3 | Llamadores, todos en este bloque: `app/cycle.py` (TX2 y `_procesa_grupo`), `app/apply_cola.py:1282`, `app/apply_harvest_reconciliacion.py:231` y `app/optimizer/replay.py:96` (pasa el conjunto vacío explícito, para no cambiar el replay de casos viejos). | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_cycle.py tests/test_apply_cola.py tests/test_apply_harvest.py tests/test_cycle_apply.py |
| B.4 | `docs/evidencia/arreglos-01/B/prueba.py` para `en_prod.sh`: importa `decide_hygiene` del código desplegado y afirma lo mismo que B.1, sin base ni red. | test -s docs/evidencia/arreglos-01/B/prueba.py |

## Bloque C — Se quitan los negatives que ya apagaron keywords propias
lane: gate
depende: B
aviso: Se quitaron en Amazon los dos negatives del 16 de septiembre que bloqueaban «arras de boda» y «arras for wedding ceremony» en su propio ad group, y el de «wedding arras» si alcanzó a aplicarse.
prueba-real: bash docs/evidencia/arreglos-01/C/prueba.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | Unidad R2. Leer con `lee_prod.sh` el estado de las colas 10 (decisión 2321), 11 (decisión 2322) y 26 (decisión 2869) en `apply_queue`, y en el ledger de aplicación si cada negative sigue puesto o ya tiene reversa confirmada (`app/apply.py:1665` `reversa_manual` dice dónde queda el registro). Guardar en `docs/evidencia/arreglos-01/C/antes.txt`. Con eso, escribir `docs/evidencia/arreglos-01/C/prueba.sh`: lee con `lee_prod.sh` y sale 0 solo si cada cola de 10, 11 y 26 que llegó a `applied` tiene su reversa confirmada. Debe fallar hoy. | test -s docs/evidencia/arreglos-01/C/antes.txt && test -s docs/evidencia/arreglos-01/C/prueba.sh |
| C.2 | Revertir, una por una, cada cola `applied` sin reversa confirmada: `bash docs/evidencia/arreglos-01/escribe_api.sh /api/ads-optimizer/reversa/negative '{"queue_id": 10}'` (igual con 11, y con 26 solo si está `applied`). Guardar cada respuesta completa en `docs/evidencia/arreglos-01/C/reversa.txt`. Una respuesta de «reversa ya hecha» cuenta como hecha. Si una respuesta trae `confirmada` en falso, parar el bloque y escribir en ese archivo el detalle del ledger; no reintentar a ciegas. | test -s docs/evidencia/arreglos-01/C/reversa.txt |

## Bloque D — Un tope diario imposible se rechaza al guardarlo
lane: release
depende: A
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Un tope diario demasiado grande ya se rechaza al guardarlo en Ajustes. Antes se guardaba y tumbaba la aplicación de cambios en cada ciclo.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh CAP_MAXIMO cap_desde_settings
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | Unidad AE-U1 (hallazgo I-H1, ALTO). Leer `docs/evidencia/arreglos-2026-11-09/ads-escritura/SINTESIS.md` y la sección `## I-H1` de `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`. Prueba (dinero), en `tests/test_settings_escritura.py`: `proxima_config(..., caps={"bid": 2**31})` lanza `SettingsInvalido`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_settings_escritura.py -k arreglos01_d_ |
| D.2 | `app/apply.py`: `CAP_MAXIMO = 2_147_483_647` y `cap_desde_settings`, única puerta por la que un tope pasa de settings a número. La usan el motor al aplicar y `proxima_config` (`app/config_write.py`) al guardar. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_settings_escritura.py tests/test_apply.py tests/test_api_write.py |
| D.3 | Antes de desplegar, leer con `lee_prod.sh` los topes vigentes de cada plataforma y guardar en `docs/evidencia/arreglos-01/D/topes-hoy.txt`. Si alguno pasa de `CAP_MAXIMO`, parar el bloque y escribirlo ahí: con el arreglo desplegado ese valor haría fallar el ciclo de forma visible. | test -s docs/evidencia/arreglos-01/D/topes-hoy.txt |

## Bloque E — El freno de precios no se suelta solo
lane: release
depende: A
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Un día en que el motor de precios frenó un movimiento masivo ya no cuenta como día normal al siguiente, así que el freno no se suelta solo. El ISR y la comisión variable dejaron de contar como «insumo raro».
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh _INSUMOS_POR_INGRESO
| Fila | Contenido | Acepta |
| --- | --- | --- |
| E.1 | Unidad PI-U1 (hallazgos G-H1 y G-H3, ALTO). Leer `docs/evidencia/arreglos-2026-11-09/precios-insumos/SINTESIS.md` y las secciones `## G-H1` y `## G-H3` de `docs/evidencia/arreglos-2026-11-09/precios-insumos/opus/DISENO.md`. Prueba (dinero), con base, junto a `tests/test_precio_corrida.py:4392`: un día con retención por `movimiento_masivo` que no está liberada no entra a la medida del día siguiente. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_precio_corrida.py -k arreglos01_e_ |
| E.2 | `app/precio/corrida.py` `_SQL_MEDIR_PREVIA` (`:953-960`): saca el día completo del universo (plataforma, canal, fecha) cuando hubo una retención por `movimiento_masivo` o `insumo_sistemico` que no está en `v_precio_decision_liberada`. Las tasas se comparan sobre el ingreso de la misma decisión: `_INGRESO = (13, 14)` e `_INSUMOS_POR_INGRESO = frozenset({"retencion", "fee_variable"})`. Las dos cosas van juntas, sobre la misma consulta. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_precio_corrida.py tests/test_precio_compuerta.py |

## Bloque F — Un costo corregido ya no congela el SKU ni deja a todos sin evaluar
lane: release
depende: A
pruebas: datos
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Corregir un costo ya no congela ese producto ni deja a todos los demás sin evaluar. Un costo nuevo entra al día siguiente. Si una corrida fuera a cerrar más del 10 % de los productos, se detiene sin escribir y te avisa por Telegram.
prueba-real: bash docs/evidencia/arreglos-01/migracion_aplicada.sh arreglos01_costos_sin_compuerta && bash docs/evidencia/arreglos-01/desplegado.sh acepto_cierre_masivo
| Fila | Contenido | Acepta |
| --- | --- | --- |
| F.1 | Unidad PI-U3 (hallazgo H-H1, ALTO). Leer `docs/evidencia/arreglos-2026-11-09/precios-insumos/SINTESIS.md` y la sección `## H-H1` de `docs/evidencia/arreglos-2026-11-09/precios-insumos/opus/DISENO.md`. Prueba (datos de producción), en `tests/test_costs_sync.py`: un SKU sin costo en la copia no frena a los demás, y un cierre de más del 10 % de los SKU aborta sin escribir nada. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_costs_sync.py -k arreglos01_f_ |
| F.2 | Migración `0083_arreglos01_costos_sin_compuerta.sql` y su reversa: quita el trigger de la compuerta global de `migrations/0073_precio02_meli_miembro_costo.sql:265`. La reversa lo restaura con el texto exacto de la 0073. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py |
| F.3 | `app/costs.py`: `Cierre`, `PlanSku(cierres, inserciones, convergencia, sin_costo)`, `recortar(serie, *, corte)`, `planear_sku(serie, publicadas, *, corte)` y `sync_costos(conn, ruta_sqlite, *, corte, acepto_cierre_masivo=False)` con tope de 10 % de SKU cerrados; al pasarlo aborta sin escribir y manda aviso por `notifica.envia_aviso`. Un costo nuevo entra al día siguiente (`recortar`). Se quita la compuerta `rows_skipped` de `app/estimacion_insumos.py:459,479`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_costs_sync.py tests/test_estimacion_insumos.py |
| F.4 | Poner al día las tres letras que decían lo contrario: `plans/orbit-06.md:233-236`, el docstring de `app/costs.py` y el comentario de `app/costs.py:524-527`. | grep -q "día siguiente" plans/orbit-06.md |

## Bloque G — El vigilante de SP-API avisa cuando la corrida falló
lane: release
depende: A
pruebas: falla
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El vigilante de la ingesta de Amazon ya avisa cuando una corrida falló, no solo cuando faltó.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh MOTIVO_FALLO_O_FALTA
| Fila | Contenido | Acepta |
| --- | --- | --- |
| G.1 | Unidad OP-H3 (hallazgo H-H3). Leer `docs/evidencia/arreglos-2026-11-09/operacion/SINTESIS.md` y la sección `## H-H3` de `docs/evidencia/arreglos-2026-11-09/operacion/sonnet/DISENO.md` (base sonnet). Prueba (falla que ya pasó): se invierte la aserción de `tests/test_spapi_vigilante.py:59` y la prueba pasa a llamarse `test_arreglos01_g_...`: una corrida con `ok` falso cuenta como faltante. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_spapi_vigilante.py -k arreglos01_g_ |
| G.2 | `app/spapi/vigilante.py` `faltantes`: una sola condición, `ok is False` no cuenta como presente. El texto del aviso sale de una constante `MOTIVO_FALLO_O_FALTA` que dice «falló o no corrió». Sin tipos nuevos. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_spapi_vigilante.py |

## Bloque H — Revertir una subida ya no deja un piso que bloquea recortes
lane: release
depende: B
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Cuando el motor deshace una subida de bid, ya no queda un «piso» que le impedía recortar ese bid durante 90 días.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/H/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| H.1 | Unidad AD-U2 (hallazgo E-H2). Leer la sección `## E-H2` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Correr con `lee_prod.sh` su consulta de «antes de desplegar» (reversas de subida ya registradas) y guardar la salida en `docs/evidencia/arreglos-01/H/previa.txt`, con una última línea `filas=<n>`. El 2026-11-09 dio 0. Si no da 0, parar el bloque: hace falta marcador congelado y eso es otro diseño. | grep -q '^filas=0$' docs/evidencia/arreglos-01/H/previa.txt |
| H.2 | Prueba (dinero), pura, en `tests/test_optimizer_caso.py`: una trayectoria 1.00 → 1.15 → 1.00 da `piso_aprendido is None`. Y `app/optimizer/caso.py` `Trayectoria.piso_aprendido`: solo cuenta los regresos con `direccion == 1` (los que deshacen un recorte). No se agrega «techo aprendido» (decisión 15). | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_caso.py |
| H.3 | `docs/evidencia/arreglos-01/H/prueba.py` para `en_prod.sh`: la misma trayectoria de H.2 contra el código desplegado. | test -s docs/evidencia/arreglos-01/H/prueba.py |

## Bloque I — Apagar la protección de venta descarta las pausas en espera
lane: release
depende: H
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Si apagas la protección de venta, las pausas que estaban esperando se descartan. Antes se aplicaban todas de golpe.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh descarta_esperas_proteccion
| Fila | Contenido | Acepta |
| --- | --- | --- |
| I.1 | Unidad AD-U5 (hallazgo D-H1). Leer la sección `## D-H1` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Prueba (dinero), con base, en `tests/test_orbit07_m1.py`: con una pausa en `espera_proteccion` y la protección apagada, `libera_vencidos` la descarta y no la aplica. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_orbit07_m1.py -k arreglos01_i_ |
| I.2 | `app/apply_cola.py`: `descarta_esperas_proteccion(conn, platform) -> int`. La llaman `libera_vencidos` cuando la protección está apagada y `app/cli_productos.py` al apagarla, en la misma transacción que el cambio de modo. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_orbit07_m1.py tests/test_apply_cola.py tests/test_optimizer_proteccion.py |

## Bloque J — La guarda de «estructura vieja» mira la última sincronización real
lane: release
depende: I
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El motor ya sabe de verdad cuándo fue la última vez que leyó tus campañas de Amazon. Si esa lectura lleva 48 horas sin correr, deja de decidir sobre datos viejos.
prueba-real: bash docs/evidencia/arreglos-01/J/prueba.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| J.1 | Unidad AE-U2 (hallazgo E-H1 = F-H2). Leer la sección `## E-H1 = F-H2` de `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`. Prueba (dinero), en `tests/test_optimizer_windows.py`: con el último sync de estructura de hace 60 h y una escritura del aplicador de hace 1 h, la guarda dice que la estructura está vieja. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_windows.py -k arreglos01_j_ |
| J.2 | `app/optimizer/windows.py` `_SQL_SYNC_PLATAFORMA`: la fecha sale del último sync de estructura que terminó bien (acta `ads_listado_plataforma`), no de una columna que también escribe el aplicador. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_windows.py |
| J.3 | `docs/evidencia/arreglos-01/J/prueba.sh`: solo lectura; con `lee_prod.sh` saca la fecha del último sync bueno por plataforma y con `en_prod.sh` comprueba que el código desplegado devuelve esa misma fecha. Debe fallar hoy si hubo una escritura del aplicador después del último sync; si hoy coinciden, anotarlo en `docs/evidencia/arreglos-01/J/nota.txt`. | test -s docs/evidencia/arreglos-01/J/prueba.sh |

## Bloque K — Un bid con resultado dudoso espera a confirmarse
lane: release
depende: D, J
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Si Amazon no confirma un cambio de bid, el motor espera a confirmarlo antes de volver a mover ese bid. Se quitó el reenvío automático de bids viejos.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh hojas_con_bid_sin_sello MOTIVO_BID_EN_VUELO && bash docs/evidencia/arreglos-01/desplegado.sh --no _reintento_divergente
| Fila | Contenido | Acepta |
| --- | --- | --- |
| K.1 | Unidad AE-U3 (hallazgos E-H3 y F-H1; decisión 4). Leer las secciones `## E-H3` y `## F-H1` de `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`. Prueba (dinero), con base, en `tests/test_cycle_apply.py`: una hoja con un bid enviado y sin sello no recibe otro recorte en el ciclo siguiente; sale con `MOTIVO_BID_EN_VUELO`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_cycle_apply.py -k arreglos01_k_ |
| K.2 | `app/apply.py`: `hojas_con_bid_sin_sello(conn, platform) -> frozenset[int]` y `MOTIVO_BID_EN_VUELO`. Se borra `_reintento_divergente`; `reconcilia_bids` confirma o marca, nunca reenvía. `docs/APPLY.md:529` se pone al día (cambia la decisión ADV-04). | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_apply.py tests/test_cycle_apply.py tests/test_cycle.py tests/test_apply_cola.py |

## Bloque L — La corrida de precios espera el insumo del día
lane: release
depende: E
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Si la oferta del día llega tarde, el motor de precios espera a la siguiente corrida en vez de gastar la decisión del día con datos viejos. Espera hasta las 21:00 UTC.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/L/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| L.1 | Unidad PI-U2 (hallazgo J-H3; decisión 11). Leer la sección `## J-H3` de `docs/evidencia/arreglos-2026-11-09/precios-insumos/opus/DISENO.md`. Antes de tocar código, leer en el servidor las horas reales de la corrida de precios (`ssh goncloud 'crontab -u gon -l'`, solo las líneas de precios) y guardarlas en `docs/evidencia/arreglos-01/L/horas.txt`. Si no hay ninguna corrida después de las 21:00 UTC, parar el bloque y escribirlo ahí: la espera dejaría días sin decisión. | test -s docs/evidencia/arreglos-01/L/horas.txt |
| L.2 | Prueba (dinero), en `tests/test_precio_compuerta.py`: un lote con mayoría `oferta_desactualizada` a las 13:10 UTC devuelve motivo de espera y la corrida no escribe decisiones; a las 21:10 UTC devuelve `None` y decide. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_precio_compuerta.py -k arreglos01_l_ |
| L.3 | `app/precio/compuerta.py`: `MOTIVOS_INSUMO_ATRASADO`, `ESPERA_INSUMOS_HASTA = time(21, 0)` e `insumo_atrasado(motivos, *, ahora_utc) -> str \| None`. `_decidir_pendientes` (`app/precio/corrida.py`) la usa: si espera, regresa sin escribir. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_precio_compuerta.py tests/test_precio_corrida.py |
| L.4 | `docs/evidencia/arreglos-01/L/prueba.py` para `en_prod.sh`: los dos casos de L.2 contra `insumo_atrasado` del código desplegado. | test -s docs/evidencia/arreglos-01/L/prueba.py |

## Bloque M — El despliegue espera a que no haya un ciclo corriendo
lane: release
depende: A
pruebas: instala
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Un despliegue ya no puede cortar a la mitad un ciclo del motor. Espera a que termine.
prueba-real: ssh goncloud 'docker ps --filter name=orbit-app-1 --format "{{.Status}}"' | grep -q '^Up'
| Fila | Contenido | Acepta |
| --- | --- | --- |
| M.1 | Unidad OP-J4 (hallazgo J-H4). Leer la sección `## J-H4` de `docs/evidencia/arreglos-2026-11-09/operacion/opus/DISENO.md` y el injerto de la síntesis. Prueba (instalar en las máquinas), con `ORBIT_SIMULACION=1`, en `tests/test_despliegue_motor.py`: con un proceso `app.cli` vivo, `recrea_contenedor` sale 75 sin recrear; sin procesos, recrea y sale 0. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py -k arreglos01_m_ |
| M.2 | `tools/despliegue/comun.sh`: `recrea_contenedor()` construye la imagen aparte y en UN solo comando remoto espera cero procesos `app.cli` y recrea. Devuelve 0, 75 (no recreó) o error. En reversa espera 300 s y recrea de todos modos con una línea `AVISO`. Se borra `compose_up` y migran sus dos llamadores. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py |

## Bloque N — El cron de Orbit vive en el repo y su instalador nunca quita los avisos
lane: release
depende: M
pruebas: instala
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Los horarios de las tareas de Orbit ya viven en un archivo del repositorio. Instalarlos nunca quita los avisos de falla que ya tienes.
prueba-real: test "$(ssh goncloud 'crontab -u gon -l' | grep -c cron-avisa)" -ge "$(cat docs/evidencia/arreglos-01/N/envoltorios-antes.txt)"
| Fila | Contenido | Acepta |
| --- | --- | --- |
| N.1 | Unidad OP-J1 (hallazgo J-H1; decisión 18). Leer la sección `## J-H1` de `docs/evidencia/arreglos-2026-11-09/operacion/sonnet/DISENO.md` (base sonnet) y los injertos de la síntesis. Leer el crontab real: `ssh goncloud 'crontab -u gon -l'`. Sembrar `tools/despliegue/crontab-orbit.txt` con el bloque `# === Orbit` tal como está hoy, sin cambiar una sola hora. Guardar el número de líneas con `cron-avisa` en `docs/evidencia/arreglos-01/N/envoltorios-antes.txt` (solo el número). Si una línea del bloque trae una llave o un token escrito, parar el bloque y anotar en `docs/evidencia/arreglos-01/N/diferencia.md` qué tarea es, sin el valor: esa línea no entra al repo. | python3 -c "n=int(open('docs/evidencia/arreglos-01/N/envoltorios-antes.txt').read());assert n>=8" && test -s tools/despliegue/crontab-orbit.txt |
| N.2 | `tools/despliegue/cron_orbit.sh instala\|revisa`, con el molde de `tools/despliegue/cron_jev_vigia.sh` (respalda el crontab antes de escribir; correrlo dos veces deja lo mismo). `instala` exige `test -x /usr/local/bin/cron-avisa`, cuenta envoltorios antes y después y, si bajan, aborta sin escribir. `revisa` compara el bloque del servidor contra el archivo y sale 1 con el diff. `ingest diario` y `spapi-diario.sh` siguen sin envoltorio; `cron-avisa` sigue viviendo solo en el servidor. Prueba (instalar y deshacer en las máquinas), con un `crontab` falso en el PATH, en `tests/test_despliegue_motor.py`: `test_arreglos01_n_el_instalador_no_quita_cron_avisa`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py -k arreglos01_n_ |
| N.3 | `tools/despliegue/desplegar.sh`: corre `cron_orbit.sh revisa` en el ensayo e `instala` en el despliegue real, igual que hoy con `cron_vigia` (`:56` y `:225`). Se borra el heredoc de `docs/DEPLOY.md:1160-1212` y queda una línea que apunta al script. Como N.1 sembró el archivo con lo que hay hoy, esta primera instalación no cambia ninguna tarea. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py |

## Bloque O — Una venta reciente conocida detiene la pausa
lane: release
depende: K
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Si una keyword tuvo una venta reciente, el motor ya no la pausa aunque falte el dato de un día o la keyword haya dejado de recibir filas.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/O/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| O.1 | Unidad AD-U3 (hallazgos A-H1 y A-H2). Leer las secciones `## A-H1` y `## A-H2` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Correr con `lee_prod.sh` su consulta de «antes de desplegar» (episodios de protección y filas `espera_proteccion`) y guardar en `docs/evidencia/arreglos-01/O/previa.txt`, con una última línea `filas=<n>`. El 2026-11-09 dio 0. Si no da 0, parar el bloque. | grep -q '^filas=0$' docs/evidencia/arreglos-01/O/previa.txt |
| O.2 | Pruebas (dinero): pura, en `tests/test_optimizer_proteccion.py`, `pedidos=None, ancla=D-3, cubierta=True` da `esperar` y abre episodio; con base, en `tests/test_orbit07_m1.py`, una venta en el hueco entre la ventana de cortes y la reciente detiene la pausa. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_proteccion.py tests/test_orbit07_m1.py -k arreglos01_o_ |
| O.3 | `app/optimizer/proteccion.py` `evalua()`: sin episodio, `if lectura.ancla is not None: return _espera(..., abre=True)` antes de la decisión 6, que queda intacta. `lee_cache` (`app/proteccion_venta.py`) conserva el ancla aunque falte cobertura; la ventana reciente de cada hoja empieza el día siguiente al fin de su ventana de cortes (la cota sale de las constantes que ya existen, no de un `DIAS_LECTURA = 40` nuevo). Se borra `hay_venta` y la compuerta de `app/cycle.py:1250-1254` pasa a «episodio o ancla reciente». | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_proteccion.py tests/test_orbit07_m1.py tests/test_cycle.py |
| O.4 | `docs/evidencia/arreglos-01/O/prueba.py` para `en_prod.sh`: el caso puro de O.2 contra `evalua` del código desplegado. | test -s docs/evidencia/arreglos-01/O/prueba.py |

## Bloque P — No se sube el bid de una keyword que está en espera de venta
lane: release
depende: O
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El motor ya no sube el bid de una keyword cuya pausa está en espera por una venta reciente.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/P/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| P.1 | Unidad AD-U7 (hallazgo C-H4; decisión 14). Leer la sección `## C-H4` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Prueba (dinero), pura: con `frena_subidas=True` y un factor de subida, `politica.decide` da `Mantener("subida_en_espera_de_venta")`; con `frena_subidas=False` decide como hoy. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/ -k arreglos01_p_ |
| P.2 | `InsumosProteccion.frena_subidas: bool = False` (marcador congelado: los casos viejos se rejuegan igual) y, en `app/optimizer/politica.py` `decide`, `factor > 0` con espera activa da `Mantener("subida_en_espera_de_venta")`. El ciclo lo llena desde el episodio de espera. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_proteccion.py tests/test_orbit07_m1.py tests/test_cycle.py |
| P.3 | `docs/evidencia/arreglos-01/P/prueba.py` para `en_prod.sh`: los dos casos de P.1 contra el código desplegado. | test -s docs/evidencia/arreglos-01/P/prueba.py |

## Bloque Q — Un impulso cuyo grupo pausaste se cierra limpio
lane: release
depende: F
pruebas: datos
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Si pausas el grupo de un impulso, Orbit lo da por terminado y lo dice así. Antes el vigía fallaba todos los días por ese impulso y el producto no podía volver a impulsarse.
prueba-real: bash docs/evidencia/arreglos-01/migracion_aplicada.sh arreglos01_impulso_desarmado && bash docs/evidencia/arreglos-01/desplegado.sh VEREDICTO_DESARMADO
| Fila | Contenido | Acepta |
| --- | --- | --- |
| Q.1 | Unidad AE-U4 (hallazgo I-H2; decisión 7). Leer la sección `## I-H2` de `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`. Prueba (escribe datos de producción), en `tests/test_impulso_io.py`: un lote `desarmado` con métricas estancadas (19 días, 5,000 impresiones, 40 clics, 0 pedidos) y un pausador falso cuyo `pausa` hace fallar la prueba. El vigía guarda la lectura final con veredicto `desarmado`, no falla, y `frase_final("desarmado")` devuelve su frase. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_impulso_io.py -k arreglos01_q_ |
| Q.2 | Migración `0084_arreglos01_impulso_desarmado.sql` y su reversa, con el patrón de cabecera de la 0079: el CHECK `lectura_veredicto` de `impulso_lectura` admite `desarmado`. La reversa falla con mensaje claro si ya hay filas `desarmado` y lo dice en su cabecera. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py |
| Q.3 | `app/impulso.py`: `EstadoVeredicto` suma `"desarmado"` y `VEREDICTO_DESARMADO = VeredictoImpulso("desarmado", True, "pausar")`; `veredicto_impulso` no cambia. `app/impulso_io.py` `_vigila_uno`: un lote `desarmado` cuya acción no es `graduar` toma `VEREDICTO_DESARMADO`; en la rama `pausar`, un lote `pausado` o `desarmado` va por `pausador.pausa_huerfanas(lote)` y no por `pausa`. `app/pantalla_productos.py` `FRASES_FINALES` suma la frase de `desarmado` («Lo pausaste desde la fábrica. Ya no gasta.»). Actualizar la ficha de esa pantalla en `.cursor/skills/verify-orbit/features/`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_impulso.py tests/test_impulso_io.py tests/test_impulso_vigia_cron.py |

## Bloque R — Las publicaciones de MeLi con variaciones sí reciben su precio
lane: release
depende: L
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Las publicaciones de Mercado Libre con variaciones ya pueden recibir su precio. Antes fallaban siempre. Aplica cuando precios pase a en vivo.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh --no _unidad_de_listing
| Fila | Contenido | Acepta |
| --- | --- | --- |
| R.1 | Unidad PI-U4 (hallazgo G-H2). Leer la sección `## G-H2` de `docs/evidencia/arreglos-2026-11-09/precios-insumos/opus/DISENO.md`. Prueba (dinero), en `tests/test_precio_write.py`: una publicación con miembros `111` y `222` llama `poner_precio('MLM123', ['111','222'], ...)`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_precio_write.py -k arreglos01_r_ |
| R.2 | `app/precio/puerto.py`: `Vitrina.unidades(conn, listing_ids) -> Mapping[int, UnidadPrecio]`, lector por lote. Migran todos los que arman la unidad a mano, incluidos `app/precio/corrida.py:884` y `app/precio/goals_write.py:427`. Se borra `_unidad_de_listing`. `_ruta_error` a la entrada de `escribir`. Sin candado de texto en `tests/test_architecture.py`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_precio_write.py tests/test_precio_corrida.py tests/test_goals_write.py |
| R.3 | No está medido cómo responde MeLi cuando las variaciones de una publicación tienen precios distintos. Con `lee_prod.sh`, contar cuántas publicaciones de MeLi tienen variaciones con precios distintos hoy y guardarlo en `docs/evidencia/arreglos-01/R/variaciones.txt`. Si hay alguna, la escritura de esas publicaciones queda retenida con motivo propio hasta que se decida; anotarlo ahí. | test -s docs/evidencia/arreglos-01/R/variaciones.txt |

## Bloque S — El ciclo reintenta si las métricas llegan tarde
lane: release
depende: K, N
pruebas: falla
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Si las métricas de Amazon llegan tarde, el ciclo y el vigía lo vuelven a intentar a las 10:40, 13:40 y 16:40 UTC. Si al final no llegaron, te avisan.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/S/prueba.py && test "$(ssh goncloud 'crontab -u gon -l' | grep -c '8,10,13,16')" -ge 4
| Fila | Contenido | Acepta |
| --- | --- | --- |
| S.1 | Unidad OP-H2 (hallazgo H-H2 = I-H3 = J-H2; decisión 17). Leer `docs/evidencia/arreglos-2026-11-09/operacion/SINTESIS.md` (la «Forma final» manda) y la sección `## H-H2 = I-H3 = J-H2` de `docs/evidencia/arreglos-2026-11-09/operacion/opus/DISENO.md`. Antes de tocar código, buscar en el repo y en el crontab del servidor quién lee el código de salida de `cycle`, `bids`, `avisos-campana`, `impulso-vigia` y `jev-senales`, y guardar la lista en `docs/evidencia/arreglos-01/S/llamadores.txt`. Si alguno trata el 75 como falla, parar el bloque. | test -s docs/evidencia/arreglos-01/S/llamadores.txt |
| S.2 | Prueba (falla que ya pasó), en `tests/test_metricas_listas.py`: `test_arreglos01_s_cada_consumidor_tiene_horario_tras_el_ultimo_plazo_de_ingesta` lee `tools/despliegue/crontab-orbit.txt` y afirma que cada consumidor de métricas tiene una hora después de `fin_de_intentos`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_metricas_listas.py -k arreglos01_s_ |
| S.3 | `app/metricas_listas.py`: `SALIDA_ESPERA = 75`, `fin_de_intentos(hoy)` (último `HORARIOS_INGESTA` + `TOPE_PARED_SEGUNDOS`, `app/ads/diario.py:56,60`) y `salida_sin_metricas(comando, *, ahora=None) -> int`: antes del fin imprime la espera y devuelve 75; después escribe a stderr y devuelve 1. Llamadores: `app/cli.py:113`, `app/cli_bids.py:74-76,125`, `app/jev_senales.py:809`. Sin banderas `--reintento` ni `--ultimo`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_metricas_listas.py tests/test_cli.py tests/test_cli_bids.py |
| S.4 | `tools/despliegue/crontab-orbit.txt`: horas `8,10,13,16` en la misma línea de avisos-campana, impulso-vigia y los dos `cycle`, con el minuto de hoy. El despliegue las instala con `cron_orbit.sh` (bloque N). `docs/evidencia/arreglos-01/S/prueba.py` para `en_prod.sh`: `fin_de_intentos` da las 16:00 UTC y `salida_sin_metricas` antes de esa hora da 75. | grep -c '8,10,13,16' tools/despliegue/crontab-orbit.txt \| grep -qx 4 && test -s docs/evidencia/arreglos-01/S/prueba.py |

## Bloque T — El freno por un cambio caduca y se invalida si hubo cortes en el mismo ad group
lane: release
depende: P
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El freno que un cambio de bid deja sobre las subidas ya caduca el día 31 y no cuenta si en esos días hubo negatives o harvests en el mismo ad group. Antes podía frenar subidas hasta 90 días por una semana ruidosa.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh evidencia_vencida
| Fila | Contenido | Acepta |
| --- | --- | --- |
| T.1 | Unidad AD-U6a (hallazgos C-H1 y C-H3; decisión 13). Leer las secciones `## C-H1` y `## C-H3` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md` y la nota de la síntesis (con la caducidad, C-H3 queda inalcanzable y no lleva código propio). Pruebas (dinero): un episodio con x+31 cumplido da `evidencia_vencida` y no frena; un negative o harvest aplicado en el mismo ad group dentro de la ventana invalida el episodio. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/ -k arreglos01_t_ |
| T.2 | Lectura: quinta fuente de invalidación y caducidad en x+31, en `app/lecturas_caso.py`, `app/optimizer/economia_cambio.py`, `app/optimizer/caso.py`, `app/optimizer/politica.py:385` (`_freno_trayectoria`) y `app/optimizer/goals.py:883`. Cumplir la regla de replay del encabezado: marcador congelado, o la consulta previa de la sección en 0 guardada en `docs/evidencia/arreglos-01/T/previa.txt`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_optimizer_caso.py tests/test_optimizer_goals.py tests/test_orbit07_m5m6.py tests/test_cycle.py |

## Bloque U — Juicio v2 del cambio de bid, encendido
lane: release
depende: T
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: El motor ya exige un mínimo de clics para juzgar si un cambio de bid funcionó, y frena la subida aunque la semana previa haya tenido 0 ventas. Quedó encendido en las plataformas donde ya corría el juicio anterior.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh economia_cambio_v2 && bash docs/evidencia/arreglos-01/U/prueba.sh
| Fila | Contenido | Acepta |
| --- | --- | --- |
| U.1 | Unidad AD-U6b (hallazgo C-H2). Leer la sección `## C-H2` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Pruebas (dinero), en `tests/test_orbit07_m5m6.py`: con v2, menos clics que el mínimo da «sin juicio»; `post.venta <= pre.venta` frena aunque `pre.venta` sea 0. Con v1 el veredicto no cambia. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_orbit07_m5m6.py -k arreglos01_u_ |
| U.2 | `economia_cambio_v2` como versión nueva, elegida por setting. Las decisiones ya juzgadas con v1 conservan su veredicto; solo los episodios nuevos usan v2. `productos-modo --bid-economia` acepta `economia_cambio_v2`. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_orbit07_m5m6.py tests/test_optimizer_goals.py tests/test_productos_meta_ciclo.py |
| U.3 | Antes del despliegue, leer con `lee_prod.sh` qué economía de bid tiene hoy cada plataforma y guardarlo en `docs/evidencia/arreglos-01/U/antes.txt`. Escribir `docs/evidencia/arreglos-01/U/prueba.sh`: solo lectura; sale 0 solo si cada plataforma que en `antes.txt` tenía la v1 tiene ahora `economia_cambio_v2`. | test -s docs/evidencia/arreglos-01/U/antes.txt && test -s docs/evidencia/arreglos-01/U/prueba.sh |
| U.4 | Después del despliegue, encender v2 en cada plataforma que tenía v1: `ssh goncloud 'docker exec orbit-app-1 python -m app.cli productos-modo --platform <plataforma> --bid-economia economia_cambio_v2'`. Guardar la salida en `docs/evidencia/arreglos-01/U/encendido.txt`. Las plataformas que estaban apagadas se quedan apagadas. Para deshacerlo, el mismo comando con el valor de `antes.txt`. | test -s docs/evidencia/arreglos-01/U/antes.txt |

## Bloque V — Una keyword que pierde la meta de su producto pasa a la de su familia
lane: release
depende: U
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Cuando una keyword pierde la meta de su producto, pasa a la meta de su familia en ese mismo ciclo. Ya no brinca a la meta general de la plataforma. Si el ledger viene atrasado, se acerca poco a poco en vez de saltar.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/V/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| V.1 | Unidad AD-U8 (hallazgo B-H1; decisión 12). Leer la sección `## B-H1` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Antes de tocar código, comprobar qué devuelve hoy `converge_al_destino(None, ...)` y escribirlo en `docs/evidencia/arreglos-01/V/converge.txt` (se infirió que cae a la meta de plataforma; no está comprobado). | test -s docs/evidencia/arreglos-01/V/converge.txt |
| V.2 | Prueba (dinero): cambia la expectativa de `tests/test_orbit07_m2.py:402-438` (la keyword toma el valor de su familia en el mismo ciclo) y un caso nuevo `test_arreglos01_v_...`: con motivo `ledger_rancio` converge en vez de saltar. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_orbit07_m2.py |
| V.3 | `app/optimizer/productos.py`: `ancla_familia(previo, destino, *, version) -> tuple[Decimal \| None, bool]` y `MOTIVOS_DATO_TRANSITORIO = frozenset({"ledger_rancio"})`. Única fuente para el ciclo (`app/cycle.py:3136`) y para la cola (`app/economia_revalidacion_io.py:151`, que hoy lo arma con una copia a mano). No se interpola entre economías (`plans/orbit-07-motor-decisiones.md:237`). | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_orbit07_m2.py tests/test_productos_meta_ciclo.py tests/test_cycle.py tests/test_apply_cola.py |
| V.4 | `docs/evidencia/arreglos-01/V/prueba.py` para `en_prod.sh`: los dos casos de V.2 contra `ancla_familia` del código desplegado. | test -s docs/evidencia/arreglos-01/V/prueba.py |

## Bloque W — La pausa de producto no ignora un día reciente sin medir
lane: release
depende: V
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: La pausa de producto, que hoy corre solo en práctica, ya toma en cuenta un día reciente al que le falta el dato. Antes lo descartaba sin decirlo.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh venta_reciente_sin_medir dias_recientes_sin_medir
| Fila | Contenido | Acepta |
| --- | --- | --- |
| W.1 | Unidad AD-U4 (hallazgo A-H3, corre en shadow). Leer la sección `## A-H3` de `docs/evidencia/arreglos-2026-11-09/ads-decision/opus/DISENO.md`. Campo `dias_recientes_sin_medir` en `EntradaProducto` (`app/optimizer/productos.py`, llenado en `app/productos_io.py:310`) y motivo `venta_reciente_sin_medir` en `motivos_economicos_pausa` (`app/apply_producto.py:182-187`). No es un impedimento: no debe bloquear la meta de producto (`_motivos_sin_meta`, `app/optimizer/productos.py:464-465`). Sin prueba nueva. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_productos_pausa.py tests/test_productos_io.py tests/test_apply_producto.py tests/test_productos_meta_ciclo.py |

## Bloque X — Tope máximo para las cifras de dinero tecleadas a mano
lane: release
depende: W
pruebas: dinero
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Orbit ya rechaza una cifra tecleada por error. Un bid a mano no pasa de 10 dólares o 180 pesos, un presupuesto diario no pasa del doble del más alto que tienes hoy, y una meta manual de ACoS va de 10 a 45 %.
prueba-real: bash docs/evidencia/arreglos-01/en_prod.sh docs/evidencia/arreglos-01/X/prueba.py
| Fila | Contenido | Acepta |
| --- | --- | --- |
| X.1 | Unidad AE-U5 (hallazgo I-H4; decisiones 5 y 6). Leer la sección `## I-H4` de `docs/evidencia/arreglos-2026-11-09/ads-escritura/opus/DISENO.md`. Medir con `lee_prod.sh` el presupuesto diario más alto de hoy por moneda (campañas activas) y guardarlo en `docs/evidencia/arreglos-01/X/presupuestos.txt`. El tope de presupuesto diario es el doble de ese valor, redondeado hacia arriba al entero. | test -s docs/evidencia/arreglos-01/X/presupuestos.txt |
| X.2 | Prueba (dinero): `planea_ajuste` con un presupuesto de 5000 MXN lanza `ValueError`; un bid a mano de 11 USD se rechaza; una meta manual de ACoS de 5 % o de 60 % se rechaza. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_api_fabrica.py tests/test_fabrica_plan.py tests/test_optimizer_goals.py -k arreglos01_x_ |
| X.3 | `app/optimizer/goals.py`: `TECHOS_HUMANOS_POR_MONEDA` (bid USD 10 y MXN 180; presupuesto diario según X.1), `exige_bajo_techo` y `exige_banda_meta_manual` (10 a 45 %). Se llaman en `app/api_fabrica.py:226` y `tools/fabrica_campanas.py:587,612`, no dentro de `valida_parametros`. Solo rechazan lo que teclea una persona; no tocan lo que decide el motor. El mensaje de rechazo va en español llano. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_api_fabrica.py tests/test_fabrica_plan.py tests/test_optimizer_goals.py tests/test_goals_write.py |
| X.4 | `docs/evidencia/arreglos-01/X/prueba.py` para `en_prod.sh`: `exige_bajo_techo` rechaza 5000 MXN de presupuesto y acepta el presupuesto más alto de hoy. | test -s docs/evidencia/arreglos-01/X/prueba.py |

## Bloque Y — Corregir el producto de un ASIN mueve sus ventas pasadas, con rastro
lane: release
depende: Q
pruebas: datos
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Cuando corriges a qué producto pertenece un ASIN de Amazon, sus ventas pasadas se mueven al producto correcto y queda registro de cada movimiento. Las ventas que hoy no tienen producto no se tocan.
prueba-real: bash docs/evidencia/arreglos-01/migracion_aplicada.sh arreglos01_ledger_reatribucion && bash docs/evidencia/arreglos-01/desplegado.sh _reatribuir_ventas
| Fila | Contenido | Acepta |
| --- | --- | --- |
| Y.1 | Unidad PI-U5 (hallazgo H-H4; decisión 16). Leer la sección `## U6. H-H4` de `docs/evidencia/arreglos-2026-11-09/precios-insumos/sonnet/DISENO.md` (base sonnet). Prueba (datos de producción), con base, en `tests/test_ledger_atribucion.py`: un UPDATE que cambia el producto de una venta deja una fila en `ledger_reatribucion`; un UPDATE de otro campo sigue rechazado; un UPDATE de sin producto a producto sigue rechazado. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ledger_atribucion.py -k arreglos01_y_ |
| Y.2 | Migración `0085_arreglos01_ledger_reatribucion.sql` y su reversa: tabla `ledger_reatribucion` y trigger `ledger_solo_reatribuye`, que escribe él mismo la fila de auditoría. Solo de producto a producto y solo Amazon. La reversa conserva la tabla de auditoría si ya tiene filas y lo dice. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_despliegue_motor.py tests/test_ledger_atribucion.py |
| Y.3 | Código: `_reatribuir_ventas(conn, ventas, *, run_id) -> int`, llamado donde hoy se detecta el cambio de producto de un ASIN. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_ledger_atribucion.py tests/test_ledger_sync.py |

## Bloque Z — Un impulso pausado con su propio botón no se queda atorado
lane: release
depende: Q
tipo: bug
pruebas: falla
despliegue: desplegar=tools/despliegue/desplegar.sh checklist=tools/despliegue/checklist.sh rollback=tools/despliegue/rollback.sh
aviso: Un impulso que pausas con su propio botón ya no puede quedarse atorado sin cerrar.
prueba-real: bash docs/evidencia/arreglos-01/desplegado.sh arreglos01_z
| Fila | Contenido | Acepta |
| --- | --- | --- |
| Z.1 | Decisión 8 del dueño: unidad aparte, después de Q. Sospecha, no comprobada: un impulso pausado con su propio botón puede atorarse en `app/impulso.py:187` igual que el de Q. Reproducirlo primero con una prueba con base en `tests/test_impulso_io.py` (`test_arreglos01_z_...`) que falle en el SHA de Q. Escribir la causa con archivo:línea en `docs/evidencia/arreglos-01/Z/causa.md`. Si no se reproduce, escribir ahí por qué y qué se probó, y parar el bloque: no se fuerza un arreglo. | test -s docs/evidencia/arreglos-01/Z/causa.md |
| Z.2 | Arreglar la causa donde nace, con la misma salida limpia que Q (el lote cierra con un veredicto que la pantalla dice en español llano). En la función arreglada, una línea de comentario con la regla y la marca `arreglos01_z`, para que la prueba real vea el arreglo desplegado. | PYTHONPATH=. uv run --frozen --python 3.12 python -m pytest -q tests/test_impulso_io.py tests/test_impulso.py tests/test_impulso_vigia_cron.py |
