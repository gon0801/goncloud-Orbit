# V.0: conclusion de las cuatro sondas (2026-10-10)

Corridas en vivo dentro de `orbit-app-1`, una sonda a la vez, orden 1-2-3-4:
sin bandera, con `--acepto-mutacion-real` y relectura sin bandera. Salidas
literales (salvo `sku`/`asin` redactados, regla 16) en este directorio, todas
con exit 0. Ninguna sonda quedo sin sellar ni sin regresar.

## Sonda 1: pausa de product ad — SELLADA

- Cuerpo que Amazon acepta: `{"productAds": [{"adId": "284583606382521",
  "state": "PAUSED"}]}` (207 sin errores); regreso a `ENABLED` aceptado (207).
- Entidad como estaba: `iguales: true`, "OK: pauso y reactivo"; relectura con
  el anuncio en `ENABLED`.

## Sonda 2: presupuesto de campana — SELLADA (US y MX)

- US (campana 138625505369345, 9.52): cambio a 10.52 aceptado (207); regreso a
  9.52 aceptado (207). `iguales: true`. Relectura: 9.52.
- MX (elegida por lectura, menor campaignId PAUSED+DAILY: 177498916708097,
  500.0): cambio a 501.0 aceptado (207); regreso a 500.0 aceptado (207).
  `iguales: true`. Relectura: 500.0.
- Minimo: PUT con `budget.budget` 0.01 RECHAZADO en ambos paises (207 con
  `budgetError` `BUDGET_OUT_OF_MARKET_PLACE_RANGE`: "daily budget is lower
  than the minimum lower bound required by the marketplace"). El cuerpo no
  nombra numero. El `finally` regreso el presupuesto en los dos casos.

## Sonda 3: ajuste de placement — SELLADA

- Cuerpo que Amazon acepta: `{"campaigns": [{"campaignId": "138625505369345",
  "dynamicBidding": {"strategy": "LEGACY_FOR_SALES", "placementBidding":
  [{"placement": "PLACEMENT_TOP", "percentage": 1}]}}]}` (207).
- Una lista parcial de `placementBidding` NO reemplaza la entera: Amazon
  MEZCLA (`lista_parcial_reemplaza: false`; la otra entrada, PP@40, siguio).
- Regreso con la lista de antes aceptado (207) mas PUT en 0 del sobrante, que
  si lo borro (`cero_borra: true`). `iguales: true`. Relectura: lista [PP@40].
- Placement: un ajuste se puede mandar solo; para quitar uno hay que mandarlo
  en 0, omitirlo no lo quita.

## Sonda 4: gasto fuera de Amazon — SELLADA CON DESVIACION AUTORIZADA

- Valor candidato que acepto Amazon: `MINIMIZE_SPEND` (primer candidato, 207;
  lectura confirma `offAmazonBudgetControlStrategy: MINIMIZE_SPEND`).
- El PUT de regreso con {} no fue rechazado (207 sin errores) pero no cambio
  nada; lo sellado es "mandar {} no regresa el valor". Quedo MINIMIZE_SPEND en
  campana PAUSED (no cambia entregas), anotado en `desviacion`. Relectura lo
  confirma. Guia punto 7: reportado bajo Desviaciones.

## Los cinco datos del cambio 9

1. Lista parcial de `placementBidding`: NO reemplaza; mezcla.
2. `offAmazonSettings` aceptado: `MINIMIZE_SPEND`.
3. Minimo diario amazon_us: no medido (0.01 rechazado sin numero en el mensaje).
4. Minimo diario amazon_mx: no medido (0.01 rechazado sin numero en el mensaje).
5. Exito se decide por errores anidados y lectura, nunca por el status 207.

## Para V.3 e impulso

- Para V.3: el boton de limitar gasto fuera de Amazon hoy no tiene camino de
  regreso sellado (o se presenta sin regreso, o se sella antes).
- Para impulso: `PRESUPUESTO_MINIMO_CAMPANA` se queda en `None`; el impulso usa
  el tope entre 14.

## Desviaciones

- `A1U-Auto-Discovery-US.offAmazonSettings` = `MINIMIZE_SPEND` (antes `{}`),
  campana pausada, no cambia entregas (sonda 4, excepcion autorizada punto 7).
