# E.0a: veredicto sobre las fuentes de `shipping_fee` (REPRICING 01)

**Qué es este documento.** Es el entregable de la fila **E.0a** del plan `plans/repricing-01.md` v1.3 y el **insumo de E.0b** (la regla de costo por orden en la ingesta) **y de la parte 2 del acta E.2** (el valor del envío y el ingreso por envío). Lo escribió el lead de la Fase 10 del autopilot (runbook `docs/runbooks/autopilot-fase10.md` de goncloud-openclaw, carril D), **solo con lecturas**: rol lector `ORBIT_DSN_READ`, cada consulta dentro de `BEGIN READ ONLY … ROLLBACK`, con el corredor `correr.sh` de esta carpeta.

**Estado: veredicto parcial.** El documento de origen que el plan exige (la vista de transacciones de Seller Central, o la contabilidad que alimenta el ledger) **no existe todavía**: la carpeta `/Users/dn/dev/orbit-insumos/E.0/` no existe al 2026-09-18 (`ls … | wc -l` → `0`). Por regla del runbook, **ningún par recibe `duplicado` ni `componentes distintos`**. Los tres quedan `sin veredicto`, y abajo dice qué documento resuelve cada uno. Todo lo demás ya está medido: la lista literal de órdenes, cada cargo con su identidad completa, las hipótesis con su fuente y la regla de costo que resulta de cada veredicto posible. Con el documento en mano, el veredicto se resuelve en una tarde.

**Corrida.** `salidas/CORRIDA.txt`: `estado: COMPLETA`, 2026-09-18T10:34:35Z a 2026-09-18T10:34:57Z UTC, `commit_del_repo: 9f028f7`, `consultas_con_cambios_sin_commitear: 0`. «Hoy» es la fecha UTC de esa corrida, **2026-09-18**. La ventana de 90 días es `[2026-09-18 − 90, 2026-09-18]` = `[2026-06-20, 2026-09-18]`. Cada tabla cita el `.sql` de `consultas/` que la produjo, y su salida literal está en `salidas/<mismo nombre>.txt`.

---

## Resumen en una página

1. **Las «18 órdenes de US con tres cargos» del plan hoy son 54.** El número del plan (hecho 14, medido el 2026-09-16) era un efecto del **rezago de ingesta** del reporte de etiquetas: de las 54 etiquetas `shipping_label` de esas órdenes, **36 entraron en la corrida de ingesta del 2026-09-17** (columna `observado` de la tabla 2.3), un día después de aquella medición. 54 − 36 = 18. Las 54 órdenes son **exactamente** las que traen `finance:LabmanLabelPurchase` **y** `shipping_label` (tablas 2.2 y 2.4).
2. **`finance:LabmanLabelPurchase` es una fuente nueva: empieza el 2026-08-18 en US** (y el 2026-09-07 en MX, 3 filas), mientras `shipping_label` y `finance:ShippingHB` traen **una fila por orden cada mes desde diciembre de 2025** (tabla 2.7). En septiembre, **las tres fuentes de US traen 40 órdenes cada una**.
3. **El par Labman / `shipping_label` casa casi siempre**: 54 pares, **32 idénticos al centavo**, 53 a ≤ 1 % y 1 a más de 5 %. La etiqueta lleva de 0 a 3 días de fecha después del Labman (tabla 2.4). Reproduce la segunda parte del hecho 22 del plan (53 de 54 pares a ≤ 1 %).
4. **El par `ShippingHB` / `shipping_label` no casa nunca**: ShippingHB vale del 6.1 % al 20.5 % de la etiqueta en la muestra de 20 (tabla 2.5). **El par Labman / `ShippingHB` tampoco**: 0 de 57 en US a ≤ 5 % (tabla 2.6).
5. **Lectura provisional, sin veredicto:** los números son **compatibles con** «Labman y `shipping_label` son el mismo cobro de etiqueta informado por dos caminos desde el 2026-08-18» y con «ShippingHB es otro componente (la retención de Amazon sobre el envío que cobra el vendedor)». **Compatible no es veredicto**: un monto idéntico no distingue «el mismo cargo dos veces» de «dos cargos del mismo importe» (por ejemplo, una etiqueta y su reposición). Eso solo lo dice el documento.
6. **Los descartes por convención de signos son 105–112 por corrida** (promedio 107.2 en 27 corridas de la ingesta contable, 90 días; tabla 6). **Orbit no guarda su plataforma ni su `fee_type`**: esa clasificación exige leer la base de contabilidad, fuera del permiso de esta fase. La consulta está lista (sección 6).

**Veredicto de los tres pares: `sin veredicto`** (tabla de la sección 4, con la razón y el documento que resuelve cada uno).

**Qué tiene que hacer David para cerrar la fila:** exportar desde Seller Central (Estados Unidos) la vista de transacciones (*Payments → Transaction view*, o el detalle de la orden en *Manage Orders → Order details → Transactions*) de **cada una de las 54 órdenes de la lista de la sección 7**, y dejar los archivos en `/Users/dn/dev/orbit-insumos/E.0/`. Con eso, el lead de la siguiente fase compara cargo por cargo y dicta los tres veredictos.

---

## 1. Insumos

| Insumo | Estado | Consecuencia |
|---|---|---|
| Documento de origen (Seller Central o contabilidad) en `/Users/dn/dev/orbit-insumos/E.0/` | **no existe** (`0` archivos al 2026-09-18) | los tres pares, `sin veredicto` |
| Ledger de Orbit (`ledger_event`, `ingest_run`) | leído en producción, rol lector, `READ ONLY` | tablas 2.1 a 2.7 y 6 |
| Documentación pública de la SP-API | leída con la red disponible; lo que no se encontró queda `unknown` con la URL | sección 3 |
| Sonda de Finances del repo (`docs/evidencia/margen-estimado-01/0.2/finances-probe-2026-09-08.json`) | leída | sección 3 |
| Base de contabilidad (`/mnt/data/appdata/accounting/data/accounting.db` en `goncloud`) | **no leída**: no es la base de Orbit ni el rol lector, y la preaprobación de la fase es «solo `SELECT`, solo por el rol lector» | clasificación de descartes por signo `unknown` (sección 6) |

## 2. Lo medido

### 2.1 Fuentes de `shipping_fee` en la ventana de 90 días — `consultas/00-sonda-formato-90d.sql`

Montos en MXN y con signo negativo, por la convención del ledger. Todo el ledger está en MXN, incluido `amazon_us` (ver `docs/evidencia/margen-estimado-01/0.2/reporte.md`).

| plataforma | kind | identidad | partes | moneda | filas | órdenes | filas sin orden | suma | promedio |
|---|---|---|---|---|---|---|---|---|---|
| amazon_mx | fee | finance:LabmanLabelPurchase | 6 | MXN | 3 | 3 | 0 | -379.2700 | -126.42 |
| amazon_mx | fee | finance:MFNPostageFee | 6 | MXN | 180 | 180 | 0 | -15469.0000 | -85.94 |
| amazon_mx | fee | finance:MFNShippingChargeback | 6 | MXN | 1 | 1 | 0 | -44.0300 | -44.03 |
| amazon_mx | fee | finance:ShippingChargeback | 6 | MXN | 2 | 2 | 0 | -88.0000 | -44.00 |
| amazon_mx | fee | finance:ShippingHB | 6 | MXN | 9 | 9 | 0 | -112.2300 | -12.47 |
| amazon_mx | fee | shipping_label | 4 | MXN | 6 | 6 | 0 | -682.5300 | -113.76 |
| amazon_us | fee | finance:LabmanLabelPurchase | 6 | MXN | 57 | 57 | 0 | -25233.8300 | -442.70 |
| amazon_us | fee | finance:ShippingHB | 6 | MXN | 173 | 173 | 0 | -14743.2600 | -85.22 |
| amazon_us | fee | shipping_label | 4 | MXN | 170 | 170 | 0 | -78143.6600 | -459.67 |

Forma literal de `source_event_id`, un ejemplo por identidad (el de menor `id`):

| plataforma | identidad | `source_event_id` | event_date | monto | moneda |
|---|---|---|---|---|---|
| amazon_mx | finance:LabmanLabelPurchase | `amazon\|finance\|fee\|701-2817823-2361800\|\|LabmanLabelPurchase` | 2026-09-07 | -212.3400 | MXN |
| amazon_mx | finance:MFNPostageFee | `amazon\|finance\|fee\|701-4257300-4364269\|\|MFNPostageFee` | 2026-06-20 | -91.0000 | MXN |
| amazon_mx | finance:MFNShippingChargeback | `amazon\|finance\|fee\|702-9891228-8684241\|\|MFNShippingChargeback` | 2026-08-25 | -44.0300 | MXN |
| amazon_mx | finance:ShippingChargeback | `amazon\|finance\|fee\|701-3970925-8755408\|AJ-EX0N-9H0U\|ShippingChargeback` | 2026-07-03 | -44.0000 | MXN |
| amazon_mx | finance:ShippingHB | `amazon\|finance\|fee\|702-0755266-8150655\|PI-5SUZ-85ZP\|ShippingHB` | 2026-07-07 | -12.5300 | MXN |
| amazon_mx | shipping_label | `amazon\|shipping_label\|701-4878182-1526602\|2026-07-13` | 2026-07-13 | -73.1100 | MXN |
| amazon_us | finance:LabmanLabelPurchase | `amazon_us\|finance\|fee\|114-5372309-4692239\|\|LabmanLabelPurchase` | 2026-08-18 | -446.4600 | MXN |
| amazon_us | finance:ShippingHB | `amazon_us\|finance\|fee\|111-1950993-1017060\|9P-C0NN-HCR9\|ShippingHB` | 2026-06-20 | -90.5000 | MXN |
| amazon_us | shipping_label | `amazon_us\|shipping_label\|111-1950993-1017060\|2026-06-22` | 2026-06-22 | -467.4200 | MXN |

Las dos formas medidas en E.1 se confirman: `finance`, seis partes, `<plataforma>|finance|fee|<order_id>|<sku o vacío>|<subtipo>`; `shipping_label`, cuatro partes, `<plataforma>|shipping_label|<order_id>|<fecha>`. **Ninguna trae un id de evento de Amazon**: la llave `finance` es orden + SKU + subtipo (sin fecha) y la de `shipping_label` es orden + día. Eso importa para E.0b: como `ledger_event` deduplica por `(platform, kind, source_event_id)` (`app/ledger.py`, `ON CONFLICT … DO NOTHING`), dos cargos `finance` del mismo subtipo en la misma orden y SKU, aunque sean de días distintos, o dos etiquetas de la misma orden el mismo día, **se quedan en uno**: el segundo cuenta como «conflicto dedupe» y no entra. La llave la arma la contabilidad, no Orbit.

### 2.2 Órdenes por número de cargos y por combinación de fuentes (90 días) — `consultas/01-ordenes-por-numero-de-cargos-90d.sql`

| plataforma | cargos por orden | órdenes |
|---|---|---|
| amazon_mx | 1 | 181 |
| amazon_mx | 2 | 10 |
| amazon_us | 2 | 119 |
| amazon_us | 3 | 54 |

| plataforma | combinación de fuentes | órdenes |
|---|---|---|
| amazon_mx | `finance:MFNPostageFee` | 179 |
| amazon_mx | `finance:ShippingHB + shipping_label` | 6 |
| amazon_mx | `finance:LabmanLabelPurchase + finance:ShippingHB` | 3 |
| amazon_mx | `finance:ShippingChargeback` | 2 |
| amazon_mx | `finance:MFNPostageFee + finance:MFNShippingChargeback` | 1 |
| amazon_us | `finance:ShippingHB + shipping_label` | 116 |
| amazon_us | `finance:LabmanLabelPurchase + finance:ShippingHB + shipping_label` | 54 |
| amazon_us | `finance:LabmanLabelPurchase + finance:ShippingHB` | 3 |

