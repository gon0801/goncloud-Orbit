# Validación local de las consultas de E.1

**DATOS SINTÉTICOS, NO PRODUCCIÓN.** Todo lo que sigue corre contra una base
Postgres local (`localhost:5432`, esquema de `migrations/0001_initial.sql`
+ `migrations/0030_spapi_orders.sql` + `migrations/0031_spapi_orders_bitemporal.sql`
aplicados tal cual — `0030`/`0031` agregan
`spapi_order_observation.fulfillment_status`, que usa `rezago-emision.sql`
desde la ronda r1), sembrada con `validacion-local/semilla.sql`. No hay
ninguna conexión a producción en este documento.

**Regla desde la ronda r4c: la base NO se borra.** Cada corrida usa un
nombre único (por ejemplo `e1_validacion_r4c_<hora>`) y se deja sin
`DROP DATABASE` al terminar — un borrado de base o de directorio por
shell activa un hook de seguridad que lo convierte en una pregunta que
nadie contesta y frena la corrida. Los temporales (copias mutantes de una
consulta, logs de `psql -f`) van en un directorio de `mktemp -d`, que
tampoco se borra. Cada ronda declara en su reporte al coordinador la ruta
y el nombre de base que dejó.

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
} | psql "postgresql://orbit:orbit@localhost:5432/<db>" -X -P pager=off -tA -v ON_ERROR_STOP=1
```

Las salidas literales completas quedan en
`docs/evidencia/repricing-01/E.1/validacion-local/salidas/*.txt` (una por
consulta, con `-tA`, `\echo` de inicio/fin y `ROLLBACK` — mismo formato que
`correr.sh` escribiría en `salidas/*.txt` contra producción).

## Cómo se corrió

Con `<db>` un nombre único por corrida (p.ej. `e1_validacion_r4c_104204`),
sin borrarla al terminar:

```
psql "postgresql://orbit:orbit@localhost:5432/postgres" \
  -c "CREATE DATABASE <db> OWNER orbit;"
psql "postgresql://orbit:orbit@localhost:5432/<db>" -f migrations/0001_initial.sql
psql "postgresql://orbit:orbit@localhost:5432/<db>" -f migrations/0030_spapi_orders.sql
psql "postgresql://orbit:orbit@localhost:5432/<db>" -f migrations/0031_spapi_orders_bitemporal.sql
psql "postgresql://orbit:orbit@localhost:5432/<db>" \
  -f docs/evidencia/repricing-01/E.1/validacion-local/semilla.sql
```

## Ronda r2 (2026-09-17): E1-PMIXTO

El verificador rechazó r1 por hallazgo medio: la fixture de "sin
imputación silenciosa" (`E1-PNOCOST`, hallazgo 3 original) no discriminaba
el arreglo, porque su grupo tenía TODAS las órdenes sin costo (`avg()`
ya daba `NULL` solo). Se agregó `E1-PMIXTO` con costo MIXTO dentro del
mismo grupo (4 órdenes con costo, 2 sin) — ver la sección "Hallazgo 3"
abajo para las dos salidas literales (con y sin el arreglo).

## Ronda r3 (2026-09-17): identidad de fuente real + hallazgos 20/21/22

El dueño autorizó la lectura de producción, pero **solo al lead**; esta
sesión (implementador de E.1) sigue en local, sin acceso a producción. El
lead midió en producción que el formato de `source_event_id` de las
rondas anteriores estaba MAL (ver `medicion.md`, "Formato real medido por
el lead"): hay DOS formas reales (`finance`, 6 partes; `shipping_label`, 4
partes), no la forma única inventada. **Todos** los fixtures de esta
semilla se reescribieron para usar las dos formas reales; ninguno usa el
formato viejo. Además se corrigieron tres hallazgos del revisor (20, 21,
22) — ver sus secciones abajo.

## Ronda r4a (2026-09-17): cuatro hechos que la producción mostró

El lead corrió `correr.sh` contra producción (rol `orbit_read`, `BEGIN
READ ONLY`) y midió cuatro cosas que la semilla sintética de r3 no podía
mostrar (citadas literal en cada `.sql` como "medido por el lead
2026-09-17"): (1) el rezago de emisión del hecho 15 (p50 27/22 días) NO se
reproduce con ninguna fecha de Orbit; (2) `rezago-ingesta.sql` mezclaba la
carga inicial (2026-08-31) con la ingesta incremental, inflando el p50 a
~134 días; (3) las 148 órdenes que `descartes.sql` rotulaba
`sin_venta_ligada` sí tenían venta, solo les faltaba `product_id`; (4) hay
pares de etiqueta casi idénticos (452.69 vs 449.69) que, bajo
`componentes`, duplican el costo de la orden. Esta sesión NO leyó
producción — todos los hechos vienen del encargo del coordinador; aquí
solo se valida que las consultas corregidas los reproducen con datos
sintéticos equivalentes.

- **`rezago-emision.sql`**: reescrita con 4 medidas (principal
  cargo-venta con dos variantes, purchase_date, diagnóstico
  shipping_label, Shipped). Salida real (`rezago-emision.txt`, ANTES de
  la ronda r4c — `amazon_mx` sube de 43 a 44 órdenes y estrena una
  `ordenes_con_rezago_negativo=1` desde r4c con `E1-PDOSFECHAS`; ver la
  sección "Ronda r4c" más abajo para la salida vigente):

  ```
  amazon_mx|primer_cargo|43|43|0|0|0|0|226
  amazon_us|primer_cargo|34|32|2|0|0|0|0
  ```

  (a) principal: `amazon_us` muestra `ordenes_sin_venta=2` — las dos
  órdenes sin ninguna fila de venta (`ord_sin_venta`, `ord_rezago`); el
  resto de columnas confirma p50/p90 en 0 salvo un máximo de 226 días,
  que viene de `E1-PMIXTO` (venta hace 250-251 días, cargo hace 20-25 —
  un artefacto legítimo de reusar esa fixture, no un bug).

  ```
  amazon_us|34|1|33|0|6|6|6
  ```

  (b) contra `purchase_date`: cobertura PARCIAL real (1 de 34 con dato,
  el resto sin) — se agregó `purchase_date` a la observación sembrada de
  `ord_con_shipped` para que la consulta demuestre la rama "con dato" y
  no solo la rama "sin dato" (con cero filas de `purchase_date`, la
  consulta habría corrido pero sin probar el cálculo del rezago).

  ```
  amazon_us|34|7|27|1|-5|0|2.5000000000000004|5
  ```

  (c) diagnóstico shipping_label: `ordenes_con_diff_negativa=1`
  (`ord_reznegativo`, diferencia −5, excluida del percentil).

- **`rezago-ingesta.sql`**: dos filas por plataforma
  (`todas`/`solo_incremental`) más el resumen de días de ingesta. Salida
  real (actualizada en la ronda r4c: las 2 filas de cargo de
  `E1-PDOSFECHAS`, con `observed_at` por defecto de hoy, suman 2 filas
  más a `amazon_mx` en ambos buckets frente a la cita de r4a):

  ```
  amazon_mx|solo_incremental|44|16.5|141.7|181
  amazon_mx|todas|46|20.5|141.5|181
  4|2026-08-18|2026-09-17
  ```

  `todas=46` vs `solo_incremental=44`: las 2 filas de `E1-PCARGA` con
  `observed_at` hace 30 días (la más vieja de toda la semilla, por eso se
  convierte en `primer_dia_de_ingesta` = 2026-08-18, calculado, no
  literal) quedan EXCLUIDAS de `solo_incremental`. `dias_de_ingesta=4`
  (hoy, hace 1 día, hace 23 días de `ord_rezago`, hace 30 días de la
  carga inicial) — sin cambio, `E1-PDOSFECHAS` no agrega un día de
  ingesta nuevo porque su `observed_at` es el de hoy, ya contado.

- **`descartes.sql`**: partido en `sin_fila_de_venta` y
  `venta_sin_producto`, con columna `platform`. Salida real:

  ```
  amazon_mx|venta_sin_producto|1|0
  amazon_us|sin_fila_de_venta|2|0
  ```

  `venta_sin_producto=1` (`ord_venta_sin_producto`, la venta existe pero
  sin `product_id`) separado de `sin_fila_de_venta=2`
  (`ord_sin_venta`/`ord_rezago`, sin ninguna venta). El total `usable`
  (`amazon_mx|usable|41` en la salida original de r4a, `42` desde la
  ronda r4c porque `E1-PDOSFECHAS` suma una orden usable más en
  `amazon_mx`; `amazon_us|usable|30` sin cambio) no varía por el
  criterio en sí: el resto de las consultas (`envio-por-producto.sql`
  etc.) sigue exigiendo venta CON `product_id` vía `orden_producto`, sin
  tocar esa lógica.

- **`cargos-por-orden-y-fuente.sql`**, resultado (d)/(e), pares de
  etiqueta:

  ```
  ord_par_casi_igual|amazon_us|452.6900|449.6900|3.0000|1.0066712624252263
  ord_par_lejos|amazon_us|500.0000|100.0000|400.0000|5.0000000000000000
  amazon_us|5|50|260.00000000000006|400.0000|1.5|3.6000000000000005|5.0000000000000000|1|0|4
  ```

  `ord_par_casi_igual` (452.69 vs 449.69, igual que el caso medido por el
  lead) da `difieren_hasta_1pct=1`; `ord_par_lejos` (500 vs 100) cae en
  `difieren_mas_5pct` — la consulta discrimina "casi igual" de "muy
  distinto", no solo corre.

- **`01-historia-disponible.sql`** (nueva): confirma que la historia real
  es menor a 365 días:

  ```
  shipping_fee|amazon_mx|2026-03-20|2026-09-17|45|181
  shipping_fee|amazon_us|2026-04-30|2026-09-16|43|140
  ```

  Y la columna `dias_con_datos`, agregada a `envio-por-producto.sql`,
  `productos-que-alcanzan-minimo.sql` y `efecto-margen.sql`, lo refleja
  fila por fila:

  ```
  1|amazon_mx|365|componentes|2|95|95|95|95.0000|181
  amazon_mx|365|componentes|10|3|181
  ```

  La ventana de 365 días para `amazon_mx` muestra `dias_con_datos=181`
  (no 365) — la ventana de 90 días de las mismas filas muestra
  `dias_con_datos=90` (completa, porque 90 días son posteriores al inicio
  real de la historia).

## Ronda r4c (2026-09-17): fecha de venta distinta de fecha de cargo

El verificador aprobó r4a/r4b (25 cifras de `medicion.md` rastreadas a
producción) y dejó UN hallazgo medio de la validación LOCAL: ninguna
orden de la semilla tenía dos cargos de envío en fechas DISTINTAS, así
que en `rezago-emision.sql` medida (a) intercambiar `primer_cargo` y
`ultimo_cargo` no cambiaba ninguna salida local — la fixture no
discriminaba esa parte de la consulta.

Se agregó **`E1-PDOSFECHAS`**: venta el día D (hace 10 días), un cargo
`finance:ShippingHB` el día D−1 (hace 11 días) y un cargo
`shipping_label` el día D+2 (hace 8 días, con la fecha de parte 4 igual a
su propio `event_date`, coherente con el hallazgo de r4a de que esa
fecha ES el `event_date` de la fila). Todo dentro de la ventana de 90
días. Esperado: `primer_cargo` (D−1) − venta (D) = **−1** (negativo,
fuera del percentil); `ultimo_cargo` (D+2) − venta (D) = **+2**
(positivo).

Confirmado con una consulta ad-hoc sobre la base de esta ronda (no una
consulta de `consultas/`, solo un `SELECT` de inspección):

```
ord_dos_fechas|sale|2026-09-07|400.0000|
ord_dos_fechas|fee|2026-09-06|-90.0000|amazon_mx|finance|fee|ord_dos_fechas||ShippingHB
ord_dos_fechas|fee|2026-09-09|-60.0000|amazon_mx|shipping_label|ord_dos_fechas|2026-09-09
```

venta=09-07; primer_cargo=09-06 (09-06−09-07=−1); último_cargo=09-09
(09-09−09-07=+2).

### Salida real (con la consulta SIN mutar), medida (a)

```
amazon_mx|primer_cargo|44|44|0|1|0|0|226
amazon_mx|ultimo_cargo|44|44|0|0|0|0|226
```

`primer_cargo` ahora muestra `ordenes_con_rezago_negativo = 1` (la nueva
orden) mientras `ultimo_cargo` muestra `0` — antes de esta ronda, con
todos los cargos de una orden en la misma fecha, esas dos filas eran
indistinguibles.

### Salida MUTANTE (intercambiando las etiquetas `primer_cargo`/`ultimo_cargo`)

Se copió `consultas/rezago-emision.sql` a un archivo temporal (fuera del
árbol versionado, en el directorio de `mktemp` de esta ronda — ver abajo)
y se intercambiaron las dos ramas del `UNION ALL` de la CTE `principal`
(la rama que antes usaba `co.primer_cargo` ahora usa `co.ultimo_cargo`, y
viceversa, conservando la etiqueta `variante`). Corrida contra la MISMA
base:

```
amazon_mx|primer_cargo|44|44|0|0|0|0|226
amazon_mx|ultimo_cargo|44|44|0|1|0|0|226
```

El `1` de `ordenes_con_rezago_negativo` se movió de `primer_cargo` a
`ultimo_cargo` — la salida CAMBIA al intercambiar las etiquetas, lo que
prueba que la fixture `E1-PDOSFECHAS` sí discrimina cuál de las dos
variantes está calculando cada rama. El archivo mutante y su salida
quedaron en el directorio temporal de esta ronda (no se borraron, ver
regla de no-limpieza abajo).

### Regla de no limpieza (ronda r4c en adelante)

El dueño pidió que ninguna corrida borre bases ni directorios por shell
(el hook de seguridad lo convierte en una pregunta que nadie contesta y
frena la corrida). Desde esta ronda: la base de cada corrida tiene un
nombre único (por ejemplo `e1_validacion_r4c_104204`) y se deja sin
`DROP DATABASE`; los archivos temporales (copias mutantes, logs) van a un
directorio de `mktemp -d` que tampoco se borra. La ruta y el nombre
exactos de esta ronda están en el reporte al coordinador, no en este
documento (cambian en cada corrida).

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

Nuevos en la ronda r3 (identidad de fuente real + hallazgos 20/21/22):

- **Todos los fixtures existentes** se reescribieron al formato real
  (`<plataforma>|finance|fee|<order_id>|<sku>|<subtipo>` o
  `<plataforma>|shipping_label|<order_id>|<fecha>`); ninguno quedó con el
  formato viejo.
- **E1-PMFN** (MX): `finance:MFNPostageFee` (200) + `shipping_label` (120)
  en la misma orden — las dos lecturas difieren (`componentes=320`,
  `duplicado_etiqueta=200`).
- **E1-PCHARGE**: una orden con `finance:ShippingHB` (80) +
  `finance:ShippingChargeback` (20), y otra con
  `finance:MFNShippingChargeback` (15) sola — los chargebacks van al
  "resto" (mismo valor en las dos lecturas, porque no hay fuente de
  etiqueta compitiendo).
- **E1-PSHIPLABEL**: dos órdenes — una con la fecha de parte 4 de
  `shipping_label` ANTERIOR al cargo (rezago positivo = 5) y otra con la
  fecha POSTERIOR al cargo (rezago negativo = −5, debe contarse aparte y
  quedar fuera del percentil) — hallazgo 21.
- **E1-PLUT**: observación `'Shipped'` con `last_updated_time` temprano
  (hace 25 días) pero `observed_at` tardío (hace 2 días, simula un
  backfill) — hallazgo 21, para demostrar que usar `last_updated_time` en
  vez de `observed_at` evita un rezago negativo artificial.
- **E1-PVIEJO**: 6 órdenes, TODAS a 100-105 días atrás (dentro de
  180/365, fuera de 90 días) — hallazgo 20, la fila de la ventana de 90
  días debe salir con `ordenes=0` y `alcanza_minimo=false`, no
  desaparecer.
- **Fila `kind='refund'`** con `fee_type='shipping_fee'`, sin producto ni
  orden ligada a las demás consultas — hallazgo 22, visible SOLO en
  `00-sonda-formato.sql` (que no filtra por `kind`).

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
Salida real (columnas re-numeradas en la ronda r3 tras borrar
`ordenes_muestreadas`, hallazgo 26):

```
10|amazon_mx|90|componentes|1|f|1|1|0|f|90||400.0000000000000000|
```

Columnas: `ordenes_sin_costo = 1` (columna 8), `costo_unidad_mxn_promedio`
vacío (NULL, columna 12) y `margen_unidad_mxn_reconstruido` vacío (NULL,
última columna) — el margen del grupo NO se calculó con lo que sí había
(ingreso 400 disponible, columna 13), se dejó `NULL` completo porque
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
`validacion-local/salidas/efecto-margen.txt`, ronda r3):

```
17|amazon_mx|90|componentes|6|t|6|2|0|f|90||500.0000000000000000|
```

`ordenes=6`, `ordenes_sin_costo=2` (columna 8), `costo_unidad_mxn_promedio`
vacío (NULL), `margen_unidad_mxn_reconstruido` vacío (NULL) — con solo 2 de
6 órdenes sin costo, un `avg()` ingenuo SÍ habría dado un número (no NULL
solo); el arreglo lo anula explícitamente de todos modos.

Salida MUTANTE, SIN el arreglo (probada en la ronda r2, quitando las tres
expresiones `case when ordenes_sin_costo/ordenes_sin_ingreso = 0 then
avg(...) end` y dejando `avg(fu.costo_unidad_mxn)` /
`avg(fu.ingreso_unidad_mxn_resuelto)` / `avg(ingreso) - avg(costo) -
mediana(L)` a secas — en una copia temporal del archivo fuera del repo,
contra la misma base, y descartada después de capturar la salida; la
numeración de columnas es la de r2, antes de borrar
`ordenes_muestreadas`):

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

**Actualizado en la ronda r3** (ver "Hecho nuevo" más abajo para el
detalle de la identidad de fuente medida en producción). `00-sonda-formato.sql`
corre primero (prefijo `00`) y muestra la distribución real de las partes
2 y 6 de `source_event_id`, con el número de partes, contra la semilla
sintética (ya reescrita al formato real):

```
amazon_mx|fee|finance|MFNPostageFee|6|1
amazon_mx|fee|finance|ShippingHB|6|37
amazon_mx|fee|shipping_label||4|1
amazon_us|fee|finance|LabmanLabelPurchase|6|4
amazon_us|fee|finance|MFNShippingChargeback|6|1
amazon_us|fee|finance|OtraFuenteRara|6|1
amazon_us|fee|finance|ShippingChargeback|6|1
amazon_us|fee|finance|ShippingHB|6|25
amazon_us|fee|shipping_label||4|5
amazon_us|fee||||2
```

`OtraFuenteRara` aparece explícitamente (no se pierde entre las seis
conocidas) — si esto pasara en producción, avisaría que el vocabulario de
fuentes cambió antes de confiar en `duplicado_etiqueta`. La cobertura de
`envio-por-producto.sql` (ver "Hecho nuevo" abajo) confirma que ese valor
raro no se pierde en la tabla principal tampoco.

### Hallazgo 5 — rezago de emisión con `fulfillment_status`

**Superado por el hallazgo 21 de la ronda r3** (`rezago-emision.sql` se
reescribió por completo: dos medidas separadas, `last_updated_time` en
vez de `observed_at`, columnas nuevas). Ver la sección "Hallazgo 21"
abajo para la evidencia vigente; `E1-PSHIP`/`E1-PSINSHIP` de esta ronda
siguen sembrados y siguen contribuyendo al conteo de cobertura, pero la
cita puntual de "5 días" de esta sección ya no corresponde al formato de
salida actual.

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
`finance:LabmanLabelPurchase` (60 y 40, formato real desde la ronda r3)
más una de `shipping_label` (70). Salida real:

```
11|amazon_us|90|componentes|1|170|170|170|170.0000
11|amazon_us|90|duplicado_etiqueta|1|100|100|100|100.0000
```

`componentes = 170` (60+40+70, correcto: suma todo). `duplicado_etiqueta =
100` = `max(60+40, 70) = max(100, 70) = 100` — suma DENTRO de
`finance:LabmanLabelPurchase` primero (100), la compara contra
`shipping_label` (70), toma la mayor. El bug de la ronda r1 (`max()` fila
por fila sin agrupar) habría dado `max(60, 40, 70) = 70`, un número menor
y equivocado. El resultado (b) de `cargos-por-orden-y-fuente.sql`
confirma el agregado por fuente:

```
ord_multi_fila_misma_fuente|amazon_us|finance:LabmanLabelPurchase|100.0000|2
ord_multi_fila_misma_fuente|amazon_us|shipping_label|70.0000|1
```

(`filas=2` para `finance:LabmanLabelPurchase`, monto ya sumado = 100).

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

**Actualizado en la ronda r3 (hallazgo 28):** la columna única de
histéresis se partió en dos —
`productos_con_salida_con_histeresis` (hubo una salida explícita,
`ordenes < 3`, después de haber estado evaluado) y
`productos_con_reentrada_con_histeresis` (hubo una entrada DESPUÉS de una
salida ya contada, un ciclo completo). Salida real de `parpadeo.txt`
después de la ronda r3 (con más productos sembrados: `E1-PVIEJO`,
`E1-PMIXTO`, etc. también entran a esta consulta):

```
amazon_mx|componentes|3|0|3|2|1
amazon_us|componentes|2|0|2|0|0
```

- `amazon_us` (`E1-P6`, `E1-P8`): **corte seco** los marca como parpadeo
  (`2`) porque en algún momento cruzan por debajo de 6. **Con histéresis,
  NINGUNO cuenta** (`0` salidas y `0` reentradas): ninguno de los dos
  tiene una caída explícita por debajo de 3 después de haber estado
  evaluado.
- `amazon_mx` (`E1-PHIST` y otros): 3 productos alcanzan el mínimo en
  alguna ventana, los 3 parpadean con corte seco, pero solo 2 tienen una
  salida real con histéresis y solo 1 (`E1-PHIST`, diseñado para
  `[0, 6, 8, 8, 2, 6]` órdenes vieja→nueva: entra, se mantiene, cae a 2
  — salida real — y reentra en la ventana 6) completa el ciclo con
  reentrada.

Esto demuestra que la histéresis reduce falsos positivos frente al corte
seco, y que la reentrada exige un ciclo completo, no cualquier entrada.

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

### Hallazgo 18 (ronda r1) y hallazgo 26 (ronda r3) — columna correcta, sin duplicado

`ord_multi_unidad` (`product_id=5`, `quantity=3`) demuestra la diferencia
entre órdenes y unidades reales: fila
`5|amazon_mx|90|componentes|1|f|3|1|0|f|100||300.0000000000000000|` —
`ordenes=1` (columna 5) pero `unidades_totales_muestreadas=3` (columna 7,
`sum(quantity)` real). La ronda r1 había dejado una columna
`ordenes_muestreadas` IDÉNTICA a `ordenes` (`count(distinct order_id)`
repetido dos veces) — se borró en la ronda r3 (hallazgo 26); ya no
aparece en la salida.

### Hallazgo 19 — resultado (b) acotado

**Cita actualizada en la ronda r4c** (las rondas r4a/r4b agregaron
órdenes con pares de etiqueta y no se había vuelto a citar esta lista
completa). `cargos-por-orden-y-fuente.txt`, resultado (b): a la fecha de
la ronda r4c aparecen `ord_chargeback`, `ord_dos_fechas`,
`ord_dos_fuentes`, `ord_mfn_shipping_label`, `ord_multi_fila_misma_fuente`,
`ord_par_casi_igual`, `ord_par_lejos` y `ord_tres_fuentes` — las órdenes
de la semilla con más de una fuente distinta o más de una fila por
fuente (`ord_dos_fechas`, de esta ronda, entra porque trae
`finance:ShippingHB` y `shipping_label`, dos fuentes distintas). El resto
de las órdenes usables (una sola fuente, una sola fila) NO aparecen en
(b), aunque sí se cuentan en la distribución (a):
`amazon_mx|1|42`, `amazon_mx|2|2`, `amazon_us|1|28`, `amazon_us|2|4`,
`amazon_us|3|2`.

### Hecho nuevo (ronda r3) — identidad de fuente real

`E1-PMFN` (`product_id = 18`, MX): `finance:MFNPostageFee` (200) +
`shipping_label` (120). Salida real:

```
18|amazon_mx|90|componentes|1|320|320|320|320.0000
18|amazon_mx|90|duplicado_etiqueta|1|200|200|200|200.0000
```

`componentes = 320` (200+120, correcto). `duplicado_etiqueta =
greatest(200, 120) = 200` — las DOS lecturas difieren, con la fuente
`MFNPostageFee` que la sonda de producción reveló y que las rondas
anteriores no contemplaban (habría caído en `fuente_no_reconocida` con el
código de r1/r2).

`E1-PCHARGE` (`product_id = 19`, US): una orden con `finance:ShippingHB`
(80) + `finance:ShippingChargeback` (20), otra con
`finance:MFNShippingChargeback` (15) sola. Salida real:

```
19|amazon_us|90|componentes|2|57.5|78.75|91.5|100.0000
19|amazon_us|90|duplicado_etiqueta|2|57.5|78.75|91.5|100.0000
```

`componentes` y `duplicado_etiqueta` son IDÉNTICAS (mediana 57.5 en
ambas) — correcto: ninguna de las dos órdenes trae una fuente de
etiqueta, así que los dos chargebacks y `ShippingHB` van completos al
"resto" en las dos lecturas, sin que la disputa de E.0 les aplique.

Cobertura (`envio-por-producto.txt`, resultado (b)), confirma las seis
fuentes reales representadas en la semilla. **Cifras vigentes a la ronda
r4c** (suben con cada fixture nueva que usa una fuente conocida; la de
`amazon_mx` incluye ahora `E1-PDOSFECHAS`, que aporta 1 a
`shipping_label` y 1 a `finance:ShippingHB`):

```
amazon_mx|2|0|1|43|0|0|0|0|0
amazon_us|7|4|1|25|1|1|1|1|1
```

(`shipping_label`, `finance:LabmanLabelPurchase`,
`finance:MFNPostageFee`, `finance:ShippingHB`,
`finance:ShippingChargeback`, `finance:MFNShippingChargeback`,
`fuente_desconocida`, `fuente_no_reconocida`, `multi_fila_misma_fuente` —
en ese orden de columnas.)

### Hallazgo 20 — grilla completa en efecto-margen.sql

`E1-PVIEJO` (`product_id = 22`): 6 órdenes, todas a 100-105 días atrás.
Salida real:

```
22|amazon_mx|90|componentes|0|f|0|0|0|f||||
22|amazon_mx|180|componentes|6|t|6|0|0|f|90||260.0000000000000000|
22|amazon_mx|365|componentes|6|t|6|0|0|f|90||260.0000000000000000|
```

La fila de 90 días SALE, con `ordenes = 0` y `alcanza_minimo = f` — antes
(con `JOIN` normal en vez de `LEFT JOIN` sobre la grilla `top10 ×
ventanas`), un producto sin órdenes en una ventana simplemente
desaparecía de la tabla para esa ventana, en vez de mostrar el cero
explícito. A 180 y 365 días sí tiene sus 6 órdenes.

### Hallazgo 21 — rezago-emision reescrita (dos medidas, sin observed_at)

`E1-PSHIPLABEL`: `ord_reznotivo` (fecha de parte 4 anterior al cargo,
rezago positivo) y `ord_reznegativo` (fecha posterior, rezago negativo).
Salida real del resultado (i) principal:

```
amazon_us|32|5|27|1|0|3.5000000000000004|5
```

`ordenes_con_rezago_negativo = 1` (`ord_reznegativo`, rezago = −5, EXCLUIDO
del percentil); `p90 = 3.5` y `max = 5` se calculan solo sobre las órdenes
con rezago ≥ 0 (incluye `ord_reznotivo`, rezago = +5).

`E1-PLUT`: observación `'Shipped'` con `last_updated_time` temprano
(hace 25 días) pero `observed_at` tardío (hace 2 días). Salida real del
resultado (ii) contraste:

```
amazon_us|32|2|30|0|10|14|15
```

`ordenes_con_dato = 2` (`ord_con_shipped` y `ord_lut`).
`ordenes_con_rezago_negativo = 0` — con `last_updated_time`, `ord_lut` da
rezago = (cargo hace 10 días) − (Shipped hace 25 días) = **+15**, positivo
y razonable. Si la consulta usara `observed_at` (el bug de la ronda r1),
`ord_lut` habría dado rezago = (hace 10) − (hace 2) = **−8**, negativo y
sin sentido — exactamente el sesgo "hacia abajo y sin tope" que describe
el hallazgo 21, no el "~1 día" que decía la versión anterior. `p50=10,
p90=14, max=15` sobre `[5, 15]` (los rezagos de `ord_con_shipped` y
`ord_lut`) confirman que ambos entraron positivos al percentil.

### Hallazgo 22 — sonda ampliada

Salida real de `00-sonda-formato.sql`:

```
amazon_mx|refund|finance|ShippingHB|6|1
```

La fila `kind='refund'` (`ord_refund_shipping_fee`) aparece SOLO aquí —
confirmado por su ausencia en `descartes.txt` y en todas las demás
consultas (que filtran `kind='fee'`). La columna `n_partes` (penúltima,
`6`) confirma la forma de 6 partes para esta fila. El tercer resultado
(cobertura de `spapi_order_observation`):

```
70|2|2
```

70 órdenes con cargo, 2 con alguna observación SP-API, 2 con
`fulfillment_status='Shipped'` — coincide con que solo `ord_con_shipped`
y `ord_lut` tienen filas sembradas en `spapi_order_observation`.

## Qué NO valida este documento

- El formato real de `source_event_id` en producción SE MIDIÓ (ronda r3,
  ver `medicion.md`), pero esta sesión no lo verificó por su cuenta — el
  lead lo entregó ya medido. `00-sonda-formato.sql` sigue corriendo
  primero contra producción cuando el dueño ejecute `correr.sh`, por si el
  formato vuelve a cambiar (residual declarado, hallazgo 27).
- El desplazamiento real entre las seis ventanas móviles de `parpadeo.sql`
  (se usó 30 días, supuesto declarado en el propio archivo).
- La escritura atómica de `correr.sh` (`.txt.parcial` → `mv`) contra un
  `ssh` real — no se puede probar sin producción; solo se verificó la
  sintaxis (`bash -n`) y la lógica se revisó a mano.
