# ORBIT 19 — Plan formal de catalogo abierto y evaluacion

Version: 1.4, 2026-09-06. Workflow: `harness-work` sobre 0.1–0.4.
Estado: 0.1–0.4 cerradas. Ads MX **verificada** (cost/clicks; impressions -1
declarado). Cobertura de ausentes = Sin datos (regla de 0.4, no candado de
B.1 sobre 0.4). Fase A no espera Ads. PR #185: publicar esta rama para CI;
el verde de `4c09189` era el plan anterior.
Solicitud del dueno: todos los articulos seleccionables, con metricas para comparar
su conveniencia publicitaria. Formalizar no equivale a aprobar objetivos ni gasto.

Spec delta: `docs/superpowers/specs/2026-09-06-catalogo-campanas-abierto-design.md`.
No existe spec.md raiz en la base revisada; se usa la convencion de specs del repo.
Precedencia: contrato del proyecto → spec aprobado ORBIT19 → este registro de tareas.
F1 (§1–§10 de FABRICA 01 + API UI) es el contrato de produccion **hasta
A.5**. ORBIT 19 es el contrato de la fase A; no sustituye F1 hoy. El
codigo no cambia hasta A.
`team_validation_mode: subagent`: Producto/datos, Arquitectura, Seguridad/QA/esceptico.

## Resultado y limites

Purpose: permitir decidir que anunciar sin confundir falta de historial con rechazo,
y comparar datos atribuibles, con sus limitaciones visibles.

Entrega A: abrir seleccion por publicacion, objetivos explicitos y recuperacion v1/v2.
Entrega B: completar la comparacion Ads por ASIN y economia observada. Terminar A
no completa ORBIT19. Una fuente Ads inaccesible bloquea B, nunca A.
Disponibilidad comercial es Recommended: Sin verificar no bloquea la comparacion.

Quedan fuera salvo ampliacion acordada: anadir a campanas existentes, catalogo MeLi,
resolver automaticamente mapas Odoo, margen prospectivo obligatorio, reviews, nota
compuesta, limites totales de experimento y cambios en reglas del optimizador.
Las publicaciones sin mapa se inventarian y muestran como pendientes de vinculacion;
no se inventa producto/SKU para poder anunciarlas.

## Decisiones (cerradas 2026-09-06 21:31 UTC)

Acta: `docs/evidencia/orbit-19/0.2/confirmacion.md`.

| ID | Cierre |
|---|---|
| D1 | Orden por `margen_neto_pct` maduro (porcentaje, `[2026-02-20, D-15)`). Muestra visible. NULL al final, no 0%. MX/US no se mezclan. Ads ausente = Sin datos; COMPLETED no basta para Por probar. |
| D2 | Manual si NULL, =0, <0 o clamp > margen conocido. El manual no acredita rentabilidad; preview declara cero/negativo/inferior al objetivo; sigue seleccionable. |
| D3 | Solo campanas nuevas. Existentes = otro plan. |
| D4 | Muestra limitada visible, no entra al sort ni al target. Motor intacto. |

0.2 cierra negocio **y** contrato tecnico (API/CLI v2, compatibilidad v1,
0019/0020, reversa con recuperacion v2). Confirmar D1–D4 no bastaba; ambos
quedan en E/0.2. Precisiones de revision incluidas. Este cierre no es go
de creacion real ni de deploy.

### Evidencia Fase 0 (2026-09-06)

- 0.1 `docs/evidencia/orbit-19/0.1/reporte.md` — SELECT 21:00 UTC. MX 249/3
  elegibles, US 119/2. 115 multilisting. 291 listings bridge sin mapa Odoo
  (23 Active). `fabrica_lote` vacia. Cero mutacion Amazon.
- 0.3 `docs/evidencia/orbit-19/0.3/reporte.md` — `spAdvertisedProduct`
  **verificada** (amazon_mx: forma, permisos, cost/clicks; E/0.3/conciliacion-sumas.txt;
  impressions -1). Cobertura de ausentes no demostrada (gzip solo-actividad).
  `salesSameSku30d` 400. US no sondada. Stock FBA/FBM observados; Featured
  Offer no_verificada.
