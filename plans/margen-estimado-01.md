# MARGEN ESTIMADO 01 — Plan formal

Fecha: 2026-09-08 UTC. Estado: plan redactado; cero implementacion.
Solicitud: formalizar estimacion antes de Ads con precio/costo/comisiones y
retenciones disponibles, incluso sin ventas, separada del margen observado.

Spec delta: [contrato de estimacion](../docs/superpowers/specs/2026-09-08-margen-estimado-design.md).
Precedencia: contrato del proyecto → specs observados ORBIT 19 → sub-spec nuevo
→ tareas. No hay spec.md raiz en origin/master `80b6403`.
`team_validation_mode: subagent`: Arquitectura; Producto/Esceptico; Seguridad/QA.
`formatter_baseline: configured`: Ruff 0.15.20 y pre-commit existentes;
`.github/workflows/quality.yml` ejecuta pytest completo con PostgreSQL 16.
`formatter_baseline_action: none`. Evaluacion: [validacion](../docs/evidencia/margen-estimado-01/plan-validacion.md).

## Alcance y autorizacion

Purpose: ayudar a comparar que anunciar cuando el historial no permite medir
margen, sin presentar supuestos como ventas o rentabilidad observada.

Tres bloques, **12 tareas**: 0 (3 de investigacion/contrato), A (4 de datos y
calculo), B (4 de integracion/validacion + 1 de cierre). Todas por ejecutar.
La peticion actual autoriza la planificacion. Implementar o desplegar la funcion
requiere la instruccion posterior correspondiente; no se ha concedido gasto.

Required ahora: investigar y cerrar fuentes/contrato en bloque 0 cuando se
ordene ejecutarlo. A/B son alcance propuesto, condicionado a 0.3. No afirmar que
las APIs, tasas o costos de la cuenta ya estan verificados. Si 0 falla, emitir
dictamen y ajustar alcance; no avanzar rellenando desconocidos.

Se registra `margen-estimado-01` sin cambiar el plan activo de Reputacion. Usar
nombre y ruta explicitos en cada sesion, nunca confiar en el active global.

## Bloque 0 — fuentes y contrato, sin implementar

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | [lane:gate] [needs-spike] Inventariar precio/oferta/costo/FX por mercado; responsable lead de datos, solo lectura. Evidencia en `docs/evidencia/margen-estimado-01/0.1/`. [tdd:skip:investigacion] | SELECT fechados y consultas fuente acreditan claves, cardinalidad, fecha precio, canal, unidad/BOM, vigencia costo, FX y conteos por motivo; dictamen viable/no viable/requiere cambio por fuente. | - | cc:完了 [1e578a7] |
| 0.2 | [lane:gate] [needs-spike] Verificar cotizacion de fees, costos directos y contrato fiscal; responsable lead de integracion. Evidencia en `0.2/`; cotejar respuestas contra docs oficiales y fuentes de la cuenta. [tdd:skip:sonda-documental] | Matriz por componente/mercado/canal con fuente, base, signo, moneda, impuestos, vigencia, tasa cuando corresponda y pertenencia a totales; muestras sanitizadas y ejemplo economico completo por mercado viable, o dictamen de inviabilidad con evidencia y propuesta de enmienda; desconocidos declarados. | 0.1 | cc:完了 [1e578a7] |
| 0.3 | [lane:gate] Cerrar acta tecnica/economica, alcance soportado y criterios numericos; responsable lead. Actualizar spec/plan y `0.3/acta.md`; revision independiente. [tdd:skip:contrato] | Fija TTL/cadencias por fuente, tarifa FBM si se compromete ese canal, normalizacion I/R y politicas versionadas, contrato de snapshots/API, permisos, universo y casos por mercado/canal; ninguna decision fiscal/tarifa obligatoria abierta para liberar A. Fuentes inviables dejan A/B sin liberar. | 0.1, 0.2 | cc:完了 [normalizacion MX FBA; FBM/US excluidos] |

0.1 consulta Orbit con transacciones READ ONLY y snapshots consistentes de
bridge/accounting; no escanea ordenes ni datos personales innecesarios. No abre
secretos para inventariar schema. 0.2 usa credenciales existentes solo por el
cliente/loader normal, sin imprimirlas. No crear un adaptador de produccion en
la sonda. No registrar tasas de ejemplo como politica de cuenta.

