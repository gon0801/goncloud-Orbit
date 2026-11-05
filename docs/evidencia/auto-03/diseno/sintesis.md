# Síntesis de arquitectura AUTO-03

## Problema y base

Se pidió diseñar, sin implementar. La fase Ground reutilizó `pstack:how` completo:
dos exploradores de código y un explicador. Se contrastaron las rutas pertinentes
con `origin/master` ce956518 y se hicieron consultas de producción solo de lectura.
El alcance preserva los dueños actuales de pujas y retiro de anuncios; no cambia
la autoridad del inventario ni crea un segundo escritor.

## Uso y forma elegida

El ciclo carga entradas por lote, llama a `resolver_producto(entrada)` y entrega
su meta a la cascada existente. La pantalla consume la evaluación persistida.
Un segundo consumidor, el replay, usa `resolver_producto(entrada_congelada)`;
no vuelve a consultar fuentes actuales. El boceto completo de llamadas y tipos
está en el diseño enlazado al final.

`Producto`, `Dinero` y las variantes de evidencia completa o ausente preceden a
la política: se aplica foundational-thinking. La lectura oculta SQL, identidad
y madurez; la función pura concentra metas y pausas detrás de una entrada y una
salida. El llamador conserva la coordinación transaccional que ya le corresponde.
La validación de fuentes queda en esa entrada, conforme a boundary-discipline.
La cola y retiro conservan sus dueños: separate-before-serializing-shared-state
se aplica evitando un segundo escritor, en lugar de añadir locks entre motores.

## Opciones completas y comparación

Se produjeron dos candidatos aislados antes de sintetizar:

- [A](candidatos/a.md): evaluación dentro del ciclo y nuevo peldaño de target.
- [B](candidatos/b.md): controlador previo con restricción financiera según
  contribución total y gasto publicitario de la misma ventana.

Las perspectivas usaron los modelos disponibles `gpt-5.6-sol` y `gpt-6.1-sol`.
La configuración local de pstack nombra Fable y modelos de Cursor no disponibles
en esta herramienta. No se usó Fable. El comparador independiente usó
`gpt-6-sol`. No hubo candidatos sin entrega.

La [comparación independiente](comparacion.md) inicialmente eligió B por su cuenta
financiera congruente. El lead prefería A por su integración con la política vigente.
Para resolver la diferencia se midió el historial real: Ads por producto empieza
en agosto; la ventana financiera empieza en febrero. B debe abstenerse hoy.
El evaluador revisó esa premisa y recomendó A cuando su meta se describe como
referencia histórica, sin prometer una utilidad causal exacta.

| Criterio de la rúbrica | A | B | Lectura final del lead |
|---|---:|---:|---|
| Autoridad única | 2 | 2 | Ambos requieren cerrar cola → retiro. Se conserva un escritor. |
| Economía y atribución | 2 | 2 | A es estimación explícita; B es cuenta válida con datos hoy ausentes. |
| Control real | 2 | 2 | Ambos reconocen pujas compartidas. Se rechaza vetar una subida de todos por uno. |
| Interfaz y profundidad | 2 | 1 | Una resolución por producto y un encolador vigente son más simples. |
| Recuperación | 1 | 2 | Se toman de B la autorización tipada y el vínculo con la intención. |
| Utilidad con datos disponibles | 2 | 0 | B no puede cubrir su ventana financiera; A tiene alcance parcial verificable. |
| Total | 11 | 9 | Base A, con correcciones e injertos de B. |

Son puntuaciones de comparación, no mediciones de calidad ni aprobación de producción.

## Compromisos y alternativas

- Se acepta cobertura inicial limitada a cambio de no asignar margen ajeno a
  ventas de otro SKU. Podría no haber hojas elegibles todavía.
- Se acepta conservar los goals explícitos a cambio de mantener la autoridad
  que ya tiene el dueño. La meta individual no será un tope absoluto.
- Se acepta una primera entrega sin pausa económica de productos con ventas
  en grupos compartidos a cambio de no alterar las pujas de otros productos.