- 0.2 `docs/evidencia/orbit-19/0.2/confirmacion.md` — D1–D4 + API/CLI +
  migracion/reversa. SELECT signo 21:24 UTC: MX 5 pos / 0 cero / 0 neg /
  244 null; US 2 / 0 / 0 / 117.
- 0.4 `docs/evidencia/orbit-19/0.4/politica-comparacion.md` — cerrada.
  Por probar no se asigna con COMPLETED. Madurez exige observacion
  posterior al cierre de atribucion. 0.4 no espera B.1.

## Etapas y tareas

DoD significa criterio binario de terminado. `E/<task>/` abrevia
`docs/evidencia/orbit-19/<task>/`: SQL, resultados sin secretos, contratos, reporte de
pruebas y capturas. Crear esos artefactos durante la tarea; no fingir evidencia futura.
`pytest_focal` = `PYTHONPATH=. .venv/bin/python -m pytest -q <archivo modificado>`.
Cada bug/regresion debe demostrar fallo contra el codigo previo y exito con el nuevo.
La suite completa corre al final de cada entrega/PR en CI, no se duplica localmente.

### Fase 0 — Investigacion y contrato

Purpose: separar hechos disponibles, decisiones y dependencias externas.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Catalogo/SKU/margen/lotes actuales | E/0.1 contiene SELECT y salida UTC con conteos por mercado, IDs ambiguos/omitidos y lotes recuperables; ninguna mutacion Amazon | - | cc:完了 [2026-09-06 21:00 UTC, E/0.1] |
| 0.2 | [stage:planificacion] [lane:gate] [tdd:skip:docs-contract] Cerrar negocio, API/CLI v2, migracion y rollback | D1–D4 resueltas segun contrato **y** API/CLI v2 + compatibilidad + migraciones + reversa con recuperacion v2 cerrados en E/0.2. Confirmar D1–D4 no basta. | 0.1 | cc:完了 [2026-09-06 21:31 UTC, E/0.2/confirmacion.md] |
| 0.3 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Contratos de reporte Ads y disponibilidad | E/0.3 asigna verificada/verificada_parcial/no_verificada con motivo por fuente; disponible incluye docs, muestra, grano, atribucion, permisos y conciliacion; no_verificada no se trata como cero | - | cc:完了 [Ads MX verificada: cost/clicks; impressions -1; E/0.3/conciliacion-sumas.txt] |
| 0.4 | [stage:planificacion] [lane:gate] [tdd:skip:docs-contract] Cerrar politica de comparacion | E/0.4 fija campos/ventana/cobertura/madurez del reporte **verificado**, objetivo, igualdad, precedencias y ordenes; cada fixture del spec tiene resultado; si Ads no verificada, permanece pendiente. Cobertura/Por probar son regla cerrada, no dependencia de B.1 | 0.2,0.3 | cc:完了 [2026-09-06 22:11 UTC, E/0.4; no espera B.1] |

### Fase A — Catalogo abierto

