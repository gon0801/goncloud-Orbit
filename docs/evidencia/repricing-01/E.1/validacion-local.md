# Validación local de las consultas de E.1

**DATOS SINTÉTICOS, NO PRODUCCIÓN.** Todo lo que sigue corre contra una base
Postgres desechable (`e1_validacion`, `localhost:5432`, esquema de
`migrations/0001_initial.sql` + `migrations/0030_spapi_orders.sql` +
`migrations/0031_spapi_orders_bitemporal.sql` aplicados tal cual —
`0030`/`0031` agregan `spapi_order_observation.fulfillment_status`, que
usa `rezago-emision.sql` desde la ronda r1), sembrada con
`validacion-local/semilla.sql`. No hay ninguna conexión a producción en
este documento. Al terminar esta tarea la base se destruyó
(`DROP DATABASE e1_validacion`).

## Ronda r1 (2026-09-17): mismo formato que `correr.sh`

Hallazgo 9 pedía que lo validado localmente sea EXACTAMENTE lo que
`correr.sh` corre contra producción. Desde esta ronda, ambos usan el mismo
pipeline (con `printf`, no `echo` con builtin — algunos `echo` interpretan
escapes de forma distinta entre shells, y eso rompía el `\echo` de psql en
la primera prueba de esta ronda):

```
{
  printf '%s\n' 'BEGIN READ ONLY;'
  printf '%s\n' "\\echo -- INICIO <nombre>"
  cat consultas/<nombre>.sql
  printf '%s\n' "\\echo -- FIN <nombre>"
  printf '%s\n' 'ROLLBACK;'
} | psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" -X -P pager=off -tA -v ON_ERROR_STOP=1
```

Las salidas literales completas quedan en
`docs/evidencia/repricing-01/E.1/validacion-local/salidas/*.txt` (una por
consulta, con `-tA`, `\echo` de inicio/fin y `ROLLBACK` — mismo formato que
`correr.sh` escribiría en `salidas/*.txt` contra producción).

## Cómo se corrió

```
psql "postgresql://orbit:orbit@localhost:5432/postgres" \
  -c "CREATE DATABASE e1_validacion OWNER orbit;"
psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" -f migrations/0001_initial.sql
psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" -f migrations/0030_spapi_orders.sql
psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" -f migrations/0031_spapi_orders_bitemporal.sql
psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" \
  -f docs/evidencia/repricing-01/E.1/validacion-local/semilla.sql
```

## Ronda r2 (2026-09-17): E1-PMIXTO

El verificador rechazó r1 por hallazgo medio: la fixture de "sin
imputación silenciosa" (`E1-PNOCOST`, hallazgo 3 original) no discriminaba
el arreglo, porque su grupo tenía TODAS las órdenes sin costo (`avg()`
ya daba `NULL` solo). Se agregó `E1-PMIXTO` con costo MIXTO dentro del
mismo grupo (4 órdenes con costo, 2 sin) — ver la sección "Hallazgo 3"
abajo para las dos salidas literales (con y sin el arreglo).

## Bordes sembrados en `semilla.sql` (ronda r1 amplía la ronda anterior)

De la ronda anterior: montos negativos; producto con 6 órdenes en 90 días y
otro con 5; orden multi-producto; orden multi-unidad; cargo sin venta
ligada; cargo sin `order_id`; `source_event_id` nulo con `order_id`
presente; orden con 2 y con 3 cargos de fuentes distintas; envíos de hace
91 y 181 días; rezago de ingesta de 7 días.

Nuevos en esta ronda (uno por hallazgo que lo exige):

- **E1-P8**: 8 órdenes a 90 días y 12 a 180 días (hallazgo 1 — que
  `efecto-margen.sql` no filtre por un umbral de órdenes en cada ventana).
- **E1-PCOST**: venta hace 40 días, cargo (envío) hace 10 días, con DOS
  vigencias de `sku_cost` que cambian a los 30 días atrás (50 MXN antes,
  80 MXN después) — hallazgo 2, costo a la fecha de la venta vs del cargo.
- **E1-PNOCOST**: orden usable sin ninguna fila en `sku_cost` — hallazgo 3.
- **E1-PMULTIFILA**: dos filas de `LabmanLabelPurchase` (60 y 40) más una
  de `shipping_label` (70) en la misma orden — hallazgo 7.
