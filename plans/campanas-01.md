# CAMPANAS 01 — Auditoria de campanas pausadas y candidatas a reactivar

> **Propósito**: el diagnostico del 2026-08-26 (spec
> `docs/superpowers/specs/2026-08-26-banda-cero-ventas-design.md`) encontro
> que la mayoria de las entidades ENABLED sin impresiones viven en campanias
> PAUSED — y varias de esas campanias tienen historial RENTABLE (Arras
> Manual: $2,348 gasto / $12,166 revenue, ACOS ~19%; AGM2M Auto MX: $2,162 /
> $5,179; A1U Exact+Phrase: $785 / $2,317). No se sabe si las pausas fueron
> decision del dueno o herencia del sistema viejo. Reactivar es decision
> humana; este plan solo produce el analisis completo para decidir.
> Relacion con el spec: la "superficie de diagnostico" (opcion C sellada)
> muestra el sintoma; este plan responde la pregunta de negocio.

## Phase 1 — Analisis [lane:analisis]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **URGENTE** — Analisis completo de campanas pausadas: por cada campana PAUSED con historial (orders>0 o cost>0): ventana completa de metricas (cost/revenue/orders/ACOS historico y por mes), fecha de la pausa (si es deducible de metricas: ultimo dia con impresiones), entidades vivas dentro, y veredicto candidato (reactivar / reactivar con ajuste / dejar muerta) con la razon en datos. Incluye las campanias ENABLED con entidades sin impresiones 30d (AC, AU2, AGMX, AD_READY: diagnostico de por que no se sirven — bid bajo vs sin volumen). Reporte en `out/` + resumen al dueno. `[tdd:skip:analisis-datos]` | Reporte con TODAS las campanas pausadas con historial, numeros contra la base viva (regla 8), veredicto por campana con razon; el dueno puede decidir reactivar/dejar morir sin pedir mas datos | - | cc:完了 [1814e4d] |

## Notas

- NO ejecutar reactivaciones: el analisis informa, el dueno decide y ejecuta
  (o encarga) la reactivacion. Si se reactivan las Exact US, eso destraba
  los goals harvest de ORBIT 04 task 4.2 (que exige destinos ENABLED).
- Candidato natural a correrlo: una sesion de analisis con acceso a la base
  viva (tunel); no requiere codigo nuevo ni deploy.
