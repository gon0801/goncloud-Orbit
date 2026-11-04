# SP-API 01 / 0.5 — Sellers (identidad)

Fecha: 2026-09-09 05:41 UTC. Solo lectura (2 GET + 1 refresh LWA).
Sin re-sonda pendiente: se cita la evidencia previa y se confirma con la
sonda de este plan.

## Evidencia previa citada

`docs/evidencia/reputacion-01/0.4/reporte.md`: `GET /sellers/v1/account` y
`GET /sellers/v1/marketplaceParticipations` responden 200, participations
MX + US, storeName EHV. Sirve para identidad, no para salud.

## Comando exacto

```bash
ssh goncloud 'docker exec -i orbit-app-1 python3 - --fuente sellers --mercado amazon_mx' < tools/sonda_spapi.py
```

## Respuesta Amazon (version v1)

- `GET /sellers/v1/marketplaceParticipations`: **200**, 6 participations
  (MX + US entre ellas).
  `claves_top`/`claves_item`: `marketplace`, `participation`, `storeName`.
  Rate limit: `0.016`.
- `GET /sellers/v1/account`: **200** (esta vez no hubo 4xx en NA).
  `claves_top`: `business`, `businessType`,
  `marketplaceParticipationList`, `primaryContact`, `sellingPlan`.
  Rate limit: `0.5`. Sin `sellerId` a la vista (confirma F2).
- Aviso deprecacion: ninguno.

## Procedencia de `--seller-id` (para 0.3)

Una sola lectura con el cliente de solo lectura existente
(`app.ads.client.AdsClient.get("/v2/profiles")`, mismo camino de
`app/ads/structure_api.py`); solo se imprimio el id:

- Perfil Amazon MX (`countryCode=MX`): `accountInfo.id` = `A29XRL07YRN0L`
  (ID publico de vendedor, no secreto).

## Veredicto

| fuente | estado | motivo |
|---|---|---|
| sellers | **verificada** | evidencia previa + 200 en ambas, MX+US presentes |
