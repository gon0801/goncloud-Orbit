# ORBIT 19 — Catalogo abierto y evaluacion para campanas

Estado: BORRADOR, solo planificacion. Ninguna tarea de implementacion autorizada
por guardar este documento. Solicitud del dueno 2026-09-06.

Spec delta propuesto: docs/superpowers/specs/2026-09-06-catalogo-campanas-abierto-design.md.
Prioridad: seleccionar sin bloqueo economico y comparar con evidencia trazable.
`team_validation_mode: subagent` — Producto/datos, Arquitectura y Seguridad/QA/esceptico.

## Decisiones para cerrar el plan

| Decision | Propuesta | Estado |
|---|---|---|
| Objetivo de comparacion | Rentabilidad con espacio para probar nuevos | Consultado, pendiente |
| Lanzamientos sin margen | Objetivo ACoS manual del grupo, presupuestos/bids explicitos | Consultado, pendiente |
| Alcance | Crear nuevas campanas; anadir a existentes como ampliacion | Consultado, pendiente |
| Publicaciones multiples | Elegir individualmente una o varias del producto | Propuesto, cumple solicitud |
| Nota de calidad | Metricas + motivos + evidencia; sin score arbitrario | Propuesto |

## Entregas y tareas

La entrega A resuelve el bloqueo actual. B completa la comparacion publicitaria
que solicita el dueno; no se declara completado todo ORBIT19 al terminar solo A.
Las tareas dependientes de decisiones pendientes no se implementan antes de
resolverlas. Orden ejecutable dentro de cada entrega segun Depends.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | [lane:gate] [tdd:skip:investigacion] Validar catalogo actual: identidades/SKU, cobertura de margen y lotes recuperables | SELECT productivo documentado, omisiones y duplicados conocidos; no depende del nuevo reporte Ads; cero mutaciones Amazon | - | cc:TODO |
| 0.2 | [lane:gate] [tdd:skip:docs-contract] Cerrar decisiones del dueno y enmienda de specs anteriores | Objetivo y alcance resueltos; spec sin contradicciones; matriz de casos y rollback v1/v2 definidos | 0.1 | cc:TODO |
| 0.3 | [lane:gate] [tdd:skip:investigacion] Verificar fuentes Ads por producto y disponibilidad | Contrato, permisos, campos, grano, atribucion y muestra reconciliados; carencias documentadas bloquean solo la ingesta afectada de B, nunca A | - | cc:TODO |
| A.1 | [lane:gate] [tdd:required] Versionar plan y preparar persistencia para listings multiples y target manual | Migracion nueva; fixtures v1 mantienen lectura/huella, v2 soporta None real; integridad producto/listing/SKU/mercado y objetivo probada | 0.2 | cc:TODO |
| A.2 | [lane:gate] [tdd:required] Motor/CLI/API con seleccion explicita y objetivo manual | Sin ventas, margen negativo, muestra pequena y varios ASIN llegan al preview; IDs invalidos y SKU duplicados fallan antes de POST; grupos mixtos no inventan margen | A.1 | cc:TODO |
| A.3 | [lane:gate] [tdd:required] Registro, recuperacion e idempotencia v1/v2 | Dos ASIN del mismo producto se registran; doble envio/orden distinto no duplica; error parcial conserva IDs; reconciliar/registrar/pausar lotes antiguos y nuevos funciona | A.2 | cc:TODO |
| A.4 | [lane:gate] [tdd:required] Selector por publicacion, fotos, objetivo, causas y revision | Nuevo y mal desempeno seleccionables; metricas faltantes no muestran0; objetivo/modo/gasto explicitos; cambiar parametros invalida preview; teclado/movil verificados | A.2,A.3 | cc:TODO |
| A.5 | [lane:release] [tdd:skip:validacion-entrega] Review, PR, CI y despliegue de apertura | Regresiones rojo/verde; Ruff/pre-commit; suite completa en CI PR; backup y rollback compatibles; smoke GET/preview real, sin crear anuncios | A.4 | cc:TODO |
| B.1 | [lane:gate] [tdd:required] Ingerir rendimiento Ads por producto anunciado | Cliente existente; append-only; mapeo exacto marketplace/ASIN/SKU y entidades; cobertura/lag trazables; totales conciliados con Amazon y sin doble conteo | 0.2,0.3 | cc:TODO |
| B.2 | [lane:gate] [tdd:required] Exponer economia observada y evidencia separada | Calculo financiero unico conserva semantica madura; muestras pequenas etiquetadas, guardas monetarias/costos/cargos intactas; mismo producto no se duplica por ASIN | 0.2 | cc:TODO |
| B.3 | [lane:gate] [tdd:required] Datos de disponibilidad con frescura | Stock vendible y estado sustentados en fuente; FBA/FBM reconciliados; sin dato o sin permiso = Sin verificar; fuera de stock no se confunde con rechazo tecnico de creacion | 0.2,0.3 | cc:TODO |
| B.4 | [lane:gate] [tdd:required] Evaluacion y ordenes explicados | Estados del spec con razones; ACoS desde sumas; cero/faltante diferenciados; madurez y muestra visibles; no atribuye metricas de grupo a ASIN; ranking no bloquea seleccion | B.1,B.2,B.3 | cc:TODO |
| B.5 | [lane:gate] [tdd:required] Comparador en selector y seccion Por probar | Orden/filtros no pierden seleccion; moneda/grano/ventana visibles; producto malo y nuevo siguen seleccionables; muestra pequena no parece certeza | A.4,B.4 | cc:TODO |
| B.6 | [lane:release] [tdd:skip:validacion-entrega] Validacion final y despliegue | Review independiente; regresiones rojo/verde; Ruff/pre-commit; CI completo PR; conciliacion externa y demo de casos; A y B comprobados en produccion | A.5,B.5 | cc:TODO |