Purpose: seleccionar publicaciones sin exigir historial y registrar lo creado sin perder reversa.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] Plan versionado y migracion expansiva | pytest_focal sobre plan/migracion pasa; v1 mantiene huella exacta; v2 permite null real y varios listings/producto; reorden no cambia hash y objetivo distinto si; integridad SQL rechaza mercado/SKU invalidos; E/A.1 contiene pruebas | 0.2 | cc:完了 [03256e0] |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] Normalizacion comun CLI/API/motor | pytest_focal de fabrica/API pasa; fixtures sin ventas, margen negativo y varios ASIN alcanzan preview; contratos mezclados y SKU duplicados fallan con cero POST; el target del grupo mixto permanece explicito | A.1 | cc:完了 [pendiente de CI en PR; E/A.2] |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] Registro, idempotencia y recuperacion | pytest_focal de fabrica pasa con dos listings/producto, doble envio, orden distinto y fallo parcial; v1/v2 pueden registrar/reconciliar/pausar con mismos IDs y sin POST duplicado; evidencias E/A.3 | A.2 | cc:完了 [pendiente de CI en PR; E/A.3] |
| A.4 | [stage:implementacion] [lane:gate] [tdd:required] Selector, objetivo y revision | pytest_focal UI pasa; casosAC1–AC5/AC7/AC10 verificados en navegador teclado/movil; foto/ASIN/SKU y target visibles; cambiar parametros invalida preview; capturas E/A.4 | A.2,A.3 | cc:完了 [pendiente de CI en PR; E/A.4] |
| A.R | [stage:revision] [lane:gate] [tdd:skip:revision] Revision independiente de A | Reviewer devuelve APPROVE sobre SHA concreto y casos v1/v2, datos ausentes, token, idempotencia y reversa; ningun hallazgo bloqueante abierto; E/A.R enlaza resultado | A.4 | cc:完了 [6ce45c9; E/A.R] |
| A.5 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] CI, integracion y despliegue A | Ruff/pre-commit pasan; PR con suite completa verde; SHA del deploy identificado; ensayo rollback con lotev2 parcial recuperable; backup; smoke GET/preview; E/A.5 contiene URLs, SHA y resultados | A.R | cc:WIP [E/A.5/preflight, E/A.5/correcciones-review] |

### Fase B — Comparacion con evidencia

Purpose: distinguir resultado, muestra y frescura usando datos de la publicacion real.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| B.1 | [stage:implementacion] [lane:gate] [tdd:required] Reporte Ads por producto anunciado | Fuente Ads verificada; pytest_focal ingesta pasa; append-only y mapeo correcto; fixture campana multiproducto no reparte agregados; muestra externa conciliada por misma ventana/moneda/campos en E/B.1 | 0.4 | cc:TODO |
| B.2 | [stage:implementacion] [lane:gate] [tdd:required] Economia observada y evidencia | pytest_focal financiero pasa; v_margen_producto conserva valores previos; muestra limitada solo con integridad valida; dos ASIN comparten grano y no duplican ventas; E/B.2 contiene SELECT antes/despues | 0.2,A.1 | cc:TODO |
| B.3 | [stage:implementacion] [lane:gate] [tdd:required] Recommended: disponibilidad comercial | Con fuente verificada, stock/estado/frescura conciliados y tests de cero/viejo/desconocido pasan; sin fuente, registrar ampliacion pendiente, no declarar integracion terminada; E/B.3 muestra resultado real | 0.3,0.2 | cc:TODO |
| B.4 | [stage:implementacion] [lane:gate] [tdd:required] Evaluacion, ratios y orden estable | pytest_focal de evaluacion pasa en todos los fixtures del spec: sumas, igualdad, targets distintos, ausencia, muestra, duplicados y null; acepta disponibilidad desconocida; ninguna etiqueta bloquea seleccion | B.1,B.2,0.4 | cc:TODO |
| B.5 | [stage:implementacion] [lane:gate] [tdd:required] Comparador y Por probar | pytest_focal UI pasa; AC6/AC8/AC10 y fixtures del spec reproducidos en navegador; filtros/orden no pierden seleccion; objetivo/grano/ventana/muestra visibles; E/B.5 contiene capturas | A.4,B.4 | cc:TODO |
| B.R | [stage:revision] [lane:gate] [tdd:skip:revision] Revision independiente de B | APPROVE sobre SHA y comprobaciones de fuente, ratios, atribucion, permisos, cache/frescura y UX; ningun bloqueante; E/B.R enlaza informe | B.5 | cc:TODO |
| B.6 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] Validacion final y despliegue | Ruff/pre-commit y suite completa CI PR verdes; conciliacion externa archivada; smoke productivo y AC1–AC10 completos; fuentes opcionales pendientes declaradas; A+B funcionales, no solo estadosnull | A.5,B.R | cc:TODO |