### 2.3 Las 54 órdenes de US con tres cargos, cargo por cargo — `consultas/02-ordenes-us-tres-cargos-desglose.sql`

`observado` es el día en que la ingesta trajo la fila y `corrida` es su `ingest_run`. La columna «documento de origen» es la **hipótesis** de la sección 3, no un hecho.

| orden | identidad | `source_event_id` | fee_type | monto | moneda | event_date | observado | corrida | documento de origen (hipótesis) |
|---|---|---|---|---|---|---|---|---|---|
| 111-0040443-2266607 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-0040443-2266607\|\|LabmanLabelPurchase` | shipping_fee | -452.6900 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 111-0040443-2266607 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-0040443-2266607\|9P-C0NN-HCR9\|ShippingHB` | shipping_fee | -88.8000 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 111-0040443-2266607 | `shipping_label` | `amazon_us\|shipping_label\|111-0040443-2266607\|2026-09-04` | shipping_fee | -449.6900 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 111-0818188-2803467 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-0818188-2803467\|\|LabmanLabelPurchase` | shipping_fee | -127.1200 | MXN | 2026-09-05 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 111-0818188-2803467 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-0818188-2803467\|VR-QI3Y-C9WW\|ShippingHB` | shipping_fee | -102.9900 | MXN | 2026-09-05 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 111-0818188-2803467 | `shipping_label` | `amazon_us\|shipping_label\|111-0818188-2803467\|2026-09-07` | shipping_fee | -2149.8700 | MXN | 2026-09-07 | 2026-09-09 | 137 | reporte de etiquetas de Buy Shipping |
| 111-0868713-1444220 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-0868713-1444220\|\|LabmanLabelPurchase` | shipping_fee | -452.6900 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 111-0868713-1444220 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-0868713-1444220\|57-SQKK-1FZD\|ShippingHB` | shipping_fee | -88.8000 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 111-0868713-1444220 | `shipping_label` | `amazon_us\|shipping_label\|111-0868713-1444220\|2026-09-04` | shipping_fee | -449.6900 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 111-1163630-1763411 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-1163630-1763411\|\|LabmanLabelPurchase` | shipping_fee | -432.0700 | MXN | 2026-08-20 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 111-1163630-1763411 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-1163630-1763411\|5J-VRP9-WMNO\|ShippingHB` | shipping_fee | -88.6900 | MXN | 2026-08-20 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 111-1163630-1763411 | `shipping_label` | `amazon_us\|shipping_label\|111-1163630-1763411\|2026-08-21` | shipping_fee | -429.7200 | MXN | 2026-08-21 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 111-2217269-8321827 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-2217269-8321827\|\|LabmanLabelPurchase` | shipping_fee | -437.0500 | MXN | 2026-09-11 | 2026-09-15 | 254 | Finances, camino de etiqueta nuevo |
| 111-2217269-8321827 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-2217269-8321827\|L3-Y2YN-IRYX\|ShippingHB` | shipping_fee | -74.2600 | MXN | 2026-09-11 | 2026-09-15 | 254 | Finances, retención sobre el envío |
| 111-2217269-8321827 | `shipping_label` | `amazon_us\|shipping_label\|111-2217269-8321827\|2026-09-14` | shipping_fee | -437.0500 | MXN | 2026-09-14 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 111-3323353-2869018 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-3323353-2869018\|\|LabmanLabelPurchase` | shipping_fee | -450.3700 | MXN | 2026-09-04 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 111-3323353-2869018 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-3323353-2869018\|DZ-4DLR-XZUE\|ShippingHB` | shipping_fee | -73.5700 | MXN | 2026-09-04 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 111-3323353-2869018 | `shipping_label` | `amazon_us\|shipping_label\|111-3323353-2869018\|2026-09-07` | shipping_fee | -450.3700 | MXN | 2026-09-07 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 111-3622584-1220235 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-3622584-1220235\|\|LabmanLabelPurchase` | shipping_fee | -449.9700 | MXN | 2026-08-27 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 111-3622584-1220235 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-3622584-1220235\|UT-6NDI-O3IP\|ShippingHB` | shipping_fee | -88.4700 | MXN | 2026-08-27 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 111-3622584-1220235 | `shipping_label` | `amazon_us\|shipping_label\|111-3622584-1220235\|2026-08-28` | shipping_fee | -449.9700 | MXN | 2026-08-28 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 111-4577498-1639410 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-4577498-1639410\|\|LabmanLabelPurchase` | shipping_fee | -452.3900 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 111-4577498-1639410 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-4577498-1639410\|2W-B7K3-MMOK\|ShippingHB` | shipping_fee | -88.7400 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 111-4577498-1639410 | `shipping_label` | `amazon_us\|shipping_label\|111-4577498-1639410\|2026-09-02` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 111-5080630-3746660 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-5080630-3746660\|\|LabmanLabelPurchase` | shipping_fee | -452.6900 | MXN | 2026-09-03 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 111-5080630-3746660 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-5080630-3746660\|VR-QI3Y-C9WW\|ShippingHB` | shipping_fee | -88.8000 | MXN | 2026-09-03 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 111-5080630-3746660 | `shipping_label` | `amazon_us\|shipping_label\|111-5080630-3746660\|2026-09-04` | shipping_fee | -449.6900 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 111-8285600-1114613 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|111-8285600-1114613\|\|LabmanLabelPurchase` | shipping_fee | -466.5800 | MXN | 2026-09-15 | 2026-09-16 | 273 | Finances, camino de etiqueta nuevo |
| 111-8285600-1114613 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|111-8285600-1114613\|JH-ICBG-83U8\|ShippingHB` | shipping_fee | -89.1200 | MXN | 2026-09-15 | 2026-09-16 | 273 | Finances, retención sobre el envío |
| 111-8285600-1114613 | `shipping_label` | `amazon_us\|shipping_label\|111-8285600-1114613\|2026-09-15` | shipping_fee | -468.9000 | MXN | 2026-09-15 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 112-0034856-6910644 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-0034856-6910644\|\|LabmanLabelPurchase` | shipping_fee | -437.0500 | MXN | 2026-09-13 | 2026-09-15 | 254 | Finances, camino de etiqueta nuevo |
| 112-0034856-6910644 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-0034856-6910644\|ID-CCI7-XGML\|ShippingHB` | shipping_fee | -89.1200 | MXN | 2026-09-13 | 2026-09-15 | 254 | Finances, retención sobre el envío |
| 112-0034856-6910644 | `shipping_label` | `amazon_us\|shipping_label\|112-0034856-6910644\|2026-09-14` | shipping_fee | -437.0500 | MXN | 2026-09-14 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 112-1184008-4055445 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-1184008-4055445\|\|LabmanLabelPurchase` | shipping_fee | -452.6900 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 112-1184008-4055445 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-1184008-4055445\|YU-EPEN-SB3Y\|ShippingHB` | shipping_fee | -74.0000 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 112-1184008-4055445 | `shipping_label` | `amazon_us\|shipping_label\|112-1184008-4055445\|2026-09-04` | shipping_fee | -449.6900 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 112-3494183-9567456 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-3494183-9567456\|\|LabmanLabelPurchase` | shipping_fee | -452.6900 | MXN | 2026-09-03 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 112-3494183-9567456 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-3494183-9567456\|VR-QI3Y-C9WW\|ShippingHB` | shipping_fee | -88.8000 | MXN | 2026-09-03 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 112-3494183-9567456 | `shipping_label` | `amazon_us\|shipping_label\|112-3494183-9567456\|2026-09-04` | shipping_fee | -449.6900 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 112-3596235-4597059 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-3596235-4597059\|\|LabmanLabelPurchase` | shipping_fee | -450.6400 | MXN | 2026-08-27 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 112-3596235-4597059 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-3596235-4597059\|UT-6NDI-O3IP\|ShippingHB` | shipping_fee | -73.8300 | MXN | 2026-08-27 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 112-3596235-4597059 | `shipping_label` | `amazon_us\|shipping_label\|112-3596235-4597059\|2026-08-27` | shipping_fee | -450.6400 | MXN | 2026-08-27 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 112-6174413-7626643 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-6174413-7626643\|\|LabmanLabelPurchase` | shipping_fee | -450.3700 | MXN | 2026-09-07 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 112-6174413-7626643 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-6174413-7626643\|F2-QTYV-7A5N\|ShippingHB` | shipping_fee | -88.2800 | MXN | 2026-09-07 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 112-6174413-7626643 | `shipping_label` | `amazon_us\|shipping_label\|112-6174413-7626643\|2026-09-07` | shipping_fee | -450.3700 | MXN | 2026-09-07 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 112-6443860-1744200 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-6443860-1744200\|\|LabmanLabelPurchase` | shipping_fee | -451.2100 | MXN | 2026-09-10 | 2026-09-12 | 197 | Finances, camino de etiqueta nuevo |
| 112-6443860-1744200 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-6443860-1744200\|2W-B7K3-MMOK\|ShippingHB` | shipping_fee | -88.4500 | MXN | 2026-09-10 | 2026-09-12 | 197 | Finances, retención sobre el envío |
| 112-6443860-1744200 | `shipping_label` | `amazon_us\|shipping_label\|112-6443860-1744200\|2026-09-11` | shipping_fee | -452.1000 | MXN | 2026-09-11 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 112-8779521-3393860 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-8779521-3393860\|\|LabmanLabelPurchase` | shipping_fee | -448.4300 | MXN | 2026-08-22 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 112-8779521-3393860 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-8779521-3393860\|PY-CVMD-FI7W\|ShippingHB` | shipping_fee | -73.6400 | MXN | 2026-08-22 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 112-8779521-3393860 | `shipping_label` | `amazon_us\|shipping_label\|112-8779521-3393860\|2026-08-24` | shipping_fee | -448.4300 | MXN | 2026-08-24 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 112-8838991-4635437 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-8838991-4635437\|\|LabmanLabelPurchase` | shipping_fee | -449.5200 | MXN | 2026-08-25 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 112-8838991-4635437 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-8838991-4635437\|2W-B7K3-MMOK\|ShippingHB` | shipping_fee | -73.6500 | MXN | 2026-08-25 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 112-8838991-4635437 | `shipping_label` | `amazon_us\|shipping_label\|112-8838991-4635437\|2026-08-26` | shipping_fee | -449.5200 | MXN | 2026-08-26 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 112-8879394-1986653 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|112-8879394-1986653\|\|LabmanLabelPurchase` | shipping_fee | -447.9700 | MXN | 2026-09-14 | 2026-09-16 | 273 | Finances, camino de etiqueta nuevo |
| 112-8879394-1986653 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|112-8879394-1986653\|S5-P3XQ-A6A6\|ShippingHB` | shipping_fee | -89.1200 | MXN | 2026-09-14 | 2026-09-16 | 273 | Finances, retención sobre el envío |
| 112-8879394-1986653 | `shipping_label` | `amazon_us\|shipping_label\|112-8879394-1986653\|2026-09-15` | shipping_fee | -450.1900 | MXN | 2026-09-15 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-0929035-4394624 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-0929035-4394624\|\|LabmanLabelPurchase` | shipping_fee | -452.6900 | MXN | 2026-09-03 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 113-0929035-4394624 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-0929035-4394624\|PV-AVJL-I78M\|ShippingHB` | shipping_fee | -74.0000 | MXN | 2026-09-03 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 113-0929035-4394624 | `shipping_label` | `amazon_us\|shipping_label\|113-0929035-4394624\|2026-09-04` | shipping_fee | -449.6900 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-1624244-7881060 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-1624244-7881060\|\|LabmanLabelPurchase` | shipping_fee | -434.2000 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 113-1624244-7881060 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-1624244-7881060\|ID-CCI7-XGML\|ShippingHB` | shipping_fee | -88.7400 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 113-1624244-7881060 | `shipping_label` | `amazon_us\|shipping_label\|113-1624244-7881060\|2026-09-02` | shipping_fee | -434.2000 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-1641125-1029034 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-1641125-1029034\|\|LabmanLabelPurchase` | shipping_fee | -449.5200 | MXN | 2026-08-26 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 113-1641125-1029034 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-1641125-1029034\|UN-G9LH-KH5G\|ShippingHB` | shipping_fee | -73.6500 | MXN | 2026-08-26 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 113-1641125-1029034 | `shipping_label` | `amazon_us\|shipping_label\|113-1641125-1029034\|2026-08-26` | shipping_fee | -449.5200 | MXN | 2026-08-26 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 113-1907141-0514600 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-1907141-0514600\|\|LabmanLabelPurchase` | shipping_fee | -451.2100 | MXN | 2026-09-10 | 2026-09-12 | 197 | Finances, camino de etiqueta nuevo |
| 113-1907141-0514600 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-1907141-0514600\|I5-0XZ9-12IO\|ShippingHB` | shipping_fee | -88.4500 | MXN | 2026-09-10 | 2026-09-12 | 197 | Finances, retención sobre el envío |
| 113-1907141-0514600 | `shipping_label` | `amazon_us\|shipping_label\|113-1907141-0514600\|2026-09-11` | shipping_fee | -452.1000 | MXN | 2026-09-11 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-2811291-5425028 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-2811291-5425028\|\|LabmanLabelPurchase` | shipping_fee | -434.2000 | MXN | 2026-09-03 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 113-2811291-5425028 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-2811291-5425028\|3D-7NIJ-3BGK\|ShippingHB` | shipping_fee | -88.7400 | MXN | 2026-09-03 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 113-2811291-5425028 | `shipping_label` | `amazon_us\|shipping_label\|113-2811291-5425028\|2026-09-03` | shipping_fee | -434.4800 | MXN | 2026-09-03 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-4596105-9681840 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-4596105-9681840\|\|LabmanLabelPurchase` | shipping_fee | -451.4100 | MXN | 2026-09-08 | 2026-09-10 | 159 | Finances, camino de etiqueta nuevo |
| 113-4596105-9681840 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-4596105-9681840\|2W-B7K3-MMOK\|ShippingHB` | shipping_fee | -73.7400 | MXN | 2026-09-08 | 2026-09-10 | 159 | Finances, retención sobre el envío |
| 113-4596105-9681840 | `shipping_label` | `amazon_us\|shipping_label\|113-4596105-9681840\|2026-09-09` | shipping_fee | -450.1100 | MXN | 2026-09-09 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-4810240-3846666 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-4810240-3846666\|\|LabmanLabelPurchase` | shipping_fee | -448.4300 | MXN | 2026-08-23 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 113-4810240-3846666 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-4810240-3846666\|PV-AVJL-I78M\|ShippingHB` | shipping_fee | -88.3700 | MXN | 2026-08-23 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 113-4810240-3846666 | `shipping_label` | `amazon_us\|shipping_label\|113-4810240-3846666\|2026-08-24` | shipping_fee | -448.4300 | MXN | 2026-08-24 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 113-5725957-8297853 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-5725957-8297853\|\|LabmanLabelPurchase` | shipping_fee | -450.3700 | MXN | 2026-09-06 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 113-5725957-8297853 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-5725957-8297853\|PY-CVMD-FI7W\|ShippingHB` | shipping_fee | -73.5700 | MXN | 2026-09-06 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 113-5725957-8297853 | `shipping_label` | `amazon_us\|shipping_label\|113-5725957-8297853\|2026-09-07` | shipping_fee | -450.3700 | MXN | 2026-09-07 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-6664176-4990621 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-6664176-4990621\|\|LabmanLabelPurchase` | shipping_fee | -446.4600 | MXN | 2026-08-18 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 113-6664176-4990621 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-6664176-4990621\|9P-C0NN-HCR9\|ShippingHB` | shipping_fee | -89.0200 | MXN | 2026-08-18 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 113-6664176-4990621 | `shipping_label` | `amazon_us\|shipping_label\|113-6664176-4990621\|2026-08-19` | shipping_fee | -446.0200 | MXN | 2026-08-19 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 113-7945413-9549847 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-7945413-9549847\|\|LabmanLabelPurchase` | shipping_fee | -451.4100 | MXN | 2026-09-07 | 2026-09-09 | 137 | Finances, camino de etiqueta nuevo |
| 113-7945413-9549847 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-7945413-9549847\|I5-0XZ9-12IO\|ShippingHB` | shipping_fee | -88.4800 | MXN | 2026-09-07 | 2026-09-09 | 137 | Finances, retención sobre el envío |
| 113-7945413-9549847 | `shipping_label` | `amazon_us\|shipping_label\|113-7945413-9549847\|2026-09-08` | shipping_fee | -451.4100 | MXN | 2026-09-08 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 113-8552624-7805017 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|113-8552624-7805017\|\|LabmanLabelPurchase` | shipping_fee | -448.4300 | MXN | 2026-08-22 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 113-8552624-7805017 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|113-8552624-7805017\|PY-CVMD-FI7W\|ShippingHB` | shipping_fee | -73.6400 | MXN | 2026-08-22 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 113-8552624-7805017 | `shipping_label` | `amazon_us\|shipping_label\|113-8552624-7805017\|2026-08-24` | shipping_fee | -448.4300 | MXN | 2026-08-24 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-0083672-7380279 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-0083672-7380279\|\|LabmanLabelPurchase` | shipping_fee | -431.5700 | MXN | 2026-08-25 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-0083672-7380279 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-0083672-7380279\|7Q-518K-IKVE\|ShippingHB` | shipping_fee | -88.3800 | MXN | 2026-08-25 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-0083672-7380279 | `shipping_label` | `amazon_us\|shipping_label\|114-0083672-7380279\|2026-08-26` | shipping_fee | -431.5700 | MXN | 2026-08-26 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-0106578-7610669 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-0106578-7610669\|\|LabmanLabelPurchase` | shipping_fee | -452.3900 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 114-0106578-7610669 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-0106578-7610669\|ST-MV02-LRFL\|ShippingHB` | shipping_fee | -73.9500 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 114-0106578-7610669 | `shipping_label` | `amazon_us\|shipping_label\|114-0106578-7610669\|2026-09-02` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-0761571-9993866 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-0761571-9993866\|\|LabmanLabelPurchase` | shipping_fee | -449.5200 | MXN | 2026-08-26 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-0761571-9993866 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-0761571-9993866\|UT-6NDI-O3IP\|ShippingHB` | shipping_fee | -88.3800 | MXN | 2026-08-26 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-0761571-9993866 | `shipping_label` | `amazon_us\|shipping_label\|114-0761571-9993866\|2026-08-26` | shipping_fee | -449.5200 | MXN | 2026-08-26 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-0965980-4478658 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-0965980-4478658\|\|LabmanLabelPurchase` | shipping_fee | -451.2100 | MXN | 2026-09-10 | 2026-09-12 | 197 | Finances, camino de etiqueta nuevo |
| 114-0965980-4478658 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-0965980-4478658\|2W-B7K3-MMOK\|ShippingHB` | shipping_fee | -88.4500 | MXN | 2026-09-10 | 2026-09-12 | 197 | Finances, retención sobre el envío |
| 114-0965980-4478658 | `shipping_label` | `amazon_us\|shipping_label\|114-0965980-4478658\|2026-09-11` | shipping_fee | -452.1000 | MXN | 2026-09-11 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-1410350-4190617 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-1410350-4190617\|\|LabmanLabelPurchase` | shipping_fee | -451.2100 | MXN | 2026-09-11 | 2026-09-12 | 197 | Finances, camino de etiqueta nuevo |
| 114-1410350-4190617 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-1410350-4190617\|K8-KF5Z-H8MT\|ShippingHB` | shipping_fee | -88.4500 | MXN | 2026-09-11 | 2026-09-12 | 197 | Finances, retención sobre el envío |
| 114-1410350-4190617 | `shipping_label` | `amazon_us\|shipping_label\|114-1410350-4190617\|2026-09-11` | shipping_fee | -452.1000 | MXN | 2026-09-11 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-1534882-5053008 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-1534882-5053008\|\|LabmanLabelPurchase` | shipping_fee | -450.1100 | MXN | 2026-09-09 | 2026-09-11 | 178 | Finances, camino de etiqueta nuevo |
| 114-1534882-5053008 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-1534882-5053008\|JH-ICBG-83U8\|ShippingHB` | shipping_fee | -88.2300 | MXN | 2026-09-09 | 2026-09-11 | 178 | Finances, retención sobre el envío |
| 114-1534882-5053008 | `shipping_label` | `amazon_us\|shipping_label\|114-1534882-5053008\|2026-09-10` | shipping_fee | -451.2100 | MXN | 2026-09-10 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-2030282-2885022 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-2030282-2885022\|\|LabmanLabelPurchase` | shipping_fee | -450.6400 | MXN | 2026-08-26 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-2030282-2885022 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-2030282-2885022\|0X-THNZ-4W2E\|ShippingHB` | shipping_fee | -73.8300 | MXN | 2026-08-26 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-2030282-2885022 | `shipping_label` | `amazon_us\|shipping_label\|114-2030282-2885022\|2026-08-27` | shipping_fee | -450.6400 | MXN | 2026-08-27 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-2631069-0829830 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-2631069-0829830\|\|LabmanLabelPurchase` | shipping_fee | -437.0500 | MXN | 2026-09-12 | 2026-09-15 | 254 | Finances, camino de etiqueta nuevo |
| 114-2631069-0829830 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-2631069-0829830\|ID-CCI7-XGML\|ShippingHB` | shipping_fee | -89.1200 | MXN | 2026-09-12 | 2026-09-15 | 254 | Finances, retención sobre el envío |
| 114-2631069-0829830 | `shipping_label` | `amazon_us\|shipping_label\|114-2631069-0829830\|2026-09-14` | shipping_fee | -437.0500 | MXN | 2026-09-14 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-2885038-7129058 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-2885038-7129058\|\|LabmanLabelPurchase` | shipping_fee | -450.3700 | MXN | 2026-09-04 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 114-2885038-7129058 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-2885038-7129058\|UN-4O7B-FJ96\|ShippingHB` | shipping_fee | -88.2800 | MXN | 2026-09-04 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 114-2885038-7129058 | `shipping_label` | `amazon_us\|shipping_label\|114-2885038-7129058\|2026-09-07` | shipping_fee | -450.3700 | MXN | 2026-09-07 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-4068501-4322654 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-4068501-4322654\|\|LabmanLabelPurchase` | shipping_fee | -431.5700 | MXN | 2026-08-25 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-4068501-4322654 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-4068501-4322654\|7Q-518K-IKVE\|ShippingHB` | shipping_fee | -88.3800 | MXN | 2026-08-25 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-4068501-4322654 | `shipping_label` | `amazon_us\|shipping_label\|114-4068501-4322654\|2026-08-26` | shipping_fee | -431.5700 | MXN | 2026-08-26 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-4681368-9831437 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-4681368-9831437\|\|LabmanLabelPurchase` | shipping_fee | -432.4400 | MXN | 2026-09-05 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 114-4681368-9831437 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-4681368-9831437\|ID-CCI7-XGML\|ShippingHB` | shipping_fee | -88.2800 | MXN | 2026-09-05 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 114-4681368-9831437 | `shipping_label` | `amazon_us\|shipping_label\|114-4681368-9831437\|2026-09-07` | shipping_fee | -432.4400 | MXN | 2026-09-07 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-4913582-6944237 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-4913582-6944237\|\|LabmanLabelPurchase` | shipping_fee | -433.4400 | MXN | 2026-09-08 | 2026-09-10 | 159 | Finances, camino de etiqueta nuevo |
| 114-4913582-6944237 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-4913582-6944237\|ID-CCI7-XGML\|ShippingHB` | shipping_fee | -88.4800 | MXN | 2026-09-08 | 2026-09-10 | 159 | Finances, retención sobre el envío |
| 114-4913582-6944237 | `shipping_label` | `amazon_us\|shipping_label\|114-4913582-6944237\|2026-09-09` | shipping_fee | -432.2000 | MXN | 2026-09-09 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-4981320-0453829 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-4981320-0453829\|\|LabmanLabelPurchase` | shipping_fee | -452.3900 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 114-4981320-0453829 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-4981320-0453829\|8F-69M5-T8Q1\|ShippingHB` | shipping_fee | -73.9500 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 114-4981320-0453829 | `shipping_label` | `amazon_us\|shipping_label\|114-4981320-0453829\|2026-09-02` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-5372309-4692239 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-5372309-4692239\|\|LabmanLabelPurchase` | shipping_fee | -446.4600 | MXN | 2026-08-18 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-5372309-4692239 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-5372309-4692239\|UN-G9LH-KH5G\|ShippingHB` | shipping_fee | -90.2100 | MXN | 2026-08-18 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-5372309-4692239 | `shipping_label` | `amazon_us\|shipping_label\|114-5372309-4692239\|2026-08-19` | shipping_fee | -446.0200 | MXN | 2026-08-19 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-5463230-4249834 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-5463230-4249834\|\|LabmanLabelPurchase` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 114-5463230-4249834 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-5463230-4249834\|9P-C0NN-HCR9\|ShippingHB` | shipping_fee | -88.7400 | MXN | 2026-09-02 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 114-5463230-4249834 | `shipping_label` | `amazon_us\|shipping_label\|114-5463230-4249834\|2026-09-02` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-5549809-3818641 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-5549809-3818641\|\|LabmanLabelPurchase` | shipping_fee | -452.3900 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 114-5549809-3818641 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-5549809-3818641\|3P-YWC7-4SCY\|ShippingHB` | shipping_fee | -73.9500 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 114-5549809-3818641 | `shipping_label` | `amazon_us\|shipping_label\|114-5549809-3818641\|2026-09-02` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-6533163-7350645 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-6533163-7350645\|\|LabmanLabelPurchase` | shipping_fee | -455.1400 | MXN | 2026-09-12 | 2026-09-15 | 254 | Finances, camino de etiqueta nuevo |
| 114-6533163-7350645 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-6533163-7350645\|PY-CVMD-FI7W\|ShippingHB` | shipping_fee | -74.2600 | MXN | 2026-09-12 | 2026-09-15 | 254 | Finances, retención sobre el envío |
| 114-6533163-7350645 | `shipping_label` | `amazon_us\|shipping_label\|114-6533163-7350645\|2026-09-14` | shipping_fee | -455.1400 | MXN | 2026-09-14 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-7895262-8664208 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-7895262-8664208\|\|LabmanLabelPurchase` | shipping_fee | -448.4300 | MXN | 2026-08-22 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-7895262-8664208 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-7895262-8664208\|FY-KLOG-DLH5\|ShippingHB` | shipping_fee | -91.2400 | MXN | 2026-08-22 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-7895262-8664208 | `shipping_label` | `amazon_us\|shipping_label\|114-7895262-8664208\|2026-08-24` | shipping_fee | -448.4300 | MXN | 2026-08-24 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-8219024-9505824 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-8219024-9505824\|\|LabmanLabelPurchase` | shipping_fee | -451.4100 | MXN | 2026-09-08 | 2026-09-09 | 137 | Finances, camino de etiqueta nuevo |
| 114-8219024-9505824 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-8219024-9505824\|YU-EPEN-SB3Y\|ShippingHB` | shipping_fee | -88.4800 | MXN | 2026-09-08 | 2026-09-09 | 137 | Finances, retención sobre el envío |
| 114-8219024-9505824 | `shipping_label` | `amazon_us\|shipping_label\|114-8219024-9505824\|2026-09-08` | shipping_fee | -451.4100 | MXN | 2026-09-08 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-8722709-2528214 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-8722709-2528214\|\|LabmanLabelPurchase` | shipping_fee | -450.3700 | MXN | 2026-09-06 | 2026-09-08 | 126 | Finances, camino de etiqueta nuevo |
| 114-8722709-2528214 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-8722709-2528214\|PY-CVMD-FI7W\|ShippingHB` | shipping_fee | -88.2800 | MXN | 2026-09-06 | 2026-09-08 | 126 | Finances, retención sobre el envío |
| 114-8722709-2528214 | `shipping_label` | `amazon_us\|shipping_label\|114-8722709-2528214\|2026-09-07` | shipping_fee | -450.3700 | MXN | 2026-09-07 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-9524054-1545838 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-9524054-1545838\|\|LabmanLabelPurchase` | shipping_fee | -434.4800 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, camino de etiqueta nuevo |
| 114-9524054-1545838 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-9524054-1545838\|5J-VRP9-WMNO\|ShippingHB` | shipping_fee | -88.8000 | MXN | 2026-09-04 | 2026-09-05 | 102 | Finances, retención sobre el envío |
| 114-9524054-1545838 | `shipping_label` | `amazon_us\|shipping_label\|114-9524054-1545838\|2026-09-04` | shipping_fee | -431.6000 | MXN | 2026-09-04 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-9573938-6749849 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-9573938-6749849\|\|LabmanLabelPurchase` | shipping_fee | -449.9700 | MXN | 2026-08-27 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-9573938-6749849 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-9573938-6749849\|2W-B7K3-MMOK\|ShippingHB` | shipping_fee | -88.4700 | MXN | 2026-08-27 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-9573938-6749849 | `shipping_label` | `amazon_us\|shipping_label\|114-9573938-6749849\|2026-08-28` | shipping_fee | -449.9700 | MXN | 2026-08-28 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |
| 114-9638166-5892224 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-9638166-5892224\|\|LabmanLabelPurchase` | shipping_fee | -452.3900 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, camino de etiqueta nuevo |
| 114-9638166-5892224 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-9638166-5892224\|VO-0U7E-EWSL\|ShippingHB` | shipping_fee | -73.9500 | MXN | 2026-09-01 | 2026-09-04 | 76 | Finances, retención sobre el envío |
| 114-9638166-5892224 | `shipping_label` | `amazon_us\|shipping_label\|114-9638166-5892224\|2026-09-02` | shipping_fee | -452.3900 | MXN | 2026-09-02 | 2026-09-17 | 292 | reporte de etiquetas de Buy Shipping |
| 114-9764833-1795459 | `finance:LabmanLabelPurchase` | `amazon_us\|finance\|fee\|114-9764833-1795459\|\|LabmanLabelPurchase` | shipping_fee | -446.4600 | MXN | 2026-08-18 | 2026-08-31 | 51 | Finances, camino de etiqueta nuevo |
| 114-9764833-1795459 | `finance:ShippingHB` | `amazon_us\|finance\|fee\|114-9764833-1795459\|PY-CVMD-FI7W\|ShippingHB` | shipping_fee | -74.1800 | MXN | 2026-08-18 | 2026-08-31 | 51 | Finances, retención sobre el envío |
| 114-9764833-1795459 | `shipping_label` | `amazon_us\|shipping_label\|114-9764833-1795459\|2026-08-19` | shipping_fee | -446.0200 | MXN | 2026-08-19 | 2026-09-04 | 76 | reporte de etiquetas de Buy Shipping |

