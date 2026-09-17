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
  tres fuentes son componentes distintos de un mismo envío). Suma DENTRO de
  cada fuente antes de sumar entre fuentes (corregido en la ronda r1,
  hallazgo 7 — ver abajo).
- `duplicado_etiqueta`: cuando la orden trae las DOS fuentes de etiqueta
  (`LabmanLabelPurchase` y `shipping_label`), suma todas las filas DENTRO de
  cada una de esas dos fuentes primero, y cuenta solo la MAYOR de las dos
  sumas; cualquier otra fuente (p.ej. `ShippingHB`) se suma completa.
  **Esta es una hipótesis del lead, no el veredicto del revisor escéptico**
  citado en `plan-validacion-ebm.md` (que descartaba órdenes por monto
  exacto duplicado, prueba que dio cero coincidencias y quedó con un hueco
  declarado). Ninguna de las dos lecturas se sella aquí.

## Ronda de corrección r1 (2026-09-17)

El revisor devolvió CHANGES (5 altas / 8 medias) sobre el commit `743b3cf`.
Todos los hallazgos se corrigieron en el mismo árbol, sin producción:

- **Hallazgo 1 (alta)** — `efecto-margen.sql` leía la DoD al revés (pedía
  "≥10 productos", el código exigía ≥10 órdenes POR producto y borraba en
  silencio la fila de 90 días de cualquier producto con pocas órdenes ahí).
  Corregido: se eligen los 10 productos con más órdenes a 365 días por
  plataforma, y se emiten SIEMPRE las tres ventanas para esos mismos 10
  productos, con una columna `alcanza_minimo` (≥6) por ventana.
- **Hallazgo 2 (alta)** — costo y FX se resolvían a la fecha del CARGO, no
  de la VENTA (`sku_cost` es vigente a la fecha de la venta,
  `migrations/0001_initial.sql` l.169). Corregido: `venta_event_date`
  alimenta el lateral de `sku_cost` y los dos `fx_resolve`; `event_date`
  (del cargo) solo decide en qué ventana de 90/180/365 cae la orden.
- **Hallazgo 3 (alta)** — una orden sin `sku_cost` desaparecía del
  promedio, imputando su ausencia al resto. Corregido: `ordenes_sin_costo`
  y `ordenes_sin_ingreso` cuentan esas órdenes; el margen del grupo sale
  `NULL` (no un promedio) cuando cualquiera de las dos es > 0.
- **Hallazgo 4 (alta)** — sin sonda de formato: si la parte 6 de
  `source_event_id` no trae los literales esperados, `duplicado_etiqueta`
  salía idéntica a `componentes` sin avisar. Se agregó
  `consultas/00-sonda-formato.sql` (corre primero, prefijo `00`) y una
  cobertura por fuente al final de cada consulta con lecturas
  (`envio-por-producto.sql`; las demás la comparten vía el mismo patrón de
  `orden_costo`).
- **Hallazgo 5 (alta)** — `rezago-emision.sql` declaraba un hueco que NO
  era tal: `spapi_order_observation.fulfillment_status` existe
  (`migrations/0031_spapi_orders_bitemporal.sql` l.30, poblada como
  `fulfillment.fulfillmentStatus` por `app/spapi/orders.py` l.256).
  Reescrita: mide `event_date` del cargo menos la primera observación
  `'Shipped'` de la orden, declara el error de ~1 día por la cadencia
  diaria de re-observación, y publica cobertura
  (`ordenes_con_observacion_shipped` / `ordenes_sin_observacion_shipped`).
- **Hallazgo 6 (media)** — ventana inconsistente entre consultas.
  Corregido: TODAS usan `[hoy-N, hoy)` semiabierta, con "hoy" =
  `(now() AT TIME ZONE 'UTC')::date` (no `current_date`, que depende de la
  zona horaria de la sesión).
- **Hallazgo 7 (media)** — `duplicado_etiqueta` colapsaba dos filas de la
  MISMA fuente con `max()` en vez de sumarlas primero. Corregido en las
  cinco consultas que lo calculan; validado con una orden sintética de dos
  filas `LabmanLabelPurchase` (ver `validacion-local.md`). Se agregó
  `tiene_multi_fila_misma_fuente` / `ordenes_con_multi_fila_misma_fuente`
  a la cobertura.
- **Hallazgo 8 (media)** — un `fuente_tipo` presente pero no reconocido
  entraba en silencio a `usable`. Ahora `descartes.sql` tiene la razón
  `fuente_no_reconocida` (separada de `fuente_desconocida`, que es
  ausencia de partes), y esas órdenes se excluyen del resto de las
  consultas (mismo criterio que `fuente_desconocida`, para que
  `descartes.sql` y las demás tablas no se contradigan sobre qué es
  "usable"). `cargos-por-orden-y-fuente.sql` las etiqueta
  `fuente_no_reconocida:<valor>`.
