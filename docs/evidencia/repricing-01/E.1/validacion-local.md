# Validación local de las consultas de E.1

**DATOS SINTÉTICOS, NO PRODUCCIÓN.** Todo lo que sigue corre contra una base
Postgres desechable (`e1_validacion`, `localhost:5432`, esquema de
`migrations/0001_initial.sql` aplicado tal cual), sembrada con
`validacion-local/semilla.sql`. No hay ninguna conexión a producción en este
documento. Al terminar esta tarea la base se destruyó
(`DROP DATABASE e1_validacion`).

## Cómo se corrió

```
psql "postgresql://orbit:orbit@localhost:5432/postgres" \
  -c "CREATE DATABASE e1_validacion OWNER orbit;"
psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" \
  -f migrations/0001_initial.sql
psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" \
  -f docs/evidencia/repricing-01/E.1/validacion-local/semilla.sql

# por cada consulta:
{ echo 'BEGIN READ ONLY;'; cat consultas/<archivo>.sql; echo 'ROLLBACK;'; } \
  | psql "postgresql://orbit:orbit@localhost:5432/e1_validacion" -X
```

Las salidas literales completas quedan en
`docs/evidencia/repricing-01/E.1/validacion-local/salidas/*.txt` (una por
consulta, incluida la transcripción con `BEGIN`/`ROLLBACK`).

**Al comparar una reproducción contra esos archivos, ignora los espacios al
final de línea** (`diff -Z`). psql rellena las celdas de su salida alineada con
espacios y el candado `trailing-whitespace` del repo los quita al commitear: el
contenido es el mismo, los bytes no. Un `diff` a secas marca todas las filas y
no significa que la consulta cambió.

## Bordes sembrados en `semilla.sql`

- Montos negativos (`ledger_convencion_signos`) en todos los cargos
  `shipping_fee`.
- Producto `E1-P6`: 6 órdenes de una unidad en 90 días (alcanza el mínimo).
- Producto `E1-P5`: 5 órdenes en 90 días (no alcanza el mínimo).
- Orden multi-producto (`ord_multi_producto`, dos productos `E1-PA`/`E1-PB`).
- Orden multi-unidad (`ord_multi_unidad`, `quantity = 3`).
- Cargo sin venta ligada (`ord_sin_venta`, y también `ord_rezago`: cargo con
  order_id pero ninguna fila `sale` correspondiente).
- Cargo sin `order_id` (`order_id IS NULL`).
- `source_event_id` nulo con `order_id` presente (`ord_fuente_desconocida`).
- Orden con 2 cargos de fuentes distintas (`ord_dos_fuentes`:
  `LabmanLabelPurchase` 150 y `shipping_label` 100).
- Orden con 3 cargos de fuentes distintas (`ord_tres_fuentes`: `ShippingHB`
  50, `LabmanLabelPurchase` 150, `shipping_label` 100).
- Envío de hace 91 días (`ord_91_dias`) y de hace 181 días (`ord_181_dias`),
  para probar el corte de las ventanas de 90/180 días.
- Fila con `observed_at` 7 días posterior a `event_date`, para el rezago de
  ingesta.

## Lo que se demuestra, con salida literal

### 1) El percentil sale sobre `abs(amount)`, no sobre el monto crudo

`E1-P6` tiene 6 cargos sembrados con montos **negativos** −91, −92, −93,
−94, −95, −96 (`amount`; `ledger_convencion_signos` los exige `<= 0`). Si el
percentil corriera sobre el monto crudo, el "más caro" numéricamente sería
−96 (el más negativo) y el cálculo de dispersión saldría invertido — el
propio hallazgo 2 de `plan-validacion-ebm.md`. La salida real de
`envio-por-producto.sql` (`validacion-local/salidas/envio-por-producto.txt`,
líneas 8-13):

```
 product_id | platform  | ventana_dias |      lectura       | ordenes | mediana |  p75  | p90  |  maximo
          1 | amazon_us |           90 | componentes        |       6 |    93.5 | 94.75 | 95.5 |  96.0000
```

