# JEV ADS 01 — asesoría semántica para Ads

Estado: plan creado el 2026-10-03. Cierre de ledger 2026-10-04: filas 0.1 a 2.R
en `cc:DONE` con su evidencia. Cierre del 2026-10-04 (B7 a B11): R1 a R18 en
`cc:DONE` y 2.3 desplegada apagada. Piloto 3.1 corrido y decisión 3.2 tomada
el 2026-10-04: seguir y ampliar en `plans/jev-ads-02.md`. R21 sigue abierta.
Base de planificación: commit `1a4021f`. `team_validation_mode: manual-pass`.

**Resultado:** el operador ve una evaluación trazable de la relación entre cada
búsqueda y los productos de un grupo o de un plan de campañas. La evaluación
ayuda a revisar negativos, destinos de harvest y semillas existentes.

**Spec skip reason:** [el diseño de Jev Ads](../docs/superpowers/specs/2026-10-03-jev-ads-design.md)
ya fija el contrato de producto. Este plan ordena su implementación sin cambiarlo.
Precedencia: `docs/CONTEXTO.md` > diseño Jev Ads > este plan.

**Alcance de esta entrega:** asesoría solicitada por CLI, fichas aprobadas,
revisiones persistidas y lectura en cortes y fábrica. Reseñas, generación de
keywords, autorización de efectos Ads y cambios en las reglas económicas quedan
para planes distintos. El diseño y la
[evidencia previa](../docs/evidencia/jev-ads-01/README.md) no constituyen una
implementación en Orbit.

## Contratos que ninguna tarea puede cambiar

- El módulo asesor no se llama desde `cycle.py`, `apply_cola.py` ni
  `apply_harvest.py`. Un fallo o apagado de Jev deja igual la decisión y su
  aplicación, incluido el veto de 48 horas.
- El juicio reutilizable recibe sólo el término literal y una versión de
  ficha de producto. La relación tiene tres valores: `satisface`,
  `no_satisface` e `informacion_insuficiente`.
- Un producto compatible observado permite un testigo positivo con la cobertura
  visible. Un negativo para todos exige un conjunto no vacío, exhaustivo,
  identificado y con ficha y juicio vigentes para cada miembro. El censo actual
  de grupos Amazon no demuestra esa exhaustividad.
- El origen y el destino de harvest se evalúan por separado. Un negativo de
  ruteo no alimenta `negative_biblioteca`. Las semillas se revisan contra los
  productos del nuevo plan, sin volver a calcular las semillas históricas.
- `decided_at` y `captured_at` se conservan por separado. Una ficha posterior
  no pasa a ser evidencia disponible al decidir. Las revisiones y los intentos
  son append-only; GET no escribe ni llama a TypeSafe.
- Los secretos se cargan desde `ORBIT_SECRETS_DIR` y no entran al repo, al
  request guardado ni a los logs. El rol asesor no tiene permisos sobre la cola,
  el ledger, las decisiones, los goals o las bibliotecas.
- Un plan de fábrica se identifica por su contenido canónico y su huella. El
  preview actual no crea un borrador persistido. ASIN-like queda fuera del
  clasificador de texto.

**Validación del plan:** revisión manual de producto, arquitectura, seguridad,
QA y objeciones. Producto: el operador ve cobertura y fuente. Arquitectura:
el asesor queda fuera de APPLY y reutiliza pares aislados. Seguridad: el rol
asesor no puede actuar en Ads y el token queda fuera del repo. QA: cada fase
tiene un resultado observable y pruebas de fallos. Objeción principal: el
catálogo actual no permite exclusiones universales de grupos Amazon. Se acepta
ese resultado indeterminado; no se inventa cobertura.

**Base de calidad:** `pyproject.toml` ya configura Ruff; `.pre-commit-config.yaml`
incluye los candados locales y `.github/workflows/quality.yml` ejecuta la suite
completa. `formatter_baseline: configured`. No hace falta una tarea de instalación.
Durante cada tarea se corren pruebas focalizadas. El commit final de cada bloque
pasa pre-commit; CI corre la batería completa una vez por SHA, en jobs cuya unión
es la batería. No se usa `--no-verify` ni se reducen pruebas por tipo de cambio.