La venta ligada a cada una (`consultas/03-ventas-de-ordenes-us-tres-cargos.sql`): una fila `sale` por orden; una unidad cada una salvo `114-4981320-0453829` (2 unidades). `shipping_price` viene nulo en las 54, que es el hecho 16 (en US es nulo, no cero). 2 de las 54 ventas no traen `product_id` (la primera parte del hecho 22 del plan: órdenes con cargo de envío cuya venta no trae `product_id`).

| orden | fecha de venta | product_id | SKUs del producto en US | unidades | monto | moneda | shipping_price | `source_event_id` |
|---|---|---|---|---|---|---|---|---|
| 111-0040443-2266607 | 2026-09-04 | 263 | 9P-C0NN-HCR9,EI-RBOM-7B96 | 1 | 3093.9500 | MXN |  | `amazon_us\|sale_gross\|111-0040443-2266607\|\|9P-C0NN-HCR9` |
| 111-0818188-2803467 | 2026-09-05 | 760 | 12-P13M-IHF9,VR-QI3Y-C9WW | 1 | 2480.4500 | MXN |  | `amazon_us\|sale_gross\|111-0818188-2803467\|\|VR-QI3Y-C9WW` |
| 111-0868713-1444220 | 2026-09-04 | 265 | 57-SQKK-1FZD | 1 | 2621.7000 | MXN |  | `amazon_us\|sale_gross\|111-0868713-1444220\|\|57-SQKK-1FZD` |
| 111-1163630-1763411 | 2026-08-21 | 354 | 5J-VRP9-WMNO,W5-XH59-R1FN | 1 | 2716.9800 | MXN |  | `amazon_us\|sale_gross\|111-1163630-1763411\|\|5J-VRP9-WMNO` |
| 111-2217269-8321827 | 2026-09-12 | 315 | L3-Y2YN-IRYX,Q2-2IMG-250B | 1 | 2072.5300 | MXN |  | `amazon_us\|sale_gross\|111-2217269-8321827\|\|L3-Y2YN-IRYX` |
| 111-3323353-2869018 | 2026-09-05 | 627 | DZ-4DLR-XZUE,T5-TQA3-5QM5 | 1 | 1830.1400 | MXN |  | `amazon_us\|sale_gross\|111-3323353-2869018\|\|DZ-4DLR-XZUE` |
| 111-3622584-1220235 | 2026-08-28 | 369 | UT-6NDI-O3IP | 1 | 2614.7500 | MXN |  | `amazon_us\|sale_gross\|111-3622584-1220235\|\|UT-6NDI-O3IP` |
| 111-4577498-1639410 | 2026-09-02 | 359 | 2W-B7K3-MMOK | 1 | 2708.5700 | MXN |  | `amazon_us\|sale_gross\|111-4577498-1639410\|\|2W-B7K3-MMOK` |
| 111-5080630-3746660 | 2026-09-04 | 760 | 12-P13M-IHF9,VR-QI3Y-C9WW | 1 | 2609.2800 | MXN |  | `amazon_us\|sale_gross\|111-5080630-3746660\|\|VR-QI3Y-C9WW` |
| 111-8285600-1114613 | 2026-09-15 | 111 | JH-ICBG-83U8 | 1 | 2779.3400 | MXN |  | `amazon_us\|sale_gross\|111-8285600-1114613\|\|JH-ICBG-83U8` |
| 112-0034856-6910644 | 2026-09-13 | 345 | ID-CCI7-XGML | 1 | 2745.2000 | MXN |  | `amazon_us\|sale_gross\|112-0034856-6910644\|\|ID-CCI7-XGML` |
| 112-1184008-4055445 | 2026-09-04 | 1625 | YU-EPEN-SB3Y | 1 | 2349.3400 | MXN |  | `amazon_us\|sale_gross\|112-1184008-4055445\|\|YU-EPEN-SB3Y` |
| 112-3494183-9567456 | 2026-09-03 | 760 | 12-P13M-IHF9,VR-QI3Y-C9WW | 1 | 2410.3700 | MXN |  | `amazon_us\|sale_gross\|112-3494183-9567456\|\|VR-QI3Y-C9WW` |
| 112-3596235-4597059 | 2026-08-27 | 369 | UT-6NDI-O3IP | 1 | 2575.8500 | MXN |  | `amazon_us\|sale_gross\|112-3596235-4597059\|\|UT-6NDI-O3IP` |
| 112-6174413-7626643 | 2026-09-07 | 587 | F2-QTYV-7A5N | 1 | 2707.7400 | MXN |  | `amazon_us\|sale_gross\|112-6174413-7626643\|\|F2-QTYV-7A5N` |
| 112-6443860-1744200 | 2026-09-11 | 359 | 2W-B7K3-MMOK | 1 | 2693.5800 | MXN |  | `amazon_us\|sale_gross\|112-6443860-1744200\|\|2W-B7K3-MMOK` |
| 112-8779521-3393860 | 2026-08-22 | 367 | PY-CVMD-FI7W | 1 | 2600.1000 | MXN |  | `amazon_us\|sale_gross\|112-8779521-3393860\|\|PY-CVMD-FI7W` |
| 112-8838991-4635437 | 2026-08-26 | 359 | 2W-B7K3-MMOK | 1 | 2542.3400 | MXN |  | `amazon_us\|sale_gross\|112-8838991-4635437\|\|2W-B7K3-MMOK` |
| 112-8879394-1986653 | 2026-09-14 | 305 | S5-P3XQ-A6A6,UT-S0ZQ-2FAO | 1 | 2015.6900 | MXN |  | `amazon_us\|sale_gross\|112-8879394-1986653\|\|S5-P3XQ-A6A6` |
| 113-0929035-4394624 | 2026-09-03 | 274 | IJ-L6WR-HSNY,PV-AVJL-I78M | 1 | 2528.8700 | MXN |  | `amazon_us\|sale_gross\|113-0929035-4394624\|\|PV-AVJL-I78M` |
| 113-1624244-7881060 | 2026-09-01 | 345 | ID-CCI7-XGML | 1 | 2761.6300 | MXN |  | `amazon_us\|sale_gross\|113-1624244-7881060\|\|ID-CCI7-XGML` |
| 113-1641125-1029034 | 2026-08-26 | 619 | UN-4O7B-FJ96,UN-G9LH-KH5G | 1 | 1686.3200 | MXN |  | `amazon_us\|sale_gross\|113-1641125-1029034\|\|UN-G9LH-KH5G` |
| 113-1907141-0514600 | 2026-09-10 | 335 | I5-0XZ9-12IO | 1 | 2427.7100 | MXN |  | `amazon_us\|sale_gross\|113-1907141-0514600\|\|I5-0XZ9-12IO` |
| 113-2811291-5425028 | 2026-09-03 | 353 | 3D-7NIJ-3BGK | 1 | 2518.1600 | MXN |  | `amazon_us\|sale_gross\|113-2811291-5425028\|\|3D-7NIJ-3BGK` |
| 113-4596105-9681840 | 2026-09-09 | 359 | 2W-B7K3-MMOK | 1 | 2536.9800 | MXN |  | `amazon_us\|sale_gross\|113-4596105-9681840\|\|2W-B7K3-MMOK` |
| 113-4810240-3846666 | 2026-08-23 | 274 | IJ-L6WR-HSNY,PV-AVJL-I78M | 1 | 2581.5100 | MXN |  | `amazon_us\|sale_gross\|113-4810240-3846666\|\|PV-AVJL-I78M` |
| 113-5725957-8297853 | 2026-09-06 | 367 | PY-CVMD-FI7W | 1 | 2563.9300 | MXN |  | `amazon_us\|sale_gross\|113-5725957-8297853\|\|PY-CVMD-FI7W` |
| 113-6664176-4990621 | 2026-08-18 | 263 | 9P-C0NN-HCR9,EI-RBOM-7B96 | 1 | 3107.7300 | MXN |  | `amazon_us\|sale_gross\|113-6664176-4990621\|\|9P-C0NN-HCR9` |
| 113-7945413-9549847 | 2026-09-08 | 335 | I5-0XZ9-12IO | 1 | 2542.8100 | MXN |  | `amazon_us\|sale_gross\|113-7945413-9549847\|\|I5-0XZ9-12IO` |
| 113-8552624-7805017 | 2026-08-22 | 367 | PY-CVMD-FI7W | 1 | 2534.8700 | MXN |  | `amazon_us\|sale_gross\|113-8552624-7805017\|\|PY-CVMD-FI7W` |
| 114-0083672-7380279 | 2026-08-25 | 355 | 7Q-518K-IKVE | 1 | 2469.6400 | MXN |  | `amazon_us\|sale_gross\|114-0083672-7380279\|\|7Q-518K-IKVE` |
| 114-0106578-7610669 | 2026-09-02 |  |  | 1 | 2643.9600 | MXN |  | `amazon_us\|sale_gross\|114-0106578-7610669\|\|ST-MV02-LRFL` |
| 114-0761571-9993866 | 2026-08-26 | 369 | UT-6NDI-O3IP | 1 | 2661.2000 | MXN |  | `amazon_us\|sale_gross\|114-0761571-9993866\|\|UT-6NDI-O3IP` |
| 114-0965980-4478658 | 2026-09-10 | 359 | 2W-B7K3-MMOK | 1 | 2590.4800 | MXN |  | `amazon_us\|sale_gross\|114-0965980-4478658\|\|2W-B7K3-MMOK` |
| 114-1410350-4190617 | 2026-09-11 | 110 | K8-KF5Z-H8MT,KN-V8NQ-XDWN | 1 | 2718.2500 | MXN |  | `amazon_us\|sale_gross\|114-1410350-4190617\|\|K8-KF5Z-H8MT` |
| 114-1534882-5053008 | 2026-09-10 | 111 | JH-ICBG-83U8 | 1 | 2615.5400 | MXN |  | `amazon_us\|sale_gross\|114-1534882-5053008\|\|JH-ICBG-83U8` |
| 114-2030282-2885022 | 2026-08-27 | 1621 | 0X-THNZ-4W2E | 1 | 2639.6700 | MXN |  | `amazon_us\|sale_gross\|114-2030282-2885022\|\|0X-THNZ-4W2E` |
| 114-2631069-0829830 | 2026-09-13 | 345 | ID-CCI7-XGML | 1 | 2632.8100 | MXN |  | `amazon_us\|sale_gross\|114-2631069-0829830\|\|ID-CCI7-XGML` |
| 114-2885038-7129058 | 2026-09-04 | 619 | UN-4O7B-FJ96,UN-G9LH-KH5G | 1 | 1684.0100 | MXN |  | `amazon_us\|sale_gross\|114-2885038-7129058\|\|UN-4O7B-FJ96` |
| 114-4068501-4322654 | 2026-08-25 | 355 | 7Q-518K-IKVE | 1 | 2498.6200 | MXN |  | `amazon_us\|sale_gross\|114-4068501-4322654\|\|7Q-518K-IKVE` |
| 114-4681368-9831437 | 2026-09-06 | 345 | ID-CCI7-XGML | 1 | 2620.7100 | MXN |  | `amazon_us\|sale_gross\|114-4681368-9831437\|\|ID-CCI7-XGML` |
| 114-4913582-6944237 | 2026-09-08 | 345 | ID-CCI7-XGML | 1 | 2672.1100 | MXN |  | `amazon_us\|sale_gross\|114-4913582-6944237\|\|ID-CCI7-XGML` |
| 114-4981320-0453829 | 2026-09-02 | 676 | 1F-OSCN-XRMR,8F-69M5-T8Q1,O2-6H06-O2CI | 2 | 4085.9700 | MXN |  | `amazon_us\|sale_gross\|114-4981320-0453829\|\|8F-69M5-T8Q1` |
| 114-5372309-4692239 | 2026-08-19 | 619 | UN-4O7B-FJ96,UN-G9LH-KH5G | 1 | 1795.9900 | MXN |  | `amazon_us\|sale_gross\|114-5372309-4692239\|\|UN-G9LH-KH5G` |
| 114-5463230-4249834 | 2026-09-02 | 263 | 9P-C0NN-HCR9,EI-RBOM-7B96 | 1 | 3056.0600 | MXN |  | `amazon_us\|sale_gross\|114-5463230-4249834\|\|9P-C0NN-HCR9` |
| 114-5549809-3818641 | 2026-09-02 | 143 | 3P-YWC7-4SCY,D0-KYU5-58K9,TX-FVN6-JBH6 | 1 | 2274.2000 | MXN |  | `amazon_us\|sale_gross\|114-5549809-3818641\|\|3P-YWC7-4SCY` |
| 114-6533163-7350645 | 2026-09-12 | 367 | PY-CVMD-FI7W | 1 | 2612.2700 | MXN |  | `amazon_us\|sale_gross\|114-6533163-7350645\|\|PY-CVMD-FI7W` |
| 114-7895262-8664208 | 2026-08-22 | 120 | FY-KLOG-DLH5,YC-E4ZP-5KCT | 1 | 2544.1600 | MXN |  | `amazon_us\|sale_gross\|114-7895262-8664208\|\|FY-KLOG-DLH5` |
| 114-8219024-9505824 | 2026-09-08 | 1625 | YU-EPEN-SB3Y | 1 | 2496.2300 | MXN |  | `amazon_us\|sale_gross\|114-8219024-9505824\|\|YU-EPEN-SB3Y` |
| 114-8722709-2528214 | 2026-09-07 | 367 | PY-CVMD-FI7W | 1 | 2656.2000 | MXN |  | `amazon_us\|sale_gross\|114-8722709-2528214\|\|PY-CVMD-FI7W` |
| 114-9524054-1545838 | 2026-09-04 | 354 | 5J-VRP9-WMNO,W5-XH59-R1FN | 1 | 2738.2400 | MXN |  | `amazon_us\|sale_gross\|114-9524054-1545838\|\|5J-VRP9-WMNO` |
| 114-9573938-6749849 | 2026-08-28 | 359 | 2W-B7K3-MMOK | 1 | 2639.3300 | MXN |  | `amazon_us\|sale_gross\|114-9573938-6749849\|\|2W-B7K3-MMOK` |
| 114-9638166-5892224 | 2026-09-02 |  |  | 1 | 2643.9600 | MXN |  | `amazon_us\|sale_gross\|114-9638166-5892224\|\|VO-0U7E-EWSL` |
| 114-9764833-1795459 | 2026-08-18 | 367 | PY-CVMD-FI7W | 1 | 2502.2100 | MXN |  | `amazon_us\|sale_gross\|114-9764833-1795459\|\|PY-CVMD-FI7W` |