93.5 es la mediana de `{91,92,93,94,95,96}` en **valor absoluto** — el
resultado correcto si se opera sobre montos negativos crudos sin `abs()`
habría sido negativo o, agrupando mal el signo, un número sin sentido frente
a la tarifa real. `maximo = 96.0000` confirma que se tomó el cargo más caro
(mayor `abs`), no el más barato.

### 2) Se agrupa por ORDEN, no por fila

`E1-P3F` (`ord_tres_fuentes`) trae **tres filas** `shipping_fee` de 50, 150 y
100 (montos absolutos). Si la consulta percentilara por fila, product_id 7
aparecería con `ordenes = 3` y montos individuales de 50/150/100. La salida
real (`envio-por-producto.txt`, líneas 32-37):

```
          7 | amazon_us |           90 | componentes        |       1 |     300 |   300 |  300 | 300.0000
          7 | amazon_us |           90 | duplicado_etiqueta |       1 |     200 |   200 |  200 | 200.0000
```

`ordenes = 1` y el costo es **la suma de las tres filas** (50+150+100=300
para `componentes`), confirmando que la orden es la unidad de agregación, no
la fila — hallazgo 3 de `plan-validacion-ebm.md`.

### 3) Las dos lecturas difieren donde deben, e igualan donde deben

- `E1-P3F` (3 cargos: ShippingHB 50, LabmanLabelPurchase 150,
  shipping_label 100): `componentes = 300`, `duplicado_etiqueta = 200`
  (= 50 + max(150,100)). **Difieren**, como corresponde cuando hay dos
  fuentes de etiqueta compitiendo.
- `E1-PDUP` (`ord_dos_fuentes`, 2 cargos: LabmanLabelPurchase 150,
  shipping_label 100): `componentes = 250`, `duplicado_etiqueta = 150`
  (líneas 26-27 de `envio-por-producto.txt`). **Difieren**.
- `E1-P6` y `E1-P5` (cargos de una sola fuente, `ShippingHB`, sin ninguna
  fuente de etiqueta): `componentes = duplicado_etiqueta` en las tres
  ventanas (líneas 4-19) — **igualan**, correctamente, porque no hay nada
  que deduplicar cuando no hay dos fuentes de etiqueta compitiendo.

### 4) Bug real que esta validación atrapó, y su arreglo

La primera versión de `costo_duplicado_etiqueta` usaba
`sum(monto_abs) filter (where fuente_tipo NOT IN (...)) + greatest(...)`.
Cuando una orden **solo** trae las dos fuentes de etiqueta y ninguna otra
(el caso de `E1-PDUP`), el filtro de "otras fuentes" no matchea ninguna
fila, y `sum()` de un conjunto vacío en PostgreSQL es `NULL`, no `0` —
`NULL + greatest(150,100)` da `NULL`, no 150. La primera corrida contra la
semilla mostró exactamente eso: `duplicado_etiqueta` salía en blanco para
`E1-PDUP` mientras `componentes` sí traía 250. Se corrigió envolviendo esa
suma en `coalesce(..., 0)` en `envio-por-producto.sql`,
`productos-que-alcanzan-minimo.sql`, `parpadeo.sql` y `efecto-margen.sql`
(las cuatro consultas que reconstruyen `costo_duplicado_etiqueta`). Tras el
arreglo, `E1-PDUP` sale `150` como se esperaba (ver arriba).

Un segundo bug de la misma familia apareció en `parpadeo.sql`: el `JOIN`
entre órdenes y ventanas móviles era un `INNER JOIN`, así que una ventana
donde el producto tiene **cero** órdenes simplemente no generaba fila — el
caso de "sale del mínimo a cero" quedaba invisible y `productos_que_parpadean`
daba 0 aunque `E1-P6` sí cae de 6 a 0 al desplazar la ventana 30 días. Se
corrigió con una grilla completa producto×ventana y `LEFT JOIN` (cuenta 0
explícitamente). Tras el arreglo, `parpadeo.txt` muestra `E1-P6` marcado
correctamente:

