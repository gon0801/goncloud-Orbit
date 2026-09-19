# REPRICING 01 A.4 — sonda real de escritura y reversa

Fecha de inicio: 2026-09-19 UTC.

El dueño autorizó literalmente: `usa los 3 de prueba`. La sonda se amplió de
una a tres publicaciones MX/FBA; el listing 1253 quedó como control negativo.

## Publicaciones

| listing | seller SKU | ASIN | P | P + 0.01 | goal live | decision | cambio | submissionId |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1213 | `SK-YBQX-XQWV` | `B0C8RVWG4F` | 988.00 MXN | 988.01 MXN | 35.00% (`goal_id=3`) | 2 | 2 | `905178d2bc984c159ceb3835e096d0d2` |
| 1284 | `Q3-7EV3-8TBH` | `B0D836626Q` | 1288.00 MXN | 1288.01 MXN | 53.00% (`goal_id=5`) | 3 | 3 | `bf9db60876d147adb87b643d94abeb0e` |
| 1295 | `9D-5LHS-JYW6` | `B0D86QLQ3Y` | 699.00 MXN | 699.01 MXN | 59.50% (`goal_id=4`) | 4 | 4 | `e6ccb9ccb9be45b682ecba9d78223f3d` |

Control negativo: listing 1253, SKU `0O-B6OS-8RRE`, ASIN `B0CJT6BK86`,
848.00 MXN antes y después de los tres PATCH.

## Resultado hasta ahora

- Las observaciones de Pricing de 2026-09-18 y 2026-09-19 mostraban P y Buy Box propia
  para las cuatro publicaciones.
- Los tres PATCH devolvieron HTTP 200, `status=ACCEPTED`, `issues=[]` y un
  `submissionId`.
- El GET de Listings Items posterior mostró 988.01, 1288.01 y 699.01 MXN.
- El control negativo conservó 848.00 MXN.
- Los tres cambios siguen `enviado`. La reversa no puede nacer mientras el original
  esté abierto: se ejecutará después de que una observación de un día UTC posterior
  confirme P+0.01 mediante `cerrar_por_observacion`.

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

1. Observación D+1 en P+0.01 y cierre de cambios 2, 3 y 4.
2. Dry-run y go de `tools/precio_reversa.py` para los tres cambios.
3. GET vivo en P y observación D+1 de cada reversa.
4. Control negativo todavía en 848.00 MXN.
5. PR de esta rama mergeado con CI verde.
