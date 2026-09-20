# REPRICING 01 A.4 — sonda real de escritura y reversa

Fecha de inicio: 2026-09-19 UTC.

El dueño autorizó literalmente: `usa los 3 de prueba`. La sonda se amplió de
una a tres publicaciones MX/FBA; el listing 1253 quedó como control negativo.

## Publicaciones

| listing | seller SKU | P | P + 0.01 | cambio | submissionId | reversa | submissionId reversa |
|---:|---|---:|---:|---:|---|---:|---|
| 1213 | `SK-YBQX-XQWV` | 988.00 MXN | 988.01 MXN | 2 | `905178d2bc984c159ceb3835e096d0d2` | 5 | `cfd19184ddd84596868c848a09ae15d1` |
| 1284 | `Q3-7EV3-8TBH` | 1288.00 MXN | 1288.01 MXN | 3 | `bf9db60876d147adb87b643d94abeb0e` | 6 | `0e85a3933dcf45f3a31531aea075f5fa` |
| 1295 | `9D-5LHS-JYW6` | 699.00 MXN | 699.01 MXN | 4 | `e6ccb9ccb9be45b682ecba9d78223f3d` | 7 | `74a112029d684ef48733fd2d2d628da6` |

Control negativo: listing 1253, SKU `0O-B6OS-8RRE`, ASIN `B0CJT6BK86`,
848.00 MXN antes y después de los tres PATCH.

## Resultado hasta ahora

- Las observaciones de Pricing de 2026-09-18 y 2026-09-19 mostraban P y Buy Box propia
  para las cuatro publicaciones.
- Los tres PATCH devolvieron HTTP 200, `status=ACCEPTED`, `issues=[]` y un
  `submissionId`.
- El GET de Listings Items posterior mostró 988.01, 1288.01 y 699.01 MXN.
- El control negativo conservó 848.00 MXN.
- La ingesta manual `spapi_pricing` del 2026-09-20 (`ingest_run=335`) escribió
  342 observaciones, omitió 0 y confirmó P+0.01 en los tres listings y 848.00 MXN
  en el control.
- `cerrar_por_observacion` cerró los cambios 2/3/4 como `confirmado` por
  `observacion`: 3 confirmados, 0 no confirmados.
- El dry-run de reversa produjo la huella `07bd7818894c8e7f`. El go creó las
  reversas 5/6/7; Amazon devolvió HTTP 200, `ACCEPTED`, sin issues, y los tres
  `submissionId` de la tabla.
- El GET posterior confirmó de nuevo 988.00, 1288.00 y 699.00 MXN. El control
  conservó 848.00 MXN.
- Los goals 3/5/4 quedaron cerrados con `valid_to=2026-09-20`; no queda ningún
  goal vigente para los tres listings.

## Forma aceptada

Amazon aceptó `productType=PRODUCT` con `replace` de
`/attributes/purchasable_offer`; `value_with_tax` viajó como string decimal de dos
lugares. Es la misma forma que se sella en `app/spapi/precio_write.py`.

La forma coincide con el ejemplo oficial de Amazon para actualización de precios:
<https://github.com/amzn/selling-partner-api-samples/blob/main/use-cases/pricing/code/java/src/main/java/lambda/SubmitPriceHandler.java>.

## Discrepancia medida

El GET confirmó el nuevo `purchasable_offer`, pero `summaries.lastUpdatedDate` no
cambió: conservó 2024-07-13, 2026-07-30 y 2026-07-28 respectivamente, todas anteriores
a `enviado_at`. Ese campo no evidencia la actualización de oferta en estos listings.
La DoD que exige `lastUpdatedDate > enviado_at` queda pendiente de corregirse con una
fuente temporal que Amazon realmente actualice; no se declara cumplida.

## Pendiente para cerrar A.4

1. Observación del 2026-09-21 en P y cierre de las reversas 5/6/7.
2. Control negativo todavía en 848.00 MXN.
3. Revisión final y PR de esta rama mergeado con CI verde.
