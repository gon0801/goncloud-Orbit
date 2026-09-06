# ORBIT 19 — Validacion formal de harness-plan

Snapshot del scoring **pre-cierre 0.2** (2026-09-06). No es estado vivo:
D1–D4 y 0.1–0.4 cerraron despues. Ver `plans/catalogo-campanas-01.md`.
`team_validation_mode: subagent`. Referencias: spec ORBIT19 y el plan.

## Evidencia y memoria consultadas

- Codigo/schema en origin/master aa92611 y documentos CONTEXTO,
  ADS_OPTIMIZER_V2_DESIGN, MARGEN-ENTIDAD, FABRICA01 y FABRICA UI01.
- SELECT productivo del catalogo/margen: MX249/3 elegibles, US119/2;
  detalle de causas/grano/ventana en el spec. No confundir conteos de productos
  con los342/176 listings; una publicacion no es necesariamente un producto.
- Fuentes oficiales Amazon consultadas2026-09-06: guia Sponsored Products,
  reporte de producto anunciado y reglas de atribucion (enlaces en spec).
  Existencia documental del reporte no implica permiso/cobertura real:0.3 verifica.
- Memoria project-scoped en docs/specs/planes: exclusion deliberada de sinmargen,
  residual manual y restriccion multilisting. La nueva solicitud cambia el
  objetivo; enmendar contrato en0.2, no relajar el motor silenciosamente.
- No se observo servicio harness-mem disponible; busqueda local sin resultados
  relevantes no prueba ausencia de memoria externa. No se consultaron otros proyectos.

## Revision por perspectivas

| Perspectiva | Agente | Hallazgo incorporado | Resultado |
|---|---|---|---|
| Producto/datos | plan_catalogo_metricas | ACoS por ASIN requiere nueva fuente; separar margen realizado, muestra y frescura; no promediar targets | Adoptado en0.3/0.4/B |
| Arquitectura | plan_catalogo_arch | Plan v2, PK por listing, compatibilidad CLI/API, archivos compartidos y rollback con lectorv2 | Adoptado enA y propiedad de archivos |
| Seguridad/QA/esceptico | plan_catalogo_critica | Stages/DoD con evidencia;0.2 cierre condicional; desconocidos no completan ingesta; inventario de operaciones sin falsa aprobacion | Adoptado en plan formal |

La revision anterior del borrador obtuvo APPROVE tras separar0.3 de A. La revision
formal aporta los refinamientos anteriores. La revision final de la version formal
se adjunta al PR185 al cerrar esta tarea; no se confunde con aprobar implementacion.

## Puntuacion de alternativas de planificacion

Juicio editorial del plan, no datos de rentabilidad ni score de los productos.
Escala1–5 (1=debil,5=fuerte). Ejes: ajuste al producto(P), evidencia(E), valor(V),
viabilidad(F), seguridad de regresion(R), utilidad futura(U), seguridad(S),
verificabilidad(D). No se usa una suma para ocultar un eje insuficiente.

| Alternativa | P | E | V | F | R | U | S | D | Decision y condicion |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Seleccion por listing + objetivo explicito + recuperacion | 5 | 5 | 5 | 4 | 3 | 4 | 4 | 5 | Required tras D2/D3 y pruebas migracion/compatibilidad |
| Comparacion con reporte Ads por producto y evidencia separada | 5 | 3 | 5 | 4 | 4 | 5 | 4 | 5 | Required para B, implementacion condicionada a fuente verificada0.3 |
| Disponibilidad verificada sin bloquear porunknown | 4 | 2 | 4 | 3 | 4 | 4 | 4 | 4 | Recommended; spike antes de ingesta, no dependencia del nucleo |
| Margen proyectado obligatorio antes de lanzar | 3 | 2 | 3 | 2 | 3 | 4 | 3 | 2 | Optional; si dueno lo elige, replanificar fuentes/modelo |
| Anadir a campanas existentes | 4 | 3 | 4 | 3 | 2 | 4 | 3 | 3 | Optional pendienteD3; especificar reversa antes de implementar |
| Score opaco o reparto de agregados por ASIN | 2 | 1 | 2 | 3 | 1 | 1 | 3 | 1 | Reject: falsa precision/atribucion |

Las opciones con E<=2 no son Required. Riesgos de regresion se preceden con
contrato, migracion expansiva y fixtures; no se rebajan candados para hacer pasar
una puntuacion. **Snapshot:** al escribir este archivo D1–D4 estaban
pendientes; el cierre vivo esta en E/0.2/confirmacion.md.

## Gates de planificacion

| Gate | Evidencia / limite | Estado |
|---|---|---|
| Spec/Plans fit | Contrato y DoD incluyen casos, API/CLI, fuentes, A+B y decisiones | Snapshot: 0.2 pendiente. Cierre vivo: plan 1.2 / E/0.2 |
| Memory/wheel | Reusa fabrica, goals, reporting y recuperacion; enmienda residual conocido | Revisado |
| Product fit | Todo producto valido seleccionable; comparacion asesora y no bloqueo economico | Revisado |
| Security fit | Sin secretos publicados, sin escritura comercial como smoke; autorizaciones no inventadas | Revisado |
| Quality baseline | Pytest, Ruff0.15.20, pre-commit y Quality CI del repo | Existente |
| Works in practice | AC1–AC10, fuentes conciliadas, versionado, rollback y smoke por SHA | DoD definido; ejecucion pendiente |

## Revision final

Resultado: APPROVE del agente independiente plan_formal_final sobre los tres
documentos formales. Confirmo dependencias A/B, disponibilidad opcional, formulas,
DoD, compatibilidad y ausencia de aprobaciones fingidas. Valida planificacion,
no autoriza implementacion/despliegue/gasto.

Verificacion mecanica: 17 tareas con ID unico, dependencias existentes y sin ciclos;
todas con stage/lane/TDD. Snapshot: cc:TODO. Cierre vivo: 0.1–0.4
`cc:完了` en el plan. 0.4 no espera B.1 (B.1 Depends 0.4). Manifest: orbit-ui-01.
Ruff y pre-commit locales; CI de esta rama se corre al publicar el PR #185
(el verde de `4c09189` era el plan anterior).
