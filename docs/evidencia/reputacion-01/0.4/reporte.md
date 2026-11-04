# REPUTACION 01 / 0.4 — Sonda Keepa + Account Health SP-API

Fecha: 2026-09-07/08 UTC. Lead. Lectura minima (1 llamada Keepa con
`update=0`, 2 GETs SP-API firmados).

Sello: Keepa **no_verificada** (sin suscripcion activa).
SP-API Sellers **verificada** (identidad/participations, NO salud).
Account Health **no_verificada** (sin endpoint publico).

## Veredicto por fuente

| fuente | estado | motivo |
|---|---|---|
| Keepa `GET /product` domain=11 + asin | **no_verificada** | 402 `No active API plan found`, `tokensLeft=0`, `tokensConsumed=0` (la sonda no gasto). Key existe en Orbit secrets (65 B) pero sin plan. Costo de entrar: suscripcion Keepa (fuera de D6; reabrir con el dueno si se quiere). |
| SP-API `GET /sellers/v1/account` | **verificada** | 200 firmado (LWA + SigV4 legacy, `us-east-1`): participations MX (A1AM78C64UM0Y8) + resto, storeName EHV. Grano: cuenta. Util para identidad, NADA de salud. |
| SP-API `GET /sellers/v1/marketplaceParticipations` | **verificada** | 200, mismo contenido de participations. |
| Account Health (SP-API) | **no_verificada** | Sin endpoint publico conocido (Seller Central only). Si aparece fuente oficial+barata, entra como ampliacion. |

## Consecuencia para 0.5 (mayor)

El plan puntua "Snapshots rating/count (Keepa, MeLi API)" como
Required para v1. Sin Keepa, **Amazon se queda sin fuente oficial**:
el acta debe degradar snapshots Amazon a Recommended-via-Apify (si
0.2 cierra) o a Sin-verificar-declarado en v1. A.2 quedaria MeLi +
lo que 0.2 habilite; nada de esto bloquea A.4/A.5-MeLi/A.6.
