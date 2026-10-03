# JEV ADS 01 — asesoría semántica para Ads

Estado: plan creado el 2026-10-03; todas las tareas de implementación pendientes.
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
| Persistencia | próxima migración libre después de `0046` y pruebas de esquema | Cuatro tablas, índices, roles y reglas append-only |
| Operación | `tools/jev_ads.py`, `tests/test_jev_cli.py` | Solicitar o retomar un lote con presupuesto y límite de contexto |
| Cortes | `app/api_dashboard.py`, `templates/cortes.html`, `tests/test_api_dashboard.py` | Mostrar asesoría guardada junto a la decisión original |
| Fábrica | `app/fabrica_web.py`, `app/api_fabrica.py`, pantalla de fábrica y sus tests | Revisar un preview sellado y mostrar asesoría ligada a su huella |

Antes de editar la pantalla de fábrica, localizar sus archivos de plantilla y
JavaScript en el commit de trabajo. La migración toma el próximo número libre
en ese momento; no reservar `0047` por adelantado.

## Tareas y dependencias

Cada fila termina con evidencia en `docs/evidencia/jev-ads-01/ejecucion/<id>/`.
El responsable escribe allí comandos, resultados y SHA. No se fabrican pruebas
futuras en este plan. `cc:TODO` significa que la implementación no ha empezado.

| Task | Contenido | DoD | Depends | Status |
| --- | --- | --- | --- | --- |
| 0.1 | `[stage:investigacion] [lane:fast]` Reparar en cambio separado la discrepancia entre el snippet de `plans/repricing-01.md` y `plans/manifest.json`. | La prueba focalizada indicada arriba falla antes del arreglo y pasa después; el texto corregido conserva el estado real de repricing; pre-commit pasa. | - | cc:TODO |
| 0.2 | `[stage:planificacion] [lane:gate]` Confirmar contra el HEAD de implementación las rutas, el siguiente número de migración, el esquema de roles y la procedencia de fichas. Fijar el conjunto de casos reales que el piloto podrá etiquetar, sin enviarlos aún. | E/0.2 incluye mapa de archivos y SELECT de sólo lectura por mercado; registra `desconocido` para roster no probado, fuente de cada ficha y disponibilidad de casos de negativos, harvest y semillas. El spec sigue sin delta o se documenta el cambio antes de codificar. | - | cc:TODO |
| 1.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Crear tipos puros y `componer` en `app/jev_ads.py`. | Pruebas focalizadas pasan para un compatible con hueco, todos negativos con hueco, conjunto vacío, variante sin ficha, juicio insuficiente, fallo y ASIN-like. La función no importa red ni DB. | 0.2 | cc:TODO |
| 1.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Crear migración y `app/jev_catalogo.py`; añadir comando administrativo para registrar y revocar fichas aprobadas. | Pruebas de DB demuestran cuatro tablas, FKs, hash y solicitud idempotente, append-only, roles de mínimo privilegio, UTC en triggers, lookup por IDs y LEFT JOIN que conserva listing/estado ausentes. Un censo sin prueba de exhaustividad queda `desconocido`. | 1.1 | cc:TODO |
| 1.3 | `[stage:implementacion] [lane:gate] [tdd:required]` Crear `app/jev_juicios.py` con una ficha por request y modelo fijo `jev-1.13.0`. | Fake HTTP confirma que sólo viajan término y ficha; categorías, probabilidades, finitud, modelo e IDs se validan; orden de Choice y versión cambian la clave; timeout, respuesta inválida, redirección y contexto excedido producen estados visibles sin secreto en salida. | 1.1 | cc:TODO |
| 1.4 | `[stage:implementacion] [lane:gate] [tdd:required]` Unir catálogo, juicios y revisión en `AsesorAds.evaluar` y CLI de lote acotado. | Una prueba observa revisión e intención confirmadas antes del HTTP, evento de resultado tras HTTP, reanudación después de crash, rechazo de la misma solicitud con otro payload y reutilización sólo de éxitos válidos. Desactivar Jev deja los tests de `cycle`, `apply_cola` y `apply_harvest` sin diferencias de resultado. | 1.2, 1.3 | cc:TODO |
| 2.1 | `[stage:implementacion] [lane:gate] [tdd:required]` Añadir `AsesorAds.leer` y asesoría guardada a `/cortes`. | GET muestra compatibilidad, cobertura, fecha, ficha, origen/destino y fallo o vigencia desconocida. Tests de API prueban cero HTTP externo y cero INSERT/UPDATE. Relevancia compatible no se presenta como error económico; veto sigue visible. | 1.4 | cc:TODO |
| 2.2 | `[stage:implementacion] [lane:gate] [tdd:required]` Exportar del preview el plan canónico y las filas fuente de semillas, revisarlos mediante CLI y mostrar el resultado para esa huella. | Tests prueban que el export contiene plan, huella y fuentes; cambiar productos, semillas o parámetros cambia la revisión visible; repetir la misma huella conserva fuentes congeladas; un negativo heredado se coteja con nuevos productos; ningún paso crea o elimina keywords ni modifica biblioteca. | 1.4 | cc:TODO |
| 2.R | `[stage:revision] [lane:gate]` Revisar con otra IA los cambios de datos y permisos de 1.1 a 2.2, usando `cross-review.ps1`. | El reporte identifica SHA y comandos que reproducen cada bloqueante; una ronda de corrección agrupa hallazgos. Sólo un bloqueante reproducible reabre revisión. Ninguno queda abierto. | 2.1, 2.2 | cc:TODO |
| 2.3 | `[stage:cierre-pr] [lane:gate]` Integrar y desplegar el asesor apagado, con rollback probado antes del despliegue. | Hooks y CI completos verdes sobre SHA final; migración y rollback ensayados en entorno de prueba; smoke GET y CLI sin credenciales Ads; ninguna tarea automática llama a Jev; no cambian decisiones, cola, ledger ni bibliotecas. Evidencia incluye SHA, tests, consulta de permisos y checklist de despliegue. | 2.R, 0.1 | cc:TODO |
| 3.1 | `[stage:medicion] [lane:release]` Ejecutar manualmente el piloto con fichas aprobadas, presupuesto fijado y casos reales etiquetados por una persona. | Reporte separa MX y US y los tres usos; publica denominadores, cobertura, desacuerdos confirmados, errores, abstenciones, orden Choice, latencia y costo USD con `usage` real o desconocido. Cada envío a TypeSafe queda autorizado para el lote; ningún término se registra con secreto ni se envía sin ficha aprobada. | 2.3 | cc:TODO |
| 3.2 | `[stage:cierre-pr] [lane:release]` Decidir con los resultados si conservar la asesoría V1 y qué ampliar. | Acta enlaza dataset versionado, método, SHA y reporte; declara límites de la muestra y decisión de seguir, ajustar o retirar. Cualquier automatización de acciones abre otro spec y plan con reversa y criterios medidos. | 3.1 | cc:TODO |

Las tareas 1.2 y 1.3 pueden avanzar en paralelo después de 1.1, con archivos
distintos. La tarea 1.4 une sus contratos. Las tareas 2.1 y 2.2 también pueden
avanzar en paralelo si cada una trabaja en su propia rama. Una sola persona o
agente integra el estado compartido y ejecuta los candados finales.

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
