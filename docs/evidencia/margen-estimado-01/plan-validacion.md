# Validacion del plan MARGEN ESTIMADO 01

Fecha: 2026-09-08 UTC. Base de lectura `80b6403`, origin/master.
Alcance: investigacion documental/local y plan formal; sin consultas financieras
productivas, pruebas de API privadas, cambios de runtime ni creacion de anuncios.

## Evidencia interna y memoria

| Evidencia | Hallazgo que gobierna el plan |
|---|---|
| `app/listings.py:159`, `:287`, `:335` | Precio del bridge mutable en listing; no conserva fecha fuente; precios divergentes/multiples SKU requieren resolver oferta |
| `app/disponibilidad.py:175` | Bridge expone fetched_at y fulfillment_channel, pero hay que verificar su significado para el precio |
| `app/costs.py:14` y `:27` | Costos vigentes, cero rechazado, valoracion Odoo con includes_tax=false; no prueba cobertura de todos los costos directos |
| `app/fx.py:9` y `migrations/0001_initial.sql:215` | Una resolucion de FX existente USD→MXN, exacta o previa dentro de limite; no inventar tasa ni par inverso |
| `app/ledger.py:10` y `:18` | Cargos/retenciones historicos; marketplace US no obliga a moneda USD en ledger |
| `app/economia_observada.py:1`, `migrations/0021_economia_observada.sql` | Observado compartido por producto/plataforma, muestra limitada separada; conservar ambos |
| `app/fabrica_web.py:140`, `:226`; `app/static/js/fabrica.js` | Integrar bloque informativo sin cambiar seleccion ni las dos rutas de creacion |
| `docs/MARGEN-ENTIDAD.md:24`, `:114`, `:168` | Precedente de contribucion cuando hay exclusiones; precio bruto/costo neto y margen decisorio no son intercambiables |
| `docs/evidencia/orbit-19/0.2/confirmacion.md` | D1–D4 ratificadas: manual ante margen no fiable, muestra limitada no gobierna sort ni target |

Memoria project-scoped consultada: `docs/CHAT-CONTEXT.md`, specs/plans de fabrica
y ORBIT 19, `docs/PENDIENTES-AUTOMATIZACION.md`, memoria local del proyecto
`MEMORY.md` y notas de worktree/pre-push/merge. Esta ultima contiene bloqueos de
otro entorno del 2026-09-06; no demuestra un bloqueo actual ni cambia permisos.
No se observo `.claude/` en el worktree; no afirmar que no existe memoria externa.
No se modificaron SSOT personales ni archivos de Reputacion.

Fuentes externas oficiales y sus limites: seccion S2 del spec. Product Fees
acredita una API de estimacion, no acceso de esta cuenta ni cobertura de impuestos.
La referencia SAT consultada es historica: no se usa como regla/tasa 2026.

## Alternativas y clasificacion

Puntuacion de planificacion 1–5 (5 favorable); no confianza estadistica del producto.

| Alternativa | Encaje | Evidencia | Valor | Viabilidad | Regresion | Utilidad futura | Seguridad | Verificable |
|---|---|---|---|---|---|---|---|---|
| A. Contribucion por venta con insumos verificados y desglose | 5 | 3 | 5 | 3 | 4 | 4 | 4 | 4 |
| B. Solo diferencia precio menos COGS, nombrada literalmente | 4 | 3 | 3 | 4 | 4 | 2 | 4 | 4 |
| C. Rellenar margen ausente con promedios/estimado y derivar target | 2 | 1 | 3 | 3 | 1 | 2 | 2 | 2 |

- Required: inventario de fuentes y contrato economico; separar medidas, moneda,
  fecha y faltantes; pruebas de no contaminacion decisoria y conciliacion externa.
- Required condicionado a 0.3: alternativa A, unica que resuelve la necesidad
  con comisiones y costos directos. Evidencia tecnica de fuentes prospectivas
  aun incompleta: `[needs-spike]` explicito antes de cualquier implementacion.
- Recommended: orden explicito por estimado comparable; seguimiento posterior
  estimado/real cuando exista muestra compatible. No incluidos en las 12 tareas.
- Optional: escenarios editables de precio/costos, reserva de devoluciones,
  overhead por unidad, soporte de multiples ofertas seleccionables. Cada uno
  necesita contrato propio; no son dependencias de v1.
- Reject para esta entrega: B como sustituto del resultado completo; C; score
  opaco0–100, tasas universales, precio mas reciente sin fecha, cuotas nuevas de
  gasto, uso del estimado como target, Repricing/Reputacion/harvest/placements.
  Su rechazo es de alcance, no cancelacion de los otros planes pendientes.

## Revision independiente

`team_validation_mode: subagent`. Tres agentes de lectura, sin delegar permisos
productivos ni implementacion:

| Perspectiva | Conclusion y accion incorporada |
|---|---|
| Arquitectura (`margen_fuentes`) | Adopcion condicional: reutilizar costo/FX, verificar fecha/oferta y capturar inputs reproducibles; no hay fuente prospectiva acreditada de todos los cargos |
| Producto/Esceptico (`margen_producto`) | Adopcion condicional: contribucion por venta, no neto ficticio; excluir contaminacion del target/orden; denominador incompatible con ACoS/halo |
| Seguridad/QA (`margen_qa`) | Requiere SELECT previo, moneda por componente, no doble conteo, as-of, GET sin HTTP ni secretos, negativos/cero y prueba real de cobertura |

Relectura del borrador: corregida la posibilidad de cerrar investigacion con
dictamen de inviabilidad acreditado, sin fingir ejemplos MX/US o tarifa FBM de
un canal no soportado. Tambien se distingue integracion de A en A.4 y de B en B.4, sin deploy hasta
B.5, y se aclara que Product Fees es candidato documental. Arquitectura y QA
aprobaron; Producto aprueba tras las dos correcciones anteriores incorporadas.
El cierre de producto sigue exigiendo enmienda visible
para cualquier mercado/canal fuera del compromiso de 0.3.

## Coherencia y gates

| Gate | Evidencia / efecto |
|---|---|
| Spec y tareas | S1/S6→B.2; S2→0.1/0.2/0.3; S3→A.3/A.4; S4→A.1/A.2; S5→B.1; S7→B.3/B.4/B.5 |
| Memoria y alcance | Extension ya prevista en ORBIT 19; ninguna segunda vista observada ni motor viejo reutilizado |
| Objetivo de producto | Cero ventas admite estimacion; desconocido no se disfraza de mal producto ni bloquea seleccion |
| Seguridad | Credenciales via loader existente en ingesta, allowlist SP-API; GET de lectura; otros servicios preservados |
| Calidad | Ruff 0.15.20/pre-commit/pytest/PostgreSQL y dashboard harness ya configurados; no instalar otro framework |
| Funcionamiento real | 14 criterios de aceptacion; ejemplos reales por mercado/canal; solo nulls no cierra entrega; reversa previa |

Conteo y dependencias: 0.1→0.2→0.3; A.1→A.2→A.3→A.4;
B.1→B.2→B.3→B.4→B.5. **12 tareas**, ninguna implementada.
El trabajo documental se valida en PR CI; sus pruebas no son evidencia de que
la funcion propuesta exista. El deploy pertenece a B.5, fuera de esta entrega.