- **E1-PNOREC**: `fuente_tipo` = `'OtraFuenteRara'`, fuera del vocabulario
  conocido — hallazgo 8.
- **E1-PHOY**: cargo con `event_date` = hoy — hallazgo 6 (ventana
  semiabierta).
- **E1-PSHIP** / **E1-PSINSHIP**: una orden con observación `'Shipped'` 5
  días antes del cargo, otra sin ninguna observación `'Shipped'` —
  hallazgo 5.
- **E1-PHIST**: catorce órdenes en tres racimos (140-145, 95-96, 5-10 días
  atrás) diseñadas para que las seis ventanas móviles den
  `[0, 6, 8, 8, 2, 6]` órdenes (vieja→nueva): entra, se mantiene, SALE de
  verdad (2 < 3) justo después de haber estado evaluada, y reentra —
  hallazgo 12, flapeo genuino.

Nuevo en la ronda r2:

- **E1-PMIXTO**: 6 órdenes del mismo producto/plataforma con cargo de
  envío reciente (mismo grupo de ventana), 4 con venta dentro de la
  vigencia de `sku_cost` y 2 con venta ANTES de que esa vigencia empezara
  — hallazgo 3 original, esta vez con costo MIXTO dentro del grupo (ver
  sección "Hallazgo 3" abajo).

## Hallazgo por hallazgo, con salida literal

### Hallazgo 1 — top 10 por órdenes a 365 días, tres ventanas SIEMPRE

`E1-P8` (`product_id = 8`) tiene 8 órdenes a 90 días y 12 a 180/365 días.
La versión anterior habría borrado la fila de 90 días si el umbral fuera
"≥10 órdenes en la ventana". La salida real de `efecto-margen.sql`
(`validacion-local/salidas/efecto-margen.txt`) trae las TRES ventanas:

```
8|amazon_us|90|componentes|8|t|8|8|8|0||90||280.0000000000000000
8|amazon_us|180|componentes|12|t|12|12|12|0||90||280.0000000000000000
8|amazon_us|365|componentes|12|t|12|12|12|0||90||280.0000000000000000
```

`ordenes=8` a 90 días SÍ aparece (no se borró), y `alcanza_minimo` (columna
6) es `t` en las tres — confirma que ya no se filtra por un umbral de
órdenes en la ventana, sino que se seleccionan los productos con más
órdenes a 365 días (top 10 por plataforma) y se muestran siempre las tres
ventanas.

### Hallazgo 2 — costo a la fecha de la VENTA, no del cargo

`E1-PCOST` (`product_id = 9`) tiene sku_cost de 50 MXN vigente hasta hace
30 días y 80 MXN después; la venta fue hace 40 días (dentro del período de
50) y el cargo de envío hace 10 días (dentro del período de 80). Si el
costo se resolviera a la fecha del cargo (el bug), saldría 80. La salida
real:

```
9|amazon_mx|90|componentes|1|f|1|1|0|0|f|90|50.0000000000000000|500.0000000000000000|360
```

`costo_unidad_mxn_promedio = 50.0000...` — resuelto a la fecha de la
VENTA, no del cargo. `margen = 500 (ingreso) − 50 (costo) − 90 (L mediana)
= 360`, consistente con la columna final.

### Hallazgo 3 — sin imputación silenciosa

`E1-PNOCOST` (`product_id = 10`) no tiene ninguna fila en `sku_cost`.
Salida real:

```
10|amazon_mx|90|componentes|1|f|1|1|1|0||90||400.0000000000000000|
```

Columnas: `ordenes_sin_costo = 1` (columna 9), `costo_unidad_mxn_promedio`
vacío (NULL, columna 13) y `margen_unidad_mxn_reconstruido` vacío (NULL,
última columna) — el margen del grupo NO se calculó con lo que sí había
(ingreso 400 disponible, columna 14), se dejó `NULL` completo porque
`ordenes_sin_costo > 0`.

**Esta fixture NO discrimina el arreglo** (hallazgo verificador, ronda r2):
`E1-PNOCOST` tiene una sola orden, así que `avg(costo_unidad_mxn)` sobre un
grupo enteramente sin costo ya da `NULL` por sí solo — quitar la anulación
explícita (`case when ordenes_sin_costo = 0 then avg(...) end`) y dejar
`avg(...)` a secas no habría cambiado esta salida ni una coma. Todos los
grupos de la ronda r1 con `ordenes_sin_costo > 0` tenían el mismo defecto:
o todas sus órdenes sin costo, o todas con costo — nunca una mezcla.