Un cron separado ocultaría parte del cálculo, pero expondría al ciclo la
coordinación de versiones, frescura y dos decisiones potencialmente incompatibles.
Perdió frente a resolver en el mismo snapshot. Guardar un goal mutable por producto
también simplificaría la lectura, pero obligaría a sincronizarlo con el margen:
se descartó en favor de una referencia calculada y evidencia inmutable.

## Injertos y correcciones

De B se incorporaron la carga por lote antes de recorrer hojas, variantes de datos
completos/ausentes, autorización automática tipada y propiedad explícita de las
pausas. Su encolador adicional se descartó: se amplía `apply_cola.encola_cortes`.

De A se conserva la función pura y el resultado generado en el mismo snapshot
que el ciclo. El historial append-only no se convierte en política mutable para
el siguiente ciclo. Se elimina el requisito genérico de stock positivo de A:
stock no reconciliado no puede paralizar el motor actual ni convertirse en cero.

Se corrigieron ambas propuestas donde daban por completa la evidencia de un reporte
exitoso. Faltan datos de producto/fecha hasta demostrar lo contrario. También se
explicita que una comprobación de halo de 30 días no cubre automáticamente el
periodo de 90 días que usa la política de hojas.

La pausa de cero ventas toma como propuesta los pisos de PAUSE, no el umbral de
aprendizaje 350/36 de A. El nuevo grano queda declarado y pendiente de sello. La
reversa usa `retiro_ids` y autorización humana real; no la firma abreviada e
inexistente del candidato A.

## Banderas rojas revisadas

- Módulos superficiales: no se introducen servicios por cada etapa; quedan carga
  de fuentes y política pura, con tres archivos máximos para seguir la decisión.
- Dueños duplicados: no hay PUT nuevo fuera de retiro ni otro escritor de bids.
- Dos caminos para lo mismo: no se agrega un encolador de productos paralelo.
- Listas copiadas: fracciones, máximos y pisos se consumen de sus fuentes únicas.
- Estado sincronizado a mano: evaluación inmutable referida por decisiones; no
  tabla global de política vigente ni TTL entre dos crons.
- Interfaces que ocultan riesgos: se nombran origen, ventana, cobertura, alcance
  y abstenciones. El cap de operaciones no se presenta como límite monetario duro.

## Verificación del artefacto

Se leyeron completos ambos candidatos y sus críticas. Se contrastaron las firmas
reales de cascada, retiro y reposición y las fuentes SQL de dinero y atribución.
Los ejemplos monetarios se calcularon con Decimal. El documento final explica
qué firmas y esquemas son propuestas, y no contiene stubs instalados en `app/`.
No se ejecutaron tests de texto ni se cambió el motor.
El cierre del comparador encontró un caso de reversa: observar PAUSED tras una
respuesta incierta no demuestra autoría. Se incorporó esa distinción y se hizo
explícito el cambio requerido para que `repone` no recree anuncios archivados
con una autorización que solo permite reponer.

## Política pendiente y próximo paso

¿Se adopta la pausa agregada por producto con los pisos propuestos de 100 clics
y 500 MXN/40 USD? Es una decisión nueva por su alcance, aunque reutilice números
existentes. ¿Cuántos productos cumplen toda la evidencia requerida? La medición
financiera sola no lo responde; se resolverá con la lectura de elegibilidad.

La primera implementación sería el resolutor y su adaptación a la cascada,
verificando el caso monetario de margen 8% y meta 4%.

## Fases del skill

Ground y Sketch terminados. Agree entrega una propuesta concreta al dueño.
Implement queda fuera de la petición de diseño. Scrap se aplicó a la premisa de
usar la ventana financiera completa sin historial Ads: no se añadió un fallback
que inventara el gasto que falta. La arquitectura final queda en
[`2026-10-24-decisiones-producto-design.md`](../../../superpowers/specs/2026-10-24-decisiones-producto-design.md).
