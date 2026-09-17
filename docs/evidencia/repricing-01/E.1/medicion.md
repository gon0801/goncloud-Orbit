# E.1 — Medición del envío por producto (FBM)

## Qué es esto

Este documento es el **insumo** del acta de E.2 (`docs/evidencia/repricing-01/E.2/acta.md`,
todavía no escrita): E.2 sella la ventana, el mínimo con histéresis, el
percentil de valor y qué pasa con un producto sin historia, **con** los
números que produce este documento, no antes.

La ronda de cinco perspectivas de contexto fresco (producto, arquitectura,
seguridad, QA, escéptico) sobre `plans/repricing-01.md` cerró el 2026-09-16 y
está mergeada en PR #293. Esa ronda **redefinió** el alcance de E.1: entre
otras cosas, quitó el supuesto de que la fase E habilita 222 publicaciones
(son ~20), fijó que el percentil se calcula sobre `abs(amount)` agrupando
por orden (no por fila), y dejó abierta — sin veredicto — la disputa de si
las fuentes de `shipping_fee` duplican dinero. `docs/evidencia/repricing-01/plan-validacion-ebm.md`
trae el detalle completo de esa ronda; este documento no repite sus
números, los cita.

**E.1 depende de E.0**, que NO es parte de esta fase del trabajo: E.0
clasifica qué representa cada fuente de `shipping_fee` contra el
documento contable de origen (no se puede resolver desde Postgres).
Mientras E.0 no cierre, `L` no se sella. Por eso **todas** las tablas de
este documento que dependen de esa disputa se entregan bajo **dos
lecturas**, con una columna `lectura`:

- `componentes`: suma todas las fuentes RECONOCIDAS de la orden. Suma
  DENTRO de cada fuente antes de sumar entre fuentes.
- `duplicado_etiqueta`: **HIPÓTESIS DEL LEAD, NO el veredicto de E.0.**
  Costo = `greatest(suma de finance:LabmanLabelPurchase + finance:MFNPostageFee,
  suma de shipping_label) + suma del resto` (`finance:ShippingHB`,
  `finance:ShippingChargeback`, `finance:MFNShippingChargeback`). NO SE
  SABE qué representan `ShippingHB` ni los chargebacks — eso lo resuelve
  E.0.

**Extracción**: 2026-09-17, en UTC. **Rol**: `orbit_read`. **Transacciones**:
todas `BEGIN READ ONLY ... ROLLBACK` (ninguna consulta pudo escribir nada,
aunque hubiera querido). Las 10 consultas de `consultas/*.sql` corrieron
contra producción vía `correr.sh`, sin error (los 10 `.err` en `salidas/`
están vacíos); sus salidas literales viven en
`docs/evidencia/repricing-01/E.1/salidas/*.txt` y son la ÚNICA fuente de
las cifras de este documento — ninguna se tecleó a mano ni se redondeó a
ojo.

## Formato real medido por el lead (2026-09-17)

El supuesto de formato de `source_event_id` de las rondas r1/r2 estaba
mal. El lead midió (sonda `00-sonda-formato.sql`, `salidas/00-sonda-formato.txt`)
que hay DOS formas reales:

```
finance, 6 partes:        <plataforma>|finance|fee|<order_id>|<sku o vacío>|<subtipo>
shipping_label, 4 partes: <plataforma>|shipping_label|<order_id>|<fecha YYYY-MM-DD>
```

Esto YA NO ES un supuesto: está medido. Las consultas de E.1 usan esta
identidad de fuente desde la ronda r3.

## Ronda de corrección r4b (2026-09-17) — tablas llenas con cifras reales

Esta ronda llenó las tablas de este documento con las cifras de
`salidas/*.txt` (10 salidas, cero errores). No se tocó ninguna consulta:
si al leer una salida algo parecía andar mal, se reporta al coordinador,
no se corrige aquí (ver el reporte de esta tarea). Un hallazgo así: la
medida (b) de `rezago-emision.sql` (cargo vs `purchase_date`) da, en la
salida real, `ordenes_con_rezago_negativo = 0` para `amazon_us` y
`rezago_max_dias = 1` (MX) / `3` (US) — cifras que NO coinciden con la
descripción del hecho nuevo de la ronda r4a ("37 negativas en cada una...
máx 0"). Se usan aquí las cifras REALES de la salida (regla de oro), no
las de la descripción previa.

**Explicación (del lead):** no es un error de ninguna de las dos. La sonda
previa del lead midió contra el **primer** cargo de la orden; la medida (b)
versionada mide contra el **último** (en US, 447 órdenes traen 2 cargos y
55 traen 3, en fechas que pueden diferir: tabla f). Con el primer cargo
aparecen 37 diferencias negativas por plataforma y máximo 0; con el último,
0 negativas en US y máximo 1 (MX) / 3 (US). Las dos lecturas dicen lo
mismo para el acta E.2: el cargo cae entre −1 y +3 días de la fecha de
compra, no a 22–27 días. La cifra que vale en este documento es la de la
consulta versionada, que es la que se puede re-correr.

## Ronda de corrección r4a (2026-09-17)

r4a cambió las CONSULTAS (`rezago-emision.sql`, `rezago-ingesta.sql`,
`descartes.sql`, `cargos-por-orden-y-fuente.sql`, y la nueva
`01-historia-disponible.sql`) por cuatro cosas que el lead midió
corriendo `correr.sh` contra producción con las consultas de r3: (1) el
rezago de emisión del hecho 15 no se reproducía con ninguna fecha
disponible; (2) `rezago-ingesta.sql` mezclaba la carga inicial con la
incremental; (3) las órdenes rotuladas `sin_venta_ligada` en realidad
tenían venta, sin `product_id`; (4) hay pares de etiqueta casi idénticos
que duplican el costo bajo `componentes`. r4b (arriba) llena las tablas
con la segunda corrida, ya con las consultas corregidas.

## Ronda de corrección r3 (2026-09-17) — hecho nuevo + 3 hallazgos del revisor

- **Hallazgo 21 (alta)** — `rezago-emision.sql` reescrita con CUATRO
  medidas separadas: (a) principal cargo−venta del ledger (variantes
  primer/último cargo); (b) cargo−`purchase_date`; (c) diagnóstico
  cargo−fecha de parte 4 de `shipping_label`; (d) cargo−primer
  `last_updated_time` con `Shipped`. Negativos excluidos del percentil,
  contados aparte.
- **Hallazgo 20 (media)** — `efecto-margen.sql`: grilla completa
  (`top10 × ventanas × lecturas`, `CROSS JOIN` + `LEFT JOIN`), la fila de
  un producto del top10 sale siempre. El top10 se calcula sobre 365 días
  en los dos `SELECT`.
- **Hallazgo 22 (media)** — `00-sonda-formato.sql`: sin `kind='fee'`
  (solo aquí); número de partes de `source_event_id`; tercer `SELECT` de
  cobertura de `spapi_order_observation`.

**Bajas**: 23) cobertura por fuente publicada una vez, en
`envio-por-producto.sql`. 25) `coalesce(bool_or(fx_pendiente), false)`.
26) se borraron las columnas duplicada/muerta `ordenes_muestreadas` y
`ventanas_con_datos`. 28) histéresis partida en
`productos_con_salida_con_histeresis` / `productos_con_reentrada_con_histeresis`.

**Residual (hallazgo 27)**: `00-sonda-formato.sql` INFORMA, no frena la
corrida — `correr.sh` no interpreta su salida ni aborta si el formato
cambia otra vez; el freno es humano.

## Ronda de corrección r2 (2026-09-17)

