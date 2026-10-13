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
| `consultas/03-fbm-sin-stock.sql` | Cruce de disponibilidad 0 contra el último status (punto 3). El SKU no sale: cada fila por `listing_id` y `product_id`. |
| `sonda_fees_fbm.py` | Sonda Product Fees con `IsAmazonFulfilled: false` (punto 2). |
| `salidas/` | La salida literal (saneada) de cada consulta y `CORRIDA.txt`. |

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

## 3. FBM sin existencias — `salidas/03-fbm-sin-stock.txt`

Misma corrida del punto 1. Cruce de la última observación de
`disponibilidad_observation` (fuente `fbm`) con cantidad 0 contra el último
`status` de `spapi_listing_estado_observation` del mismo SKU.

| plataforma | último status con cantidad 0 | SKUs |
| --- | --- | ---: |
| amazon_mx | BUYABLE,DISCOVERABLE | 9 |
| amazon_mx | DISCOVERABLE | 13 |
| amazon_us | BUYABLE,DISCOVERABLE | 7 |
| amazon_us | DISCOVERABLE | 5 |

**Conclusión 3: la disponibilidad de FBM debe mirar también la cantidad.**
Con la última cantidad observada en 0, 16 de 34 SKUs (9 de 22 en MX, 7 de
12 en US) siguen `BUYABLE,DISCOVERABLE`: el estado solo no basta para
declarar «disponible» un FBM sin existencias. Eso entra al encargo de la
sección 2 (F.4 lee disponibilidad por canal).

Caveat medido: la ingesta de `disponibilidad_observation` trae su última
observación del **2026-09-07** en las cuatro combinaciones de fuente y
plataforma (247+67+121+111 SKUs); la de `spapi_listing_estado_observation`
está viva (2026-10-13). El cruce es el que pide la guía (última fila con
cantidad 0 contra último status), y aunque el stock de esos 16 hubiera
repuesto desde el 2026-09-07, el resultado demuestra que el estado puede
quedarse `BUYABLE` con cantidad 0 observada: mirar solo el estado dejaría
pasar publicaciones sin existencias. La sequía de la ingesta de
disponibilidad es un dato aparte que se reporta aquí para que la sección 2
lo tenga a la vista.