**`E1-PMIXTO` (`product_id = 17`, agregado en esta ronda) sí discrimina**:
6 órdenes del mismo producto/plataforma, con cargo de envío reciente (así
que las 6 caen en el MISMO grupo `product_id, platform, ventana_dias,
lectura` en las tres ventanas) pero costo MIXTO — 4 órdenes con venta
dentro de la vigencia de `sku_cost` (60 MXN) y 2 con venta ANTES de que esa
vigencia empezara (sin costo a su fecha).

Salida real, CON el arreglo (la que corre en el repo,
`validacion-local/salidas/efecto-margen.txt`):

```
17|amazon_mx|90|componentes|6|t|6|6|2|0|f|90||500.0000000000000000|
```

`ordenes=6`, `ordenes_sin_costo=2` (columna 9), `costo_unidad_mxn_promedio`
vacío (NULL), `margen_unidad_mxn_reconstruido` vacío (NULL) — con solo 2 de
6 órdenes sin costo, un `avg()` ingenuo SÍ habría dado un número (no NULL
solo); el arreglo lo anula explícitamente de todos modos.

Salida MUTANTE, SIN el arreglo (quitando las tres expresiones `case when
ordenes_sin_costo/ordenes_sin_ingreso = 0 then avg(...) end` y dejando
`avg(fu.costo_unidad_mxn)` / `avg(fu.ingreso_unidad_mxn_resuelto)` /
`avg(ingreso) - avg(costo) - mediana(L)` a secas — probado en una copia
temporal del archivo fuera del repo, contra la misma base, y descartada
después de capturar la salida):

```
17|amazon_mx|90|componentes|6|t|6|6|2|0|f|90|60.0000000000000000|500.0000000000000000|350
```

`costo_unidad_mxn_promedio = 60.0000...` — el promedio de SOLO las 4
órdenes con costo conocido, ignorando en silencio las 2 sin costo (`avg()`
de Postgres descarta los `NULL` en vez de propagarlos). `margen = 500 − 60
− 90 = 350`: un número calculable y aparentemente válido que esconde que 2
de las 6 órdenes no tenían costo. Este es exactamente el defecto que el
hallazgo 3 original pedía cerrar, y que la fixture de la ronda r1
(`E1-PNOCOST`) no alcanzaba a probar.

### Hallazgo 4 — sonda de formato y cobertura

`00-sonda-formato.sql` corre primero (prefijo `00`) y muestra la
distribución real de la parte 6 de `source_event_id` contra la semilla
sintética:

```
amazon_mx|fee|amazon_mx|ShippingHB|25
amazon_us|fee|amazon_us|LabmanLabelPurchase|4
amazon_us|fee|amazon_us|OtraFuenteRara|1
amazon_us|fee|amazon_us|shipping_label|3
amazon_us|fee|amazon_us|ShippingHB|23
amazon_us|fee|||2
```

`OtraFuenteRara` aparece explícitamente (no se pierde entre las tres
conocidas) — si esto pasara en producción, avisaría que el supuesto de
formato está mal antes de confiar en `duplicado_etiqueta`. La cobertura al
final de `envio-por-producto.sql` confirma que un valor así no se pierde
en la tabla principal tampoco:

```
amazon_us|23|3|3|1|1|1
```

(`ordenes_con_shippinghb=23, labman=3, shipping_label=3,
fuente_desconocida=1, fuente_no_reconocida=1, multi_fila_misma_fuente=1` —
tres ceros habrían sido sospechosos; acá ninguno lo es porque la semilla
sembró exactamente uno de cada caso raro).

### Hallazgo 5 — rezago de emisión con `fulfillment_status`

`E1-PSHIP` tiene una observación `'Shipped'` 5 días antes del cargo;
`E1-PSINSHIP` no tiene ninguna. Salida real de `rezago-emision.sql`:

```
amazon_us|27|1|26|5|5|5
```