Areas afectadas previstas: app/fabrica_plan.py, fabrica_web.py, api_fabrica.py,
tools/fabrica_campanas.py, templates/fabrica.html, static/js/fabrica.js,
static/css/fabrica.css; nuevas migraciones y modulo de evaluacion; reporting y
scheduler existentes para la fuente por producto. El spike0.3 precisa nombres de
contratos nuevos antes de escribirlos. Nunca editar migraciones selladas.

## Matriz de aceptacion de producto

1. Producto nuevo sin ventas: elegir publicacion, capturar objetivo y ver plan;
   margen ausente permanece ausente. No se interpreta como producto malo.
2. Producto con ventas pero menos de30 fechas: disponible; evidencia limitada visible.
3. Producto con ACoS desfavorable o margen negativo: seleccionable con explicacion.
4. Dos ASIN del mismo producto: seleccion explicita, anuncios/SKU correctos y
   snapshot completo; ventas/margen de producto no sumados dos veces.
5. ASIN sin vinculo, SKU ausente o mercado cruzado: motivo concreto, ningun POST.
6. Una venta atractiva y 100 ventas comparables no presentan igual cantidad de
   evidencia. Sin reporte no se representa como gasto/ventas cero.
7. Grupo mixto: objetivo manual visible e identico en preview, huella y goals.
8. Inventario cero observado, inventario viejo e inventario desconocido se distinguen.
9. Plan antiguo en error puede reconciliarse, registrarse y pausarse despues del cambio.
10. Ninguna etiqueta o reordenacion crea/pausa anuncios ni modifica los ya elegidos.

## Opciones evaluadas

| Opcion | Valor | Riesgo/viabilidad | Clasificacion |
|---|---|---|---|
| Seleccion por listing + target explicito + evidencia separada | Resuelve bloqueo y falta de comparacion | Viable; requiere migracion y lector compatible | Required tras cerrar decisiones |
| Margen proyectado obligatorio para lanzar | Util para futuro, exige fees/precios vigentes fiables | Retrasa apertura y puede recrear bloqueo | Optional, salvo eleccion del dueno |
| Score0–100 con pesos iniciales inventados | Parece simple pero oculta datos faltantes | No demostrado; falsa precision | Reject |
| Repartir datos de campana entre productos | Facil reutilizacion aparente | Atribucion falsa, contamina ranking | Reject |
| Quitar guardas de margen del motor o rellenar con promedio/0 | Desbloqueo aparente | Cambia decisiones y falsifica dato | Reject |
| Anadir a campanas existentes | Util, requiere seleccion campaña/grupo y reversa de product_ad | Flujo adicional, pendiente de alcance | Optional/pendiente |
| Stock y capacidad de servir con fuente real | Evita recomendar anuncios que no pueden mostrarse | Fuente/frescura deben validarse, unknown visible | Recommended dentro de B |
| Reviews, margen futuro, score calibrado y limites totales de experimento | Valor potencial posterior | Requieren contratos/fuentes adicionales | Optional |

## Validacion del plan

- Producto/datos: falta de historial no es mal desempeno; el margen realizado
  tiene grano producto+plataforma y no sustituye al prospectivo.
- Arquitectura: impedir fallos despues de POST con migracion, versionado y
  deduplicacion por SKU, no solo quitar disabled del frontend.
- Seguridad/QA/esceptico: token y confirmacion preservados; shadow no evita gasto;
  no simular autor individual; clasificacion no gobierna el optimizador.
- Contratos consultados: CONTEXTO, ADS_OPTIMIZER_V2_DESIGN, specs FABRICA01 y UI01,
  docs/MARGEN-ENTIDAD, migraciones y codigo vigente. Estos documentos contienen
  decisiones previas revisadas; se enmiendan solo al aprobar este borrador.
- Memoria: consulta project-scoped de documentos/planes; busqueda en memoria local
  de la sesion sin coincidencias relevantes. No se observo servicio harness-mem
  disponible; no se afirma ausencia de decisiones fuera de los documentos leidos.
- Baseline existente: pytest, Ruff0.15.20 en pre-commit y Quality CI. No hace falta
  instalar otro stack. Tests locales solo archivos cambiados; bateria completa
  al final en CI de cada PR, repetir solo si nuevas correcciones lo justifican.

## Alcance operativo de la futura ejecucion

Lecturas de produccion y consultas Amazon de reporte/catalogo para conciliacion;
credenciales existentes cargadas sin imprimir ni copiar secretos al repo.
PR/CI, backup, migracion y despliegue solo Orbit; bridge/accounting permanecen
sin cambios. Una sonda que cree campanas y pueda gastar sigue diferida y necesita
su alcance concreto: seleccion, objetivo, presupuesto, modo y reversa. Aprobar
un plan de codigo no equivale a ordenar esa sonda ni a autorizar gasto nuevo.

## Continuacion

Cerrar las tres decisiones consultadas y actualizar este borrador. Para una
sesion dedicada: iniciar `codex` en este repositorio y dar la instruccion
"Ejecuta ORBIT19 segun plans/catalogo-campanas-01.md, empezando por0.1; conserva
las decisiones pendientes como pendientes y no realices la sonda Amazon".
No cambiar el plan activo ni arrancar implementacion durante esta planificacion.