### 2.4 Par `finance:LabmanLabelPurchase` / `shipping_label` — `consultas/04-pares-labman-shipping-label.sql`

Ventana de E.1 (365 días, `[2026-09-18 − 365, 2026-09-18)`), la misma que dio el hecho 22. `dif %` es la diferencia absoluta sobre el mayor de los dos montos. `días` es la fecha de la etiqueta menos la del Labman.

| orden | Labman | `shipping_label` | dif abs | dif % | fecha Labman | fecha etiqueta | días | ShippingHB de la orden | product_id | unidades |
|---|---|---|---|---|---|---|---|---|---|---|
| 111-2217269-8321827 | 437.0500 | 437.0500 | 0.0000 | 0.0000 | 2026-09-11 | 2026-09-14 | 3 | 74.2600 | 315 | 1 |
| 111-3323353-2869018 | 450.3700 | 450.3700 | 0.0000 | 0.0000 | 2026-09-04 | 2026-09-07 | 3 | 73.5700 | 627 | 1 |
| 111-3622584-1220235 | 449.9700 | 449.9700 | 0.0000 | 0.0000 | 2026-08-27 | 2026-08-28 | 1 | 88.4700 | 369 | 1 |
| 111-4577498-1639410 | 452.3900 | 452.3900 | 0.0000 | 0.0000 | 2026-09-01 | 2026-09-02 | 1 | 88.7400 | 359 | 1 |
| 112-0034856-6910644 | 437.0500 | 437.0500 | 0.0000 | 0.0000 | 2026-09-13 | 2026-09-14 | 1 | 89.1200 | 345 | 1 |
| 112-3596235-4597059 | 450.6400 | 450.6400 | 0.0000 | 0.0000 | 2026-08-27 | 2026-08-27 | 0 | 73.8300 | 369 | 1 |
| 112-6174413-7626643 | 450.3700 | 450.3700 | 0.0000 | 0.0000 | 2026-09-07 | 2026-09-07 | 0 | 88.2800 | 587 | 1 |
| 112-8779521-3393860 | 448.4300 | 448.4300 | 0.0000 | 0.0000 | 2026-08-22 | 2026-08-24 | 2 | 73.6400 | 367 | 1 |
| 112-8838991-4635437 | 449.5200 | 449.5200 | 0.0000 | 0.0000 | 2026-08-25 | 2026-08-26 | 1 | 73.6500 | 359 | 1 |
| 113-1624244-7881060 | 434.2000 | 434.2000 | 0.0000 | 0.0000 | 2026-09-01 | 2026-09-02 | 1 | 88.7400 | 345 | 1 |
| 113-1641125-1029034 | 449.5200 | 449.5200 | 0.0000 | 0.0000 | 2026-08-26 | 2026-08-26 | 0 | 73.6500 | 619 | 1 |
| 113-4810240-3846666 | 448.4300 | 448.4300 | 0.0000 | 0.0000 | 2026-08-23 | 2026-08-24 | 1 | 88.3700 | 274 | 1 |
| 113-5725957-8297853 | 450.3700 | 450.3700 | 0.0000 | 0.0000 | 2026-09-06 | 2026-09-07 | 1 | 73.5700 | 367 | 1 |
| 113-7945413-9549847 | 451.4100 | 451.4100 | 0.0000 | 0.0000 | 2026-09-07 | 2026-09-08 | 1 | 88.4800 | 335 | 1 |
| 113-8552624-7805017 | 448.4300 | 448.4300 | 0.0000 | 0.0000 | 2026-08-22 | 2026-08-24 | 2 | 73.6400 | 367 | 1 |
| 114-0083672-7380279 | 431.5700 | 431.5700 | 0.0000 | 0.0000 | 2026-08-25 | 2026-08-26 | 1 | 88.3800 | 355 | 1 |
| 114-0106578-7610669 | 452.3900 | 452.3900 | 0.0000 | 0.0000 | 2026-09-01 | 2026-09-02 | 1 | 73.9500 |  | 1 |
| 114-0761571-9993866 | 449.5200 | 449.5200 | 0.0000 | 0.0000 | 2026-08-26 | 2026-08-26 | 0 | 88.3800 | 369 | 1 |
| 114-2030282-2885022 | 450.6400 | 450.6400 | 0.0000 | 0.0000 | 2026-08-26 | 2026-08-27 | 1 | 73.8300 | 1621 | 1 |
| 114-2631069-0829830 | 437.0500 | 437.0500 | 0.0000 | 0.0000 | 2026-09-12 | 2026-09-14 | 2 | 89.1200 | 345 | 1 |
| 114-2885038-7129058 | 450.3700 | 450.3700 | 0.0000 | 0.0000 | 2026-09-04 | 2026-09-07 | 3 | 88.2800 | 619 | 1 |
| 114-4068501-4322654 | 431.5700 | 431.5700 | 0.0000 | 0.0000 | 2026-08-25 | 2026-08-26 | 1 | 88.3800 | 355 | 1 |
| 114-4681368-9831437 | 432.4400 | 432.4400 | 0.0000 | 0.0000 | 2026-09-05 | 2026-09-07 | 2 | 88.2800 | 345 | 1 |
| 114-4981320-0453829 | 452.3900 | 452.3900 | 0.0000 | 0.0000 | 2026-09-01 | 2026-09-02 | 1 | 73.9500 | 676 | 2 |
| 114-5463230-4249834 | 452.3900 | 452.3900 | 0.0000 | 0.0000 | 2026-09-02 | 2026-09-02 | 0 | 88.7400 | 263 | 1 |
| 114-5549809-3818641 | 452.3900 | 452.3900 | 0.0000 | 0.0000 | 2026-09-01 | 2026-09-02 | 1 | 73.9500 | 143 | 1 |
| 114-6533163-7350645 | 455.1400 | 455.1400 | 0.0000 | 0.0000 | 2026-09-12 | 2026-09-14 | 2 | 74.2600 | 367 | 1 |
| 114-7895262-8664208 | 448.4300 | 448.4300 | 0.0000 | 0.0000 | 2026-08-22 | 2026-08-24 | 2 | 91.2400 | 120 | 1 |
| 114-8219024-9505824 | 451.4100 | 451.4100 | 0.0000 | 0.0000 | 2026-09-08 | 2026-09-08 | 0 | 88.4800 | 1625 | 1 |
| 114-8722709-2528214 | 450.3700 | 450.3700 | 0.0000 | 0.0000 | 2026-09-06 | 2026-09-07 | 1 | 88.2800 | 367 | 1 |
| 114-9573938-6749849 | 449.9700 | 449.9700 | 0.0000 | 0.0000 | 2026-08-27 | 2026-08-28 | 1 | 88.4700 | 359 | 1 |
| 114-9638166-5892224 | 452.3900 | 452.3900 | 0.0000 | 0.0000 | 2026-09-01 | 2026-09-02 | 1 | 73.9500 |  | 1 |
| 113-2811291-5425028 | 434.2000 | 434.4800 | 0.2800 | 0.0006 | 2026-09-03 | 2026-09-03 | 0 | 88.7400 | 353 | 1 |
| 113-6664176-4990621 | 446.4600 | 446.0200 | 0.4400 | 0.0010 | 2026-08-18 | 2026-08-19 | 1 | 89.0200 | 263 | 1 |
| 114-5372309-4692239 | 446.4600 | 446.0200 | 0.4400 | 0.0010 | 2026-08-18 | 2026-08-19 | 1 | 90.2100 | 619 | 1 |
| 114-9764833-1795459 | 446.4600 | 446.0200 | 0.4400 | 0.0010 | 2026-08-18 | 2026-08-19 | 1 | 74.1800 | 367 | 1 |
| 112-6443860-1744200 | 451.2100 | 452.1000 | 0.8900 | 0.0020 | 2026-09-10 | 2026-09-11 | 1 | 88.4500 | 359 | 1 |
| 113-1907141-0514600 | 451.2100 | 452.1000 | 0.8900 | 0.0020 | 2026-09-10 | 2026-09-11 | 1 | 88.4500 | 335 | 1 |
| 114-0965980-4478658 | 451.2100 | 452.1000 | 0.8900 | 0.0020 | 2026-09-10 | 2026-09-11 | 1 | 88.4500 | 359 | 1 |
| 114-1410350-4190617 | 451.2100 | 452.1000 | 0.8900 | 0.0020 | 2026-09-11 | 2026-09-11 | 0 | 88.4500 | 110 | 1 |
| 114-1534882-5053008 | 450.1100 | 451.2100 | 1.1000 | 0.0024 | 2026-09-09 | 2026-09-10 | 1 | 88.2300 | 111 | 1 |
| 113-4596105-9681840 | 451.4100 | 450.1100 | 1.3000 | 0.0029 | 2026-09-08 | 2026-09-09 | 1 | 73.7400 | 359 | 1 |
| 114-4913582-6944237 | 433.4400 | 432.2000 | 1.2400 | 0.0029 | 2026-09-08 | 2026-09-09 | 1 | 88.4800 | 345 | 1 |
| 111-8285600-1114613 | 466.5800 | 468.9000 | 2.3200 | 0.0049 | 2026-09-15 | 2026-09-15 | 0 | 89.1200 | 111 | 1 |
| 112-8879394-1986653 | 447.9700 | 450.1900 | 2.2200 | 0.0049 | 2026-09-14 | 2026-09-15 | 1 | 89.1200 | 305 | 1 |
| 111-1163630-1763411 | 432.0700 | 429.7200 | 2.3500 | 0.0054 | 2026-08-20 | 2026-08-21 | 1 | 88.6900 | 354 | 1 |
| 111-0040443-2266607 | 452.6900 | 449.6900 | 3.0000 | 0.0066 | 2026-09-04 | 2026-09-04 | 0 | 88.8000 | 263 | 1 |
| 111-0868713-1444220 | 452.6900 | 449.6900 | 3.0000 | 0.0066 | 2026-09-04 | 2026-09-04 | 0 | 88.8000 | 265 | 1 |
| 111-5080630-3746660 | 452.6900 | 449.6900 | 3.0000 | 0.0066 | 2026-09-03 | 2026-09-04 | 1 | 88.8000 | 760 | 1 |
| 112-1184008-4055445 | 452.6900 | 449.6900 | 3.0000 | 0.0066 | 2026-09-04 | 2026-09-04 | 0 | 74.0000 | 1625 | 1 |
| 112-3494183-9567456 | 452.6900 | 449.6900 | 3.0000 | 0.0066 | 2026-09-03 | 2026-09-04 | 1 | 88.8000 | 760 | 1 |
| 113-0929035-4394624 | 452.6900 | 449.6900 | 3.0000 | 0.0066 | 2026-09-03 | 2026-09-04 | 1 | 74.0000 | 274 | 1 |
| 114-9524054-1545838 | 434.4800 | 431.6000 | 2.8800 | 0.0066 | 2026-09-04 | 2026-09-04 | 0 | 88.8000 | 354 | 1 |
| 111-0818188-2803467 | 127.1200 | 2149.8700 | 2022.7500 | 0.9409 | 2026-09-05 | 2026-09-07 | 2 | 102.9900 | 760 | 1 |