**Precondición conocida:** la suite sobre `1a4021f` tuvo 3,484 pruebas aprobadas
y un fallo ajeno a Jev: `tests/test_precio_d0.py::test_snippet_del_plan_coincide_con_el_manifest`.
Se reprodujo en el checkout base sin Jev. La tarea 0.1 lo resuelve en un cambio
propio antes del cierre de Jev. No se declara verde una batería con ese fallo.

## Archivos y dueños

| Área | Archivos previstos | Responsabilidad |
| --- | --- | --- |
| Dominio | `app/jev_ads.py`, `tests/test_jev_ads.py` | Estados tipados, composición y presentación de resultados |
| Catálogo | `app/jev_catalogo.py`, `tests/test_jev_catalogo.py` | Fichas, identidades y censo con huecos explícitos |
| Jev | `app/jev_juicios.py`, `tests/test_jev_juicios.py` | Contrato Choice de un par, transporte y respuesta validada |
| Persistencia | migración `0049` (el próximo número libre al crearla; ya mergeada, no se edita) y pruebas de esquema | Cuatro tablas, índices, roles y reglas append-only |
| Operación | `tools/jev_ads.py`, `tests/test_jev_cli.py` | Solicitar o retomar un lote con presupuesto y límite de contexto |
| Cortes | `app/api_dashboard.py`, `templates/cortes.html`, `tests/test_api_dashboard.py` | Mostrar asesoría guardada junto a la decisión original |
| Fábrica | `app/fabrica_web.py`, `app/api_fabrica.py`, pantalla de fábrica y sus tests | Revisar un preview sellado y mostrar asesoría ligada a su huella |

Antes de editar la pantalla de fábrica, localizar sus archivos de plantilla y
JavaScript en el commit de trabajo. La migración tomó el próximo número libre
en su momento (`0049`, ya mergeada); una migración nueva toma el siguiente
número libre al crearse y no edita `0049` (fila R1).

## Tareas y dependencias