El cierre del bloque fija inputs, outputs y evidencia para que otro implementador
pueda ejecutar A sin decidir fiscalidad ni fabricar APIs. Puede ratificar
decisiones tecnicas rutinarias dentro del alcance; cualquier nueva exclusión
material, tasa sin soporte o uso decisorio se devuelve como enmienda al dueno.

## Bloque A — insumos y estimacion reproducible

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.1 | [lane:gate] Persistir observaciones/escenarios y politica versionada; responsable backend. Migracion nueva + tests de esquema; docs DB. [tdd:required] | DDL aditivo en PostgreSQL; referencias de oferta/precio/costo/FX/politica y tiempos UTC; pruebas rojas/verdes de append-only, idempotencia, as-of, permisos y reversa; numero migracion elegido contra master vigente. | 0.3 | cc:DONE |
| A.2 | [lane:gate] Capturar precio/oferta fechados y resolver costo/FX existentes; responsable ingesta. No cambiar identidad actual de fabrica. [tdd:required] | Tests y muestra real distinguen fresco/viejo/ausente/ambiguo; snapshot repetido no duplica ni rejuvenece; costo vigente y unidad compatibles; FX conserva origen y no extiende vigencias; conciliacion precio contra bridge. | A.1 | cc:DONE |
| A.3 | [lane:gate] Integrar cotizacion de comisiones y componentes directos/fiscales aprobados; responsable integracion. Ingesta separada con scheduler existente. [tdd:required] | Precio/SKU/canal/marketplace casan con cotizacion; errores por item/403/429/timeout no crean totales completos; fees y detalles no duplican; politica aprobada cargada con version y vigencia, sin defaults; allowlist externa exacta y errores redactados; ingesta idempotente. | A.2 | cc:WIP |
| A.4 | [lane:gate] Implementar calculador puro y lectura de escenario; responsable backend. Formula S3, sin dependencias del motor. [tdd:required] | Fixtures exactos de AC1–AC9 y AC11 pasan despues de rojo; calculo reproducible con inputs congelados, total null ante obligatorio desconocido y conserva cero/negativo; revision independiente, Ruff/pre-commit y CI completo verde; PR A integrado sin despliegue productivo hasta B.5. | A.2, A.3 | cc:TODO |

## Bloque B — Crear campanas, validacion y cierre

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| B.1 | [lane:gate] Agregar proyeccion `estimacion` a catalogo/evaluacion; responsable API. Reutilizar un lector comun, consultas por lotes. [tdd:required] | AC10/AC11/AC12: GET solo lectura sin HTTP/OAuth; ambos endpoints resuelven mismo snapshot; decimales como cadenas, None y moneda preservados; campos observados y contratos de crear intactos. | A.4 | cc:TODO |
| B.2 | [lane:fast] Mostrar estimado/desglose y motivos en tarjetas/comparador; responsable UI. Seguir rediseno PR203. [tdd:required] | AC1/AC2/AC3/AC6/AC7/AC13 en navegador: etiquetas separadas, fuente/fecha/moneda/base/exclusiones visibles, foto y enlace conservados, teclado/dia/noche/390px sin overflow y seleccion preservada. | B.1 | cc:TODO |
| B.3 | [lane:gate] Conciliar escenarios reales y compatibilidad de fabrica/motor; responsable QA/lead. Capturas y recalc independiente en `B.3/`. [tdd:required] | AC10–AC14; al menos un caso sin ventas con estimacion verificable por cada mercado/canal comprometido, universo y faltantes contados, ejemplos incompletos y discrepancias explicadas; cambio solo de estimacion deja huella/target/payload/recovery identicos. | B.1, B.2 | cc:TODO |
| B.4 | [lane:release] Revisar e integrar entrega B; responsable lead. PR desde origin/master fresco, CI completo una vez por entrega y reviews resueltas. [tdd:skip:integracion] | Diff solo de esta funcion, cambios de otras sesiones preservados; CI PostgreSQL sin skip de casos requeridos, Ruff/pre-commit verdes, revision independiente aprobada y PR integrado con SHA/evidencia. | B.3 | cc:TODO |
| B.5 | [lane:release] Desplegar y verificar solo Orbit; responsable lead operativo. Runbook, reversa ensayada y seguimiento. [tdd:skip:operacion] | Backup comprobado, migracion aditiva y release exacto; smoke GET/UI y refresh de insumos, cobertura real reconciliada y recuperacion existente conservada; desactivacion nueva ensayada; AppFlowy/plan con evidencia sin crear campanas ni tocar Reputacion/bridge/accounting. | B.4 | cc:TODO |

