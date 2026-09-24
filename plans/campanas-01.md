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
| 1.1 | **URGENTE** — Analisis completo de campanas pausadas: por cada campana PAUSED con historial (orders>0 o cost>0): ventana completa de metricas (cost/revenue/orders/ACOS historico y por mes), fecha de la pausa (si es deducible de metricas: ultimo dia con impresiones), entidades vivas dentro, y veredicto candidato (reactivar / reactivar con ajuste / dejar muerta) con la razon en datos. Incluye las campanias ENABLED con entidades sin impresiones 30d (AC, AU2, AGMX, AD_READY: diagnostico de por que no se sirven — bid bajo vs sin volumen). Reporte en `out/` + resumen al dueno. `[tdd:skip:analisis-datos]` | Reporte con TODAS las campanas pausadas con historial, numeros contra la base viva (regla 8), veredicto por campana con razon; el dueno puede decidir reactivar/dejar morir sin pedir mas datos | - | cc:完了 [de6cc7e] |

## Seguimiento Exact US (24-sep-2026 UTC)

Diagnostico solicitado por el dueno tras pausar A1U/AU2 Exact:
[`docs/evidencia/ads/2026-09-24-exact-us.md`](../docs/evidencia/ads/2026-09-24-exact-us.md).
Las filas de politica son propuestas pendientes; el cambio autorizado de
este bloque es ampliar a ~25 min la espera de reportes.

| Task | Trabajo | DoD | Status |
| --- | --- | --- | --- |
| 2.1 | Desplegar el timeout ampliado y recuperar la ingesta principal | Reportes tardios ingeridos; metricas recientes conciliadas con Amazon; checklist del deploy una vez | cc:TODO |
| 2.2 | Dar prioridad a PAUSE sobre el cooldown de bids | Politica aprobada; reproduccion historica de keyword 4925 desde 14-sep; regresion y pausa evaluada cada ciclo con madurez y veto vigentes | cc:TODO |
| 2.3 | Definir proteccion economica aunque haya ventas y a nivel campana | Limites explicitos por moneda/goal, evidencia madura, reversa y replay sin lookahead; cubrir tolerancia adaptativa creciente y puja en el piso | cc:TODO |
| 2.4 | Detectar y recuperar atraso de la ingesta principal Ads | Salud por pipeline/plataforma; productos no oculta fallo principal; aviso temprano y comportamiento explicito del motor ante atraso | cc:TODO |

## Notas de reactivacion

- NO ejecutar reactivaciones: el analisis informa, el dueno decide y ejecuta
  (o encarga) la reactivacion. Si se reactivan las Exact US, eso destraba
  los goals harvest de ORBIT 04 task 4.2 (que exige destinos ENABLED).
- Candidato natural a correrlo: una sesion de analisis con acceso a la base
  viva (tunel); no requiere codigo nuevo ni deploy.