B.3 no es dependencia de B.4: una fuente comercial no disponible no debe bloquear
comparacion financiera/Ads. Se permite cerrar el nucleo A+B con B.3 pendiente,
pero solo si la entrega declara expresamente disponibilidad Sin verificar y la
ampliacion abierta. Nunca cerrar B.1 o todo el nucleo por mostrar datos desconocidos.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/area previstos | Restriccion |
|---|---|---|
| 0.1–0.4 | Spec ORBIT19, specs FABRICA01/UI01, E/0.x, reserva en migrations/ | Lead integra contrato; no modificar specs anteriores como aprobados sin0.2 |
| A.1 | app/fabrica_plan.py, migracion nueva de grupo/publicacion, tests/test_fabrica_plan.py y test_fabrica_migracion.py | Reservar nombre/secuencia en0.2; no editar0018 |
| A.2 | app/api_fabrica.py, fabrica_web.py, tools/fabrica_campanas.py, tests/test_api_fabrica.py y test_fabrica_campanas.py | Un unico normalizador y motor |
| A.3 | tools/fabrica_campanas.py, app/fabrica_plan.py y tests de recuperacion existentes | Secuencial trasA.2 |
| A.4 | app/templates/fabrica.html, static/js/fabrica.js, static/css/fabrica.css, tests/test_ui_fabrica.py | No concurrente conB.5 |
| B.1 | app/ads/reports.py o modulo cohesivo asociado, app/cli.py, scheduler existente, nueva migracion/tabla de metricas y tests | Permisos de POST reporting existentes; contrato exacto0.4 |
| B.2 | Nueva migracion/vista financiera comun, consumidor financiero y tests asociados | TrasA.1; compartir formula, preservar proyeccion madura |
| B.3 | Adaptador de fuente de disponibilidad, snapshot y tests nuevos, ingesta existente | No tocar bridge/accounting; publicar unknown si no se verifica fuente |
| B.4 | Modulo puro nuevo de evaluacion y API/catalogo, tests nuevos | Integrar serialmente con A.2/B.5 si comparten API; no duplicar motor |
| B.5 | API/catalogo, templates/JS/CSS fabrica y tests UI | TrasA.4/B.4; archivo compartido tiene un solo editor |
| A.R/B.R/A.5/B.6 | Evidencia, docs/DEPLOY.md y docs/CHAT-CONTEXT.md | Reviewer solo lectura; lead integra y despliega |

0.2/0.4 deben convertir areas nuevas en rutas concretas antes de implementar.
Migraciones se reservan coordinadamente; B.2 depende A.1 por ese estado compartido.
Paralelismo util: investigaciones0.1/0.3; trascontratos, ingesta B.1 junto a A;
B.2/B.3 en archivos propios. No ejecutar commits sobre archivos compartidos a la vez.

## Aceptacion verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | Producto sin ventas | Seleccion y preview con objetivo valido; margennull | TestAPI + captura |
| AC2 | Menos de30 fechas con venta | Seleccionable; numero de fechas/muestra limitada visible | Testfinanciero + captura |
| AC3 | Margen negativo o ACoS desfavorable | Seleccionable con motivo; no rentabilidad inventada por clamp | Testplan/evaluacion |
| AC4 | Dos ASIN del mismo producto | Anuncios por SKU correctos; registro completo; sin duplicar dato financiero | Testintegracion simulado |
| AC5 | Mercado cruzado, ID invalido o SKU duplicado/ausente | Error tecnico antes del primer POST | Contador HTTP=0 |
| AC6 | Igual ACoS, muestras1/100; ausencia vs0 | Misma comparacion, conteos distintos; ausencia no se convierte en0 | Fixtures del spec |
| AC7 | Grupo mixto | Target manual igual en preview/huella/lote/cinco goals; unknown preservado | Testintegracion |
| AC8 | Stock0, viejo y desconocido | Tres estados distinguibles; sin dato no impide seleccionar | TestAPI/UI |
| AC9 | Lotes v1/v2 parciales | Reconciliar/registrar/pausar despues del cambio y su reversa | Ensayo staging + tests |
| AC10 | Orden/filtros/ranking | Seleccion preservada y cero escrituras automaticas | Navegador + contador HTTP |