## Fronteras de archivos e interfaces

Rutas nuevas propuestas; nombres/schema exactos sellados en 0.3 antes de A:

| Unidad | Archivos y responsabilidad | Consume / produce |
|---|---|---|
| Persistencia | `migrations/NNNN_estimacion_venta.sql`, `tests/test_estimacion_venta_schema.py`, `docs/DATABASE.md` | Acta 0.3 → observaciones append-only, politica, escenarios y permisos; NNNN es el siguiente numero libre al implementar |
| Insumos | `app/estimacion_insumos.py`, `tests/test_estimacion_insumos.py` | Snapshot de oferta + `sku_cost`/`fx_resolve` → componentes normalizados con fecha y referencias |
| Cotizacion | `app/estimacion_fees.py`, `tests/test_estimacion_fees.py` | Contexto de oferta inmutable → cotizacion por ese contexto o error tipado; fuera de GET |
| Calculo | `app/estimacion_venta.py`, `tests/test_estimacion_venta.py` | Escenario con inputs/politica/fecha → contribucion, porcentaje, desglose, estado y motivos; sin IO |
| Ingesta/lectura | `app/estimacion_repository.py`, `app/cli.py`, helper dedicado en `tools/`, `docs/DEPLOY.md` | Clientes y calculador → snapshots persistidos; reader comun por conjunto de listings y as-of |
| API | `app/fabrica_web.py`, `app/api_fabrica.py`, `tests/test_api_fabrica.py` | Reader → bloque aditivo S5 en ambas rutas; no tocar crear/recovery |
| UI | `app/templates/fabrica.html`, `app/static/js/fabrica.js`, `app/static/css/fabrica.css`, `tests/test_ui_fabrica.py` | Bloque S5 → detalle accesible, sin nueva autoridad de calculo |
| Regresion | Tests existentes de fabrica v2/targets/economia y verificador `.cursor/skills/verify-orbit/` solo si el contrato visual lo exige | Misma entrada comercial con distintas estimaciones → mismo resultado comercial |

No crear todos los archivos vacios de antemano ni ampliar monolitos: respetar
presupuestos de arquitectura. Si existe adaptador equivalente en master al
ejecutar, reutilizarlo y ajustar la tabla; no duplicar integraciones. No editar
`app/reputacion*`, sus migraciones, specs o planes. No recrear `bridge/accounting`.

## Aceptacion verificable

Todos los numeros siguientes son fixtures de prueba, nunca defaults de negocio.

| ID | Caso | Resultado esperado |
|---|---|---|
| AC1 | Sin ventas, inputs completos y base normalizada I=100, C=40, F=15, L=5, R=1, misma moneda | Contribucion39 y39%; observado null; seleccionable; objetivo manual obligatorio |
| AC2 | 1–29 fechas o margen maduro disponible | Muestra/maduro exactamente como antes; estimacion separada, sin rellenar campos observados |
| AC3 | Falta precio, costo, fee obligatorio, logistica aplicable o politica fiscal | Principal null y motivo por componente; subtotal conocido no se presenta como completo |
| AC4 | Oferta vencida/futura, cotizacion de otro precio/SKU/canal/mercado | No disponible como actual; no rejuvenecer con nueva ingesta ni reutilizar cotizacion incompatible |
| AC5 | Precio100USD, costo680MXN, tasa USD→MXN17 verificada, fees15USD, L5USD, R1USD | Costo40USD, contribucion39USD; sin tasa utilizable null; tasa/fecha/original preservados |
| AC6 | Dos ASIN de producto comun con precios distintos; o un listing con ofertas ambiguas | Estimados propios en primer caso; identidad_ambigua en segundo; ventas observadas no duplicadas |
| AC7 | I100 y costos totales100 o110 | Contribucion0 o−10; no ocultar, no clamp y no bloquear seleccion |
| AC8 | Total fees15, desglose referral10 + fulfillment5 | Restar15 una vez; no restar30 ni volver a sumar fulfillment como L |
| AC9 | Costo cero, NaN/Infinity, dinero fuera de schema o tasa no documentada | Rechazo o motivo de ausencia, nunca numero valido; fee cero requiere evidencia |
| AC10 | Cambiar solo estimacion manteniendo solicitud comercial y datos observados | Mismo plan, huella, payload, target, budgets, bids y recuperacion v1/v2; motor no consume estimado |
| AC11 | Repetir evento, corregir despues y consultar as-of anterior | Idempotencia para repeticion; correccion nueva; snapshot anterior reproducible sin datos futuros |
| AC12 | Cargar catalogo/evaluacion y pulsar Actualizar con fuente externa caida | GET solo DB sin OAuth/HTTP/escritura; fallos/frescura declarados y UI operativa |
| AC13 | Filtrar/ordenar/actualizar, teclado, dia/noche, ancho390px | Seleccion y enlace independiente conservados; sin overflow de pagina; denominador/exclusiones disponibles |
| AC14 | Cotizacion/precio reales vs salida en ambos mercados soportados | Cada componente concilia con su fuente/contexto; un catalogo solo con nulls no cierra entrega |