Cada fila termina con evidencia en `docs/evidencia/jev-ads-01/ejecucion/<id>/`.
El responsable escribe allí comandos, resultados y SHA. No se fabrican pruebas
futuras en este plan. `cc:TODO` significa que la implementación no ha empezado.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.1 | `[stage:investigacion] [lane:fast]` Reparar en cambio separado la discrepancia entre el snippet de `plans/repricing-01.md` y `plans/manifest.json`. | La prueba focalizada indicada arriba falla antes del arreglo y pasa después; el texto corregido conserva el estado real de repricing; pre-commit pasa. | - | cc:DONE B1 #390 afc1f3b |
| 0.2 | `[stage:planificacion] [lane:gate]` Confirmar contra el HEAD de implementación las rutas, el siguiente número de migración, el esquema de roles y la procedencia de fichas. Fijar el conjunto de casos reales que el piloto podrá etiquetar, sin enviarlos aún. | E/0.2 incluye mapa de archivos y SELECT de sólo lectura por mercado; registra `desconocido` para roster no probado, fuente de cada ficha y disponibilidad de casos de negativos, harvest y semillas. El spec sigue sin delta o se documenta el cambio antes de codificar. | - | cc:DONE B1 #390 afc1f3b |
| 1.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Crear tipos puros y `componer` en `app/jev_ads.py`. | Pruebas focalizadas pasan para un compatible con hueco, todos negativos con hueco, conjunto vacío, variante sin ficha, juicio insuficiente, fallo y ASIN-like. La función no importa red ni DB. | 0.2 | cc:DONE B2 #391 224449f |
| 1.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Crear migración y `app/jev_catalogo.py`; añadir comando administrativo para registrar y revocar fichas aprobadas. | Pruebas de DB demuestran cuatro tablas, FKs, hash y solicitud idempotente, append-only, roles de mínimo privilegio, UTC en triggers, lookup por IDs y LEFT JOIN que conserva listing/estado ausentes. Un censo sin prueba de exhaustividad queda `desconocido`. | 1.1 | cc:DONE B2 #391 224449f |
| 1.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Crear `app/jev_juicios.py` con una ficha por request y modelo fijo `jev-1.13.0`. | Fake HTTP confirma que sólo viajan término y ficha; categorías, probabilidades, finitud, modelo e IDs se validan; orden de Choice y versión cambian la clave; timeout, respuesta inválida, redirección y contexto excedido producen estados visibles sin secreto en salida. | 1.1 | cc:DONE B3 #392 1136d78 |
| 1.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Unir catálogo, juicios y revisión en `AsesorAds.evaluar` y CLI de lote acotado. | Una prueba observa revisión e intención confirmadas antes del HTTP, evento de resultado tras HTTP, reanudación después de crash, rechazo de la misma solicitud con otro payload y reutilización sólo de éxitos válidos. Desactivar Jev deja los tests de `cycle`, `apply_cola` y `apply_harvest` sin diferencias de resultado. | 1.2, 1.3 | cc:DONE B3 #392 1136d78 |
| 2.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Añadir `AsesorAds.leer` y asesoría guardada a `/cortes`. | GET muestra compatibilidad, cobertura, fecha, ficha, origen/destino y fallo o vigencia desconocida. Tests de API prueban cero HTTP externo y cero INSERT/UPDATE. Relevancia compatible no se presenta como error económico; veto sigue visible. | 1.4 | cc:DONE B4 #393 a470866 |
| 2.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Exportar del preview el plan canónico y las filas fuente de semillas, revisarlos mediante CLI y mostrar el resultado para esa huella. | Tests prueban que el export contiene plan, huella y fuentes; cambiar productos, semillas o parámetros cambia la revisión visible; repetir la misma huella conserva fuentes congeladas; un negativo heredado se coteja con nuevos productos; ningún paso crea o elimina keywords ni modifica biblioteca. | 1.4 | cc:DONE B4 #393 a470866 |
| 2.R | `[stage:revision] [lane:gate]` Revisar con otra IA los cambios de datos y permisos de 1.1 a 2.2, usando `cross-review.ps1`. | El reporte identifica SHA y comandos que reproducen cada bloqueante; una ronda de corrección agrupa hallazgos. Sólo un bloqueante reproducible reabre revisión. Ninguno queda abierto. | 2.1, 2.2 | cc:DONE B5 #394 58d1ea4 |
| 2.3 | `[stage:cierre-pr] [lane:gate]` Integrar y desplegar el asesor apagado, con rollback probado antes del despliegue. | Hooks y CI completos verdes sobre SHA final; migración y rollback ensayados en entorno de prueba; smoke GET y CLI sin credenciales Ads; ninguna tarea automática llama a Jev; no cambian decisiones, cola, ledger ni bibliotecas. Evidencia incluye SHA, tests, consulta de permisos y checklist de despliegue. | 2.R, 0.1, R1, R4 | cc:TODO |
| 3.1 | `[stage:medicion] [lane:release]` Ejecutar manualmente el piloto con fichas aprobadas, presupuesto fijado y casos reales etiquetados por una persona. | Reporte separa MX y US y los tres usos; publica denominadores, cobertura, desacuerdos confirmados, errores, abstenciones, orden Choice, latencia y costo USD con `usage` real o desconocido. Cada envío a TypeSafe queda autorizado para el lote; ningún término se registra con secreto ni se envía sin ficha aprobada. | 2.3, R2, R3 | cc:DONE B11 (piloto US y MX, 648 pares; sin destino de harvest, plan de fábrica ni orden Choice: límites declarados en `ejecucion/3.1/REPORTE.md`, pasan a `jev-ads-02`) |
| 3.2 | `[stage:cierre-pr] [lane:release]` Decidir con los resultados si conservar la asesoría V1 y qué ampliar. | Acta enlaza dataset versionado, método, SHA y reporte; declara límites de la muestra y decisión de seguir, ajustar o retirar. Cualquier automatización de acciones abre otro spec y plan con reversa y criterios medidos. | 3.1 | cc:DONE B11 (seguir y ampliar: `ejecucion/3.2/ACTA.md`) |