## Secuencia de despliegue y reversa

1. Inventario de lotes y backup verificable; migracion expansiva compatible.
2. Publicar version con lector/recuperacion v1/v2, ensayada con lote parcial.
3. Habilitar nueva creacion solamente tras comprobar registros y recuperacion.
4. Ante fallo, deshabilitar nueva creacion v2 con mecanismo cerrado en0.2;
   conservar lector/recuperacion v2 y datos. No restaurar binario v1 puro.
5. Smoke productivo GET/preview, sin sonda de creacion. La sonda con gasto sigue
   diferida y requiere seleccion, objetivo, cantidades y reversa concretos.

## Confirmacion operativa previa por fase

Este inventario cumple harness-plan; no es una aprobacion concedida ni amplifica
permisos existentes. No se genera plan-preapprovals.json con decision approved sin
respuesta explicita. Al cerrar0.2 se registra el alcance realmente autorizado.

| Asunto/operacion | Motivo | Scope y limites |
|---|---|---|
| SELECT de Orbit y lectura de snapshots bridge/accounting | Confirmar identidad, costos y cobertura | 0.1/B.2/B.3; solo lectura, sin cambios a esos servicios |
| Carga de credenciales existentes via ORBIT_SECRETS_DIR | Cliente oficial y reporting para conciliacion | 0.3/B.1/B.3; rutas minimas confirmadas por adaptador; nunca imprimir valores ni copiar al repo |
| GET de catalogo/estructura y POST a reporting/reports | Obtener fuentes observadas | 0.3/B.1/B.3; reportes no modifican anuncios; ninguna nueva ruta de escritura comercial |
| git push, PR y lectura de CI | Revision y calidad | A.5/B.6; rama desde origin/master; sin force push ni --no-verify |
| Backup, migracion nueva y recrear solo Orbit | Publicar entregas recuperables | A.5/B.6; secuencia anterior; no servicios vecinos |
| Crear/pausar anuncios reales | Sonda de gasto, no necesaria para cerrar codigo con simulacion | Fuera del alcance automatico; sigue diferida, no autorizada por este plan |

## Evaluacion y trazabilidad de harness-plan

Revision y puntuacion de alternativas: `plans/catalogo-campanas-01-validacion.md`.
Reglas confirmadas del usuario: seleccion sin filtros economicos y comparacion
asesora. D1–D4 cerradas. No se inventan pesos para el producto.

Verificaciones co-requeridas: spec/plan alineados; memoria documental project-scoped
revisada; tres revisiones independientes; invariantes monetarios y de madurez del
motor intactos; pytest/Ruff/pre-commit/CI existentes; pruebas y reversa en DoD.
Fuentes publicas y evidencia real de2026-09-06 estan en el spec. La evidencia de
investigacion no sustituye la reconciliacion de la implementacion futura.

## Inicio de una sesion de ejecucion

- 0.2 y 0.4 cerrados: A.1 y B.1 se pueden pedir. 0.4 no bloquea A.
- Ads MX verificada en investigacion (cost/clicks). B.1 implementa la
  ingesta y reconcilia la tabla persistida. US no sondada. Por probar
  exige cobertura (regla de 0.4). Featured Offer Sin verificar (no
  bloquea B.4). Produccion F1 intacta hasta A.5.

En Codex puede darse la misma instruccion con el nombre completo del plan.
El plan activo del manifest sigue siendo orbit-ui-01; usar seleccion explicita
por nombre evita activar implementacion durante esta formalizacion.

Siguiente apoyo opcional: harness-plan-brief para una vista HTML del alcance,
las decisiones pendientes y los casos de aceptacion. No sustituye este contrato.