- **Hallazgo 9 (media)** — lo validado localmente no coincidía con el
  formato exacto de `correr.sh` (`-tA`, sin `ROLLBACK`). Corregido:
  `correr.sh` y la validación local corren AHORA el mismo pipeline exacto
  (`BEGIN READ ONLY; \echo INICIO; <consulta>; \echo FIN; ROLLBACK;`, con
  `printf` en vez de `echo` para evitar diferencias de interpretación de
  escapes entre shells).
- **Hallazgo 10 (media)** — `correr.sh` ahora escribe a
  `<nombre>.txt.parcial` y solo hace `mv` a `<nombre>.txt` si el pipeline
  completo salió en 0; el stderr se guarda aparte en `<nombre>.err`.
- **Hallazgo 12 (media)** — se agregó `productos_que_parpadean_con_histeresis`
  a `parpadeo.sql`, recorriendo las seis ventanas de la más vieja a la más
  nueva y contando solo una salida EXPLÍCITA (`ordenes < 3`) que ocurre
  justo después de haber estado evaluado — no cualquier "todavía no tenía
  historia". Validado con tres productos sintéticos que discriminan las
  tres situaciones (ver `validacion-local.md`, hallazgo 12).
- **Hallazgo 13 (media)** — esta sección y "Supuestos declarados" (abajo)
  se actualizaron a lo que queda después de los arreglos.

**Bajas (de una línea, resueltas):**
14) `(observed_at/now() AT TIME ZONE 'UTC')::date` en vez de `current_date`
o casts implícitos, en todas las comparaciones de fecha. 15) `and le.kind =
'fee'` agregado al filtro de población. 16) `orden_producto` (y todo join
relacionado) usa `(order_id, platform)`, no solo `order_id`. 18)
`efecto-margen.sql` ya no llama `unidades_muestreadas` a un conteo de
órdenes: ahora `ordenes_muestreadas` (cuenta órdenes) y
`unidades_totales_muestreadas` (`sum(quantity)` real) son columnas
separadas. 19) El resultado (b) de `cargos-por-orden-y-fuente.sql` se
acotó a las órdenes con más de una fuente o más de una fila por fuente (ver
tabla de los tres resultados, abajo).

**No cambiado (decisión del lead):** el hallazgo 11 pedía cambiar el patrón
de conexión de `correr.sh` a `psql -U app_read`. El patrón de conexión lo
fija el runbook de la fase y no se puede comprobar ese rol sin leer
producción (fuera del alcance permitido de esta sesión); `correr.sh`
conserva exacto el patrón con `ORBIT_DSN_READ` y `test -n`.

**Residual declarado (hallazgo 17):** las consultas de E.1 repiten el CTE
`poblacion → cargos → sum_por_fuente → orden_meta → orden_costo` de forma
literal en 10 lugares distintos (uno por archivo/bloque que necesita el
costo por orden). Es deliberado — hecho 13 del plan pide "un solo filtro
escrito en un CTE común repetido literal", precisamente para que un
arreglo como los hallazgos 7 u 8 de esta ronda sea imposible de aplicar a
medias (si una sola copia se actualiza y las otras no, las consultas
divergen en silencio). El costo es mantenimiento: cualquier arreglo futuro
a esa lógica tiene que tocar las 10 copias, y esta ronda ya lo demostró
(los hallazgos 7 y 8 se corrigieron en las cinco/diez consultas a la vez).
Queda como residual, no como bug: cuando E.0/E.2 sellen la regla real, vale
la pena evaluar una vista o función SQL compartida — pero eso es decisión
de esa tarea, no de E.1, que es de solo lectura y no toca el esquema.

## Por qué las salidas dicen `unknown`

El 2026-09-17 el lead intentó una sonda trivial de solo lectura contra
producción y el clasificador de permisos de su sesión la negó (motivo:
Production Reads). Desde entonces nadie la ha vuelto a intentar, ni el
lead ni sus subagentes: la negativa se respetó y no se rodeó. Por eso
todas las cifras de producción de este documento están en `unknown`.

Esta tarea (E.1) se limitó a: escribir y versionar las consultas, escribir
el corredor, y validar la mecánica de cada consulta contra una base local
**desechable** con datos **sintéticos** (ver `validacion-local.md`).
Ninguna consulta se corrió contra producción, y ninguna cifra de esta
sección es real: todas las celdas marcadas `unknown` lo están porque nadie
con permiso las corrió todavía.

Para producirlas, el dueño corre, desde la raíz del repo:

```
! bash docs/evidencia/repricing-01/E.1/correr.sh
```