## Ejecucion y calidad

- Antes de cada regresion/invariante: SELECT fuente real y evidencia fechada;
  no cuenta el fixture inventado ni una consulta de hace meses.
- RED/GREEN local solo en el archivo de test cambiado. Comando:
  `PYTHONPATH=. .venv/bin/python -m pytest -q tests/<archivo_de_la_tarea>.py`.
  Mostrar fallo que discrimina el codigo previo; despues corregir.
- Ruff fijado por repo: `uvx ruff==0.15.20 check .` y
  `uvx ruff==0.15.20 format --check .`; `pre-commit run --all-files`.
- Una bateria completa por entrega al final, en PR CI con PostgreSQL. Abrir PR,
  leer resultado del SHA exacto; un push solo no dispara CI de feature branch.
  Los tests focales de integracion no sustituyen la bateria. No `--no-verify`.
- Rama nueva desde origin/master tras fetch; antes del PR comprobar
  `git log origin/master..HEAD` contiene solo commits del trabajo.
- A.1–A.4 en orden; B.1→B.2→B.3→B.4→B.5. El plan completo no se ejecuta
  saltando 0.3. Puede correrse por tarea o por bloque, con review al cerrar cada
  bloque. El full test de A y B corresponde a entregas distintas.

## Operaciones previstas y permisos

Esta tabla describe el alcance futuro; no crea autorizaciones nuevas ni un
`plan-preapprovals.json` aprobado. Las instrucciones previas de la sesion siguen
vigentes dentro de su alcance. Cualquier aprobacion necesaria se solicita sobre
un resultado concreto, no antes de preparar la evidencia.

| Asunto | Motivo | Scope y limites |
|---|---|---|
| SELECT Orbit y lectura de snapshots | Confirmar fuentes antes de invariantes | 0.1/0.2/B.3; read-only, consultas minimas sin datos personales |
| Credenciales SP-API existentes via loader | Sondar/capturar cotizaciones oficiales | 0.2/A.3; ruta exacta y permisos documentados antes de uso; no volcar secretos; no cambiar cliente Ads |
| POST de consulta de fees y GET fuente oficial | Obtener comisiones para precio/canal | 0.2/A.3; allowlist de cotizacion, sin precio/campanas/feeds comerciales |
| Git push, PR y CI | Versionar/revisar plan y entregas | Rama propia; no force push ni bypass; integracion de A en A.4 y de B en B.4; deploy solo B.5 |
| AppFlowy EHV Tasks | Registrar planificacion y ejecucion por separado | Tarea de plan termina al entregar documentos; implementacion no se marca Done |
| Backup, migracion e ingesta en Orbit | Publicar datos y UI verificables | B.5; despliegue y reversa concretos, otros servicios y trabajo de Reputacion preservados |
| Crear/pausar anuncios o cambiar precio | No necesario para estimacion informativa | Fuera de este plan; no autorizado |

## Inicio de otra sesion

Comando de inicio: `claude`.
Primer mensaje: `/harness-work --plan margen-estimado-01 0.1`.
Contexto obligatorio: leer este plan y su spec; ejecutar solo la investigacion
0.1, sin iniciar A ni tocar Reputacion. Si el runner no acepta ese flag, indicar
la ruta `plans/margen-estimado-01.md` explicitamente; no cambiar el plan activo.
Adecuado para verificar primero las fuentes que determinan la viabilidad.

Los pendientes de sonda de creacion, harvest, placements y presupuestos siguen
en sus planes/backlog. No son dependencias de este comparador informativo.
