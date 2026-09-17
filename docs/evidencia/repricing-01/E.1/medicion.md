# E.1 — Medición del envío por producto (FBM)

## Qué es esto

Este documento es el **insumo** del acta de E.2 (`docs/evidencia/repricing-01/E.2/acta.md`,
todavía no escrita): E.2 sella la ventana, el mínimo con histéresis, el
percentil de valor y qué pasa con un producto sin historia, **con** los
números que produce este documento, no antes.

La ronda de cinco perspectivas de contexto fresco (producto, arquitectura,
seguridad, QA, escéptico) sobre `plans/repricing-01.md` cerró el 2026-09-16 y
está mergeada en PR #293 (`docs(repricing-01): la revisión de E/B/M corrige
cuatro hechos que el plan daba por medidos`). Esa ronda **redefinió** el
alcance de E.1: entre otras cosas, quitó el supuesto de que la fase E
habilita 222 publicaciones (son ~20), fijó que el percentil se calcula sobre
`abs(amount)` agrupando por orden (no por fila), y dejó abierta — sin
veredicto — la disputa de si las tres fuentes de `shipping_fee`
(`ShippingHB`, `LabmanLabelPurchase`, `shipping_label`) duplican dinero.
`docs/evidencia/repricing-01/plan-validacion-ebm.md` trae el detalle completo
de esa ronda; este documento no repite sus números, los cita.

**E.1 depende de E.0**, que no es parte de esta fase del trabajo: E.0
clasifica qué representa cada una de las tres fuentes de `shipping_fee`
contra el documento contable de origen (no se puede resolver desde
Postgres). Mientras E.0 no cierre, `L` en US no se sella. Por eso **todas**
las tablas de este documento que dependen de esa disputa se entregan bajo
**dos lecturas**, con una columna `lectura`:

- `componentes`: suma todos los cargos de envío de la orden (asume que las
  tres fuentes son componentes distintos de un mismo envío).
- `duplicado_etiqueta`: cuando la orden trae las DOS fuentes de etiqueta
  (`LabmanLabelPurchase` y `shipping_label`), cuenta solo la mayor de las
  dos. **Esta es una hipótesis del lead, no el veredicto del revisor
  escéptico** citado en `plan-validacion-ebm.md` (que descartaba órdenes por
  monto exacto duplicado, prueba que dio cero coincidencias y quedó con un
  hueco declarado). Ninguna de las dos lecturas se sella aquí.

## Por qué las salidas dicen `unknown`

El clasificador de permisos de la sesión del lead **negó** la lectura de
producción el **2026-09-17** (motivo `[Production Reads]`). Esta tarea (E.1)
se limitó a: escribir y versionar las consultas, escribir el corredor, y
validar la mecánica de cada consulta contra una base local **desechable**
con datos **sintéticos** (ver `validacion-local.md`). Ninguna consulta se
corrió contra producción, y ninguna cifra de esta sección es real: todas las
celdas marcadas `unknown` lo están porque nadie con permiso las corrió
todavía.

Para producirlas, el dueño corre, desde la raíz del repo:

```
! bash docs/evidencia/repricing-01/E.1/correr.sh
```

Eso deja las salidas literales en `docs/evidencia/repricing-01/E.1/salidas/`,
una por consulta. Cuando existan, sus números reemplazan los `unknown` de
las tablas de abajo — sin inventar ni copiar cifras de
`plan-validacion-ebm.md` (que mide ventanas y muestras distintas a las que
producen estas consultas).

## Tablas (cita del archivo .sql que las produce; celdas `unknown`)

### 1) Envío por producto — `consultas/envio-por-producto.sql`

Por `(product_id, platform, ventana_dias, lectura)`: órdenes, mediana, p75,
p90, máximo del costo de envío por orden (`abs(amount)`, agrupado por
`order_id`, en ventanas de 90/180/365 días).

| product_id | platform | ventana_dias | lectura | ordenes | mediana | p75 | p90 | máximo |
|---|---|---|---|---|---|---|---|---|
| unknown | unknown | 90 / 180 / 365 | componentes / duplicado_etiqueta | unknown | unknown | unknown | unknown | unknown |

### 2) Productos que alcanzan el mínimo — `consultas/productos-que-alcanzan-minimo.sql`

Por `(platform, ventana_dias, lectura)`: cuántos productos con alguna orden
llegan a 6 órdenes.

| platform | ventana_dias | lectura | productos con alguna orden | productos que alcanzan el mínimo (≥6) |
|---|---|---|---|---|
| unknown | 90 / 180 / 365 | componentes / duplicado_etiqueta | unknown | unknown |

### 3) Descartes — `consultas/descartes.sql`

Por razón: `sin_order_id` (filas, no órdenes), `fuente_desconocida`,
`sin_venta_ligada`, `multi_producto`, `usable` (con la sub-cuenta de
multi-unidad, que NO se descarta).

| razón | órdenes | de las cuales multiunidad (no descartada) |
|---|---|---|
| sin_order_id | unknown | — |
| fuente_desconocida | unknown | unknown |
| sin_venta_ligada | unknown | unknown |
| multi_producto | unknown | unknown |
| usable | unknown | unknown |

### 4) Cargos por orden y por fuente — `consultas/cargos-por-orden-y-fuente.sql`

Tres resultados en el mismo archivo: (a) distribución de cuántas órdenes
traen 1/2/3 cargos por plataforma; (b) desglose de monto por orden y fuente;
(c) las órdenes con exactamente 3 cargos, con sus montos por fuente — insumo
directo de la disputa del hecho 14, **sin veredicto**.

| platform | n_cargos | órdenes |
|---|---|---|
| unknown | 1 / 2 / 3 | unknown |

### 5) Rezago de ingesta — `consultas/rezago-ingesta.sql`

Por plataforma: p50/p90/máximo de `observed_at::date - event_date`.

| platform | filas | p50 (días) | p90 (días) | máximo (días) |
|---|---|---|---|---|
| unknown | unknown | unknown | unknown | unknown |

### 6) Rezago de emisión — `consultas/rezago-emision.sql`

**No implementable tal como se pidió.** No existe en el esquema de Orbit
ninguna columna de "fecha de envío" (se buscó en `migrations/*.sql` y
`app/*.py`, sin match para `ship_date`/`shipped_at`/`fecha_envio`/
`fecha_de_envio`/`shipment_date`). `event_date` del ledger es la fecha del
CARGO (ya medida en el punto 5); `spapi_order_observation.purchase_date` es
la fecha de COMPRA, no de envío. La consulta deja esto documentado en su
propia salida en vez de inventar una columna sustituta.

| motivo | detalle |
|---|---|
| sin_columna_fecha_de_envio | no existe columna de fecha de envío en el esquema de Orbit |

### 7) Parpadeo del mínimo — `consultas/parpadeo.sql`

Seis ventanas móviles de 90 días desplazadas 30 días entre sí (supuesto
declarado en el .sql: el hallazgo 8 de `plan-validacion-ebm.md` no fija el
desplazamiento exacto). Por `(platform, lectura)`: productos que alcanzan el
mínimo en alguna ventana, productos estables en las seis, productos que
parpadean (entran y salen sin histéresis).

| platform | lectura | alcanzan el mínimo en alguna ventana | estables en las seis | parpadean |
|---|---|---|---|---|
| unknown | componentes / duplicado_etiqueta | unknown | unknown | unknown |

### 8) Efecto sobre el margen — `consultas/efecto-margen.sql`

Dos resultados en el mismo archivo, solo para productos con ≥10 órdenes en
la ventana: (a) margen por unidad reconstruido con `L` = mediana, comparando
ventana 90 vs 180 vs 365 días — la decisión real, no el percentil; (b) a
ventana fija de 180 días, `L` con p50 vs p75 vs p90. `L_unidad = L_orden /
unidades_de_la_orden`. El margen reconstruido aquí es un margen de
contribución simplificado (venta − costo − envío); no reconstruye
comisiones de plataforma ni Ads, y no filtra por canal FBM porque esa
columna no vive en `ledger_event`/`listing` (vive en
`estimacion_oferta_observation`, hallazgo 14 de `plan-validacion-ebm.md`).
Marca `tiene_fx_pendiente` cuando falta la tasa para convertir (nunca usa
una constante, regla S10 "FX en US").

| product_id | platform | ventana_dias | lectura | unidades | órdenes | fx pendiente | L mediana | ingreso/u | costo/u | margen/u reconstruido |
|---|---|---|---|---|---|---|---|---|---|---|
| unknown | unknown | 90 / 180 / 365 | componentes / duplicado_etiqueta | unknown | unknown | unknown | unknown | unknown | unknown | unknown |

## Supuestos declarados (no verificados contra producción por esta tarea)

- La fuente del cargo se identifica con la parte 6 de `source_event_id`
  (mismo patrón que la consulta de la disputa en `plan-validacion-ebm.md`,
  que usa las partes 2 y 6), y se asume que esa parte trae literalmente
  `ShippingHB`, `LabmanLabelPurchase` o `shipping_label`. Si la producción
  usa otro formato, `envio-por-producto.sql` y las consultas que dependen
  de `fuente_tipo` necesitan ajustarse antes de confiar en su salida.
- `parpadeo.sql` desplaza las seis ventanas 30 días entre sí; el
  desplazamiento exacto del hallazgo 8 original no está escrito en ninguna
  fuente disponible para esta tarea.
- "FBM" en `efecto-margen.sql` se aproxima como "tiene al menos una orden en
  la muestra de envío", porque el canal no vive en las tablas leídas por
  E.1.