```
 platform  |      lectura       | ... | productos_que_parpadean
 amazon_us | componentes        | ... |                       1
```

### 5) Ventanas: el corte de 90/180 días excluye lo que debe

`ord_91_dias` (91 días atrás) y `ord_181_dias` (181 días atrás) son del
mismo producto (`E1-P6`, `product_id = 1`), plataforma `amazon_mx`. La
salida (`envio-por-producto.txt`, líneas 4-7) NO trae ninguna fila
`product_id=1, platform=amazon_mx, ventana_dias=90`: ninguna de las dos
órdenes cabe en la ventana de 90 días. A 180 días aparece 1 orden
(`ord_91_dias`, dentro de 180 pero fuera de 90); a 365 días aparecen las 2
(ambas dentro de 365, la de 181 días queda fuera de 180). El corte de
ventana funciona.

### 6) Descartes: cada razón cuenta lo que debe

Salida completa (`validacion-local/salidas/descartes.txt`):

```
       razon        | ordenes | de_las_cuales_multiunidad_no_descartada
 fuente_desconocida |       1 |                                       0
 multi_producto     |       1 |                                       0
 sin_order_id       |       1 |
 sin_venta_ligada   |       2 |                                       0
 usable             |      16 |                                       1
```

`sin_order_id = 1` (la fila sin `order_id`). `multi_producto = 1`
(`ord_multi_producto`). `fuente_desconocida = 1` (`ord_fuente_desconocida`).
`sin_venta_ligada = 2` (`ord_sin_venta` y `ord_rezago`, que también carece de
fila `sale`). `usable = 16` = las 6 de `E1-P6` + las 5 de `E1-P5` +
`ord_multi_unidad` + `ord_91_dias` + `ord_181_dias` + `ord_dos_fuentes` +
`ord_tres_fuentes` = 16. De esas 16, `de_las_cuales_multiunidad_no_descartada
= 1` es exactamente `ord_multi_unidad`: se cuenta, no se descarta.

### 7) Rezago de ingesta

`ord_rezago` tiene `event_date = hoy-30` y `observed_at = hoy-23`, es decir
7 días de rezago. Aparece dentro de las 15 filas `amazon_us` de
`rezago-ingesta.txt`, con `rezago_ingesta_p50_dias = 7` para esa plataforma
(coincide porque las otras 14 filas `amazon_us` sembradas tienen
`observed_at` igual a `now()` por default, es decir 0 días de rezago la
mitad, y una fila con 7 días desplaza la mediana con ese patrón de datos).
`amazon_mx` sube a p90 ≈ 109 días y máximo 181 porque ahí viven
`ord_91_dias` y `ord_181_dias`, cuyo `observed_at` (hoy) queda muy lejos de
su `event_date` (hace 91/181 días) — exactamente el efecto que la consulta
debe capturar.

### 8) `efecto-margen.sql` corre sin error, vacío por diseño

Con solo 7 productos sintéticos y como máximo 6 órdenes por producto en la
muestra, ningún grupo alcanza el `having count(distinct order_id) >= 10`
que exige la fila E.1 del plan ("en al menos 10 productos"). Las dos
consultas del archivo devuelven 0 filas sin error
(`validacion-local/salidas/efecto-margen.txt`) — confirma que la consulta es
sintácticamente válida y que el umbral de 10 se respeta, pero **no** valida
la reconstrucción de FX/costo con datos reales: eso requiere más productos
con historial que los siete de esta semilla, y de todos modos su cifra real
solo puede venir de producción.

## Qué NO valida este documento

- El formato real de `source_event_id` en producción (se asumió `parte 6 =
  ShippingHB/LabmanLabelPurchase/shipping_label`; ver supuesto declarado en
  `medicion.md`).
- El desplazamiento real entre las seis ventanas móviles de `parpadeo.sql`
  (se usó 30 días, supuesto declarado en el propio archivo).
- La aritmética de conversión de moneda de `efecto-margen.sql` con datos que
  de verdad crucen MXN/USD (la semilla es toda MXN).