Resumen: 54 pares; **32 idénticos al centavo**; 53 a ≤ 1 %; 0 entre 1 % y 5 %; 1 a más de 5 % (`111-0818188-2803467`: Labman 127.12 contra etiqueta 2 149.87, fechas 2026-09-05 y 2026-09-07). Días entre fechas: 0 días en 13, 1 días en 31, 2 días en 7, 3 días en 3. Las 54 órdenes son las mismas 54 de la tabla 2.3.

### 2.5 Par `finance:ShippingHB` / `shipping_label` — `consultas/05-muestra-shippinghb-shipping-label.sql`

Muestra determinística de 20 (las primeras por `md5(order_id)`), ventana de 365 días. `HB/etiqueta` es el cociente.

| orden | ShippingHB | `shipping_label` | HB/etiqueta | fecha HB | fecha etiqueta | Labman | product_id | unidades | venta |
|---|---|---|---|---|---|---|---|---|---|
| 113-6520413-7129816 | 90.6900 | 448.2100 | 0.2023 | 2026-05-17 | 2026-05-18 |  | 345 | 1 | 2559.2500 |
| 114-7312582-7165047 | 90.5700 | 449.1600 | 0.2016 | 2026-04-20 | 2026-04-21 |  | 353 | 1 | 2357.4300 |
| 113-6664176-4990621 | 89.0200 | 446.0200 | 0.1996 | 2026-08-18 | 2026-08-19 | 446.4600 | 263 | 1 | 3107.7300 |
| 113-6543802-7278650 | 32.1900 | 507.4000 | 0.0634 | 2026-02-13 | 2026-02-13 |  | 369 | 1 | 2559.4800 |
| 113-5096875-0014633 | 27.8900 | 456.2600 | 0.0611 | 2026-03-23 | 2026-03-24 |  | 79 | 1 | 1502.0200 |
| 113-7564214-6959449 | 33.9100 | 457.6500 | 0.0741 | 2026-04-07 | 2026-04-08 |  | 369 | 1 | 2377.0200 |
| 113-4598814-0699463 | 89.8900 | 450.2400 | 0.1996 | 2026-04-19 | 2026-04-20 |  | 345 | 1 | 2771.6700 |
| 114-2193243-1782646 | 32.1900 | 489.2000 | 0.0658 | 2026-01-11 | 2026-01-11 |  |  | 1 | 1870.6500 |
| 113-4582914-1523449 | 32.1900 | 510.2000 | 0.0631 | 2026-03-02 | 2026-03-02 |  | 359 | 1 | 2597.3100 |
| 114-9806740-4229040 | 32.1900 | 510.2000 | 0.0631 | 2026-03-06 | 2026-03-06 |  | 619 | 1 | 1510.0100 |
| 112-0872611-3657850 | 91.3000 | 456.9500 | 0.1998 | 2026-07-20 | 2026-07-21 |  | 359 | 1 | 2621.1600 |
| 111-3610534-4848208 | 39.1600 | 507.4000 | 0.0772 | 2026-02-16 | 2026-02-16 |  | 640 | 1 | 1124.5800 |
| 112-1921965-7408226 | 31.4000 | 489.8000 | 0.0641 | 2026-03-11 | 2026-03-12 |  | 335 | 1 | 2426.2900 |
| 114-4068501-4322654 | 88.3800 | 431.5700 | 0.2048 | 2026-08-25 | 2026-08-26 | 431.5700 | 355 | 1 | 2498.6200 |
| 111-6015602-4879416 | 75.4200 | 466.2300 | 0.1618 | 2026-05-22 | 2026-05-25 |  | 273 | 1 | 2537.4900 |
| 114-1352858-3977811 | 32.1900 | 510.2000 | 0.0631 | 2026-03-05 | 2026-03-06 |  | 359 | 1 | 2331.7100 |
| 114-5011530-2377033 | 33.6200 | 507.4000 | 0.0663 | 2026-02-05 | 2026-02-05 |  | 619 | 1 | 1465.4000 |
| 114-9840937-1808268 | 91.2700 | 460.6900 | 0.1981 | 2026-07-06 | 2026-07-07 |  | 1625 | 1 | 2505.9400 |
| 114-8489359-9637031 | 74.8300 | 445.3600 | 0.1680 | 2026-05-06 | 2026-05-07 |  | 335 | 1 | 2443.0600 |
| 113-8992855-0132251 | 74.9100 | 450.2400 | 0.1664 | 2026-04-19 | 2026-04-20 |  | 348 | 1 | 2422.9700 |