Eso deja las salidas literales en `docs/evidencia/repricing-01/E.1/salidas/`,
una por consulta (`<nombre>.txt`, con `<nombre>.err` si algo falló). Cuando
existan, sus números reemplazan los `unknown` de las tablas de abajo — sin
inventar ni copiar cifras de `plan-validacion-ebm.md` (que mide ventanas y
muestras distintas a las que producen estas consultas).

## Tablas (cita del archivo .sql que las produce; celdas `unknown`)

### 0) Sonda de formato — `consultas/00-sonda-formato.sql`

Corre PRIMERO (prefijo `00`). Dos resultados: (a) distribución real de las
partes 2 y 6 de `source_event_id` para `shipping_fee`; (b) cuántas órdenes
(texto) aparecen en más de una plataforma. Si (a) muestra algo distinto de
`ShippingHB`/`LabmanLabelPurchase`/`shipping_label` en la parte 6, el
supuesto de formato que usan las demás consultas está mal y hay que
corregirlas antes de confiar en `duplicado_etiqueta`.

| platform | kind | parte_2 | parte_6 | filas |
|---|---|---|---|---|
| unknown | unknown | unknown | unknown | unknown |

| órdenes en más de una plataforma |
|---|
| unknown |

### 1) Envío por producto — `consultas/envio-por-producto.sql`

Dos resultados: (a) por `(product_id, platform, ventana_dias, lectura)`:
órdenes, mediana, p75, p90, máximo del costo de envío por orden
(`abs(amount)`, agrupado por `order_id`, ventana semiabierta `[hoy-N, hoy)`
en UTC); (b) cobertura por fuente y plataforma.

| product_id | platform | ventana_dias | lectura | ordenes | mediana | p75 | p90 | máximo |
|---|---|---|---|---|---|---|---|---|
| unknown | unknown | 90 / 180 / 365 | componentes / duplicado_etiqueta | unknown | unknown | unknown | unknown | unknown |

| platform | con ShippingHB | con LabmanLabelPurchase | con shipping_label | con fuente_desconocida | con fuente_no_reconocida | con multi-fila misma fuente |
|---|---|---|---|---|---|---|
| unknown | unknown | unknown | unknown | unknown | unknown | unknown |

### 2) Productos que alcanzan el mínimo — `consultas/productos-que-alcanzan-minimo.sql`

Por `(platform, ventana_dias, lectura)`: cuántos productos con alguna orden
llegan a 6 órdenes.

| platform | ventana_dias | lectura | productos con alguna orden | productos que alcanzan el mínimo (≥6) |
|---|---|---|---|---|
| unknown | 90 / 180 / 365 | componentes / duplicado_etiqueta | unknown | unknown |

### 3) Descartes — `consultas/descartes.sql`

Por razón: `sin_order_id` (filas, no órdenes), `fuente_desconocida`,
`fuente_no_reconocida`, `sin_venta_ligada`, `multi_producto`, `usable` (con
la sub-cuenta de multi-unidad, que NO se descarta). Prioridad cuando una
orden calza en más de una razón: `fuente_desconocida` > `fuente_no_reconocida`
> `sin_venta_ligada` > `multi_producto` > `usable`.

| razón | órdenes | de las cuales multiunidad (no descartada) |
|---|---|---|
| sin_order_id | unknown | — |
| fuente_desconocida | unknown | unknown |
| fuente_no_reconocida | unknown | unknown |
| sin_venta_ligada | unknown | unknown |
| multi_producto | unknown | unknown |
| usable | unknown | unknown |

### 4) Cargos por orden y por fuente — `consultas/cargos-por-orden-y-fuente.sql`

Tres resultados en el mismo archivo:

| # | qué mide | alcance |
|---|---|---|
| (a) | distribución de cuántas órdenes traen 1/2/3... cargos, por plataforma | todas las órdenes usables |
| (b) | desglose de monto por orden y por fuente | SOLO órdenes con más de una fuente distinta, o más de una fila de la misma fuente (hallazgo 19) — no todas |
| (c) | órdenes con exactamente 3 cargos, con sus montos por fuente | insumo directo de la disputa del hecho 14, **sin veredicto** |

| platform | n_cargos | órdenes |
|---|---|---|
| unknown | 1 / 2 / 3 | unknown |

| order_id | platform | fuente | monto | filas |
|---|---|---|---|---|
| unknown | unknown | unknown | unknown | unknown |

### 5) Rezago de ingesta — `consultas/rezago-ingesta.sql`

Por plataforma: p50/p90/máximo de
`(observed_at AT TIME ZONE 'UTC')::date - event_date`.

| platform | filas | p50 (días) | p90 (días) | máximo (días) |
|---|---|---|---|---|
| unknown | unknown | unknown | unknown | unknown |

