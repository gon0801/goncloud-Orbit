# MARGEN ESTIMADO 01 · 0.3 — Acta de cierre del bloque 0

Fecha: 2026-09-08 UTC. Revisión de las evidencias 0.1 y 0.2. Este acta no
autoriza implementación A/B, cambios a Ads, precios o campañas.

## Lo que ya está comprobado

1. Existe una identidad verificable de oferta en bridge, con marketplace, SKU,
   ASIN, canal, precio y `fetched_at`. Orbit todavía no conserva las tres últimas
   dimensiones; `listing_price` es mutable y puede estar desfasado.
2. Todos los listings Orbit de MX y US tienen costo vigente MXN, declarado neto
   de IVA. La tasa USD→MXN vigente en la consulta tenía cuatro días de edad;
   Orbit ya sabe resolverla con fecha y sin inventar una tasa.
3. La Product Fees API se autentica con el loader existente y devuelve total y
   detalle para FBM de MX/US y FBA US. El total y sus detalles son una misma
   representación del cargo y se deben reconciliar, nunca sumar dos veces.
4. La liquidación histórica contiene fees, reembolsos y retenciones, pero no es
   cotización actual ni una regla de cálculo de la siguiente venta. En especial,
   `isr_withheld` llega sin `order_id`; no existe una base de prorrateo para una
   publicación sin ventas.

## Alcance que no puede liberar A/B aún

| Decisión pendiente | Por qué es material | Evidencia necesaria |
|---|---|---|
| Retención prospectiva por mercado | Define R en `I - C - F - L - R`; cambiarla cambia el resultado y la etiqueta | Política fiscal vigente del negocio: fuente, régimen, base, tasas, vigencia, moneda y tratamiento de ISR/IVA |
| Logística FBM | `shipping_fee` histórico no identifica tarifa actual por oferta/canal | Fuente vigente de envío/fulfilment/embalaje, o declaración documentada de que otro componente la incluye |
| FBA MX | Una oferta con inventario FBA devolvió `InvalidParameterValue` al cotizar | Contexto FBA válido por SKU/precio o decisión explícita de dejar FBA MX fuera de v1 |
| Frescura de precio | Hay tres filas US antiguas y no se acreditó cadencia contractual | Cadencia del bridge o regla de expiración basada en esa fuente; no TTL inventado |
| Unidad/BOM del costo | Los esquemas de Orbit y accounting sólo vinculan costo con `product_id`/SKU; no describen kit, multiplicador ni unidad vendida | Fuente Odoo u operativa que pruebe oferta → producto → unidad/BOM, o exclusión explícita de ofertas sin esa equivalencia |

Estas son decisiones de producto, fiscalidad o integración; no se resuelven con
un default, una tasa histórica ni un cambio de código. Hasta entonces el estado
correcto de cualquier publicación afectada es `incompleta`/`desactualizada`, con
motivo, y los valores principales permanecen `null`.

## Contrato que queda sellado para la continuación

- Nombre v1: **Contribución estimada por venta · antes de Ads**, no margen neto.
- Grano: oferta con contexto `(marketplace, seller_sku, ASIN, canal, precio,
  fecha fuente)`, no producto ni listing sin contexto.
- Cálculo sólo si todos los componentes obligatorios del alcance acordado están
  completos y son compatibles: `I - C - F - L - R`. Dinero se conserva en su
  moneda de origen; conversión usa `fx_resolve` y guarda tasa/fecha/procedencia.
- `margen_neto_pct` observado, muestra limitada, orden inicial, selección,
  objetivo manual, huella, bids, budgets, motor y recuperación permanecen
  intactos. El estimado no genera ACoS de equilibrio ni target.
- GET de catálogo sólo lee Orbit. Credenciales y Product Fees viven en una
  ingesta separada, con respuestas sanitizadas y sin mutar bridge/accounting.
- Un componente ausente no equivale a cero. Cero/negativo comprobado sí se
  conserva. Reembolsos, almacenamiento y overhead quedan enumerados como
  exclusiones, no ocultos.

## Decisión de liberación

**Bloque 0: investigación ejecutada; liberación de A/B bloqueada.** La evidencia
demuestra factibilidad parcial, no el contrato económico completo. El siguiente
paso es resolver las cinco decisiones de la tabla con la persona responsable de
fiscalidad/operación. Cuando existan, se enmienda esta acta, se fijan cadencias y
universo soportado, y se habilita A.1. Si una fuente no se consigue, el alcance
se reduce explícitamente por mercado/canal; no se rellena el cálculo.
