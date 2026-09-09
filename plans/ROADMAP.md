# ROADMAP maestro — Orbit (consolidado, lead 2026-09-08)

Fuente ÚNICA de planes y pendientes: este archivo absorbe
`docs/PENDIENTES-AUTOMATIZACION.md` (AUTO-01..AUTO-10, registrado
2026-09-06; el archivo viejo es solo un redirect). Base:
`docs/traspaso/MODULOS-AVANZADOS.md` (5 módulos, verbatim, NO
editar) cruzado con `plans/manifest.json`, TODOs por plan
(auditados), y follow-ups de evidencia. Este archivo es el índice
ejecutable: estado real + secuencia + pendientes. No es plan
harness (sin tasks propias); los stubs viven en `plans/`.
Espejo de seguimiento: grid EHV Tasks en AppFlowy (tareas AUTO-01..10
+ plan activo; el repo manda, AppFlowy refleja).

Regla: antes de implementar cada función nueva, plan formal con
alcance, fuente verificada, contrato de decisión, dependencias,
pruebas, revisión y despliegue. No se marca completo por escribir
el plan: se enlaza el plan y se conserva el seguimiento hasta
evidencia de implementación y validación.

## Estado por módulo (criterio del doc vs realidad)

### M1 Repricing — SIN PLAN (stub pendiente, AUTO-07)

- Criterio doc: cambia precio en ambas plataformas con historial.
- Realidad: 0%. Precondición `margen-estimado-01` cerrada para FBA MX;
  FBM y Amazon US siguen como ampliaciones explícitas.
- Gaps: sin stub; necesita SP-API Pricing/Competitivo (ver Fase 0;
  plan de lecturas: `plans/sp-api-01.md`).

### M2 Campañas por API — Amazon IMPLEMENTADO / CIERRE OPERATIVO PENDIENTE

- Criterio doc: Auto + Exact + Product Targeting en Amazon y básica
  en MeLi.
- Amazon: `fabrica-01` tareas 1–10 DONE + `fabrica-ui-01` en master verificado
  (13 passed) + `campanas-01`, `campana-activa-01`, `cortes-01`
  cerrados. Cumplido, EXCEPTO la sonda real (tarea 11, pendiente):
  shapes POST sellados como HIPOTESIS hasta la sonda; F2/AUTO-02
  depende de ella. Seguimiento: tarea ORBIT 17 en AppFlowy
  (In progress).
- MeLi Ads (proposal-only, bloqueado a nivel cuenta): sin plan ni
  stub. Gap.

### M3 Reputación — v1 CERRADA / v2 PENDIENTE (AUTO-09)

- Criterio doc (rating + historial + ≥3 alertas): cumplido (5 tipos,
  pantalla, digest, cron en vivo).
- `reputacion-01`: CERRADA 2026-09-08 (A.3 como ampliación E/A.3).
- `reputacion-02` (stub): v2 pendiente — incluye Account Health, Buy
  Box, fakes, sentimiento y vinculación con acciones (pausar
  campañas / no promocionar). Requiere SP-API (Buy Box, Account
  Health) + REP-FOLLOW-2 (datos Amazon).

### M4 Promociones — SIN PLAN (stub pendiente, AUTO-08)

- Criterio doc: crea descuento y simula margen.
- Realidad: 0%. Depende: Márgenes + Repricing (simulación).

### M5 Envíos — SIN PLAN (stub pendiente)

- Criterio doc: lista ambas plataformas con estado actualizado.
- Realidad: 0%. Depende: Orders APIs (SP-API + MeLi Orders/Shipments;
  lado SP-API en `plans/sp-api-01.md`).

### Fase 0 (modelos + auth APIs) — PARCIAL

- Ads API + MeLi OAuth: sí. SP-API: la auth LWA ya vive en
  producción (`app/estimacion_fees.py` + `app/publicacion_fotos.py`, dos
  refrescadores ad hoc por consolidar); plan de lecturas en
  `plans/sp-api-01.md` (Fase 0 Por probar, D1–D7 abiertas; escribir el plan
  no cierra nada). Gap: bloquea M1, M5 y reputación v2 parcial.
- Transversales: tokens 600/uid (NO cifrados at rest — gap
  declarado); sin colas/Redis por decisión (desviación consciente
  del doc); observabilidad de integraciones parcial.

## Secuencia propuesta (respeta dependencias)

1. Cierres operativos: sonda real `fabrica-01` tarea 11 y después
   `orbit-05` 2.3/2.5 cuando exista un harvest natural.
2. SP-API auth + lecturas (plan: `plans/sp-api-01.md`; la auth LWA ya
   funciona en producción, Fase 0 Por probar, D1–D7 abiertas) — desbloquea M1,
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
  stub.
- **Lead**: colas chicas (orbit-05
  2.3/2.5, bids-01 1.5, orbit-02 3.4); sonda real fábrica (tarea
  11, decide el lead); causa conciliación ventas/compras
  (orbit-19/0.3, no demostrada); stubs SP-API/repricing/
  meli-ads/promociones/envíos; REP-FOLLOW-1/3 (opcionales).
- **Higiene**: `cortes-ui-01` deduplicado en manifest 2026-09-08
  (quedó la entry con 1.2 pendiente de confirmación del dueño).