### 6) Rezago de emisión — `consultas/rezago-emision.sql`

**Corregido en la ronda r1 (hallazgo 5):** la versión anterior declaraba un
hueco que no era tal. Mide `event_date` del cargo menos la primera
observación `'Shipped'` de la orden en `spapi_order_observation`. Error
declarado: la re-observación es diaria, así que esto sobreestima el rezago
real hasta ~1 día. Cobertura: una orden sin ninguna observación `'Shipped'`
se cuenta aparte, no se descarta ni entra al percentil.

| platform | órdenes con cargo | con observación Shipped | sin observación Shipped | p50 (días) | p90 (días) | máximo (días) |
|---|---|---|---|---|---|---|
| unknown | unknown | unknown | unknown | unknown | unknown | unknown |

### 7) Parpadeo del mínimo — `consultas/parpadeo.sql`

Seis ventanas móviles de 90 días desplazadas 30 días entre sí (supuesto
declarado en el .sql: el hallazgo 8 de `plan-validacion-ebm.md` no fija el
desplazamiento exacto). Por `(platform, lectura)`: productos que alcanzan el
mínimo en alguna ventana, productos estables en las seis, productos que
parpadean con corte seco (cualquier cruce de 6, sobreestima) y productos que
parpadean CON histéresis (entra ≥6, sale <3 de verdad — hallazgo 12).

| platform | lectura | alcanzan el mínimo en alguna ventana | estables en las seis | parpadean (corte seco) | parpadean (con histéresis) |
|---|---|---|---|---|---|
| unknown | componentes / duplicado_etiqueta | unknown | unknown | unknown | unknown |

### 8) Efecto sobre el margen — `consultas/efecto-margen.sql`

Dos resultados en el mismo archivo, para los 10 productos con más órdenes a
365 días por plataforma (hallazgo 1 — no un umbral de órdenes por ventana):
(a) margen por unidad reconstruido con `L` = mediana, comparando ventana 90
vs 180 vs 365 días, SIEMPRE las tres para esos mismos 10 productos; (b) a
ventana fija de 180 días, `L` con p50 vs p75 vs p90. `L_unidad = L_orden /
unidades_de_la_orden`. Costo y FX se resuelven a la fecha de la VENTA
(hallazgo 2), no del cargo. `ordenes_sin_costo`/`ordenes_sin_ingreso`
cuentan huecos; el margen del grupo sale `NULL` si cualquiera es > 0
(hallazgo 3, regla 3 de Orbit: un dato faltante nunca es el promedio de los
conocidos).

**El margen reconstruido aquí es contribución SIMPLIFICADA en MXN (venta −
costo − envío), sin IVA, sin ISR y sin comisiones de plataforma ni Ads. NO
es comparable contra un goal de margen de la fase B** — es insumo de E.1
para ver el efecto de la ventana/percentil, no una medición de rentabilidad
real. Tampoco filtra por canal FBM porque esa columna no vive en
`ledger_event`/`listing` (vive en `estimacion_oferta_observation`, hallazgo
14 de `plan-validacion-ebm.md`).

| product_id | platform | ventana_dias | lectura | ordenes | alcanza_minimo | unidades totales | ordenes muestreadas | sin costo | sin ingreso | fx pendiente | L mediana | costo/u | ingreso/u | margen/u |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| unknown | unknown | 90 / 180 / 365 | componentes / duplicado_etiqueta | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown | unknown |

## Supuestos declarados (no verificados contra producción por esta tarea)

- La fuente del cargo se identifica con la parte 6 de `source_event_id`
  (mismo patrón que la consulta de la disputa en `plan-validacion-ebm.md`,
  que usa las partes 2 y 6), y se asume que esa parte trae literalmente
  `ShippingHB`, `LabmanLabelPurchase` o `shipping_label`. `00-sonda-formato.sql`
  corre primero y valida esto contra producción antes que el resto; si sale
  distinto, las consultas que dependen de `fuente_tipo` necesitan ajustarse
  antes de confiar en su salida.
- `parpadeo.sql` desplaza las seis ventanas 30 días entre sí; el
  desplazamiento exacto del hallazgo 8 original no está escrito en ninguna
  fuente disponible para esta tarea.
- "FBM" en `efecto-margen.sql` se aproxima como "tiene al menos una orden en
  la muestra de envío", porque el canal no vive en las tablas leídas por
  E.1.
- El margen de `efecto-margen.sql` es contribución simplificada en MXN
  (venta − costo − envío, sin IVA, ISR ni comisiones); no es comparable
  contra un goal de margen de la fase B.
- El CTE de costo por orden se repite literal en 10 consultas/bloques
  (residual declarado, hallazgo 17) — deliberado por el hecho 13 del plan,
  con el costo de mantenimiento anotado arriba.
