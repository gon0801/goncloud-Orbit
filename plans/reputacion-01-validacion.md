# REPUTACION 01 — Validación formal de harness-plan

Plan oficial: `plans/reputacion-01.md` v1.4 (harness-plan, 2026-09-07).
`team_validation_mode`: subagent (4 perspectivas + síntesis). Este documento
es el registro de esa validación; el estado vivo está en el plan.

## Evidencia y memoria consultadas

- Plan v1.3, brief antecedente (schemas y conteos del backup
  `competitive-2026-08-22.db` verificados por el lead vía sqlite),
  `AGENTS.md`, `docs/CONTEXTO.md`, Módulo 3 de MODULOS-AVANZADOS,
  AUTO-09 (PR #186), estructura espejo ORBIT 19.
- Research externo 2026-09-07: actors Apify con cobertura .com.mx; endpoints
  MeLi (`/questions/search` con `seller_id=`/`item=`, superficies
  `/products/reviews`); Keepa API oficial con locale .com.mx. Todo marcado
  candidato: la sonda 0.x lo confirma o lo tumba.
- Memoria project-scoped: no existe `docs/specs/planes` ni servicio
  harness-mem; `.harness/` es local untracked. Sin colisiones con planes
  ajenos salvo migración (0023 tomada; el plan fija número contra HEAD).
- Baseline de calidad: ruff + pre-commit + CI quality existen; el plan los
  exige en A.7 (sin setup previo necesario: plan docs-only).

## Revisiones por perspectiva (síntesis 2026-09-07)

Veredicto global: **REQUEST_CHANGES** — 12 hallazgos accionables, nada
bloqueante, todo cerrable en acta 0.5 + DoD endurecidos. Atendidos en v1.4.

| Perspectiva | Aporte incorporado |
|---|---|
| Producto/datos | D1 condicionada a tabla recurrencia×cobertura de 0.2; claims acotados a MeLi vendedor (Amazon fuera v1); histéresis (flanco) y ventanas por recurrencia; scoring Required/Recommended/Optional/Reject |
| Arquitectura | Migración ≥0024 contra HEAD con orden vs B.6/F2; orden sugerido A.2→A.4→A.3; reuso `app/api_common.py` y convenciones de `app/cli.py`; Spec skip reason con contrato en 0.5 |
| Seguridad/QA | Cliente MeLi solo-GET default-deny + test; redacción centralizada (`app/redaction.py`) + test; atomicidad con rollback; escape de texto/links + tests; DoD y AC endurecidos |
| Escéptico | Sondas con comando+shape+muestra exigibles; seller_id vía `/users/me` (no el histórico); omitir+reportar (nunca rellenar); veredicto por fuente; D6 como mecanismo cap con reapertura si no cabe |

Contradicción aparente (D6 creíble vs sin evidencia de cabida): no dura — D6
vale como cap y 0.2 aporta la evidencia; si no cabe, D6 se reabre. Así quedó
en el plan.

## Puntuación de alternativas de alcance

Detalle y motivos en el plan (§ Alcance puntuado). Resumen: Required =
snapshots oficiales + MeLi lectura; Recommended = Apify si 0.2 + tope;
Optional = Account Health si gratis; Reject v1 = respuestas IA, acciones
automáticas, fakes, competidores, Buy Box intradía.

## Gates de planificación

| Gate | Estado |
|---|---|
| Spec/Plans fit | Spec skip reason registrado; contrato se fija en 0.5 con sondas reales |
| Memory/wheel | Reusa digest Telegram, `api_common.py`, convenciones CLI; nada duplicado |
| Product fit | Todo lo verificable entra; lo no verificado es ampliación, no cero |
| Security fit | Solo-GET MeLi, redacción, XSS, cero envíos; gasto con doble candado (0.2 + go) |
| Quality baseline | Ruff/pre-commit/CI existentes, exigidos en A.7 |
| Works in practice | AC1–AC8 + DoD endurecidos (rollback, redaction, flanco, escape, max_items) |

## Revisión final

Verificación mecánica: 13 tareas con ID único, dependencias existentes y sin
ciclos; todas con stage/lane/TDD. Siguiente verificación: acta 0.5 (cierra
D1/D3/D5/D6-detalle, granularidad, ventanas, latencia, propiedad), DoD en
verde y 0.2 demostrando cabida en $10 (o D6 se reabre). Esta validación no
autoriza implementación, sondas con costo, deploy ni gasto.