## Backlog oficial AUTO-01..AUTO-10 (no olvidar)

Registrado a solicitud del dueño 2026-09-06; absorbido aquí
2026-09-08 (los BK-1/BK-2/BK-3 temporales se pliegan: BK-1 en
AUTO-04, BK-2 en AUTO-05, BK-3 ya vivía en Fase 0). Evidencia
histórica del registro:
`docs/evidencia/AUTO-registro-20260906.md`.

| ID | Pendiente | Alcance del futuro plan | Base y dependencia | Estado |
|---|---|---|---|---|
| AUTO-01 | Verificar harvest automático completo | Seguir una propuesta real que cumpla las reglas: creación exacta, negativo de origen, readbacks, registro y recuperación. No forzar propuesta ni modificar vetos. | ORBIT 05 tareas 2.3/2.5; el mecanismo existe. No crear otro motor. | Pendiente de verificación operativa |
| AUTO-02 | Harvest por grupo de campañas | Enrutar hacia la hermana exacta, negativos cruzados y actualización de bibliotecas; compatibilidad con campañas existentes y reversa. | FABRICA 01 F2, fuera de F1. Coordinar con publicaciones/lotes v2 de ORBIT 19. | Pendiente de plan formal |
| AUTO-03 | Decisiones publicitarias por producto | Rendimiento Ads atribuible, margen, muestra, stock y disponibilidad para acciones y límites por producto. Distinguir pausar un product ad de desactivar la publicación. | ORBIT 19 entrega selector/comparación, no este motor. Fuentes por mercado conciliadas. | Pendiente de plan formal |
| AUTO-04 | Ajustes de placements | Top of Search, Product Pages y otros placements soportados; datos por ubicación, efecto sobre la puja efectiva, límites y reversa. Citado en orbit-03:128 y margen-estimado-01:156; schema listo (`ad_entity_kind`). | No hay motor de placements en Orbit. Confirmar fuente y API vigentes al planificar; no reutilizar el sistema viejo. | Pendiente de plan formal |
| AUTO-05 | Gestión de presupuestos | Redistribución entre campañas/grupos, ritmo de gasto y límites por moneda, con una sola autoridad de escritura y reversa. Incluye budgets intradía + AMS/Stream (orbit-03:128). | Hoy se capturan budgets al crear. Las cuotas de operaciones no son presupuestos de publicidad. | Pendiente de plan formal |
| AUTO-06 | Reactivación y limpieza del catálogo publicitario | Cuándo reactivar keywords/targets/campañas y si automatizar limpieza. Pausa reversible vs archivado irreversible; reponer crea identidad nueva sin historia. | Ya hay herramientas manuales y BIDS 01. No convertirlas en automatismo por defecto. | Pendiente de evaluación y plan |
| AUTO-07 | Repricing | Plan del motor de precios, con costos, margen, inventario, límites y reversa. Ver M1 arriba. | Módulos avanzados; precondición margen-estimado-01. | Pendiente de plan formal |
| AUTO-08 | Promociones | Plan de promociones y su efecto económico, con datos y autorizaciones explícitas. Ver M4 arriba. | Módulos avanzados; depende de Márgenes + Repricing. | Pendiente de plan formal |
| AUTO-09 | Reputación | Seguimiento y acciones de reputación. v1 cerrada (reputacion-01); v2 = reputacion-02 (stub). Ver M3 arriba. | Módulos avanzados; v2 requiere SP-API + REP-FOLLOW-2. | Plan v2 pendiente de brief |
| AUTO-10 | Estimación por venta antes de Ads | Precio, costo, comisiones, logística y retenciones verificables por publicación; desglose y ausencias explícitas. Comparación informativa. | `margen-estimado-01.md`; FBA MX desplegado. FBM/US quedan como ampliaciones. | Cerrado 2026-09-08 |

AUTO-01 a AUTO-05 son los gaps principales de la revisión. AUTO-06
a AUTO-09 conservan los pendientes adicionales mencionados. La
prioridad final y el orden entre planes se decidirán al planificar;
no se asignan fechas ni presupuestos aquí.

## Verificación de completitud (2026-09-08, rev 2)

- Los 5 módulos + Fase 0 + transversales del doc tienen fila aquí.
- Los planes del manifest están clasificados (cerrados, con TODO
  contados, o stubs); `active` declarado.
- AUTO-01..10 absorbidos con su alcance, base y estado; BK
  temporales eliminados (plegados, cero pérdida).
- Todos los TODO/WIP + follow-ups E/A.7 + gaps tienen dueño y
  secuencia. Auditoría 2026-09-08: cinco filas `cc:TODO/WIP` vivas
  (`bids-01` 1.5, `cortes-ui-01` 1.2, `orbit-02` 3.4 y `orbit-05`
  2.3/2.5), más la sonda `fabrica-01` tarea 11; docs/
  (specs/briefs/evidencia =
  históricos), AppFlowy y código (0 TODO reales). Faltantes
  hallados y agregados: sonda fábrica t11 + causa
  conciliación. Cero huérfanos conocidos. Espejo AppFlowy:
  tareas AUTO-01..10 + plan activo en EHV Tasks.