`ordenes_con_cargo=27, con_observacion_shipped=1, sin_observacion=26,
p50=p90=max=5` — el único caso con observación `'Shipped'` da exactamente
los 5 días sembrados, y las 26 órdenes sin observación (incluida
`E1-PSINSHIP`) se cuentan aparte, no se pierden ni se meten al percentil
con un valor inventado.

### Hallazgo 6 — ventana semiabierta, día en curso excluido

`E1-PHOY` (`product_id = 13`) tiene su único cargo con `event_date = hoy`.
No aparece en NINGUNA fila de `envio-por-producto.txt` — el `product_id
13` está ausente de la lista completa (que sí trae 1, 2, 5, 6, 7, 8, 9,
10, 11, 14, 15, 16, saltándose 3, 4 por ser multi-producto/multi-unidad-
producto-compartido y 12, 13 por `fuente_no_reconocida` y "hoy"
respectivamente). El día en curso queda fuera de las tres ventanas y de la
población base, como pide la ventana semiabierta `[hoy-N, hoy)`.

### Hallazgo 7 — suma dentro de cada fuente antes de comparar

`E1-PMULTIFILA` (`product_id = 11`) tiene DOS filas de
`LabmanLabelPurchase` (60 y 40) más una de `shipping_label` (70). Salida
real:

```
11|amazon_us|90|componentes|1|f|170|170|170|170.0000
11|amazon_us|90|duplicado_etiqueta|1|f|100|100|100|100.0000
```

`componentes = 170` (60+40+70, correcto: suma todo). `duplicado_etiqueta =
100` = `max(60+40, 70) = max(100, 70) = 100` — suma DENTRO de
`LabmanLabelPurchase` primero (100), la compara contra `shipping_label`
(70), toma la mayor. El bug de la ronda anterior (`max()` fila por fila sin
agrupar) habría dado `max(60, 40, 70) = 70`, un número menor y equivocado.
El resultado (b) de `cargos-por-orden-y-fuente.sql` confirma el agregado
por fuente:

```
ord_multi_fila_misma_fuente|amazon_us|LabmanLabelPurchase|100.0000|2
ord_multi_fila_misma_fuente|amazon_us|shipping_label|70.0000|1
```

(`filas=2` para `LabmanLabelPurchase`, monto ya sumado = 100).

### Hallazgo 8 — `fuente_no_reconocida` separada y excluida

`E1-PNOREC` (`product_id = 12`) tiene `fuente_tipo = 'OtraFuenteRara'`.
`descartes.sql`:

```
fuente_desconocida|1|0
fuente_no_reconocida|1|0
```

Dos razones separadas, cada una con 1 orden — `fuente_desconocida` es
`ord_fuente_desconocida` (partes ausentes), `fuente_no_reconocida` es
`ord_fuente_no_reconocida` (parte 6 presente pero rara). Ninguna de las dos
aparece en `envio-por-producto.txt` (`product_id 12` ausente de la lista,
igual que `fuente_desconocida` ya se excluía en la ronda anterior) —
consistente entre `descartes.sql` y el resto de las consultas.

### Hallazgo 9 — mismo formato validado que el que corre

Todas las salidas de esta ronda (arriba) se generaron con el pipeline
exacto de `correr.sh` (`BEGIN READ ONLY; \echo INICIO; <consulta>; \echo
FIN; ROLLBACK;`, `-tA`), verificado con `printf` en vez de `echo` tras
detectar que el `echo` builtin de esta shell corrompía el `\echo` (primera
corrida de la ronda salió `ERROR: syntax error at or near ""` porque
`\echo` llegó como `cho` — capturado y corregido antes de seguir).

### Hallazgo 10 — escritura atómica

No se puede probar el `mv` atómico de `correr.sh` sin `ssh` a producción
(que está prohibido esta sesión), pero el patrón `.txt.parcial` → `mv` →
`.txt` solo si `PIPESTATUS[1]` (el `ssh`/`psql`, no el bloque local de
`printf`/`cat`) es 0, y `stderr` aparte en `.err`, está en el propio
`correr.sh`; `bash -n` lo valida sintácticamente (ver VERIFY).

### Hallazgo 12 — histéresis: flapeo genuino vs falsa alarma

Tres productos, tres comportamientos, en el mismo `parpadeo.txt`:

```
amazon_mx|componentes|1|0|1|1
amazon_us|componentes|2|0|2|0
```