Qué fuentes trae cada orden (365 días; `t` = la trae):

| plataforma | ShippingHB | `shipping_label` | Labman | MFNPostageFee | órdenes |
|---|---|---|---|---|---|
| amazon_mx | f | f | f | t | 649 |
| amazon_mx | t | t | f | f | 21 |
| amazon_mx | f | f | f | f | 6 |
| amazon_mx | t | f | f | f | 4 |
| amazon_mx | t | f | t | f | 3 |
| amazon_us | t | t | f | f | 446 |
| amazon_us | t | t | t | f | 54 |
| amazon_us | t | f | f | f | 34 |
| amazon_us | t | f | t | f | 3 |

### 2.6 Par `finance:LabmanLabelPurchase` / `finance:ShippingHB` — `consultas/06-par-labman-shippinghb.sql`

| plataforma | órdenes con los dos | ≤ 1 % | 1–5 % | > 5 % | Labman promedio | ShippingHB promedio |
|---|---|---|---|---|---|---|
| amazon_mx | 3 | 0 | 0 | 3 | 126.42 | 12.35 |
| amazon_us | 57 | 0 | 0 | 57 | 442.70 | 84.07 |

### 2.7 Historia mensual por fuente — `consultas/08-historia-mensual-por-fuente.sql`

Filas por mes (en todas, órdenes = filas, salvo `amazon_us` `finance:ShippingHB` 2026-01: 53 filas en 52 órdenes).

