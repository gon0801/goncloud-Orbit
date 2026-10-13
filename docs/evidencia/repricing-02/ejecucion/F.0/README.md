# F.0: tres lecturas de producción

Fila **F.0** de `plans/repricing-02.md` (`[stage:verificacion] [lane:gate]
[tdd:skip:sonda]`), sección 1 ("Lecturas de producción"), rama
`rp02/sec1-lecturas`. Solo lectura: SELECT por `ORBIT_DSN_READ` dentro de
`BEGIN READ ONLY`, y una consulta de fees que no cambia nada. Corrida el
**2026-10-13** UTC desde el worktree, sobre `origin/master` `e972fcc`.

Qué hay en esta carpeta:

| Archivo | Qué es |
| --- | --- |
| `correr.sh` | Copia del corredor de E.0 (`docs/evidencia/repricing-01/E.0/correr.sh`) con una diferencia declarada: la salida pasa por `sanear_salidas.py` antes de tocar disco. |
| `sanear_salidas.py` | Redacta ids de orden y SKUs de cada salida (repo público). La salida cruda no se escribe en ninguna parte. |
| `solo-select.py` | Copia literal del candado estructural de E.0. |
| `consultas/00-sonda-formato-90d.sql` | Copia literal de la sonda de identidades de E.0 (punto 1). |
| `consultas/03-fbm-sin-stock.sql` | Cruce de disponibilidad 0 contra el último status de hoy (punto 3; dato). El SKU no sale: cada fila por `listing_id` y `product_id`. |
| `consultas/05-estado-contemporaneo.sql` | Status más cercano EN EL TIEMPO a la foto de cantidad 0 (punto 3; llegó en la ronda r3, revisión R01-r2). |
| `consultas/07-control-estado-por-cantidad.sql` | Control por cantidad con status cercano y de hoy, y cuántas fotos de cantidad FBM existen (r3). |
| `sonda_fees_fbm.py` | Sonda Product Fees con `IsAmazonFulfilled: false` (punto 2). |
| `salidas/` | La salida literal (saneada) de cada consulta, `CORRIDA.txt` (r1: consultas 00 y 03) y `CORRIDA-r3.txt` (r3: consultas 05 y 07, corridas desde una copia temporal de la carpeta, por eso `commit_del_repo: desconocido`). |

`CORRIDA.txt` dice `estado: COMPLETA` con `consultas_con_cambios_sin_commitear: 1`:
las consultas de esta carpeta son nuevas y van commiteadas junto con esta
salida, en el commit de F.0.

## 1. Identidades de los cargos de envío — `salidas/00-sonda-formato-90d.txt`

Comando: `bash docs/evidencia/repricing-02/ejecucion/F.0/correr.sh`
(corre las dos consultas de `consultas/` en una sola corrida, cada una en
`BEGIN READ ONLY` que termina en `ROLLBACK`). La identidad sale de
`source_event_id` partido por `|`: campo 6 si el campo 2 es `finance`, si no
el campo 2.

Ventana [2026-10-13 − 90, 2026-10-13] = [2026-07-15, 2026-10-13] UTC. Identidades medidas:

| plataforma | identidad | filas | órdenes | suma MXN |
| --- | --- | ---: | ---: | ---: |
| amazon_mx | finance:LabmanLabelPurchase | 5 | 5 | -650.46 |
| amazon_mx | finance:MFNPostageFee | 256 | 256 | -22084.00 |
| amazon_mx | finance:MFNShippingChargeback | 1 | 1 | -44.03 |
| amazon_mx | finance:ShippingChargeback | 1 | 1 | -44.00 |
| amazon_mx | finance:ShippingHB | 9 | 9 | -112.23 |
| amazon_mx | shipping_label | 8 | 8 | -985.22 |
| amazon_us | finance:LabmanLabelPurchase | 96 | 96 | -44106.98 |
| amazon_us | finance:ShippingHB | 176 | 176 | -15108.33 |
| amazon_us | shipping_label | 153 | 153 | -70600.26 |

**Conclusión 1: confirmado.** Son exactamente las seis identidades de la
tabla de fuentes de `docs/evidencia/repricing-01/E.0/veredicto.md`
(`finance:LabmanLabelPurchase`, `finance:MFNPostageFee`,
`finance:MFNShippingChargeback`, `finance:ShippingChargeback`,
`finance:ShippingHB` y `shipping_label`) y ninguna más: MX trae las seis y
US tres (Labman, ShippingHB y `shipping_label`; los `MFN*` y el
`ShippingChargeback` simple no aparecen en US en esta ventana). La forma
literal de `source_event_id` sigue siendo la de E.1 (finance 6 partes,
`shipping_label` 4 partes; en la salida el id de orden y el SKU van
redactados como `<orden>`/`<sku>`). `clasificar_cargo` de F.2 puede partir
de esas seis sin caso «identidad nueva».

