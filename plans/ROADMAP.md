# ROADMAP maestro — Orbit (consolidado, lead 2026-09-08)

Fuente: `docs/traspaso/MODULOS-AVANZADOS.md` (5 módulos, verbatim, NO
editar) cruzado con `plans/manifest.json` (23 planes), TODOs por plan
(auditados), y follow-ups de evidencia. Este archivo es el índice
ejecutable: estado real + secuencia + pendientes. No es plan
harness (sin tasks propias); los stubs viven en `plans/`.

## Estado por módulo (criterio del doc vs realidad)

### M1 Repricing — SIN PLAN (stub pendiente)

- Criterio doc: cambia precio en ambas plataformas con historial.
- Realidad: 0%. Precondición `margen-estimado-01` (9 TODO).
- Gaps: sin stub; necesita SP-API Pricing/Competitivo (ver Fase 0).

### M2 Campañas por API — Amazon CERRADO / MeLi SIN PLAN

- Criterio doc: Auto + Exact + Product Targeting en Amazon y básica
  en MeLi.
- Amazon: `fabrica-01` DONE + `fabrica-ui-01` en master verificado
  (13 passed) + `campanas-01`, `campana-activa-01`, `cortes-01`
  cerrados. Cumplido.
- MeLi Ads (proposal-only, bloqueado a nivel cuenta): sin plan ni
  stub. Gap.

### M3 Reputación — v1 CERRADA / v2 PENDIENTE

- Criterio doc (rating + historial + ≥3 alertas): cumplido (5 tipos,
  pantalla, digest, cron en vivo).
- `reputacion-01`: CERRADA 2026-09-08 (A.3 como ampliación E/A.3).
- `reputacion-02` (stub): v2 pendiente — incluye Account Health, Buy
  Box, fakes, sentimiento y vinculación con acciones (pausar
  campañas / no promocionar). Requiere SP-API (Buy Box, Account
  Health) + REP-FOLLOW-2 (datos Amazon).

### M4 Promociones — SIN PLAN (stub pendiente)

- Criterio doc: crea descuento y simula margen.
- Realidad: 0%. Depende: Márgenes + Repricing (simulación).

### M5 Envíos — SIN PLAN (stub pendiente)

- Criterio doc: lista ambas plataformas con estado actualizado.
- Realidad: 0%. Depende: Orders APIs (SP-API + MeLi Orders/Shipments).

### Fase 0 (modelos + auth APIs) — PARCIAL

- Ads API + MeLi OAuth: sí. SP-API (Orders/Pricing/Listings): sin
  plan. Gap: bloquea M1, M5 y reputación v2 parcial.
- Transversales: tokens 600/uid (NO cifrados at rest — gap
  declarado); sin colas/Redis por decisión (desviación consciente
  del doc); observabilidad de integraciones parcial.

## Secuencia propuesta (respeta dependencias)

1. `margen-estimado-01` (9 TODO) — desbloquea M1 y M4.
2. SP-API auth + lecturas (stub nuevo: `sp-api-01`) — desbloquea M1,
   M5, reputación v2.
3. `repricing-01` (stub nuevo) — M1.
4. `meli-ads-01` proposal-only (stub nuevo) — cierra M2.
5. `promociones-01` (stub nuevo) — M4 (tras 1+3).
6. `envios-01` (stub nuevo) — M5 (tras 2).
7. `reputacion-02` (stub existe) — tras 2 + REP-FOLLOW-2.
8. Integraciones cruzadas (fase 6 del doc): pausar campañas por
   rating, no promocionar mala reputación, envíos→reputación.

## Pendientes (dueño de cada uno)

- **Dueño**: subir plan Apify (REP-FOLLOW-2); confirmar pantalla
  cortes-ui (1.2); briefs v2/repricing/promos/envíos antes de cada
  stub; `active` sigue en `reputacion-01` (mover al siguiente).
- **Lead**: margen-estimado-01 A.1→B.5; colas chicas (orbit-05
  2.3/2.5, bids-01 1.5, orbit-02 3.4); stubs SP-API/repricing/
  meli-ads/promociones/envíos; REP-FOLLOW-1/3 (opcionales).
- **Higiene**: `cortes-ui-01` duplicado en manifest (2 entries).

## Backlog sin plan (no olvidar)

- BK-1 Placements Amazon (multiplicadores Top of Search / Product
  Pages): sonda de datos por campaña + reglas con reversa. Citado en
  orbit-03:128 (fases 4-5, ORBIT 07/08 inexistentes) y
  margen-estimado-01:156. Schema listo (`ad_entity_kind`).
- BK-2 Budgets intradía + AMS/Stream (orbit-03:128, mismo futuro).
- BK-3 Tokens cifrados at rest (transversal del doc master; hoy
  600/uid sin cifrar).

## Verificación de completitud (2026-09-08)

- Los 5 módulos + Fase 0 + transversales del doc tienen fila aquí.
- Los 23 planes del manifest están clasificados (cerrados, con TODO
  contados, o stubs); `active` declarado.
- Todos los TODO/WIP (14) + follow-ups E/A.7 (3) + gaps (5) tienen
  dueño y secuencia. Cero huérfanos conocidos.