Se agregó `E1-PMIXTO` (costo MIXTO dentro del mismo grupo) porque
`E1-PNOCOST` no discriminaba el arreglo del hallazgo 3 original. Se
corrigió la redacción de la negativa de lectura de producción (ver "Por
qué las cifras de este documento vienen de `salidas/*.txt`" abajo).

## Ronda de corrección r1 (2026-09-17)

El revisor devolvió CHANGES (5 altas / 8 medias) sobre el commit
`743b3cf`. Resumen: (1) top10 por 365 días, tres ventanas siempre; (2)
costo/FX a la fecha de la VENTA, no del cargo; (3) sin imputación
silenciosa (`ordenes_sin_costo`/`ordenes_sin_ingreso`, margen `NULL`);
(4) sonda de formato + cobertura; (5) `rezago-emision.sql` usa
`fulfillment_status` (reescrita otra vez en r3); (6) ventana semiabierta
`[hoy-N, hoy)` en UTC; (7) suma dentro de cada fuente antes de comparar;
(8) `fuente_no_reconocida` separada y excluida; (9)/(10) mismo formato
que `correr.sh`, escritura atómica; (12) histéresis (partida en dos en
r3); (13) este documento se actualiza en cada ronda.

**No cambiado (decisión del lead)**: hallazgo 11 (patrón de conexión de
`correr.sh` con `psql -U app_read`) — el DSN sale de `ORBIT_DSN_READ`
como argumento porque así lo fija el runbook de la fase; no se puede
comprobar ese rol sin leer producción como parte de esta tarea de
solo-lectura.

**Residual (hallazgo 17)**: el CTE `poblacion → cargos → sum_por_fuente →
orden_meta → orden_costo` se repite literal en 10 lugares. Deliberado —
hecho 13 del plan pide "un solo filtro escrito en un CTE común repetido
literal", para que un arreglo no se aplique a medias. Costo: mantenimiento
(r3 tocó las 10 copias).

## Por qué las cifras de este documento vienen de `salidas/*.txt`

El 2026-09-17 el lead intentó una sonda trivial de solo lectura contra
producción y el clasificador de permisos de su sesión la negó (motivo:
Production Reads). La negativa se respetó y no se rodeó: ni el lead ni
sus subagentes lo reintentaron. Ese mismo día el dueño autorizó la
lectura con una regla de permiso (`Bash(ssh goncloud:*)` en
`settings.local.json` del worktree del lead), y a partir de ahí el lead,
y solo el lead, corrió `correr.sh` con el rol `orbit_read` en
transacciones `READ ONLY`. Las cifras de este documento salen de
`salidas/*.txt`.

Esta tarea (implementador de E.1, ronda r4b) NO leyó producción: leyó los
archivos locales `salidas/*.txt` que el lead dejó, sin editarlos ni
borrarlos.

## Tablas

### a) Historia disponible — `01-historia-disponible.sql` / `01-historia-disponible.txt`

| fuente | platform | primer_event_date | último_event_date | filas | días_de_historia_disponible |
|---|---|---|---|---|---|
| shipping_fee | amazon_mx | 2025-12-04 | 2026-09-14 | 708 | 287 |
| shipping_fee | amazon_us | 2025-12-04 | 2026-09-15 | 1093 | 287 |
| venta | amazon_mx | 2025-11-14 | 2026-09-17 | 1223 | 307 |
| venta | amazon_us | 2025-11-20 | 2026-09-17 | 541 | 301 |

**Advertencia**: una ventana de "365 días" en las demás tablas son en
realidad **287 días** de historia de `shipping_fee` — el resto de la
ventana está vacío por construcción, no porque falten envíos. Ver columna
`dias_con_datos` en las tablas c, d y j.

### b) Formato y fuentes — `00-sonda-formato.sql` + cobertura de `envio-por-producto.sql`

`00-sonda-formato.txt`, resultado 1 (distribución real de `source_event_id`):

| platform | kind | parte_2 | parte_6 | n_partes | filas |
|---|---|---|---|---|---|
| amazon_mx | fee | finance | LabmanLabelPurchase | 6 | 3 |
| amazon_mx | fee | finance | MFNPostageFee | 6 | 648 |
| amazon_mx | fee | finance | MFNShippingChargeback | 6 | 2 |
| amazon_mx | fee | finance | ShippingChargeback | 6 | 6 |
| amazon_mx | fee | finance | ShippingHB | 6 | 28 |
| amazon_mx | fee | shipping_label | (vacío) | 4 | 21 |
| amazon_us | fee | finance | LabmanLabelPurchase | 6 | 56 |
| amazon_us | fee | finance | ShippingHB | 6 | 537 |
| amazon_us | fee | shipping_label | (vacío) | 4 | 500 |

No aparece ninguna fila con `finance:MFNPostageFee` en US, ni
`finance:ShippingChargeback`/`finance:MFNShippingChargeback` en US, ni
ninguna identidad fuera de las 6 conocidas (confirmado también por la
cobertura de abajo: `fuente_no_reconocida = 0` en ambas plataformas).

Resultado 2: `ordenes_en_mas_de_una_plataforma = 0`.

Resultado 3 (cobertura de `spapi_order_observation`, **sin desglosar por
plataforma** — la consulta no agrupa por `platform` en este result set;
es un total combinado MX+US, anotado como límite de esta sonda, no
arreglado en r4b): `ordenes_con_cargo = 1218`, `ordenes_con_alguna_observacion = 170`,
`ordenes_con_observacion_shipped = 0`.

Cobertura por fuente (`envio-por-producto.txt`, último resultado):

| platform | shipping_label | finance:LabmanLabelPurchase | finance:MFNPostageFee | finance:ShippingHB | finance:ShippingChargeback | finance:MFNShippingChargeback | fuente_desconocida | fuente_no_reconocida | multi_fila_misma_fuente |
|---|---|---|---|---|---|---|---|---|---|
| amazon_mx | 21 | 3 | 648 | 28 | 6 | 2 | 0 | 0 | 0 |
| amazon_us | 500 | 56 | 0 | 536 | 0 | 0 | 0 | 0 | 1 |

Las sumas por plataforma coinciden exactamente con las filas de la sonda
(21+3+648+28+6+2=708 MX; 500+56+536=1092+1=1093 US con la fila
duplicada) — las dos consultas se validan cruzado.

### c) Productos que alcanzan el mínimo — `productos-que-alcanzan-minimo.sql` / `.txt`

| platform | ventana_dias | lectura | productos con alguna orden | productos que alcanzan el mínimo (≥6) | dias_con_datos |
|---|---|---|---|---|---|
| amazon_mx | 90 | componentes | 70 | 7 | 90 |
| amazon_mx | 90 | duplicado_etiqueta | 70 | 7 | 90 |
| amazon_mx | 180 | componentes | 108 | 14 | 180 |
| amazon_mx | 180 | duplicado_etiqueta | 108 | 14 | 180 |
| amazon_mx | 365 | componentes | 126 | 17 | 287 |
| amazon_mx | 365 | duplicado_etiqueta | 126 | 17 | 287 |
| amazon_us | 90 | componentes | 38 | 9 | 90 |
| amazon_us | 90 | duplicado_etiqueta | 38 | 9 | 90 |
| amazon_us | 180 | componentes | 53 | 17 | 180 |
| amazon_us | 180 | duplicado_etiqueta | 53 | 17 | 180 |
| amazon_us | 365 | componentes | 59 | 22 | 287 |
| amazon_us | 365 | duplicado_etiqueta | 59 | 22 | 287 |

Ambas lecturas dan el MISMO número de productos que alcanzan el mínimo en
cada fila — la disputa de la lectura mueve el costo de la orden, pero en
esta corrida no movió a ningún producto por encima o por debajo del
umbral de 6.

### d) Envío por producto (mediana, p75, p90, máximo) — `envio-por-producto.sql` / `.txt`

Lectura `componentes`, completa (454 filas, generada por script desde `envio-por-producto.txt`):

| product_id | platform | ventana_dias | ordenes | mediana | p75 | p90 | máximo | dias_con_datos |
|---|---|---|---|---|---|---|---|---|
| 94 | amazon_mx | 180 | 1 | 70 | 70 | 70 | 70.0000 | 180 |
| 94 | amazon_mx | 365 | 1 | 70 | 70 | 70 | 70.0000 | 287 |
| 109 | amazon_mx | 365 | 1 | 136.83 | 136.83 | 136.83 | 136.8300 | 287 |
| 110 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 110 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 118 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 121 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 123 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 123 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 123 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 125 | amazon_mx | 180 | 2 | 95 | 95 | 95 | 95.0000 | 180 |
| 125 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 134 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 134 | amazon_mx | 180 | 2 | 68 | 69 | 69.6 | 70.0000 | 180 |
| 134 | amazon_mx | 365 | 2 | 68 | 69 | 69.6 | 70.0000 | 287 |
| 138 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 138 | amazon_mx | 180 | 3 | 95 | 95 | 95 | 95.0000 | 180 |
| 138 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 140 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 140 | amazon_mx | 180 | 2 | 93 | 94 | 94.6 | 95.0000 | 180 |
| 140 | amazon_mx | 365 | 2 | 93 | 94 | 94.6 | 95.0000 | 287 |
| 143 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 145 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 145 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 145 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 162 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 162 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 164 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 164 | amazon_mx | 180 | 2 | 91 | 91 | 91 | 91.0000 | 180 |
| 164 | amazon_mx | 365 | 2 | 91 | 91 | 91 | 91.0000 | 287 |
| 180 | amazon_mx | 365 | 2 | 95 | 95 | 95 | 95.0000 | 287 |
| 183 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 183 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 183 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 184 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 185 | amazon_mx | 90 | 11 | 91 | 91 | 91 | 91.0000 | 90 |
| 185 | amazon_mx | 180 | 18 | 91 | 91 | 95 | 95.0000 | 180 |
| 185 | amazon_mx | 365 | 26 | 91 | 91 | 95 | 95.0000 | 287 |
| 187 | amazon_mx | 90 | 3 | 91 | 91 | 91 | 91.0000 | 90 |
| 187 | amazon_mx | 180 | 8 | 91 | 92 | 95 | 95.0000 | 180 |
| 187 | amazon_mx | 365 | 10 | 91 | 94 | 95 | 95.0000 | 287 |
| 203 | amazon_mx | 90 | 7 | 70 | 70 | 131.94800000000006 | 224.8700 | 90 |
| 203 | amazon_mx | 180 | 16 | 70 | 70 | 77.6 | 224.8700 | 180 |
| 203 | amazon_mx | 365 | 25 | 66 | 70 | 70 | 224.8700 | 287 |
| 204 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 204 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 207 | amazon_mx | 90 | 10 | 91 | 91 | 95.40299999999999 | 135.0300 | 90 |
| 207 | amazon_mx | 180 | 25 | 91 | 95 | 95 | 135.0300 | 180 |
| 207 | amazon_mx | 365 | 30 | 91 | 95 | 95 | 135.0300 | 287 |
| 208 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 208 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 210 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 210 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 212 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 212 | amazon_mx | 365 | 2 | 95 | 95 | 95 | 95.0000 | 287 |
| 230 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 230 | amazon_mx | 180 | 1 | 70 | 70 | 70 | 70.0000 | 180 |
| 230 | amazon_mx | 365 | 1 | 70 | 70 | 70 | 70.0000 | 287 |
| 236 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 237 | amazon_mx | 90 | 1 | 84 | 84 | 84 | 84.0000 | 90 |
| 237 | amazon_mx | 180 | 2 | 82 | 83 | 83.6 | 84.0000 | 180 |
| 237 | amazon_mx | 365 | 2 | 82 | 83 | 83.6 | 84.0000 | 287 |
| 238 | amazon_mx | 180 | 4 | 95 | 95 | 95 | 95.0000 | 180 |
| 238 | amazon_mx | 365 | 5 | 95 | 95 | 95 | 95.0000 | 287 |
| 247 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 247 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 253 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 253 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 253 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 263 | amazon_mx | 90 | 2 | 91 | 91 | 91 | 91.0000 | 90 |
| 263 | amazon_mx | 180 | 2 | 91 | 91 | 91 | 91.0000 | 180 |
| 263 | amazon_mx | 365 | 2 | 91 | 91 | 91 | 91.0000 | 287 |
| 265 | amazon_mx | 90 | 3 | 91 | 91 | 91 | 91.0000 | 90 |
| 265 | amazon_mx | 180 | 6 | 91 | 94 | 95 | 95.0000 | 180 |
| 265 | amazon_mx | 365 | 6 | 91 | 94 | 95 | 95.0000 | 287 |
| 278 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 278 | amazon_mx | 180 | 1 | 70 | 70 | 70 | 70.0000 | 180 |
| 278 | amazon_mx | 365 | 1 | 70 | 70 | 70 | 70.0000 | 287 |
| 285 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 286 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 286 | amazon_mx | 180 | 1 | 70 | 70 | 70 | 70.0000 | 180 |
| 286 | amazon_mx | 365 | 1 | 70 | 70 | 70 | 70.0000 | 287 |
| 289 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 305 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 305 | amazon_mx | 180 | 2 | 66 | 66 | 66 | 66.0000 | 180 |
| 305 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 306 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 306 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 307 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 307 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 307 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 313 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 313 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 313 | amazon_mx | 365 | 2 | 80.5 | 87.75 | 92.1 | 95.0000 | 287 |
| 314 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 314 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 317 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 317 | amazon_mx | 180 | 2 | 66 | 66 | 66 | 66.0000 | 180 |
| 317 | amazon_mx | 365 | 3 | 66 | 66 | 66 | 66.0000 | 287 |
| 318 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 318 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 319 | amazon_mx | 90 | 3 | 70 | 70 | 70 | 70.0000 | 90 |
| 319 | amazon_mx | 180 | 3 | 70 | 70 | 70 | 70.0000 | 180 |
| 319 | amazon_mx | 365 | 3 | 70 | 70 | 70 | 70.0000 | 287 |
| 320 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 320 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 320 | amazon_mx | 365 | 3 | 91 | 93 | 94.2 | 95.0000 | 287 |
| 321 | amazon_mx | 90 | 2 | 57 | 63.5 | 67.4 | 70.0000 | 90 |
| 321 | amazon_mx | 180 | 3 | 66 | 68 | 69.2 | 70.0000 | 180 |
| 321 | amazon_mx | 365 | 3 | 66 | 68 | 69.2 | 70.0000 | 287 |
| 327 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 327 | amazon_mx | 180 | 2 | 68 | 69 | 69.6 | 70.0000 | 180 |
| 327 | amazon_mx | 365 | 3 | 66 | 68 | 69.2 | 70.0000 | 287 |
| 328 | amazon_mx | 90 | 2 | 91 | 91 | 91 | 91.0000 | 90 |
| 328 | amazon_mx | 180 | 3 | 91 | 93 | 94.2 | 95.0000 | 180 |
| 328 | amazon_mx | 365 | 3 | 91 | 93 | 94.2 | 95.0000 | 287 |
| 329 | amazon_mx | 365 | 1 | 80 | 80 | 80 | 80.0000 | 287 |
| 331 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 331 | amazon_mx | 180 | 2 | 68 | 69 | 69.6 | 70.0000 | 180 |
| 331 | amazon_mx | 365 | 3 | 66 | 68 | 69.2 | 70.0000 | 287 |
| 332 | amazon_mx | 90 | 3 | 91 | 91 | 91 | 91.0000 | 90 |
| 332 | amazon_mx | 180 | 7 | 91 | 95 | 95 | 95.0000 | 180 |
| 332 | amazon_mx | 365 | 12 | 95 | 95 | 95 | 95.0000 | 287 |
| 333 | amazon_mx | 180 | 34 | 95 | 95 | 95 | 95.0000 | 180 |
| 333 | amazon_mx | 365 | 58 | 95 | 95 | 95 | 221.1600 | 287 |
| 334 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 334 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 334 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 335 | amazon_mx | 90 | 9 | 91 | 91 | 91 | 91.0000 | 90 |
| 335 | amazon_mx | 180 | 34 | 95 | 95 | 95 | 95.0000 | 180 |
| 335 | amazon_mx | 365 | 44 | 95 | 95 | 95 | 95.0000 | 287 |
| 336 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 336 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 336 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 345 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 345 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 346 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 347 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 347 | amazon_mx | 180 | 2 | 93 | 94 | 94.6 | 95.0000 | 180 |
| 347 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 353 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 353 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 354 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 354 | amazon_mx | 180 | 2 | 93 | 94 | 94.6 | 95.0000 | 180 |
| 354 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 357 | amazon_mx | 90 | 2 | 91 | 91 | 91 | 91.0000 | 90 |
| 357 | amazon_mx | 180 | 3 | 91 | 93 | 94.2 | 95.0000 | 180 |
| 357 | amazon_mx | 365 | 4 | 91 | 92 | 93.8 | 95.0000 | 287 |
| 358 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 358 | amazon_mx | 180 | 2 | 93 | 94 | 94.6 | 95.0000 | 180 |
| 358 | amazon_mx | 365 | 4 | 95 | 95 | 95 | 95.0000 | 287 |
| 359 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 359 | amazon_mx | 180 | 4 | 93 | 123.42750000000001 | 174.59700000000004 | 208.7100 | 180 |
| 359 | amazon_mx | 365 | 5 | 95 | 95 | 163.226 | 208.7100 | 287 |
| 361 | amazon_mx | 90 | 3 | 91 | 91 | 91 | 91.0000 | 90 |
| 361 | amazon_mx | 180 | 4 | 91 | 92 | 93.8 | 95.0000 | 180 |
| 361 | amazon_mx | 365 | 5 | 91 | 95 | 187.952 | 249.9200 | 287 |
| 367 | amazon_mx | 180 | 3 | 95 | 95 | 95 | 95.0000 | 180 |
| 367 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 371 | amazon_mx | 90 | 3 | 91 | 147.9 | 182.04000000000002 | 204.8000 | 90 |
| 371 | amazon_mx | 180 | 7 | 95 | 95 | 138.92000000000004 | 204.8000 | 180 |
| 371 | amazon_mx | 365 | 8 | 95 | 95 | 127.93999999999998 | 204.8000 | 287 |
| 374 | amazon_mx | 180 | 1 | 80 | 80 | 80 | 80.0000 | 180 |
| 374 | amazon_mx | 365 | 1 | 80 | 80 | 80 | 80.0000 | 287 |
| 376 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 378 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 378 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 384 | amazon_mx | 90 | 2 | 70 | 70 | 70 | 70.0000 | 90 |
| 384 | amazon_mx | 180 | 2 | 70 | 70 | 70 | 70.0000 | 180 |
| 384 | amazon_mx | 365 | 3 | 70 | 70 | 70 | 70.0000 | 287 |
| 388 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 388 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 389 | amazon_mx | 180 | 3 | 80 | 80 | 80 | 80.0000 | 180 |
| 389 | amazon_mx | 365 | 3 | 80 | 80 | 80 | 80.0000 | 287 |
| 390 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 390 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 391 | amazon_mx | 365 | 2 | 80 | 80 | 80 | 80.0000 | 287 |
| 399 | amazon_mx | 365 | 1 | 80 | 80 | 80 | 80.0000 | 287 |
| 403 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 403 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 404 | amazon_mx | 90 | 2 | 66 | 66 | 66 | 66.0000 | 90 |
| 404 | amazon_mx | 180 | 2 | 66 | 66 | 66 | 66.0000 | 180 |
| 404 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 405 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 405 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 405 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 406 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 406 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 415 | amazon_mx | 180 | 2 | 66 | 66 | 66 | 66.0000 | 180 |
| 415 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 421 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 421 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 429 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 429 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 432 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 432 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 433 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 433 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 433 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 434 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 434 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 435 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 436 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 436 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 437 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 445 | amazon_mx | 365 | 4 | 66 | 66 | 66 | 66.0000 | 287 |
| 446 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 446 | amazon_mx | 365 | 3 | 66 | 66 | 66 | 66.0000 | 287 |
| 447 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 447 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 449 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 587 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 587 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 587 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 616 | amazon_mx | 90 | 2 | 66 | 66 | 66 | 66.0000 | 90 |
| 616 | amazon_mx | 180 | 3 | 66 | 66 | 66 | 66.0000 | 180 |
| 616 | amazon_mx | 365 | 4 | 66 | 66 | 66 | 66.0000 | 287 |
| 627 | amazon_mx | 90 | 2 | 66 | 66 | 66 | 66.0000 | 90 |
| 627 | amazon_mx | 180 | 5 | 66 | 66 | 66 | 66.0000 | 180 |
| 627 | amazon_mx | 365 | 5 | 66 | 66 | 66 | 66.0000 | 287 |
| 641 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 641 | amazon_mx | 180 | 2 | 90.015 | 100.02250000000001 | 106.027 | 110.0300 | 180 |
| 641 | amazon_mx | 365 | 2 | 90.015 | 100.02250000000001 | 106.027 | 110.0300 | 287 |
| 645 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 645 | amazon_mx | 180 | 1 | 70 | 70 | 70 | 70.0000 | 180 |
| 645 | amazon_mx | 365 | 1 | 70 | 70 | 70 | 70.0000 | 287 |
| 664 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 664 | amazon_mx | 180 | 2 | 93 | 94 | 94.6 | 95.0000 | 180 |
| 664 | amazon_mx | 365 | 3 | 95 | 95 | 95 | 95.0000 | 287 |
| 665 | amazon_mx | 180 | 3 | 80 | 80 | 80 | 80.0000 | 180 |
| 665 | amazon_mx | 365 | 3 | 80 | 80 | 80 | 80.0000 | 287 |
| 668 | amazon_mx | 90 | 2 | 91 | 91 | 91 | 91.0000 | 90 |
| 668 | amazon_mx | 180 | 3 | 91 | 93 | 94.2 | 95.0000 | 180 |
| 668 | amazon_mx | 365 | 3 | 91 | 93 | 94.2 | 95.0000 | 287 |
| 676 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 676 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 680 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 680 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 680 | amazon_mx | 365 | 2 | 93 | 94 | 94.6 | 95.0000 | 287 |
| 713 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 713 | amazon_mx | 180 | 4 | 95 | 95 | 95 | 95.0000 | 180 |
| 713 | amazon_mx | 365 | 6 | 95 | 95 | 95 | 95.0000 | 287 |
| 716 | amazon_mx | 90 | 1 | 84 | 84 | 84 | 84.0000 | 90 |
| 716 | amazon_mx | 180 | 1 | 84 | 84 | 84 | 84.0000 | 180 |
| 716 | amazon_mx | 365 | 2 | 82 | 83 | 83.6 | 84.0000 | 287 |
| 717 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 717 | amazon_mx | 365 | 2 | 93 | 94 | 94.6 | 95.0000 | 287 |
| 736 | amazon_mx | 90 | 1 | 84 | 84 | 84 | 84.0000 | 90 |
| 736 | amazon_mx | 180 | 1 | 84 | 84 | 84 | 84.0000 | 180 |
| 736 | amazon_mx | 365 | 2 | 82 | 83 | 83.6 | 84.0000 | 287 |
| 737 | amazon_mx | 90 | 1 | 107.9 | 107.9 | 107.9 | 107.9000 | 90 |
| 737 | amazon_mx | 180 | 1 | 107.9 | 107.9 | 107.9 | 107.9000 | 180 |
| 737 | amazon_mx | 365 | 2 | 101.45 | 104.67500000000001 | 106.61 | 107.9000 | 287 |
| 740 | amazon_mx | 90 | 1 | 74 | 74 | 74 | 74.0000 | 90 |
| 740 | amazon_mx | 180 | 1 | 74 | 74 | 74 | 74.0000 | 180 |
| 740 | amazon_mx | 365 | 2 | 77 | 78.5 | 79.4 | 80.0000 | 287 |
| 748 | amazon_mx | 90 | 1 | 66 | 66 | 66 | 66.0000 | 90 |
| 748 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 748 | amazon_mx | 365 | 3 | 66 | 66 | 66 | 66.0000 | 287 |
| 752 | amazon_mx | 90 | 1 | 70 | 70 | 70 | 70.0000 | 90 |
| 752 | amazon_mx | 180 | 2 | 68 | 69 | 69.6 | 70.0000 | 180 |
| 752 | amazon_mx | 365 | 3 | 66 | 68 | 69.2 | 70.0000 | 287 |
| 760 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 760 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 760 | amazon_mx | 365 | 2 | 93 | 94 | 94.6 | 95.0000 | 287 |
| 764 | amazon_mx | 90 | 5 | 91 | 91 | 91 | 91.0000 | 90 |
| 764 | amazon_mx | 180 | 9 | 91 | 95 | 95 | 95.0000 | 180 |
| 764 | amazon_mx | 365 | 13 | 95 | 95 | 95 | 95.0000 | 287 |
| 832 | amazon_mx | 90 | 1 | 84 | 84 | 84 | 84.0000 | 90 |
| 832 | amazon_mx | 180 | 3 | 80 | 82 | 83.2 | 84.0000 | 180 |
| 832 | amazon_mx | 365 | 7 | 80 | 82 | 87.4 | 92.5000 | 287 |
| 833 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 833 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 833 | amazon_mx | 365 | 2 | 93 | 94 | 94.6 | 95.0000 | 287 |
| 836 | amazon_mx | 90 | 2 | 84 | 84 | 84 | 84.0000 | 90 |
| 836 | amazon_mx | 180 | 4 | 82 | 84 | 84 | 84.0000 | 180 |
| 836 | amazon_mx | 365 | 4 | 82 | 84 | 84 | 84.0000 | 287 |
| 837 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 837 | amazon_mx | 180 | 1 | 91 | 91 | 91 | 91.0000 | 180 |
| 837 | amazon_mx | 365 | 1 | 91 | 91 | 91 | 91.0000 | 287 |
| 846 | amazon_mx | 90 | 2 | 66 | 66 | 66 | 66.0000 | 90 |
| 846 | amazon_mx | 180 | 2 | 66 | 66 | 66 | 66.0000 | 180 |
| 846 | amazon_mx | 365 | 2 | 66 | 66 | 66 | 66.0000 | 287 |
| 932 | amazon_mx | 180 | 1 | 66 | 66 | 66 | 66.0000 | 180 |
| 932 | amazon_mx | 365 | 1 | 66 | 66 | 66 | 66.0000 | 287 |
| 1621 | amazon_mx | 90 | 35 | 91 | 91 | 91 | 210.3600 | 90 |
| 1621 | amazon_mx | 180 | 67 | 91 | 95 | 95 | 225.0500 | 180 |
| 1621 | amazon_mx | 365 | 82 | 95 | 95 | 95 | 225.0500 | 287 |
| 1622 | amazon_mx | 90 | 1 | 91 | 91 | 91 | 91.0000 | 90 |
| 1622 | amazon_mx | 180 | 5 | 95 | 95 | 95 | 95.0000 | 180 |
| 1622 | amazon_mx | 365 | 6 | 95 | 95 | 95 | 95.0000 | 287 |
| 1625 | amazon_mx | 90 | 16 | 91 | 91 | 91 | 91.0000 | 90 |
| 1625 | amazon_mx | 180 | 26 | 91 | 95 | 95 | 114.8500 | 180 |
| 1625 | amazon_mx | 365 | 36 | 95 | 95 | 95 | 114.8500 | 287 |
| 1739 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 1739 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 1740 | amazon_mx | 90 | 8 | 91 | 91 | 91 | 91.0000 | 90 |
| 1740 | amazon_mx | 180 | 11 | 91 | 91 | 95 | 95.0000 | 180 |
| 1740 | amazon_mx | 365 | 11 | 91 | 91 | 95 | 95.0000 | 287 |
| 1743 | amazon_mx | 180 | 1 | 95 | 95 | 95 | 95.0000 | 180 |
| 1743 | amazon_mx | 365 | 1 | 95 | 95 | 95 | 95.0000 | 287 |
| 1744 | amazon_mx | 90 | 3 | 91 | 91 | 91 | 91.0000 | 90 |
| 1744 | amazon_mx | 180 | 7 | 95 | 95 | 95 | 95.0000 | 180 |
| 1744 | amazon_mx | 365 | 8 | 95 | 95 | 95 | 95.0000 | 287 |
| 79 | amazon_us | 180 | 1 | 484.15 | 484.15 | 484.15 | 484.1500 | 180 |
| 79 | amazon_us | 365 | 1 | 484.15 | 484.15 | 484.15 | 484.1500 | 287 |
| 97 | amazon_us | 90 | 1 | 534.63 | 534.63 | 534.63 | 534.6300 | 90 |
| 97 | amazon_us | 180 | 1 | 534.63 | 534.63 | 534.63 | 534.6300 | 180 |
| 97 | amazon_us | 365 | 1 | 534.63 | 534.63 | 534.63 | 534.6300 | 287 |
| 109 | amazon_us | 90 | 1 | 538.19 | 538.19 | 538.19 | 538.1900 | 90 |
| 109 | amazon_us | 180 | 2 | 511.17 | 524.6800000000001 | 532.7860000000001 | 538.1900 | 180 |
| 109 | amazon_us | 365 | 4 | 511.17 | 538.5400000000001 | 539.1700000000001 | 539.5900 | 287 |
| 110 | amazon_us | 90 | 1 | 991.76 | 991.76 | 991.76 | 991.7600 | 90 |
| 110 | amazon_us | 180 | 5 | 559.88 | 561.43 | 819.6279999999999 | 991.7600 | 180 |
| 110 | amazon_us | 365 | 5 | 559.88 | 561.43 | 819.6279999999999 | 991.7600 | 287 |
| 111 | amazon_us | 90 | 2 | 1007.0749999999999 | 1015.8374999999999 | 1021.0949999999999 | 1024.6000 | 90 |
| 111 | amazon_us | 180 | 2 | 1007.0749999999999 | 1015.8374999999999 | 1021.0949999999999 | 1024.6000 | 180 |
| 111 | amazon_us | 365 | 2 | 1007.0749999999999 | 1015.8374999999999 | 1021.0949999999999 | 1024.6000 | 287 |
| 117 | amazon_us | 90 | 1 | 550.18 | 550.18 | 550.18 | 550.1800 | 90 |
| 117 | amazon_us | 180 | 1 | 550.18 | 550.18 | 550.18 | 550.1800 | 180 |
| 117 | amazon_us | 365 | 1 | 550.18 | 550.18 | 550.18 | 550.1800 | 287 |
| 120 | amazon_us | 90 | 2 | 767.8 | 877.95 | 944.04 | 988.1000 | 90 |
| 120 | amazon_us | 180 | 2 | 767.8 | 877.95 | 944.04 | 988.1000 | 180 |
| 120 | amazon_us | 365 | 2 | 767.8 | 877.95 | 944.04 | 988.1000 | 287 |
| 137 | amazon_us | 180 | 1 | 471.45 | 471.45 | 471.45 | 471.4500 | 180 |
| 137 | amazon_us | 365 | 3 | 471.45 | 498.80499999999995 | 515.218 | 526.1600 | 287 |
| 138 | amazon_us | 180 | 1 | 544.13 | 544.13 | 544.13 | 544.1300 | 180 |
| 138 | amazon_us | 365 | 1 | 544.13 | 544.13 | 544.13 | 544.1300 | 287 |
| 139 | amazon_us | 90 | 1 | 541.37 | 541.37 | 541.37 | 541.3700 | 90 |
| 139 | amazon_us | 180 | 1 | 541.37 | 541.37 | 541.37 | 541.3700 | 180 |
| 139 | amazon_us | 365 | 1 | 541.37 | 541.37 | 541.37 | 541.3700 | 287 |
| 143 | amazon_us | 90 | 1 | 978.73 | 978.73 | 978.73 | 978.7300 | 90 |
| 143 | amazon_us | 180 | 1 | 978.73 | 978.73 | 978.73 | 978.7300 | 180 |
| 143 | amazon_us | 365 | 1 | 978.73 | 978.73 | 978.73 | 978.7300 | 287 |
| 151 | amazon_us | 180 | 2 | 539.55 | 539.75 | 539.87 | 539.9500 | 180 |
| 151 | amazon_us | 365 | 3 | 539.15 | 539.55 | 539.7900000000001 | 539.9500 | 287 |
| 152 | amazon_us | 180 | 1 | 478.34 | 478.34 | 478.34 | 478.3400 | 180 |
| 152 | amazon_us | 365 | 1 | 478.34 | 478.34 | 478.34 | 478.3400 | 287 |
| 161 | amazon_us | 180 | 1 | 487.23 | 487.23 | 487.23 | 487.2300 | 180 |
| 161 | amazon_us | 365 | 2 | 504.21500000000003 | 512.7075 | 517.803 | 521.2000 | 287 |
| 163 | amazon_us | 90 | 1 | 522.87 | 522.87 | 522.87 | 522.8700 | 90 |
| 163 | amazon_us | 180 | 2 | 531.265 | 535.4625 | 537.981 | 539.6600 | 180 |
| 163 | amazon_us | 365 | 2 | 531.265 | 535.4625 | 537.981 | 539.6600 | 287 |
| 164 | amazon_us | 180 | 1 | 525.43 | 525.43 | 525.43 | 525.4300 | 180 |
| 164 | amazon_us | 365 | 1 | 525.43 | 525.43 | 525.43 | 525.4300 | 287 |
| 187 | amazon_us | 180 | 1 | 540.21 | 540.21 | 540.21 | 540.2100 | 180 |
| 187 | amazon_us | 365 | 1 | 540.21 | 540.21 | 540.21 | 540.2100 | 287 |
| 219 | amazon_us | 180 | 1 | 508.58 | 508.58 | 508.58 | 508.5800 | 180 |
| 219 | amazon_us | 365 | 1 | 508.58 | 508.58 | 508.58 | 508.5800 | 287 |
| 263 | amazon_us | 90 | 17 | 550.18 | 558.34 | 985.372 | 993.5200 | 90 |
| 263 | amazon_us | 180 | 21 | 553.19 | 558.34 | 981.5 | 993.5200 | 180 |
| 263 | amazon_us | 365 | 23 | 550.72 | 558.13 | 896.9860000000003 | 993.5200 | 287 |
| 265 | amazon_us | 90 | 2 | 770.74 | 880.9599999999999 | 947.092 | 991.1800 | 90 |
| 265 | amazon_us | 180 | 3 | 554.72 | 772.95 | 903.8879999999999 | 991.1800 | 180 |
| 265 | amazon_us | 365 | 3 | 554.72 | 772.95 | 903.8879999999999 | 991.1800 | 287 |
| 273 | amazon_us | 90 | 4 | 539.71 | 543.9775 | 549.607 | 553.3600 | 90 |
| 273 | amazon_us | 180 | 18 | 541.25 | 554.325 | 558.555 | 558.9300 | 180 |
| 273 | amazon_us | 365 | 20 | 541.225 | 554.175 | 558.4649999999999 | 558.9300 | 287 |
| 274 | amazon_us | 90 | 3 | 976.38 | 980.8050000000001 | 983.46 | 985.2300 | 90 |
| 274 | amazon_us | 180 | 7 | 539 | 758.115 | 979.92 | 985.2300 | 180 |
| 274 | amazon_us | 365 | 10 | 539.425 | 544.3475 | 977.265 | 985.2300 | 287 |
| 305 | amazon_us | 90 | 4 | 522.345 | 640.5925 | 848.605 | 987.2800 | 90 |
| 305 | amazon_us | 180 | 5 | 524.61 | 525.03 | 802.38 | 987.2800 | 180 |
| 305 | amazon_us | 365 | 7 | 519.66 | 524.8199999999999 | 709.9300000000002 | 987.2800 | 287 |
| 307 | amazon_us | 365 | 1 | 521.99 | 521.99 | 521.99 | 521.9900 | 287 |
| 313 | amazon_us | 180 | 1 | 528.91 | 528.91 | 528.91 | 528.9100 | 180 |
| 313 | amazon_us | 365 | 2 | 280.54999999999995 | 404.72999999999996 | 479.238 | 528.9100 | 287 |
| 314 | amazon_us | 365 | 2 | 275.69000000000005 | 397.44000000000005 | 470.49000000000007 | 519.1900 | 287 |
| 315 | amazon_us | 90 | 3 | 519.66 | 734.01 | 862.62 | 948.3600 | 90 |
| 315 | amazon_us | 180 | 4 | 532.99 | 646.83 | 827.748 | 948.3600 | 180 |
| 315 | amazon_us | 365 | 4 | 532.99 | 646.83 | 827.748 | 948.3600 | 287 |
| 333 | amazon_us | 90 | 1 | 550.18 | 550.18 | 550.18 | 550.1800 | 90 |
| 333 | amazon_us | 180 | 3 | 550.18 | 554.535 | 557.148 | 558.8900 | 180 |
| 333 | amazon_us | 365 | 6 | 522.675 | 544.175 | 554.535 | 558.8900 | 287 |
| 334 | amazon_us | 180 | 2 | 512.63 | 528.38 | 537.83 | 544.1300 | 180 |
| 334 | amazon_us | 365 | 2 | 512.63 | 528.38 | 537.83 | 544.1300 | 287 |
| 335 | amazon_us | 90 | 6 | 548.065 | 883.06 | 991.53 | 991.7600 | 90 |
| 335 | amazon_us | 180 | 16 | 530.915 | 546.2249999999999 | 774.8199999999999 | 991.7600 | 180 |
| 335 | amazon_us | 365 | 25 | 520.19 | 537.79 | 557.76 | 991.7600 | 287 |
| 336 | amazon_us | 90 | 1 | 535.09 | 535.09 | 535.09 | 535.0900 | 90 |
| 336 | amazon_us | 180 | 2 | 508.805 | 521.9475 | 529.8330000000001 | 535.0900 | 180 |
| 336 | amazon_us | 365 | 3 | 482.52 | 508.805 | 524.576 | 535.0900 | 287 |
| 341 | amazon_us | 90 | 3 | 541.37 | 547.6600000000001 | 551.4340000000001 | 553.9500 | 90 |
| 341 | amazon_us | 180 | 5 | 534.02 | 541.37 | 548.918 | 553.9500 | 180 |
| 341 | amazon_us | 365 | 6 | 530.0899999999999 | 539.5325 | 547.6600000000001 | 553.9500 | 287 |
| 345 | amazon_us | 90 | 19 | 525.03 | 750.3050000000001 | 958.356 | 963.2200 | 90 |
| 345 | amazon_us | 180 | 32 | 528.8 | 541.045 | 954.024 | 963.2200 | 180 |
| 345 | amazon_us | 365 | 35 | 525.03 | 539.515 | 953.736 | 963.2200 | 287 |
| 346 | amazon_us | 180 | 1 | 536.98 | 536.98 | 536.98 | 536.9800 | 180 |
| 346 | amazon_us | 365 | 4 | 492.92 | 523.6375 | 531.643 | 536.9800 | 287 |
| 348 | amazon_us | 90 | 1 | 534.05 | 534.05 | 534.05 | 534.0500 | 90 |
| 348 | amazon_us | 180 | 3 | 525.15 | 529.5999999999999 | 532.27 | 534.0500 | 180 |
| 348 | amazon_us | 365 | 6 | 523.175 | 525.9075 | 530.105 | 534.0500 | 287 |
| 353 | amazon_us | 90 | 2 | 737.4649999999999 | 847.4425 | 913.429 | 957.4200 | 90 |
| 353 | amazon_us | 180 | 6 | 529.575 | 539.5225 | 748.575 | 957.4200 | 180 |
| 353 | amazon_us | 365 | 7 | 521.99 | 539.315 | 706.8060000000002 | 957.4200 | 287 |
| 354 | amazon_us | 90 | 6 | 535.645 | 846.975 | 952.6800000000001 | 954.8800 | 90 |
| 354 | amazon_us | 180 | 7 | 535.22 | 743.47 | 952.24 | 954.8800 | 180 |
| 354 | amazon_us | 365 | 9 | 534.83 | 536.46 | 951.36 | 954.8800 | 287 |
| 355 | amazon_us | 90 | 12 | 532.05 | 536.0275 | 910.3300000000002 | 951.5200 | 90 |
| 355 | amazon_us | 180 | 23 | 534.83 | 539.9300000000001 | 542.3580000000001 | 951.5200 | 180 |
| 355 | amazon_us | 365 | 26 | 532.05 | 539.705 | 541.995 | 951.5200 | 287 |
| 356 | amazon_us | 90 | 3 | 543.87 | 544.7149999999999 | 545.222 | 545.5600 | 90 |
| 356 | amazon_us | 180 | 8 | 524.9100000000001 | 542.04 | 544.377 | 545.5600 | 180 |
| 356 | amazon_us | 365 | 10 | 523.9549999999999 | 537.3425 | 544.039 | 545.5600 | 287 |
| 359 | amazon_us | 90 | 15 | 548.25 | 981.835 | 991.76 | 993.5200 | 90 |
| 359 | amazon_us | 180 | 31 | 544.38 | 560.2550000000001 | 988.41 | 993.5200 | 180 |
| 359 | amazon_us | 365 | 50 | 539.6800000000001 | 546.8824999999999 | 972.947 | 993.5200 | 287 |
| 367 | amazon_us | 90 | 11 | 966.66 | 972.405 | 984.54 | 989.0200 | 90 |
| 367 | amazon_us | 180 | 19 | 554.1 | 968.5799999999999 | 976.356 | 989.0200 | 180 |
| 367 | amazon_us | 365 | 22 | 550.28 | 865.68 | 973.929 | 989.0200 | 287 |
| 369 | amazon_us | 90 | 16 | 539.52 | 551.96 | 981.265 | 988.4100 | 90 |
| 369 | amazon_us | 180 | 33 | 541.9 | 554.54 | 561.43 | 988.4100 | 180 |
| 369 | amazon_us | 365 | 44 | 540.22 | 549.38 | 561.391 | 988.4100 | 287 |
| 375 | amazon_us | 180 | 3 | 545.27 | 549.23 | 551.606 | 553.1900 | 180 |
| 375 | amazon_us | 365 | 3 | 545.27 | 549.23 | 551.606 | 553.1900 | 287 |
| 587 | amazon_us | 90 | 3 | 548.52 | 768.77 | 900.9200000000001 | 989.0200 | 90 |
| 587 | amazon_us | 180 | 4 | 551.14 | 662.575 | 858.442 | 989.0200 | 180 |
| 587 | amazon_us | 365 | 4 | 551.14 | 662.575 | 858.442 | 989.0200 | 287 |
| 616 | amazon_us | 365 | 1 | 539.59 | 539.59 | 539.59 | 539.5900 | 287 |
| 619 | amazon_us | 90 | 5 | 972.69 | 982.69 | 986.488 | 989.0200 | 90 |
| 619 | amazon_us | 180 | 13 | 556.89 | 561.63 | 980.69 | 989.0200 | 180 |
| 619 | amazon_us | 365 | 33 | 539.59 | 542.39 | 561.008 | 989.0200 | 287 |
| 624 | amazon_us | 90 | 1 | 533.03 | 533.03 | 533.03 | 533.0300 | 90 |
| 624 | amazon_us | 180 | 2 | 511.78 | 522.405 | 528.78 | 533.0300 | 180 |
| 624 | amazon_us | 365 | 2 | 511.78 | 522.405 | 528.78 | 533.0300 | 287 |
| 627 | amazon_us | 90 | 4 | 545.13 | 655.425 | 846.7560000000001 | 974.3100 | 90 |
| 627 | amazon_us | 180 | 7 | 549.13 | 559.605 | 726.7020000000001 | 974.3100 | 180 |
| 627 | amazon_us | 365 | 12 | 542.39 | 551.415 | 561.225 | 974.3100 | 287 |
| 634 | amazon_us | 365 | 1 | 482.61 | 482.61 | 482.61 | 482.6100 | 287 |
| 635 | amazon_us | 180 | 1 | 501.16 | 501.16 | 501.16 | 501.1600 | 180 |
| 635 | amazon_us | 365 | 2 | 520.375 | 529.9825000000001 | 535.7470000000001 | 539.5900 | 287 |
| 640 | amazon_us | 90 | 1 | 530.58 | 530.58 | 530.58 | 530.5800 | 90 |
| 640 | amazon_us | 180 | 2 | 507.12 | 518.85 | 525.888 | 530.5800 | 180 |
| 640 | amazon_us | 365 | 4 | 535.8 | 542.405 | 544.8979999999999 | 546.5600 | 287 |
| 641 | amazon_us | 90 | 3 | 533.12 | 538.325 | 541.448 | 543.5300 | 90 |
| 641 | amazon_us | 180 | 9 | 538.16 | 543.53 | 546.506 | 555.0100 | 180 |
| 641 | amazon_us | 365 | 9 | 538.16 | 543.53 | 546.506 | 555.0100 | 287 |
| 676 | amazon_us | 90 | 2 | 747.53 | 863.13 | 932.49 | 978.7300 | 90 |
| 676 | amazon_us | 180 | 3 | 522.83 | 750.78 | 887.5500000000001 | 978.7300 | 180 |
| 676 | amazon_us | 365 | 3 | 522.83 | 750.78 | 887.5500000000001 | 978.7300 | 287 |
| 677 | amazon_us | 180 | 1 | 471.45 | 471.45 | 471.45 | 471.4500 | 180 |
| 677 | amazon_us | 365 | 1 | 471.45 | 471.45 | 471.45 | 471.4500 | 287 |
| 760 | amazon_us | 90 | 6 | 770.91 | 991.18 | 1685.58 | 2379.9800 | 90 |
| 760 | amazon_us | 180 | 8 | 554.56 | 991.18 | 1407.8199999999997 | 2379.9800 | 180 |
| 760 | amazon_us | 365 | 11 | 550.64 | 772.95 | 991.18 | 2379.9800 | 287 |
| 1621 | amazon_us | 90 | 3 | 537.79 | 756.45 | 887.646 | 975.1100 | 90 |
| 1621 | amazon_us | 180 | 5 | 535.01 | 537.79 | 800.182 | 975.1100 | 180 |
| 1621 | amazon_us | 365 | 6 | 512.4549999999999 | 537.095 | 756.45 | 975.1100 | 287 |
| 1625 | amazon_us | 90 | 5 | 556.39 | 976.38 | 985.332 | 991.3000 | 90 |
| 1625 | amazon_us | 180 | 13 | 554.54 | 561.63 | 894.1860000000003 | 991.3000 | 180 |
| 1625 | amazon_us | 365 | 20 | 520.32 | 555.1375 | 606.5070000000005 | 991.3000 | 287 |
| 1739 | amazon_us | 365 | 1 | 32.19 | 32.19 | 32.19 | 32.1900 | 287 |
| 1740 | amazon_us | 365 | 1 | 484.93 | 484.93 | 484.93 | 484.9300 | 287 |


Lectura `duplicado_etiqueta`: de 454 grupos `(product_id, platform,
ventana_dias)` en `componentes`, **72 difieren** de `duplicado_etiqueta` —
**los 72 son de `amazon_us`, ninguno de `amazon_mx`** (medido por script:
0 productos de MX cambian de lectura). Listados completos (72 filas):

| product_id | platform | ventana_dias | ordenes (comp/dup) | mediana (comp/dup) | p75 (comp/dup) | p90 (comp/dup) | máximo (comp/dup) |
|---|---|---|---|---|---|---|---|
| 110 | amazon_us | 90 | 1/1 | 991.76/540.55 | 991.76/540.55 | 991.76/540.55 | 991.7600/540.5500 |
| 110 | amazon_us | 180 | 5/5 | 559.88/558.89 | 561.43/559.88 | 819.6279999999999/560.81 | 991.7600/561.4300 |
| 110 | amazon_us | 365 | 5/5 | 559.88/558.89 | 561.43/559.88 | 819.6279999999999/560.81 | 991.7600/561.4300 |
| 111 | amazon_us | 90 | 2/2 | 1007.0749999999999/548.73 | 1015.8374999999999/553.375 | 1021.0949999999999/556.162 | 1024.6000/558.0200 |
| 111 | amazon_us | 180 | 2/2 | 1007.0749999999999/548.73 | 1015.8374999999999/553.375 | 1021.0949999999999/556.162 | 1024.6000/558.0200 |
| 111 | amazon_us | 365 | 2/2 | 1007.0749999999999/548.73 | 1015.8374999999999/553.375 | 1021.0949999999999/556.162 | 1024.6000/558.0200 |
| 120 | amazon_us | 90 | 2/2 | 767.8/543.585 | 877.95/545.5425 | 944.04/546.717 | 988.1000/547.5000 |
| 120 | amazon_us | 180 | 2/2 | 767.8/543.585 | 877.95/545.5425 | 944.04/546.717 | 988.1000/547.5000 |
| 120 | amazon_us | 365 | 2/2 | 767.8/543.585 | 877.95/545.5425 | 944.04/546.717 | 988.1000/547.5000 |
| 143 | amazon_us | 90 | 1/1 | 978.73/526.34 | 978.73/526.34 | 978.73/526.34 | 978.7300/526.3400 |
| 143 | amazon_us | 180 | 1/1 | 978.73/526.34 | 978.73/526.34 | 978.73/526.34 | 978.7300/526.3400 |
| 143 | amazon_us | 365 | 1/1 | 978.73/526.34 | 978.73/526.34 | 978.73/526.34 | 978.7300/526.3400 |
| 263 | amazon_us | 90 | 17/17 | 550.18/541.49 | 558.34/550.72 | 985.372/558.088 | 993.5200/558.4600 |
| 263 | amazon_us | 180 | 21/21 | 553.19/545.69 | 558.34/553.97 | 981.5/558.34 | 993.5200/558.9300 |
| 263 | amazon_us | 365 | 23/23 | 550.72/543.01 | 558.13/553.96 | 896.9860000000003/558.256 | 993.5200/558.9300 |
| 265 | amazon_us | 90 | 2/2 | 770.74/545.895 | 880.9599999999999/548.0975 | 947.092/549.419 | 991.1800/550.3000 |
| 265 | amazon_us | 180 | 3/3 | 554.72/550.3 | 772.95/552.51 | 903.8879999999999/553.836 | 991.1800/554.7200 |
| 265 | amazon_us | 365 | 3/3 | 554.72/550.3 | 772.95/552.51 | 903.8879999999999/553.836 | 991.1800/554.7200 |
| 274 | amazon_us | 90 | 3/3 | 976.38/535.09 | 980.8050000000001/535.9449999999999 | 983.46/536.458 | 985.2300/536.8000 |
| 274 | amazon_us | 180 | 7/7 | 539/535.09 | 758.115/537.9 | 979.92/539.34 | 985.2300/539.8500 |
| 274 | amazon_us | 365 | 10/10 | 539.425/535.9449999999999 | 544.3475/539.6375 | 977.265/542.651 | 985.2300/545.0000 |
| 305 | amazon_us | 90 | 4/4 | 522.345/522.345 | 640.5925/528.5999999999999 | 848.605/535.026 | 987.2800/539.3100 |
| 305 | amazon_us | 180 | 5/5 | 524.61/524.61 | 525.03/525.03 | 802.38/533.598 | 987.2800/539.3100 |
| 305 | amazon_us | 365 | 7/7 | 519.66/519.66 | 524.8199999999999/524.8199999999999 | 709.9300000000002/530.742 | 987.2800/539.3100 |
| 315 | amazon_us | 90 | 3/3 | 519.66/515.09 | 734.01/517.375 | 862.62/518.746 | 948.3600/519.6600 |
| 315 | amazon_us | 180 | 4/4 | 532.99/517.375 | 646.83/526.325 | 827.748/538.322 | 948.3600/546.3200 |
| 315 | amazon_us | 365 | 4/4 | 532.99/517.375 | 646.83/526.325 | 827.748/538.322 | 948.3600/546.3200 |
| 335 | amazon_us | 90 | 6/6 | 548.065/538.8399999999999 | 883.06/540.385 | 991.53/549.4449999999999 | 991.7600/558.3400 |
| 335 | amazon_us | 180 | 16/16 | 530.915/530.915 | 546.2249999999999/540.235 | 774.8199999999999/549.78 | 991.7600/558.3400 |
| 335 | amazon_us | 365 | 25/25 | 520.19/520.19 | 537.79/537.79 | 557.76/541.822 | 991.7600/558.3400 |
| 345 | amazon_us | 90 | 19/19 | 525.03/521.92 | 750.3050000000001/525.7049999999999 | 958.356/533.444 | 963.2200/547.4500 |
| 345 | amazon_us | 180 | 32/32 | 528.8/523.7750000000001 | 541.045/537.8575 | 954.024/540.007 | 963.2200/547.4500 |
| 345 | amazon_us | 365 | 35/35 | 525.03/522.94 | 539.515/536.5799999999999 | 953.736/539.638 | 963.2200/547.4500 |
| 353 | amazon_us | 90 | 2/2 | 737.4649999999999/520.365 | 847.4425/521.7925 | 913.429/522.649 | 957.4200/523.2200 |
| 353 | amazon_us | 180 | 6/6 | 529.575/521.735 | 539.5225/534.98 | 748.575/539.315 | 957.4200/539.7300 |
| 353 | amazon_us | 365 | 7/7 | 521.99/521.99 | 539.315/531.06 | 706.8060000000002/539.232 | 957.4200/539.7300 |
| 354 | amazon_us | 90 | 6/6 | 535.645/522.02 | 846.975/531.9425 | 952.6800000000001/535.645 | 954.8800/536.4600 |
| 354 | amazon_us | 180 | 7/7 | 535.22/523.28 | 743.47/535.0250000000001 | 952.24/535.716 | 954.8800/536.4600 |
| 354 | amazon_us | 365 | 9/9 | 534.83/521.99 | 536.46/534.83 | 951.36/535.4680000000001 | 954.8800/536.4600 |
| 355 | amazon_us | 90 | 12/12 | 532.05/526.815 | 536.0275/534.245 | 910.3300000000002/534.83 | 951.5200/539.6200 |
| 355 | amazon_us | 180 | 23/23 | 534.83/530.05 | 539.9300000000001/539.625 | 542.3580000000001/540.3860000000001 | 951.5200/542.6000 |
| 355 | amazon_us | 365 | 26/26 | 532.05/526.815 | 539.705/538.96 | 541.995/540.29 | 951.5200/542.6000 |
| 359 | amazon_us | 90 | 15/15 | 548.25/541.13 | 981.835/545.565 | 991.76/548.25 | 993.5200/562.0700 |
| 359 | amazon_us | 180 | 31/31 | 544.38/540.55 | 560.2550000000001/545.1800000000001 | 988.41/556.89 | 993.5200/562.0700 |
| 359 | amazon_us | 365 | 50/50 | 539.6800000000001/539.59 | 546.8824999999999/542.39 | 972.947/548.25 | 993.5200/562.0700 |
| 367 | amazon_us | 90 | 11/11 | 966.66/538.65 | 972.405/549.84 | 984.54/550.72 | 989.0200/562.7400 |
| 367 | amazon_us | 180 | 19/19 | 554.1/541.49 | 968.5799999999999/550.28 | 976.356/556.0120000000001 | 989.0200/562.7400 |
| 367 | amazon_us | 365 | 22/22 | 550.28/540.0699999999999 | 865.68/549.84 | 973.929/555.468 | 989.0200/562.7400 |
| 369 | amazon_us | 90 | 16/16 | 539.52/537.845 | 551.96/540.98 | 981.265/550.24 | 988.4100/551.9600 |
| 369 | amazon_us | 180 | 33/33 | 541.9/538.83 | 554.54/548.52 | 561.43/559.282 | 988.4100/561.4300 |
| 369 | amazon_us | 365 | 44/44 | 540.22/539.59 | 549.38/544.8249999999999 | 561.391/555.457 | 988.4100/561.4300 |
| 587 | amazon_us | 90 | 3/3 | 548.52/538.65 | 768.77/543.585 | 900.9200000000001/546.5459999999999 | 989.0200/548.5200 |
| 587 | amazon_us | 180 | 4/4 | 551.14/543.585 | 662.575/549.8299999999999 | 858.442/552.188 | 989.0200/553.7600 |
| 587 | amazon_us | 365 | 4/4 | 551.14/543.585 | 662.575/549.8299999999999 | 858.442/552.188 | 989.0200/553.7600 |
| 619 | amazon_us | 90 | 5/5 | 972.69/538.65 | 982.69/541.13 | 986.488/550.952 | 989.0200/557.5000 |
| 619 | amazon_us | 180 | 13/13 | 556.89/538.65 | 561.63/556.89 | 980.69/558.316 | 989.0200/561.6300 |
| 619 | amazon_us | 365 | 33/33 | 539.59/539.59 | 542.39/541.13 | 561.008/554.348 | 989.0200/561.6300 |
| 627 | amazon_us | 90 | 4/4 | 545.13/539.85 | 655.425/543.13 | 846.7560000000001/546.73 | 974.3100/549.1300 |
| 627 | amazon_us | 180 | 7/7 | 549.13/541.13 | 559.605/553.355 | 726.7020000000001/559.2 | 974.3100/561.6300 |
| 627 | amazon_us | 365 | 12/12 | 542.39/541.76 | 551.415/549.1875 | 561.225/556.758 | 974.3100/561.6300 |
| 676 | amazon_us | 90 | 2/2 | 747.53/521.335 | 863.13/523.8375000000001 | 932.49/525.339 | 978.7300/526.3400 |
| 676 | amazon_us | 180 | 3/3 | 522.83/522.83 | 750.78/524.585 | 887.5500000000001/525.638 | 978.7300/526.3400 |
| 676 | amazon_us | 365 | 3/3 | 522.83/522.83 | 750.78/524.585 | 887.5500000000001/525.638 | 978.7300/526.3400 |
| 760 | amazon_us | 90 | 6/6 | 770.91/545.835 | 991.18/550.525 | 1685.58/1401.75 | 2379.9800/2252.8600 |
| 760 | amazon_us | 180 | 8/8 | 554.56/550.41 | 991.18/554.48 | 1407.8199999999997/1064.1619999999998 | 2379.9800/2252.8600 |
| 760 | amazon_us | 365 | 11/11 | 550.64/541.6 | 772.95/552.52 | 991.18/554.72 | 2379.9800/2252.8600 |
| 1621 | amazon_us | 90 | 3/3 | 537.79/535.01 | 756.45/536.4 | 887.646/537.2339999999999 | 975.1100/537.7900 |
| 1621 | amazon_us | 180 | 5/5 | 535.01/524.47 | 537.79/535.01 | 800.182/536.678 | 975.1100/537.7900 |
| 1621 | amazon_us | 365 | 6/6 | 512.4549999999999/507.185 | 537.095/532.375 | 756.45/536.4 | 975.1100/537.7900 |
| 1625 | amazon_us | 90 | 5/5 | 556.39/551.96 | 976.38/552.42 | 985.332/554.802 | 991.3000/556.3900 |
| 1625 | amazon_us | 180 | 13/13 | 554.54/551.96 | 561.63/554.72 | 894.1860000000003/560.582 | 991.3000/565.4100 |
| 1625 | amazon_us | 365 | 20/20 | 520.32/520.32 | 555.1375/552.9499999999999 | 606.5070000000005/556.914 | 991.3000/565.4100 |


### e) Descartes — `descartes.sql` / `descartes.txt`

| platform | razón | órdenes | de las cuales multiunidad (no descartada) |
|---|---|---|---|
| amazon_mx | multi_producto | 1 | 0 |
| amazon_mx | usable | 595 | 0 |
| amazon_mx | venta_sin_producto | 86 | 0 |
| amazon_us | usable | 474 | 1 |
| amazon_us | venta_sin_producto | 62 | 0 |

**Razones que no aparecen en la salida = 0 órdenes** (no es que la
consulta fallara: un `GROUP BY` no emite fila para un grupo vacío) —
confirmado cruzado con `rezago-emision.txt` (a), que muestra
`ordenes_con_venta = ordenes_con_cargo` en las dos plataformas (0 sin
venta), y con la sonda (0 identidades nulas o no reconocidas):
`sin_order_id = 0`, `sin_fila_de_venta = 0`, `fuente_desconocida = 0`,
`fuente_no_reconocida = 0` en ambas plataformas.

### f) Cargos por orden y fuente — `cargos-por-orden-y-fuente.sql` / `.txt`

Distribución de cargos por orden (resultado a):

| platform | n_cargos | órdenes |
|---|---|---|
| amazon_mx | 1 | 656 |
| amazon_mx | 2 | 26 |
| amazon_us | 1 | 34 |
| amazon_us | 2 | 447 |
| amazon_us | 3 | 55 |

Pares de etiqueta (resultados d/e) — **SIN VEREDICTO: insumo de E.0**.
Resumen completo por plataforma:

| platform | órdenes | diferencia_absoluta_p50 | diferencia_absoluta_p90 | diferencia_absoluta_max | cociente_p50 | cociente_p90 | cociente_max | difieren ≤1% | difieren 1-5% | difieren >5% |
|---|---|---|---|---|---|---|---|---|---|---|
| amazon_us | 54 | 0 | 3 | 2022.75 | 1.0 | 1.0066712624252263 | 16.9121302706104468 | 53 | 0 | 1 |

**`amazon_mx` no aparece: 0 órdenes de MX tienen a la vez una fuente de
etiqueta por `finance` (`LabmanLabelPurchase`/`MFNPostageFee`) Y una fila
`shipping_label`** — MX casi siempre trae `finance:MFNPostageFee` (648
filas) pero rara vez junto con `shipping_label` en la misma orden.

Los pares que difieren **>5%** (1 de 54, la misma orden que trae la
disputa histórica del hecho 14 — `plan-validacion-ebm.md`, "Disputa
abierta", `111-0818188-2803467`):

| order_id | platform | monto_finance_etiqueta | monto_shipping_label | diferencia_absoluta | cociente |
|---|---|---|---|---|---|
| 111-0818188-2803467 | amazon_us | 127.12 | 2149.87 | 2022.75 | 16.912... |

(Esta orden trae también `finance:ShippingHB` 102.99, no mostrado en la
tabla porque el "par" es solo finance-etiqueta vs shipping_label; el
detalle de las 3 filas está en `salidas/cargos-por-orden-y-fuente.txt`,
líneas del `order_id`.)

### g) Rezago de emisión — `rezago-emision.sql` / `.txt`

**«El rezago de emisión de p50 27 días (MX) / 22 (US), p90 57-59 y
máximo 73 que el hecho 15 del plan da por medido NO se reproduce con
ninguna fecha disponible en Orbit.»**

(a) PRINCIPAL, cargo − venta del ledger:

| platform | variante | ordenes_con_cargo | ordenes_con_venta | ordenes_sin_venta | ordenes_con_rezago_negativo | p50 | p90 | máximo |
|---|---|---|---|---|---|---|---|---|
| amazon_mx | primer_cargo | 682 | 682 | 0 | 236 | 0 | 0 | 2 |
| amazon_mx | ultimo_cargo | 682 | 682 | 0 | 230 | 0 | 0 | 3 |
| amazon_us | primer_cargo | 536 | 536 | 0 | 174 | 0 | 0 | 3 |
| amazon_us | ultimo_cargo | 536 | 536 | 0 | 20 | 0 | 2 | 3 |

(b) cargo − `purchase_date` (cobertura PARCIAL):

| platform | ordenes_con_cargo | ordenes_con_dato | ordenes_sin_dato | ordenes_con_rezago_negativo | p50 | p90 | máximo |
|---|---|---|---|---|---|---|---|
| amazon_mx | 682 | 84 | 598 | 37 | 0 | 0 | 1 |
| amazon_us | 536 | 86 | 450 | 0 | 0 | 2 | 3 |

(c) DIAGNÓSTICO, cargo − fecha de parte 4 de `shipping_label` (no es una
medida de rezago independiente — ver cabecera del `.sql`):

| platform | ordenes_con_cargo | con_shipping_label | sin_shipping_label | con_diff_negativa | diff_min | diff_p50 | diff_p90 | diff_máx |
|---|---|---|---|---|---|---|---|---|
| amazon_mx | 682 | 21 | 661 | 4 | -1 | 0 | 0 | 0 |
| amazon_us | 536 | 500 | 36 | 82 | -3 | 0 | 0 | 0 |

(d) cargo − primer `last_updated_time` con `Shipped` (cobertura
DECLARADA en cero):

| platform | ordenes_con_cargo | con_dato | sin_dato | con_rezago_negativo | p50 | p90 | máximo |
|---|---|---|---|---|---|---|---|
| amazon_mx | 682 | 0 | 682 | 0 | NULL (cobertura cero, ninguna orden tiene el dato) | NULL | NULL |
| amazon_us | 536 | 0 | 536 | 0 | NULL (cobertura cero, ninguna orden tiene el dato) | NULL | NULL |

### h) Rezago de ingesta — `rezago-ingesta.sql` / `.txt`

| platform | alcance | filas | p50 | p90 | máximo |
|---|---|---|---|---|---|
| amazon_mx | todas (incluye la carga inicial) | 708 | 134 | 218 | 270 |
| amazon_mx | solo_incremental | 49 | 2 | 4.2 | 7 |
| amazon_us | todas (incluye la carga inicial) | 1093 | 118 | 226.8 | 270 |
| amazon_us | solo_incremental | 152 | 3 | 13 | 16 |

Días de ingesta: **11**. Primer día de ingesta (calculado, no literal):
**2026-08-31** (coincide con la carga inicial que describió el lead).
Último día: 2026-09-17 (hoy).

### i) Parpadeo del mínimo — `parpadeo.sql` / `.txt`

| platform | lectura | alcanzan el mínimo en alguna ventana | estables en las seis | parpadean (corte seco) | con salida (histéresis) | con reentrada (histéresis) |
|---|---|---|---|---|---|---|
| amazon_mx | componentes | 13 | 4 | 9 | 2 | 0 |
| amazon_mx | duplicado_etiqueta | 13 | 4 | 9 | 2 | 0 |
| amazon_us | componentes | 15 | 5 | 10 | 3 | 0 |
| amazon_us | duplicado_etiqueta | 15 | 5 | 10 | 3 | 0 |

**Ninguna reentrada con histéresis en ninguna plataforma/lectura**: en las
seis ventanas móviles medidas, ningún producto completó un ciclo
entra→sale→reentra; los que "salen" con histéresis (2 MX, 3 US) no
volvieron a entrar todavía.

### j) Efecto en el margen — `efecto-margen.sql` / `.txt`

Top 10 productos por plataforma con más órdenes a 365 días (**20
productos en total, ≥10 por plataforma**). **Margen de contribución
SIMPLIFICADO en MXN (venta − costo − envío), sin IVA, sin ISR y sin
comisiones de plataforma ni Ads — NO comparable contra un goal de margen
de la fase B.**

Bloque 1 (`L` = mediana, ventanas 90/180/365, 120 filas = 20 productos ×
3 ventanas × 2 lecturas):

| product_id | platform | ventana_dias | lectura | ordenes | alcanza_minimo | unidades_totales | sin_costo | sin_ingreso | fx_pendiente | L_mediana | costo/u | ingreso/u | margen/u | dias_con_datos |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 185 | amazon_mx | 90 | componentes | 11 | t | 11 | 0 | 0 | f | 91 | 302.9318181818181818 | 989.0000000000000000 | 595.0681818181819 | 90 |
| 185 | amazon_mx | 90 | duplicado_etiqueta | 11 | t | 11 | 0 | 0 | f | 91 | 302.9318181818181818 | 989.0000000000000000 | 595.0681818181819 | 90 |
| 185 | amazon_mx | 180 | componentes | 18 | t | 18 | 0 | 0 | f | 91 | 302.3750000000000000 | 1022.1666666666666667 | 628.7916666666666 | 180 |
| 185 | amazon_mx | 180 | duplicado_etiqueta | 18 | t | 18 | 0 | 0 | f | 91 | 302.3750000000000000 | 1022.1666666666666667 | 628.7916666666666 | 180 |
| 185 | amazon_mx | 365 | componentes | 26 | t | 26 | 1 | 0 | f | 91 |  | 1043.0000000000000000 |  | 287 |
| 185 | amazon_mx | 365 | duplicado_etiqueta | 26 | t | 26 | 1 | 0 | f | 91 |  | 1043.0000000000000000 |  | 287 |
| 203 | amazon_mx | 90 | componentes | 7 | t | 7 | 0 | 0 | f | 70 | 267.7685714285714286 | 1000.9271428571428571 | 663.1585714285715 | 90 |
| 203 | amazon_mx | 90 | duplicado_etiqueta | 7 | t | 7 | 0 | 0 | f | 70 | 267.7685714285714286 | 1000.9271428571428571 | 663.1585714285715 | 90 |
| 203 | amazon_mx | 180 | componentes | 16 | t | 16 | 0 | 0 | f | 70 | 286.7425000000000000 | 1042.8275000000000000 | 686.085 | 180 |
| 203 | amazon_mx | 180 | duplicado_etiqueta | 16 | t | 16 | 0 | 0 | f | 70 | 286.7425000000000000 | 1042.8275000000000000 | 686.085 | 180 |
| 203 | amazon_mx | 365 | componentes | 25 | t | 25 | 2 | 0 | f | 66 |  | 1062.9696000000000000 |  | 287 |
| 203 | amazon_mx | 365 | duplicado_etiqueta | 25 | t | 25 | 2 | 0 | f | 66 |  | 1062.9696000000000000 |  | 287 |
| 207 | amazon_mx | 90 | componentes | 10 | t | 10 | 0 | 0 | f | 91 | 283.7910000000000000 | 993.4030000000000000 | 618.612 | 90 |
| 207 | amazon_mx | 90 | duplicado_etiqueta | 10 | t | 10 | 0 | 0 | f | 91 | 283.7910000000000000 | 993.4030000000000000 | 618.612 | 90 |
| 207 | amazon_mx | 180 | componentes | 25 | t | 25 | 0 | 0 | f | 91 | 294.4164000000000000 | 1013.0972000000000000 | 627.6808 | 180 |
| 207 | amazon_mx | 180 | duplicado_etiqueta | 25 | t | 25 | 0 | 0 | f | 91 | 294.4164000000000000 | 1013.0972000000000000 | 627.6808 | 180 |
| 207 | amazon_mx | 365 | componentes | 30 | t | 30 | 1 | 0 | f | 91 |  | 1021.7476666666666667 |  | 287 |
| 207 | amazon_mx | 365 | duplicado_etiqueta | 30 | t | 30 | 1 | 0 | f | 91 |  | 1021.7476666666666667 |  | 287 |
| 332 | amazon_mx | 90 | componentes | 3 | f | 3 | 0 | 0 | f | 91 | 458.0000000000000000 | 1301.5466666666666667 | 752.5466666666666 | 90 |
| 332 | amazon_mx | 90 | duplicado_etiqueta | 3 | f | 3 | 0 | 0 | f | 91 | 458.0000000000000000 | 1301.5466666666666667 | 752.5466666666666 | 90 |
| 332 | amazon_mx | 180 | componentes | 7 | t | 7 | 0 | 0 | f | 91 | 458.0000000000000000 | 1320.5942857142857143 | 771.5942857142857 | 180 |
| 332 | amazon_mx | 180 | duplicado_etiqueta | 7 | t | 7 | 0 | 0 | f | 91 | 458.0000000000000000 | 1320.5942857142857143 | 771.5942857142857 | 180 |
| 332 | amazon_mx | 365 | componentes | 12 | t | 12 | 0 | 0 | f | 95 | 458.0000000000000000 | 1384.0266666666666667 | 831.0266666666666 | 287 |
| 332 | amazon_mx | 365 | duplicado_etiqueta | 12 | t | 12 | 0 | 0 | f | 95 | 458.0000000000000000 | 1384.0266666666666667 | 831.0266666666666 | 287 |
| 333 | amazon_mx | 90 | componentes | 0 | f | 0 | 0 | 0 | f |  |  |  |  | 90 |
| 333 | amazon_mx | 90 | duplicado_etiqueta | 0 | f | 0 | 0 | 0 | f |  |  |  |  | 90 |
| 333 | amazon_mx | 180 | componentes | 34 | t | 34 | 0 | 0 | f | 95 | 301.5000000000000000 | 1122.3520588235294118 | 725.8520588235294 | 180 |
| 333 | amazon_mx | 180 | duplicado_etiqueta | 34 | t | 34 | 0 | 0 | f | 95 | 301.5000000000000000 | 1122.3520588235294118 | 725.8520588235294 | 180 |
| 333 | amazon_mx | 365 | componentes | 58 | t | 58 | 9 | 0 | f | 95 |  | 1172.9198275862068966 |  | 287 |
| 333 | amazon_mx | 365 | duplicado_etiqueta | 58 | t | 58 | 9 | 0 | f | 95 |  | 1172.9198275862068966 |  | 287 |
| 335 | amazon_mx | 90 | componentes | 9 | t | 9 | 0 | 0 | f | 91 | 303.6000000000000000 | 1257.2766666666666667 | 862.6766666666666 | 90 |
| 335 | amazon_mx | 90 | duplicado_etiqueta | 9 | t | 9 | 0 | 0 | f | 91 | 303.6000000000000000 | 1257.2766666666666667 | 862.6766666666666 | 90 |
| 335 | amazon_mx | 180 | componentes | 34 | t | 34 | 0 | 0 | f | 95 | 302.0558823529411765 | 1246.7850000000000000 | 849.7291176470588 | 180 |
| 335 | amazon_mx | 180 | duplicado_etiqueta | 34 | t | 34 | 0 | 0 | f | 95 | 302.0558823529411765 | 1246.7850000000000000 | 849.7291176470588 | 180 |
| 335 | amazon_mx | 365 | componentes | 44 | t | 44 | 2 | 0 | f | 95 |  | 1248.9586363636363636 |  | 287 |
| 335 | amazon_mx | 365 | duplicado_etiqueta | 44 | t | 44 | 2 | 0 | f | 95 |  | 1248.9586363636363636 |  | 287 |
| 764 | amazon_mx | 90 | componentes | 5 | f | 5 | 0 | 0 | f | 91 | 444.2900000000000000 | 2059.2000000000000000 | 1523.91 | 90 |
| 764 | amazon_mx | 90 | duplicado_etiqueta | 5 | f | 5 | 0 | 0 | f | 91 | 444.2900000000000000 | 2059.2000000000000000 | 1523.91 | 90 |
| 764 | amazon_mx | 180 | componentes | 9 | t | 9 | 0 | 0 | f | 91 | 465.0633333333333333 | 2059.2000000000000000 | 1503.1366666666668 | 180 |
| 764 | amazon_mx | 180 | duplicado_etiqueta | 9 | t | 9 | 0 | 0 | f | 91 | 465.0633333333333333 | 2059.2000000000000000 | 1503.1366666666668 | 180 |
| 764 | amazon_mx | 365 | componentes | 13 | t | 13 | 0 | 0 | f | 95 | 473.0530769230769231 | 2129.6000000000000000 | 1561.5469230769231 | 287 |
| 764 | amazon_mx | 365 | duplicado_etiqueta | 13 | t | 13 | 0 | 0 | f | 95 | 473.0530769230769231 | 2129.6000000000000000 | 1561.5469230769231 | 287 |
| 1621 | amazon_mx | 90 | componentes | 35 | t | 35 | 0 | 0 | f | 91 | 222.1528571428571429 | 1124.5422857142857143 | 811.3894285714285 | 90 |
| 1621 | amazon_mx | 90 | duplicado_etiqueta | 35 | t | 35 | 0 | 0 | f | 91 | 222.1528571428571429 | 1124.5422857142857143 | 811.3894285714285 | 90 |
| 1621 | amazon_mx | 180 | componentes | 67 | t | 67 | 0 | 0 | f | 91 | 240.4822388059701493 | 1121.8871641791044776 | 790.4049253731343 | 180 |
| 1621 | amazon_mx | 180 | duplicado_etiqueta | 67 | t | 67 | 0 | 0 | f | 91 | 240.4822388059701493 | 1121.8871641791044776 | 790.4049253731343 | 180 |
| 1621 | amazon_mx | 365 | componentes | 82 | t | 82 | 6 | 0 | f | 95 |  | 1142.4270731707317073 |  | 287 |
| 1621 | amazon_mx | 365 | duplicado_etiqueta | 82 | t | 82 | 6 | 0 | f | 95 |  | 1142.4270731707317073 |  | 287 |
| 1625 | amazon_mx | 90 | componentes | 16 | t | 16 | 0 | 0 | f | 91 | 218.5550000000000000 | 1248.0000000000000000 | 938.4449999999999 | 90 |
| 1625 | amazon_mx | 90 | duplicado_etiqueta | 16 | t | 16 | 0 | 0 | f | 91 | 218.5550000000000000 | 1248.0000000000000000 | 938.4449999999999 | 90 |
| 1625 | amazon_mx | 180 | componentes | 26 | t | 26 | 0 | 0 | f | 91 | 234.6992307692307692 | 1249.5838461538461538 | 923.8846153846154 | 180 |
| 1625 | amazon_mx | 180 | duplicado_etiqueta | 26 | t | 26 | 0 | 0 | f | 91 | 234.6992307692307692 | 1249.5838461538461538 | 923.8846153846154 | 180 |
| 1625 | amazon_mx | 365 | componentes | 36 | t | 36 | 1 | 0 | f | 95 |  | 1249.1438888888888889 |  | 287 |
| 1625 | amazon_mx | 365 | duplicado_etiqueta | 36 | t | 36 | 1 | 0 | f | 95 |  | 1249.1438888888888889 |  | 287 |
| 1740 | amazon_mx | 90 | componentes | 8 | t | 8 | 0 | 0 | f | 91 | 193.3700000000000000 | 978.7362500000000000 | 694.36625 | 90 |
| 1740 | amazon_mx | 90 | duplicado_etiqueta | 8 | t | 8 | 0 | 0 | f | 91 | 193.3700000000000000 | 978.7362500000000000 | 694.36625 | 90 |
| 1740 | amazon_mx | 180 | componentes | 11 | t | 11 | 0 | 0 | f | 91 | 211.6863636363636364 | 981.5354545454545455 | 678.8490909090909 | 180 |
| 1740 | amazon_mx | 180 | duplicado_etiqueta | 11 | t | 11 | 0 | 0 | f | 91 | 211.6863636363636364 | 981.5354545454545455 | 678.8490909090909 | 180 |
| 1740 | amazon_mx | 365 | componentes | 11 | t | 11 | 0 | 0 | f | 91 | 211.6863636363636364 | 981.5354545454545455 | 678.8490909090909 | 287 |
| 1740 | amazon_mx | 365 | duplicado_etiqueta | 11 | t | 11 | 0 | 0 | f | 91 | 211.6863636363636364 | 981.5354545454545455 | 678.8490909090909 | 287 |
| 263 | amazon_us | 90 | componentes | 17 | t | 17 | 0 | 0 | f | 550.18 | 548.3223529411764706 | 3102.9558823529411765 | 2004.453529411765 | 90 |
| 263 | amazon_us | 90 | duplicado_etiqueta | 17 | t | 17 | 0 | 0 | f | 541.49 | 548.3223529411764706 | 3102.9558823529411765 | 2013.1435294117648 | 90 |
| 263 | amazon_us | 180 | componentes | 21 | t | 21 | 0 | 0 | f | 553.19 | 548.0704761904761905 | 3105.0604761904761905 | 2003.7999999999997 | 180 |
| 263 | amazon_us | 180 | duplicado_etiqueta | 21 | t | 21 | 0 | 0 | f | 545.69 | 548.0704761904761905 | 3105.0604761904761905 | 2011.2999999999997 | 180 |
| 263 | amazon_us | 365 | componentes | 23 | t | 23 | 1 | 0 | f | 550.72 |  | 3078.2830434782608696 |  | 287 |
| 263 | amazon_us | 365 | duplicado_etiqueta | 23 | t | 23 | 1 | 0 | f | 543.01 |  | 3078.2830434782608696 |  | 287 |
| 273 | amazon_us | 90 | componentes | 4 | f | 4 | 0 | 0 | f | 539.71 | 547.0000000000000000 | 2616.1425000000000000 | 1529.4325 | 90 |
| 273 | amazon_us | 90 | duplicado_etiqueta | 4 | f | 4 | 0 | 0 | f | 539.71 | 547.0000000000000000 | 2616.1425000000000000 | 1529.4325 | 90 |
| 273 | amazon_us | 180 | componentes | 18 | t | 18 | 0 | 0 | f | 541.25 | 547.0000000000000000 | 2542.7722222222222222 | 1454.5222222222221 | 180 |
| 273 | amazon_us | 180 | duplicado_etiqueta | 18 | t | 18 | 0 | 0 | f | 541.25 | 547.0000000000000000 | 2542.7722222222222222 | 1454.5222222222221 | 180 |
| 273 | amazon_us | 365 | componentes | 20 | t | 20 | 0 | 0 | f | 541.225 | 547.0000000000000000 | 2571.7410000000000000 | 1483.516 | 287 |
| 273 | amazon_us | 365 | duplicado_etiqueta | 20 | t | 20 | 0 | 0 | f | 541.225 | 547.0000000000000000 | 2571.7410000000000000 | 1483.516 | 287 |
| 335 | amazon_us | 90 | componentes | 6 | t | 6 | 0 | 0 | f | 548.065 | 303.0750000000000000 | 2457.7366666666666667 | 1606.5966666666668 | 90 |
| 335 | amazon_us | 90 | duplicado_etiqueta | 6 | t | 6 | 0 | 0 | f | 538.8399999999999 | 303.0750000000000000 | 2457.7366666666666667 | 1615.821666666667 | 90 |
| 335 | amazon_us | 180 | componentes | 16 | t | 16 | 0 | 0 | f | 530.915 | 302.0906250000000000 | 2396.0756250000000000 | 1563.0700000000002 | 180 |
| 335 | amazon_us | 180 | duplicado_etiqueta | 16 | t | 16 | 0 | 0 | f | 530.915 | 302.0906250000000000 | 2396.0756250000000000 | 1563.0700000000002 | 180 |
| 335 | amazon_us | 365 | componentes | 25 | t | 25 | 4 | 0 | f | 520.19 |  | 2393.8916000000000000 |  | 287 |
| 335 | amazon_us | 365 | duplicado_etiqueta | 25 | t | 25 | 4 | 0 | f | 520.19 |  | 2393.8916000000000000 |  | 287 |
| 345 | amazon_us | 90 | componentes | 19 | t | 19 | 0 | 0 | f | 525.03 | 330.7368421052631579 | 2720.2026315789473684 | 1864.4357894736843 | 90 |
| 345 | amazon_us | 90 | duplicado_etiqueta | 19 | t | 19 | 0 | 0 | f | 521.92 | 330.7368421052631579 | 2720.2026315789473684 | 1867.5457894736842 | 90 |
| 345 | amazon_us | 180 | componentes | 32 | t | 32 | 0 | 0 | f | 528.8 | 328.8125000000000000 | 2678.6265625000000000 | 1821.0140625000001 | 180 |
| 345 | amazon_us | 180 | duplicado_etiqueta | 32 | t | 32 | 0 | 0 | f | 523.7750000000001 | 328.8125000000000000 | 2678.6265625000000000 | 1826.0390625 | 180 |
| 345 | amazon_us | 365 | componentes | 35 | t | 35 | 2 | 0 | f | 525.03 |  | 2651.3645714285714286 |  | 287 |
| 345 | amazon_us | 365 | duplicado_etiqueta | 35 | t | 35 | 2 | 0 | f | 522.94 |  | 2651.3645714285714286 |  | 287 |
| 355 | amazon_us | 90 | componentes | 12 | t | 12 | 0 | 0 | f | 532.05 | 328.5000000000000000 | 2530.1825000000000000 | 1669.6325 | 90 |
| 355 | amazon_us | 90 | duplicado_etiqueta | 12 | t | 12 | 0 | 0 | f | 526.815 | 328.5000000000000000 | 2530.1825000000000000 | 1674.8674999999998 | 90 |
| 355 | amazon_us | 180 | componentes | 23 | t | 23 | 0 | 0 | f | 534.83 | 327.3043478260869565 | 2485.1886956521739130 | 1623.054347826087 | 180 |
| 355 | amazon_us | 180 | duplicado_etiqueta | 23 | t | 23 | 0 | 0 | f | 530.05 | 327.3043478260869565 | 2485.1886956521739130 | 1627.834347826087 | 180 |
| 355 | amazon_us | 365 | componentes | 26 | t | 26 | 1 | 0 | f | 532.05 |  | 2484.6930769230769231 |  | 287 |
| 355 | amazon_us | 365 | duplicado_etiqueta | 26 | t | 26 | 1 | 0 | f | 526.815 |  | 2484.6930769230769231 |  | 287 |
| 359 | amazon_us | 90 | componentes | 15 | t | 15 | 0 | 0 | f | 548.25 | 451.3600000000000000 | 2648.9126666666666667 | 1649.3026666666665 | 90 |
| 359 | amazon_us | 90 | duplicado_etiqueta | 15 | t | 15 | 0 | 0 | f | 541.13 | 451.3600000000000000 | 2648.9126666666666667 | 1656.4226666666664 | 90 |
| 359 | amazon_us | 180 | componentes | 31 | t | 31 | 0 | 0 | f | 544.38 | 450.6580645161290323 | 2581.8425806451612903 | 1586.804516129032 | 180 |
| 359 | amazon_us | 180 | duplicado_etiqueta | 31 | t | 31 | 0 | 0 | f | 540.55 | 450.6580645161290323 | 2581.8425806451612903 | 1590.6345161290321 | 180 |
| 359 | amazon_us | 365 | componentes | 50 | t | 50 | 8 | 0 | f | 539.6800000000001 |  | 2563.3364000000000000 |  | 287 |
| 359 | amazon_us | 365 | duplicado_etiqueta | 50 | t | 50 | 8 | 0 | f | 539.59 |  | 2563.3364000000000000 |  | 287 |
| 367 | amazon_us | 90 | componentes | 11 | t | 11 | 0 | 0 | f | 966.66 | 417.4745454545454545 | 2601.9290909090909091 | 1217.7945454545456 | 90 |
| 367 | amazon_us | 90 | duplicado_etiqueta | 11 | t | 11 | 0 | 0 | f | 538.65 | 417.4745454545454545 | 2601.9290909090909091 | 1645.8045454545454 | 90 |
| 367 | amazon_us | 180 | componentes | 19 | t | 19 | 0 | 0 | f | 554.1 | 431.1694736842105263 | 2587.3621052631578947 | 1602.0926315789475 | 180 |
| 367 | amazon_us | 180 | duplicado_etiqueta | 19 | t | 19 | 0 | 0 | f | 541.49 | 431.1694736842105263 | 2587.3621052631578947 | 1614.7026315789474 | 180 |
| 367 | amazon_us | 365 | componentes | 22 | t | 22 | 1 | 0 | f | 550.28 |  | 2582.6163636363636364 |  | 287 |
| 367 | amazon_us | 365 | duplicado_etiqueta | 22 | t | 22 | 1 | 0 | f | 540.0699999999999 |  | 2582.6163636363636364 |  | 287 |
| 369 | amazon_us | 90 | componentes | 16 | t | 16 | 0 | 0 | f | 539.52 | 435.0925000000000000 | 2664.8243750000000000 | 1690.211875 | 90 |
| 369 | amazon_us | 90 | duplicado_etiqueta | 16 | t | 16 | 0 | 0 | f | 537.845 | 435.0925000000000000 | 2664.8243750000000000 | 1691.886875 | 90 |
| 369 | amazon_us | 180 | componentes | 33 | t | 33 | 0 | 0 | f | 541.9 | 442.7721212121212121 | 2626.3690909090909091 | 1641.6969696969695 | 180 |
| 369 | amazon_us | 180 | duplicado_etiqueta | 33 | t | 33 | 0 | 0 | f | 538.83 | 442.7721212121212121 | 2626.3690909090909091 | 1644.7669696969697 | 180 |
| 369 | amazon_us | 365 | componentes | 44 | t | 44 | 6 | 0 | f | 540.22 |  | 2616.6259090909090909 |  | 287 |
| 369 | amazon_us | 365 | duplicado_etiqueta | 44 | t | 44 | 6 | 0 | f | 539.59 |  | 2616.6259090909090909 |  | 287 |
| 619 | amazon_us | 90 | componentes | 5 | f | 5 | 0 | 0 | f | 972.69 | 142.0540000000000000 | 1728.9400000000000000 | 614.1959999999999 | 90 |
| 619 | amazon_us | 90 | duplicado_etiqueta | 5 | f | 5 | 0 | 0 | f | 538.65 | 142.0540000000000000 | 1728.9400000000000000 | 1048.2359999999999 | 90 |
| 619 | amazon_us | 180 | componentes | 13 | t | 13 | 0 | 0 | f | 556.89 | 202.3469230769230769 | 1683.5976923076923077 | 924.3607692307693 | 180 |
| 619 | amazon_us | 180 | duplicado_etiqueta | 13 | t | 13 | 0 | 0 | f | 538.65 | 202.3469230769230769 | 1683.5976923076923077 | 942.6007692307693 | 180 |
| 619 | amazon_us | 365 | componentes | 33 | t | 33 | 12 | 0 | f | 539.59 |  | 1565.9827272727272727 |  | 287 |
| 619 | amazon_us | 365 | duplicado_etiqueta | 33 | t | 33 | 12 | 0 | f | 539.59 |  | 1565.9827272727272727 |  | 287 |
| 1625 | amazon_us | 90 | componentes | 5 | f | 5 | 0 | 0 | f | 556.39 | 206.8020000000000000 | 2506.5360000000000000 | 1743.344 | 90 |
| 1625 | amazon_us | 90 | duplicado_etiqueta | 5 | f | 5 | 0 | 0 | f | 551.96 | 206.8020000000000000 | 2506.5360000000000000 | 1747.774 | 90 |
| 1625 | amazon_us | 180 | componentes | 13 | t | 13 | 0 | 0 | f | 554.54 | 239.8653846153846154 | 2434.8323076923076923 | 1640.4269230769232 | 180 |
| 1625 | amazon_us | 180 | duplicado_etiqueta | 13 | t | 13 | 0 | 0 | f | 551.96 | 239.8653846153846154 | 2434.8323076923076923 | 1643.0069230769232 | 180 |
| 1625 | amazon_us | 365 | componentes | 20 | t | 20 | 4 | 0 | f | 520.32 |  | 2421.1265000000000000 |  | 287 |
| 1625 | amazon_us | 365 | duplicado_etiqueta | 20 | t | 20 | 4 | 0 | f | 520.32 |  | 2421.1265000000000000 |  | 287 |


**32 de las 120 filas (16 combinaciones producto/plataforma × 2 lecturas)
tienen `ordenes_sin_costo > 0`, y las 32 son de la ventana de 365 días —
ninguna en 90 ni en 180.** En esas filas el margen sale `NULL`: **es la
regla funcionando** (regla 3 de Orbit, un dato faltante nunca es el
promedio de los conocidos), no un hueco de la consulta — a 365 días, la
vigencia de `sku_cost` de esos productos no cubre las ventas más viejas
de la ventana.

Bloque 2 (ventana fija 180 días, percentil p50/p75/p90, 40 filas = 20
productos × 2 lecturas):

| product_id | platform | lectura | ordenes | alcanza_minimo | L_p50 | L_p75 | L_p90 | dias_con_datos |
|---|---|---|---|---|---|---|---|---|
| 185 | amazon_mx | componentes | 18 | t | 91 | 91 | 95 | 180 |
| 185 | amazon_mx | duplicado_etiqueta | 18 | t | 91 | 91 | 95 | 180 |
| 203 | amazon_mx | componentes | 16 | t | 70 | 70 | 77.6 | 180 |
| 203 | amazon_mx | duplicado_etiqueta | 16 | t | 70 | 70 | 77.6 | 180 |
| 207 | amazon_mx | componentes | 25 | t | 91 | 95 | 95 | 180 |
| 207 | amazon_mx | duplicado_etiqueta | 25 | t | 91 | 95 | 95 | 180 |
| 332 | amazon_mx | componentes | 7 | t | 91 | 95 | 95 | 180 |
| 332 | amazon_mx | duplicado_etiqueta | 7 | t | 91 | 95 | 95 | 180 |
| 333 | amazon_mx | componentes | 34 | t | 95 | 95 | 95 | 180 |
| 333 | amazon_mx | duplicado_etiqueta | 34 | t | 95 | 95 | 95 | 180 |
| 335 | amazon_mx | componentes | 34 | t | 95 | 95 | 95 | 180 |
| 335 | amazon_mx | duplicado_etiqueta | 34 | t | 95 | 95 | 95 | 180 |
| 764 | amazon_mx | componentes | 9 | t | 91 | 95 | 95 | 180 |
| 764 | amazon_mx | duplicado_etiqueta | 9 | t | 91 | 95 | 95 | 180 |
| 1621 | amazon_mx | componentes | 67 | t | 91 | 95 | 95 | 180 |
| 1621 | amazon_mx | duplicado_etiqueta | 67 | t | 91 | 95 | 95 | 180 |
| 1625 | amazon_mx | componentes | 26 | t | 91 | 95 | 95 | 180 |
| 1625 | amazon_mx | duplicado_etiqueta | 26 | t | 91 | 95 | 95 | 180 |
| 1740 | amazon_mx | componentes | 11 | t | 91 | 91 | 95 | 180 |
| 1740 | amazon_mx | duplicado_etiqueta | 11 | t | 91 | 91 | 95 | 180 |
| 263 | amazon_us | componentes | 21 | t | 553.19 | 558.34 | 981.5 | 180 |
| 263 | amazon_us | duplicado_etiqueta | 21 | t | 545.69 | 553.97 | 558.34 | 180 |
| 273 | amazon_us | componentes | 18 | t | 541.25 | 554.325 | 558.555 | 180 |
| 273 | amazon_us | duplicado_etiqueta | 18 | t | 541.25 | 554.325 | 558.555 | 180 |
| 335 | amazon_us | componentes | 16 | t | 530.915 | 546.2249999999999 | 774.8199999999999 | 180 |
| 335 | amazon_us | duplicado_etiqueta | 16 | t | 530.915 | 540.235 | 549.78 | 180 |
| 345 | amazon_us | componentes | 32 | t | 528.8 | 541.045 | 954.024 | 180 |
| 345 | amazon_us | duplicado_etiqueta | 32 | t | 523.7750000000001 | 537.8575 | 540.007 | 180 |
| 355 | amazon_us | componentes | 23 | t | 534.83 | 539.9300000000001 | 542.3580000000001 | 180 |
| 355 | amazon_us | duplicado_etiqueta | 23 | t | 530.05 | 539.625 | 540.3860000000001 | 180 |
| 359 | amazon_us | componentes | 31 | t | 544.38 | 560.2550000000001 | 988.41 | 180 |
| 359 | amazon_us | duplicado_etiqueta | 31 | t | 540.55 | 545.1800000000001 | 556.89 | 180 |
| 367 | amazon_us | componentes | 19 | t | 554.1 | 968.5799999999999 | 976.356 | 180 |
| 367 | amazon_us | duplicado_etiqueta | 19 | t | 541.49 | 550.28 | 556.0120000000001 | 180 |
| 369 | amazon_us | componentes | 33 | t | 541.9 | 554.54 | 561.43 | 180 |
| 369 | amazon_us | duplicado_etiqueta | 33 | t | 538.83 | 548.52 | 559.282 | 180 |
| 619 | amazon_us | componentes | 13 | t | 556.89 | 561.63 | 980.69 | 180 |
| 619 | amazon_us | duplicado_etiqueta | 13 | t | 538.65 | 556.89 | 558.316 | 180 |
| 1625 | amazon_us | componentes | 13 | t | 554.54 | 561.63 | 894.1860000000003 | 180 |
| 1625 | amazon_us | duplicado_etiqueta | 13 | t | 551.96 | 554.72 | 560.582 | 180 |


## Lectura del lead (no es decisión; decide el dueño en E.2)

(i) La palanca es la ventana: productos que alcanzan el mínimo pasan de
7→14→17 (MX) y 9→17→22 (US) al pasar de 90 a 180 a 365 días — pero 365
días son en realidad 287 días de historia (tabla a).
(ii) La mediana del envío casi no se mueve entre ventanas: el producto
185 (MX) da `L` mediana 91 en 90, 180 y 365 días por igual (tabla j,
bloque 1).
(iii) La histéresis reduce el parpadeo del corte seco (9→2 en MX, 10→3 en
US, tabla i) pero ninguna "salida" completó todavía una reentrada.
(iv) El único rezago que sí se mide es el de ingesta incremental: p90
4.2 días / máximo 7 en MX, p90 13 / máximo 16 en US (tabla h); el de
emisión del hecho 15 no se reproduce con ninguna fecha de Orbit (tabla g).
(v) De 54 pares de etiqueta en US, 53 difieren ≤1% (casi idénticos) y
solo 1 difiere >5% (tabla f) — insumo directo de E.0, no un veredicto.
(vi) 86 órdenes en MX y 62 en US tienen venta sin `product_id` (tabla e).

## Supuestos declarados (ya medidos, dejan de ser supuesto)

- La identidad de fuente (`shipping_label` / `finance:<subtipo>`) YA ESTÁ
  MEDIDA (ver "Formato real medido por el lead"). Deja de ser supuesto.
- `parpadeo.sql` sigue desplazando las seis ventanas 30 días entre sí; el
  desplazamiento exacto del hallazgo 8 de `plan-validacion-ebm.md` sigue
  sin estar escrito en ninguna fuente disponible.
- "FBM" en `efecto-margen.sql` se sigue aproximando como "tiene al menos
  una orden en la muestra de envío", porque el canal no vive en las
  tablas leídas por E.1.
- El margen de `efecto-margen.sql` sigue siendo contribución simplificada
  en MXN; no es comparable contra un goal de margen de la fase B.
- El CTE de costo por orden sigue repetido literal en 10
  consultas/bloques (residual, hallazgo 17).
- La sonda de formato sigue sin frenar la corrida (residual, hallazgo
  27): informa, el freno es humano.
- El patrón de conexión de `correr.sh` (DSN como argumento vía
  `ORBIT_DSN_READ`) sigue el runbook de la fase; no se cambió (decisión
  del lead, hallazgo 11).