- `E1-P6` y `E1-P8` (ambos `amazon_us`): **corte seco** los marca como
  parpadeo (`2`) porque en algún momento cruzan por debajo de 6. **Con
  histéresis, NINGUNO cuenta** (`0` en la columna final de `amazon_us`):
  `E1-P6` solo tiene 6 días de historia (nunca "sale", simplemente no
  tenía datos antes); `E1-P8` cae a 4 en una ventana intermedia, pero 4
  no es `< 3`, así que la histéresis correctamente NO lo cuenta como una
  salida real.
- `E1-PHIST` (`amazon_mx`): diseñado para un flapeo GENUINO —
  `[0, 6, 8, 8, 2, 6]` órdenes de la ventana más vieja a la más nueva.
  **Corte seco Y con histéresis** lo marcan (`1` y `1` en `amazon_mx`):
  entra en la ventana 2, se mantiene, cae a 2 (`< 3`, salida real) justo
  después de haber estado evaluada, y vuelve a entrar en la ventana 6.

Esto demuestra exactamente lo que pedía el hallazgo: la histéresis reduce
2 falsos positivos (`E1-P6`, `E1-P8`) sin perder el caso real
(`E1-PHIST`).

### Hallazgos 14/15/16 (bajas)

- **UTC**: todas las consultas usan `(now() AT TIME ZONE 'UTC')::date`
  (verificado por el hallazgo 6, arriba: `E1-PHOY` con `event_date = hoy`
  se excluye consistentemente).
- **`kind = 'fee'`**: agregado al filtro de población en las 10 copias del
  CTE; sin esto, una fila de la misma orden con `kind='sale'` y
  `fee_type` casualmente igual a `'shipping_fee'` (no debería existir por
  diseño, pero el filtro ya no depende de que nunca ocurra) se
  colaría. No hay fila así en la semilla porque el propio diseño de
  `ledger_event` no la permite generar por accidente con las herramientas
  normales de ingesta; el filtro es defensivo.
- **`(order_id, platform)`**: `00-sonda-formato.sql` (segundo resultado)
  cuenta cuántas órdenes de texto aparecen en más de una plataforma —
  salida real: `0` (la semilla no sembró esa colisión a propósito, pero la
  consulta que la detectaría ya está escrita y corre limpio; todos los
  `JOIN`/`GROUP BY` de las demás consultas usan `(order_id, platform)`
  desde esta ronda).

### Hallazgo 18 — columnas separadas, nombre correcto

`efecto-margen.sql`, fila de `E1-P8` a 180 días: `12|12` en las columnas
`unidades_totales_muestreadas` / `ordenes_muestreadas` — en este caso
coinciden porque todas las órdenes de `E1-P8` son de 1 unidad, así que no
demuestra la diferencia. La demuestra `ord_multi_unidad` (`product_id=5`,
`quantity=3`): fila `5|amazon_mx|90|...|1|f|3|1|1|0||100||300.0000...` —
`unidades_totales_muestreadas=3` (columna 7) mientras
`ordenes_muestreadas=1` (columna 8): ANTES estas dos habrían sido la misma
columna mal llamada `unidades_muestreadas=1` (contando órdenes, no
unidades); ahora se ve la diferencia real (3 unidades en 1 orden).

### Hallazgo 19 — resultado (b) acotado

`cargos-por-orden-y-fuente.txt`, resultado (b): solo aparecen
`ord_dos_fuentes`, `ord_multi_fila_misma_fuente` y `ord_tres_fuentes` — las
3 órdenes de la semilla con más de una fuente distinta o más de una fila
por fuente. Las otras ~47 órdenes usables de la semilla (una sola fuente,
una sola fila) NO aparecen en (b), aunque sí se cuentan en la distribución
(a) (`amazon_mx|1|25`, `amazon_us|1|24`).

## Qué NO valida este documento

- El formato real de `source_event_id` en producción (por eso existe
  `00-sonda-formato.sql`, que corre contra producción antes que el resto
  cuando el dueño ejecute `correr.sh`).
- El desplazamiento real entre las seis ventanas móviles de `parpadeo.sql`
  (se usó 30 días, supuesto declarado en el propio archivo).
- La escritura atómica de `correr.sh` (`.txt.parcial` → `mv`) contra un
  `ssh` real — no se puede probar sin producción; solo se verificó la
  sintaxis (`bash -n`) y la lógica se revisó a mano.