Las tareas 1.2 y 1.3 pueden avanzar en paralelo después de 1.1, con archivos
distintos. La tarea 1.4 une sus contratos. Las tareas 2.1 y 2.2 también pueden
avanzar en paralelo si cada una trabaja en su propia rama. Una sola persona o
agente integra el estado compartido y ejecuta los candados finales.

### Cierre de la implementación (ledger, 2026-10-04)

Bloque → filas → evidencia: B0 #389 fa2039ee creó el plan y el diseño;
B1 #390 afc1f3b → 0.1, 0.2 (`ejecucion/0.1`, `0.2`); B2 #391 224449f → 1.1,
1.2 (`ejecucion/B2-r1..r4`); B3 #392 1136d78 → 1.3, 1.4
(`ejecucion/B3-r1..r3`); B4 #393 a470866 → 2.1, 2.2 (`ejecucion/B4-r1..r3`);
B5 #394 58d1ea4 → 2.R (`ejecucion/B5-r1..r4`). B6a #395 8d8cdca arregló la
concurrencia del CI fuera de tabla. La verificación de evidencia por fila está
en `docs/evidencia/jev-ads-01/ejecucion/B6b/NOTAS.md`.

## Seguimientos de la revisión

Residuales no bloqueantes acumulados de las revisiones (fuente:
VEREDICTO-B2-r1 … VEREDICTO-B5-r4 y VEREDICTO-B6a-r1). Triage completo de los
hallazgos de los reportes G1-G6 de B5-r1:
`docs/evidencia/jev-ads-01/ejecucion/B6b/triage-no-bloqueantes.md`. R19 y R20
se arreglaron en ese mismo cambio de cierre; el resto sigue `cc:TODO`.
Orden: R1 y R4 van antes de 2.3 (2.3 depende de ellas); R2 y R3 van antes
de 3.1 (3.1 depende de ellas).

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| R1 | Migración NUEVA con `jev_revision.created_at DEFAULT clock_timestamp()`; no se edita `0049`, ya mergeada. Re-emite el COMMENT del encabezado que promete `<= created_at` y corrige el typo "jam el parecido" del catálogo. | La captura dentro de la transacción pasa y una captura posterior se rechaza; la prueba toma la captura con `SELECT clock_timestamp()`; `0049` intacta. | - | cc:DONE B7 #397 a71a344 |
| R2 | BLOQUE DE OPERACIÓN: camino de producción para crear revisiones de decisiones. `tools/jev_ads.py` rechaza `--decision-id`; falta leer el término y los censos de origen y destino de la decisión guardada; sin esto la asesoría de `/cortes` siempre sale `null`. Incluye el pegamento CLI del export de fábrica (`/api/fabrica/export-semillas`). Ahí viven también los hallazgos del triage marcados R2 (códigos de salida y manejo de errores del CLI, validación de entradas, dry-run fiel, row_factory y rol real con que escribe, y el perímetro sin token de `/export-semillas` y `/asesoria/{huella}`). | Un lote de decisiones corre desde la terminal y la asesoría aparece en `/cortes`; el export de fábrica se consume por CLI; ningún HTTP de más, sin secretos y con el perímetro de los endpoints definido. | - | cc:DONE B10 #401 25cebe7 |
| R3 | Vigencia: un destino modificado no marca la revisión obsoleta, como pide el spec; hoy `_vigencia_de_miembros` solo mira fichas. Va con el bloque que cree revisiones de harvest. | Prueba con destino cambiado que da Obsoleta; sigue el MISMO predicado `ficha_vigente` de `evaluar`. | R2 | cc:DONE B10 #401 25cebe7 |
| R4 | Decidir la reutilización entre revisiones: el spec la permite ("pares aún aplicables"), `0049` y el asesor la prohíben; cambiarlo exige migración. | Decisión escrita aquí o en ADR; si se permite, migración nueva con su prueba y su reversa; si no, nota en el spec. | - | cc:DONE B7 #397 a71a344 |
| R5 | `tools/jev_fichas.py revocar` escribe sin `--aplicar` (registrar sí es seco por omisión); la revocación es append-only e irreversible. | `revocar` en seco imprime y no escribe; `--aplicar` revoca; prueba que mira la fila (o su ausencia). | - | cc:DONE B9a #399 e9789d4 |
| R6 | `componer` no valida que todos los `Juicio` compartan `termino_literal_sha256` y `contrato_sha256`. | `ValueError` en mezcla, como la ficha duplicada; prueba y mutante. | - | cc:DONE B9b #400 fa9d9d6 |
| R7 | `registrar_ficha` hace SELECT y luego INSERT sin manejar `UniqueViolation` concurrente; `revocar_ficha` traduce sin savepoint y aborta la transacción previa. | Idempotencia bajo concurrencia (ON CONFLICT o savepoint) en ambos; prueba que fuerza la violación sin autocommit. | - | cc:DONE B9a #399 e9789d4 |
| R8 | `_enriquecer` solo resuelve ficha con exactamente 1 listing: un producto anunciado con 2 listings queda `ficha_ausente` para siempre. | Regla explícita y probada para multi-listing (evaluar o motivo propio); prueba con censo de 2 listings. | - | cc:DONE B9b #400 fa9d9d6 |
| R9 | La reanudación recalcula las fichas vigentes con el `ahora` nuevo: una ficha revocada o vencida entre intentos cae en "misma solicitud con otro payload". | Reanudación retoma desde el contexto congelado; prueba con ficha revocada entre intentos. | - | cc:DONE B9b #400 fa9d9d6 |
| R10 | `_exito_previo` toma el ÚLTIMO éxito (`ordinal DESC`); el spec dice "el primer éxito validado". | Toma el primero; prueba con dos éxitos del mismo par. | - | cc:DONE B9b #400 fa9d9d6 |
| R11 | `ORBIT_SECRETS_DIR` definida pero vacía cae al cwd en `app/jev_juicios.py`, `app/ads/config.py:61`, `app/notifica.py:174` y `app/reputacion_clientes.py:81`. | `or DEFAULT_SECRETS_DIR` en los cuatro puntos; prueba con la variable vacía. | - | cc:DONE B9a #399 e9789d4 |
| R12 | `/cortes`: indicador de "asesoría no disponible" si `leer` falla (hoy queda igual que "sin revisión"); rótulo "grupo" en negativos y "origen/destino" solo en harvest; truncado del detalle de error que muestra la pantalla. | Prueba que fuerza el fallo de `leer`, exige 200 y la señal visible; rótulos por ámbito con prueba. | - | cc:DONE B9b #400 fa9d9d6 |
| R13 | Vigencia: 1 consulta por listing por GET de `/cortes`; hoy es poco; vigilar con cortes pendientes reales. | Medición registrada con denominadores; umbral declarado si crece. | - | cc:DONE B11 (medicion en ejecucion/R13) |
| R14 | Partir `app/jev_ads.py` en núcleo puro / asesor / vista y sacarlo de `ALLOWLIST_TAMANO`; ahí se unifican los hashes de término duplicados entre vista y transporte. | Tres módulos con fronteras probadas (guarda de pureza incluida); fuera de la allowlist; batería verde. Va después del despliegue de 2.3, porque el archivo ya está en `ALLOWLIST_TAMANO` con su razón y el despliegue va apagado. | 2.3 | cc:DONE B10 #401 25cebe7 |
| R15 | `hechos`/`listings` como generador en `registrar_ficha`, sin prueba (solo `desconocidos` está cubierta). | Prueba con `hechos` y `listings` generador: hash y fila idénticos; mutante registrado. | - | cc:DONE B9a #399 e9789d4 |
| R16 | Reordenar solo `opciones` no tiene prueba propia (la prueba actual invierte opciones y criterios juntos). | Prueba que reordena solo `opciones` y cambia la clave; mutante. | - | cc:DONE B9b #400 fa9d9d6 |
| R17 | Guarda de pureza: `__import__`/importlib dinámicos e import en el cuerpo de clase de `AsesorAds` quedan fuera; recorrido redundante de try/if/with en la parte 1; doble rotulado; prefijo `app.` fijo para level>1. | Cada forma con su rojo y su mutante; sin recorrido redundante ni doble rotulado. | - | cc:DONE B9b #400 fa9d9d6 |
| R18 | La guarda estática de GRANT (`test_migracion_trae_fks_y_roles`) no ve GRANT multi-tabla; alinearla (pglast) o borrarla: `test_roles_de_minimo_privilegio` ya protege contra la base real. | Una sola fuente de mínimo privilegio; el mutante multi-tabla muere o la guarda no existe. | - | cc:DONE B9a #399 e9789d4 |
| R19 | Cifras de `docs/evidencia/jev-ads-01/ejecucion/B2-r1/NOTAS.md` (15/17) no cuadran con sus artefactos (14 y 32 combinadas). | Corregidas en este cambio de cierre (B6b). | - | cc:DONE B6b |
| R20 | Rutas locales `/Users/dn` en `docs/evidencia/jev-ads-01/ejecucion/B5-r1/reporte-g4-pantallas.txt` (Minor de CodeRabbit). | Anonimizadas en este cambio de cierre (B6b). | - | cc:DONE B6b |
| R21 | `tests/test_ads_write.py::test_moneda_equivocada_revierta_antes_de_cualquier_http` falla en master cuando corre despues de ciertos modulos (aislamiento entre pruebas; visto en B9a). La bateria completa de CI no lo muestra. | La prueba pasa en cualquier orden (por ejemplo, junto con `tests/test_jev_juicios.py` y `tests/test_spapi*.py`); la causa queda escrita. | - | cc:TODO |