## 2. Product Fees para una oferta FBM — `salidas/01-sonda-fees-fbm.txt`

Comando (los SKUs van por argumento y no se commitean; aquí por id interno
de producto):

```
ssh goncloud 'docker exec -i orbit-app-1 python - --sku <SKU FBM de MX> \
    --sku-us <SKU de US> --precio-mx 1489.00 --precio-us 68.50 \
    --producto-mx 109 --producto-us 79' \
    < docs/evidencia/repricing-02/ejecucion/F.0/sonda_fees_fbm.py
```

Los SKU elegidos (por consulta de solo lectura): MX, `product_id=109`
(`listing_id=1123`, FBM con 13 piezas, precio 1489 MXN); US,
`product_id=79` (`listing_id=1426`, FBM con 77 piezas, precio 68.50 USD).
El identificador de la respuesta ecoa `IsAmazonFulfilled: false` en los dos.

| universo | http | Status | tipos de fee | total | ¿impuesto? |
| --- | --- | --- | --- | --- | --- |
| amazon_mx/fbm | 200 | Success | ReferralFee | 192.54 MXN | ninguno |
| amazon_us/fbm | 200 | Success | ReferralFee, VariableClosingFee, PerItemFee | 10.28 USD | ninguno |

**Conclusión 2: ambos universos con cotizador.** Product Fees responde
`Success` para una oferta FBM en los dos marketplaces, así que ni
`amazon_mx/fbm` ni `amazon_us/fbm` se registran sin cotizador. Dos notas
para la sección 2: la cotización FBM de MX no trae comisión de logística
(solo `ReferralFee`, como pide el DoD de F.3); la de US trae tres tipos, y
el partido variable/fijo de F.3 tendrá que decidir `ReferralFee` como
variable y `PerItemFee`/`VariableClosingFee` como fijo. Ningún fee trae
`TaxAmount` distinto de cero en ninguno de los dos universos.

## 3. FBM sin existencias — `salidas/03-fbm-sin-stock.txt`, `salidas/05-estado-contemporaneo.txt`, `salidas/07-control-estado-por-cantidad.txt`

La consulta 03 (corrida original) cruza la última observación de
`disponibilidad_observation` (fuente `fbm`) con cantidad 0 contra el último
`status` de `spapi_listing_estado_observation` del mismo SKU:

| plataforma | último status de hoy con cantidad 0 | SKUs |
| --- | --- | ---: |
| amazon_mx | BUYABLE,DISCOVERABLE | 9 |
| amazon_mx | DISCOVERABLE | 13 |
| amazon_us | BUYABLE,DISCOVERABLE | 7 |
| amazon_us | DISCOVERABLE | 5 |

Ese cruce, contra el status de HOY, **no mide disponibilidad**: la foto de
cantidad es única y vieja (la última línea de 07 lo demuestra: 232 filas en
toda la historia de FBM, todas del 2026-09-07 17:39 UTC, una sola foto), y
los 16 «BUYABLE con cantidad 0» pudieron reponerse en cinco semanas. La
ronda r3 (revisión R01-r2) agregó las consultas 05 y 07, que cruzan contra
el status **contemporáneo** a esa foto:

- 05: para los 34 SKUs con cantidad 0, el status más cercano en el tiempo
  (2026-09-10 a 2.6 días en MX, 2026-09-11 a 3.5 días en US) fue
  `DISCOVERABLE` para TODOS (22 de MX, 12 de US). Ninguno BUYABLE.
- 07 (control por cantidad): con existencias, 192 de 198 estaban BUYABLE
  en el status cercano (6 no); con cantidad 0, 34 de 34 `DISCOVERABLE`.
  El estado sigue a la cantidad.

**Conclusión 3: el estado basta; un FBM sin existencias deja de ser
BUYABLE.** Con el status contemporáneo a la única foto de cantidad, 34 de
34 SKUs sin existencias no eran BUYABLE, así que la disponibilidad de FBM
sale del estado del listing y F.4 sigue su DoD («la disponibilidad de una
unidad FBM sale del estado del listing»). El cruce contra el status de hoy
(consulta 03) queda arriba como dato — su lectura correcta es «qué está
pasando hoy con los que estaban en cero hace cinco semanas», no una regla
de disponibilidad.

**Aviso para la sección 4** (dato, no conclusión de esta fila):
`disponibilidad_observation` no se alimenta desde el 2026-09-07 en fba y
fbm, MX y US (la foto FBM completa es de esa fecha; la revisión R01-r2
midio la misma fecha límite en las cuatro combinaciones de fuente y
plataforma). Si algún paso de la sección 4 necesita cantidad (p. ej. la
disponibilidad de FBA «stock y BUYABLE»), antes hay que revivir esa
ingesta; el estado del listing (`spapi_listing_estado_observation`) sí
está vivo (2026-10-13).