| plataforma | identidad | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 | 2026-06 | 2026-07 | 2026-08 | 2026-09 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| amazon_mx | `finance:LabmanLabelPurchase` |  |  |  |  |  |  |  |  |  | 3 |
| amazon_mx | `finance:MFNPostageFee` | 16 | 59 | 66 | 117 | 108 | 73 | 46 | 65 | 62 | 37 |
| amazon_mx | `finance:MFNShippingChargeback` |  |  |  |  | 1 |  |  |  | 1 |  |
| amazon_mx | `finance:ShippingChargeback` | 2 |  | 1 | 1 |  |  |  | 1 |  | 1 |
| amazon_mx | `finance:ShippingHB` | 3 | 1 | 2 | 6 | 1 | 4 | 2 | 4 | 2 | 3 |
| amazon_mx | `shipping_label` |  | 1 | 2 | 6 | 1 | 4 | 1 | 4 | 2 |  |
| amazon_us | `finance:LabmanLabelPurchase` |  |  |  |  |  |  |  |  | 17 | 40 |
| amazon_us | `finance:ShippingHB` | 39 | 53 | 47 | 74 | 70 | 51 | 41 | 58 | 65 | 40 |
| amazon_us | `shipping_label` | 6 | 52 | 47 | 73 | 70 | 50 | 42 | 57 | 63 | 40 |

| plataforma | identidad | primera fecha | última fecha | filas |
|---|---|---|---|---|
| amazon_mx | `finance:LabmanLabelPurchase` | 2026-09-07 | 2026-09-14 | 3 |
| amazon_mx | `finance:MFNPostageFee` | 2025-12-19 | 2026-09-14 | 649 |
| amazon_mx | `finance:MFNShippingChargeback` | 2026-04-19 | 2026-08-25 | 2 |
| amazon_mx | `finance:ShippingChargeback` | 2025-12-13 | 2026-09-06 | 6 |
| amazon_mx | `finance:ShippingHB` | 2025-12-04 | 2026-09-14 | 28 |
| amazon_mx | `shipping_label` | 2026-01-11 | 2026-08-24 | 21 |
| amazon_us | `finance:LabmanLabelPurchase` | 2026-08-18 | 2026-09-17 | 57 |
| amazon_us | `finance:ShippingHB` | 2025-12-04 | 2026-09-17 | 538 |
| amazon_us | `shipping_label` | 2025-12-24 | 2026-09-15 | 500 |

## 3. Qué representa cada fuente (hipótesis con su cita)

| identidad | hipótesis | fuente de la hipótesis |
|---|---|---|
| `finance:ShippingHB` | *Shipping holdback*: la retención (una comisión de referencia) que Amazon cobra sobre el envío que el vendedor cobra al comprador en órdenes que cumple el vendedor (MFN); no existe en FBA. **No es el costo de la etiqueta.** Llega en los eventos de envío de Finances. | A2X, «Amazon Seller Shipping Fees»: «a referral fee charged on shipping done by the seller (MFN). It's not applied if you use FBA» (https://www.a2xaccounting.com/ecommerce-accounting-hub/amazon-shipping-fees-accounting); Sellercloud, «Amazon Settlements» (https://help.sellercloud.com/omnichannel-ecommerce/amazon-settlements/). El objeto exacto de Finances v0 que la trae (`ShipmentEvent` → `ItemFeeList`) es `unknown`: la referencia pública (https://developer-docs.amazon/sp-api/reference/finances-v0) no se pudo leer entera con la red de esta corrida. |
| `finance:MFNPostageFee` (MX) | El costo de la etiqueta comprada con Amazon (Buy Shipping) en México, cobrado del saldo de la cuenta. Llega como `ServiceFeeEvent`. | **Medido en el repo**: `docs/evidencia/margen-estimado-01/0.2/finances-probe-2026-09-08.json` (`GET /finances/v0/financialEvents`, 2026-08-10 a 2026-08-12): `service_fee_type_counts` trae `MFNPostageFee: 5` y `MFNShippingChargeback: 5`; los ajustes de la etiqueta llegan como `AdjustmentEventList` `PostageBilling_*` (`Postage`, `FuelSurcharge`, `Tracking`, `SignatureConfirmation`, `TransactionFee`, `ImportDuty`). Que ese sea el camino del ledger es hipótesis: la contabilidad arma el `source_event_id` y el repo de contabilidad no está en esta fase. |
| `finance:LabmanLabelPurchase` (US desde 2026-08-18, MX desde 2026-09-07) | El costo de una etiqueta comprada con el servicio de etiquetas de Amazon, **informado por Finances**. «Labman» sería el servicio de etiquetas (*label manager*). Es el análogo de `MFNPostageFee` en el camino nuevo. | `unknown` en la documentación pública: la búsqueda del nombre literal no devuelve documentación de Amazon (búsqueda web del 2026-09-18). Por qué nace el 2026-08-18 (¿un cambio en el medio de pago de las etiquetas, del saldo de la tarjeta al de la cuenta?) también es `unknown`. Lo resuelve la vista de transacciones de Seller Central de una orden que lo traiga. |
| `shipping_label` | El costo de la etiqueta según el **reporte de etiquetas** de Buy Shipping que la contabilidad ingiere aparte de Finances. Una fila por orden y día. | La forma de la llave (`<plataforma>\|shipping_label\|<order_id>\|<fecha>`, tabla 2.1) no viene de Finances. Qué reporte exacto la alimenta es `unknown` desde Orbit: lo arma la contabilidad (`/mnt/data/appdata/accounting`, fuera de esta fase). |
| `finance:ShippingChargeback`, `finance:MFNShippingChargeback` (MX) | Cargos de envío que Amazon le repercute al vendedor. `MFNShippingChargeback` llega como `ServiceFeeEvent` (misma sonda del repo). Volumen mínimo: 3 filas en 90 días. | Sonda del repo (arriba); A2X, mismo artículo, «Shipping chargeback». Sin hipótesis de solape medida. |

## 4. Veredicto por par

| par | veredicto | razón y fuente |
|---|---|---|
| finance:LabmanLabelPurchase vs shipping_label | sin veredicto | Sin documento de origen. Medido: 54 pares, 32 idénticos al centavo, 53 a ≤ 1 %, la etiqueta de 0 a 3 días después; Labman nace el 2026-08-18 y en septiembre las tres fuentes de US traen 40 órdenes cada una (tablas 2.3, 2.4 y 2.7). Compatible con `duplicado` y no lo prueba: montos iguales no separan «un cobro informado dos veces» de «dos cobros del mismo importe». **Lo resuelve**: la vista de transacciones de Seller Central (US) de cada orden de la sección 7, exportada a `/Users/dn/dev/orbit-insumos/E.0/`; un solo cobro de etiqueta en la orden = `duplicado`, dos = `componentes distintos`. |
| finance:ShippingHB vs shipping_label | sin veredicto | Sin documento de origen. Medido: ShippingHB vale del 6.1 % al 20.5 % de la etiqueta en la muestra de 20 y aparece en las 537 órdenes de US con cargo de envío de los últimos 365 días (tablas 2.5 y 2.7); hipótesis: retención de Amazon sobre el envío cobrado al comprador (A2X, sección 3). Compatible con `componentes distintos` y no lo prueba. **Lo resuelve**: la misma vista de transacciones, exportada a `/Users/dn/dev/orbit-insumos/E.0/`: si el *shipping holdback* aparece como línea propia, aparte del cobro de la etiqueta, es `componentes distintos`. |
| finance:LabmanLabelPurchase vs finance:ShippingHB | sin veredicto | Sin documento de origen. Medido: 0 de 57 órdenes de US a ≤ 5 % (Labman promedio 442.70, ShippingHB 84.07; tabla 2.6). Compatible con `componentes distintos` y no lo prueba. **Lo resuelve**: la misma vista de transacciones, exportada a `/Users/dn/dev/orbit-insumos/E.0/`. |

## 5. Regla de costo por orden que resulta

Es la regla que E.0b implementa **cuando haya veredicto**. Mientras no lo haya, E.0b no arranca (depende de E.0a) y ninguna de las dos lecturas se sella. La tabla da la regla de cada lectura para que E.0b no tenga que volver a medir.