## Pruebas que deben discriminar

La [spec](../docs/superpowers/specs/2026-10-03-jev-ads-design.md) define la
conducta. Estas pruebas fijan los cinco fallos de entrada más probables:

1. Un producto observado sin `listing_id` no permite declarar incompatible a
   todo el grupo. Tarea 1.2 y composición de 1.1.
2. Una ficha de otra variante no acredita un producto. Tareas 1.1 y 1.2.
3. La misma solicitud con plan cambiado falla antes del HTTP. Tarea 1.4.
4. Un resultado obtenido por otra solicitud después de un crash no aparece
   en una revisión histórica que no lo había enlazado. Tareas 1.4 y 2.1.
5. Un término con venta puede recibir un negativo de ruteo sin entrar a
   `negative_biblioteca`. Tareas 1.1 y 2.2.

En cada bug descubierto durante la implementación, añadir una prueba que falle
contra la versión anterior y pase con la corrección. Las pruebas llaman la
interfaz que usa el operador o el módulo consumidor; no se limitan a repetir la
implementación.

## Criterios de salida

Se puede cerrar V1 cuando el operador ve la fuente y la cobertura de cada
etiqueta, puede reconstruir los resultados de una revisión por IDs y versiones,
y Jev apagado deja intactas las decisiones Ads. Un fallo de proveedor se muestra
como fallo y nunca como evidencia de incompatibilidad.

La medición del piloto decide si el costo de fichas y revisión se justifica.
Los 32 aciertos sintéticos del diseño prueban el contrato del prototipo, no la
precisión comercial en Orbit. Ninguna fila de este plan autoriza una acción
publicitaria nueva ni un deploy automático.