| fuente | si Labman / etiqueta = `duplicado` | si Labman / etiqueta = `componentes distintos` | nota |
|---|---|---|---|
| `shipping_label` | **cuenta** | **cuenta** | Es la fuente con cobertura completa de etiquetas en US desde dic-2025 (tabla 2.7). Llega con rezago de ingesta (p90 13 días en US, hecho 20). |
| `finance:LabmanLabelPurchase` | **descarta** cuando la misma orden trae `shipping_label`, contada con razón `duplicado_de_shipping_label`; **cuenta** si la orden no trae etiqueta (3 órdenes de US y 3 de MX a 90 días, tabla 2.2) | **cuenta** | Con `duplicado`, la ventana de E.3 cierra en `hoy − rezago`: para entonces la etiqueta ya llegó y el descarte es estable. |
| `finance:ShippingHB` | **cuenta**, como componente propio (retención sobre el envío), si el par HB / etiqueta sale `componentes distintos`; **descarta** contada si sale `duplicado` (improbable: 6.1 % a 20.5 % de la etiqueta en la muestra) | igual | Si va dentro de `L` o como componente aparte lo decide la parte 2 del acta E.2. |
| `finance:MFNPostageFee` | **cuenta** | **cuenta** | En MX nunca coincide con `shipping_label` (tabla 2.5: 649 órdenes con solo MFNPostageFee). |
| `finance:ShippingChargeback`, `finance:MFNShippingChargeback` | **otros**, contada | **otros**, contada | Sin veredicto propio; 3 filas a 90 días. |
| fuente desconocida o no reconocida (identidad fuera de estas seis, o `source_event_id` nulo) | **otros**, contada, nunca descartada en silencio | **otros**, contada | La regla de E.0b: «una fuente desconocida cae en `otros` contada». |

**Qué cambia el dinero.** En las 54 órdenes de la tabla 2.3, contar Labman **y** etiqueta suma el costo de la etiqueta dos veces si el veredicto es `duplicado`. La cifra en juego es la suma de los Labman de **esas 54 órdenes**: **-23 846.32 MXN** a 90 días (de `salidas/02-ordenes-us-tres-cargos-desglose.txt`). No es el total de Labman de la tabla 2.1 (57 filas): las 3 órdenes con Labman y sin etiqueta lo cuentan una sola vez con cualquier veredicto. Lo que E.1 ya midió bajo las dos lecturas (`docs/evidencia/repricing-01/E.1/medicion.md`) no cambia la ventana, el mínimo ni la histéresis: por eso la parte 1 del acta E.2 no espera a este veredicto y la parte 2 (el valor) sí.

## 6. Descartes por convención de signos — `consultas/07-descartes-por-signo.sql`

La ingesta contable (`app/ledger.py`, decisión 4 de la 0.6) **no escribe** una fila `fee`, `refund` o `withholding` con monto positivo, ni una `sale` con monto ≤ 0, y la cuenta en `ingest_run.skip_reason` como `<n>x viola ledger_convencion_signos`. Orbit solo guarda ese número por corrida:

| corrida | día | ok | escritas | saltadas | descartes por signo | `skip_reason` completo |
|---|---|---|---|---|---|---|
| 51 | 2026-08-31 | t | 8041 | 5345 | 106 | `4998x plataforma meli excluida, 12x sin listing para ASIN, 229x venta sin ASIN, 106x viola ledger_convencion_signos` |
| 52 | 2026-08-31 | t | 0 | 13386 | 106 | `8041x conflicto dedupe, 4998x plataforma meli excluida, 12x sin listing para ASIN, 229x venta sin ASIN, 106x viola ledger_convencion_signos` |
| 53 | 2026-08-31 | t | 0 | 13145 | 106 | `8041x conflicto dedupe, 4998x plataforma meli excluida, 106x viola ledger_convencion_signos` |
| 54 | 2026-08-31 | t | 0 | 13145 | 106 | `8041x conflicto dedupe, 4998x plataforma meli excluida, 106x viola ledger_convencion_signos` |
| 55 | 2026-08-31 | t | 0 | 13145 | 106 | `8041x conflicto dedupe, 4998x plataforma meli excluida, 106x viola ledger_convencion_signos` |
| 56 | 2026-08-31 | t | 0 | 13145 | 106 | `8041x conflicto dedupe, 4998x plataforma meli excluida, 106x viola ledger_convencion_signos` |
| 76 | 2026-09-04 | t | 217 | 13129 | 107 | `8011x conflicto dedupe, 5011x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 79 | 2026-09-04 | t | 0 | 13346 | 107 | `8228x conflicto dedupe, 5011x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 82 | 2026-09-04 | t | 1 | 13346 | 107 | `8228x conflicto dedupe, 5011x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 87 | 2026-09-04 | t | 0 | 13347 | 107 | `8229x conflicto dedupe, 5011x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 88 | 2026-09-04 | t | 0 | 13347 | 107 | `8229x conflicto dedupe, 5011x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 91 | 2026-09-04 | t | 0 | 13347 | 107 | `8229x conflicto dedupe, 5011x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 97 | 2026-09-04 | t | 3 | 13348 | 107 | `8229x conflicto dedupe, 5012x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 102 | 2026-09-05 | t | 58 | 13352 | 108 | `8227x conflicto dedupe, 5017x plataforma meli excluida, 108x viola ledger_convencion_signos` |
| 106 | 2026-09-06 | t | 10 | 13410 | 107 | `8285x conflicto dedupe, 5018x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 111 | 2026-09-07 | t | 12 | 13429 | 109 | `8295x conflicto dedupe, 5025x plataforma meli excluida, 109x viola ledger_convencion_signos` |
| 126 | 2026-09-08 | t | 80 | 13427 | 107 | `8294x conflicto dedupe, 5026x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 137 | 2026-09-09 | t | 34 | 13536 | 108 | `8369x conflicto dedupe, 5059x plataforma meli excluida, 108x viola ledger_convencion_signos` |
| 159 | 2026-09-10 | t | 41 | 13568 | 105 | `8399x conflicto dedupe, 5064x plataforma meli excluida, 105x viola ledger_convencion_signos` |
| 178 | 2026-09-11 | t | 18 | 13621 | 108 | `8438x conflicto dedupe, 5075x plataforma meli excluida, 108x viola ledger_convencion_signos` |
| 197 | 2026-09-12 | t | 51 | 13644 | 107 | `8451x conflicto dedupe, 5086x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 216 | 2026-09-13 | t | 12 | 13700 | 106 | `8502x conflicto dedupe, 5092x plataforma meli excluida, 106x viola ledger_convencion_signos` |
| 235 | 2026-09-14 | t | 14 | 13730 | 107 | `8514x conflicto dedupe, 5109x plataforma meli excluida, 107x viola ledger_convencion_signos` |
| 254 | 2026-09-15 | t | 74 | 13746 | 106 | `8515x conflicto dedupe, 5125x plataforma meli excluida, 106x viola ledger_convencion_signos` |
| 273 | 2026-09-16 | t | 48 | 13816 | 109 | `8581x conflicto dedupe, 5126x plataforma meli excluida, 109x viola ledger_convencion_signos` |
| 292 | 2026-09-17 | t | 57 | 13840 | 112 | `8591x conflicto dedupe, 5137x plataforma meli excluida, 112x viola ledger_convencion_signos` |
| 311 | 2026-09-18 | t | 34 | 13894 | 110 | `8646x conflicto dedupe, 5138x plataforma meli excluida, 110x viola ledger_convencion_signos` |

Resumen de 90 días: 27 corridas, mínimo 105, promedio 107.2, máximo 112. Es el mismo conjunto que se vuelve a contar cada día (la ingesta relee el snapshot completo), no 107.2 filas nuevas por día.

**Plataforma y `fee_type` de cada descarte: `unknown`.** No están en Orbit: viven en la base de contabilidad, que esta fase no lee (sección 1). La consulta, de solo lectura, está versionada en `consultas-contabilidad/descartes-por-signo.sqlite.sql`, con la misma regla que `app/ledger.py`. Cuenta como `withholding` lo que en contabilidad es `event_type = 'fee'` con `fee_category` `tax_withheld` o `isr_withheld` (`MAPA_KIND` de `app/ledger.py`), así que la condición `fee` con monto positivo ya los incluye. La puede correr David en una sola línea, sin escribir nada. En la sesión de Claude Code va con el `!` delante; en una terminal, sin el `!`:

```
! ssh goncloud "sqlite3 -readonly /mnt/data/appdata/accounting/data/accounting.db" < docs/evidencia/repricing-01/E.0/consultas-contabilidad/descartes-por-signo.sqlite.sql
```

o la resuelve E.0b, cuya DoD ya pide que «los descartes por signo salen en `ingest_run` con plataforma y `fee_type`».

## 7. La lista literal de órdenes a consultar en Seller Central

Las 54 órdenes de `amazon_us` con los tres cargos (`finance:LabmanLabelPurchase`, `finance:ShippingHB`, `shipping_label`) en la ventana de 90 días al 2026-09-18, de `consultas/02-ordenes-us-tres-cargos-desglose.sql`. Son las mismas 54 del par Labman / etiqueta de la tabla 2.4. Para el par ShippingHB / etiqueta, con estas mismas órdenes alcanza: todas traen los dos.

```
111-0040443-2266607
111-0818188-2803467
111-0868713-1444220
111-1163630-1763411
111-2217269-8321827
111-3323353-2869018
111-3622584-1220235
111-4577498-1639410
111-5080630-3746660
111-8285600-1114613
112-0034856-6910644
112-1184008-4055445
112-3494183-9567456
112-3596235-4597059
112-6174413-7626643
112-6443860-1744200
112-8779521-3393860
112-8838991-4635437
112-8879394-1986653
113-0929035-4394624
113-1624244-7881060
113-1641125-1029034
113-1907141-0514600
113-2811291-5425028
113-4596105-9681840
113-4810240-3846666
113-5725957-8297853
113-6664176-4990621
113-7945413-9549847
113-8552624-7805017
114-0083672-7380279
114-0106578-7610669
114-0761571-9993866
114-0965980-4478658
114-1410350-4190617
114-1534882-5053008
114-2030282-2885022
114-2631069-0829830
114-2885038-7129058
114-4068501-4322654
114-4681368-9831437
114-4913582-6944237
114-4981320-0453829
114-5372309-4692239
114-5463230-4249834
114-5549809-3818641
114-6533163-7350645
114-7895262-8664208
114-8219024-9505824
114-8722709-2528214
114-9524054-1545838
114-9573938-6749849
114-9638166-5892224
114-9764833-1795459
```

Dónde dejar los exportes: `/Users/dn/dev/orbit-insumos/E.0/`, fuera de cualquier repo. Un archivo por orden o uno con todas; el nombre no importa.

## 8. Reproducir

```
bash docs/evidencia/repricing-01/E.0/correr.sh
```

Desde la raíz del repo, con `ssh goncloud`. Reescribe `salidas/` y `salidas/CORRIDA.txt`, y aborta antes de tocar producción si alguna consulta contiene una palabra de escritura, una diagonal invertida, una sentencia que no empiece con `select` o `with` (`solo-select.py`: sin comentarios y respetando los strings, así que `commit;`, `END WORK;` o `do $$ … $$` no pasan y un `case … end` multilínea sí) o una palabra de control de transacción que cabe dentro de un `select` (`into`, `set_config` y demás) (un metacomando de `psql` como `\!` se ejecutaría en el servidor sin que `BEGIN READ ONLY` lo controle). Los dos candados se prueban sin tocar producción con:

```
bash docs/evidencia/repricing-01/E.0/prueba-candados.sh
```

que siembra una fuga por cada palabra y por cada forma de metacomando con un `ssh` falso en el `PATH`, exige que el corredor la rechace antes de conectar, y comprueba que las consultas reales sí pasan. Los números cambian con el día: la ventana se mueve y la ingesta sigue trayendo etiquetas con rezago.
