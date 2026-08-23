# Plans — GUARDRAILS 01: candados anti-monolito (complejidad, fronteras, tamaño)

> Purpose: convertir en CANDADOS AUTOMÁTICOS las tres defensas contra el
> monolito inauditable (la muerte del sistema viejo: 62 módulos / 206 flags /
> 147 jobs para 3 decisiones — Traspaso 2). Hoy esas defensas viven en
> revisiones humanas y en el diseño del plan; después de esto, viven en
> pre-commit + CI y no dependen de que alguien las recuerde.
>
> Contexto: decisión del dueño (sesión 2026-08-23) tras la pregunta "¿cómo
> evitamos un monolito gigante inauditable?". Se ejecuta sobre la rama
> `orbit-03/phase-2` DESPUÉS de que 2.2–2.4 pasen review (los candados deben
> nacer verdes contra el motor completo, no contra la mitad), y entra al
> PR #12 como commits propios. Registro: la evidencia va a la fila canónica
> `ORBIT 03` de AppFlowy (el trabajo embarca en su PR); no se crea fila nueva.
>
> Spec skip reason: infraestructura de calidad sin cambio de comportamiento
> de producto — no toca API, datos, permisos ni decisiones del motor; el
> product contract (docs/CONTEXTO.md + diseño v2) no cambia.
>
> team_validation_mode: manual-pass (3 tareas mecánicas de tooling; pase
> manual de perspectivas: Architecture = límites elegidos contra el código
> real, no aspiracionales; QA = candados demostrados fallando (regla 9);
> Security = sin secret-read, sin superficie nueva; Skeptic = riesgo
> principal es un candado tan laxo que no muerde — mitigado exigiendo que
> cada límite quede a distancia declarada del máximo actual del repo).
>
> unknown declarado: los máximos reales de complejidad/tamaño del código de
> 2.2–2.4 aún no existen (GLM implementando). Las tareas los miden ANTES de
> fijar límites — `not_observed != absent`.

## Phase 1 — Candados [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Presupuesto de complejidad en ruff: activar `C901` (mccabe) y `PLR0912`/`PLR0915` en pyproject. PRIMERO medir el máximo real del repo (`ruff check --select C901,PLR0912,PLR0915` con límites bajos para censar); fijar límites = máximo actual + margen chico (nunca aspiracional que rompa el repo, nunca tan laxo que no muerda; documentar el porqué de cada número en un comentario del pyproject). Registrar `plans/guardrails-01.md` en `plans/manifest.json` en este mismo commit (active sigue siendo orbit-03). `[tdd:skip:config-de-lint]` | `pre-commit run --all-files` y CI verdes CON las reglas activas; los límites elegidos y su censo (máximo actual por regla) documentados en el comentario del pyproject; una función sintética que exceda el límite es rechazada por ruff (probado localmente, salida en la evidencia) | 2.2-2.4 de orbit-03 en review APPROVE | cc:完了 |
| 1.2 | Test de fronteras de imports (`tests/test_architecture.py`): por AST (sin importar módulos), afirmar que ningún módulo de `app/optimizer/` importa `httpx`, `psycopg`, `app.ads` ni `app.db` — con la ÚNICA excepción declarada de `windows.py` (la puerta de datos, que puede importar `psycopg`/`app.db` pero tampoco `httpx`/`app.ads`). El motor puro no puede hacer IO ni hablar con la ingesta: es la regla 1 de la autopsia convertida en test. `[tdd:required]` | Test verde contra el motor completo (2.1–2.4); demostrado fallando (regla 9): con un import prohibido inyectado temporalmente en un módulo del motor, el test truena con mensaje que nombra módulo e import (evidencia literal en el red-log `.claude/state/tdd-red-log/g1.2.jsonl`) | 2.2-2.4 de orbit-03 en review APPROVE | cc:完了 |
| 1.3 | Presupuesto de tamaño por módulo (mismo `tests/test_architecture.py`): fallar si un `.py` de `app/` excede 900 líneas salvo entrada en allowlist explícita `{path: razón}` en el propio test. Sembrar la allowlist con la realidad: `app/ads/reports.py` (~1,500 líneas; razón: pipeline compartido métricas+terms, candidato declarado a partirse en report_pipeline/metrics/terms cuando se toque de nuevo). Crecer la allowlist exige editar el test = decisión visible en diff y review, nunca deriva silenciosa. `[tdd:required]` | Test verde con la allowlist actual; demostrado fallando (regla 9): bajando el umbral temporalmente el test lista los módulos excedidos con sus líneas (evidencia en red-log); la allowlist tiene razón escrita por entrada | 1.2 | cc:完了 |

## Regla anti-Goodhart (sellada, aplica a los 3 candados)

Cuando un candado dispare, las salidas VÁLIDAS son exactamente dos:
(a) simplificar de verdad, o (b) `noqa`/allowlist CON razón escrita que pasa
por review. **PROHIBIDO el gaming**: partir una función/módulo coherente en
pedazos incoherentes solo para esquivar el número es peor código y se
rechaza en review — un `noqa: C901` justificado y visible vale más que tres
helpers sin sentido. Los límites nacen del censo del código real (aprobado
por 5 revisores) + margen: su trabajo es cazar DERIVA futura, no forzar
refactors del presente.

## Priorización

- **Required**: 1.1, 1.2, 1.3 (las tres defensas acordadas; juntas son ~1
  sesión corta de trabajo).
- **Recommended**: al partir `reports.py` en el futuro, sacar su entrada de
  la allowlist en el mismo PR del split (queda anotado en la razón de 1.3).
- **Optional**: `Literal` en `guarda_plataforma` (minor declarado de la
  review de 2.1) — puede colarse en 1.2 si el archivo ya está abierto, sin
  ampliar scope.
- **Reject** (con razón):
  - Cap de líneas vía plugin externo de flake8/pylint: dependencia nueva
    para lo que un test de 30 líneas hace auditable en el propio repo.
  - Presupuesto de imports para `app/ads/`: la ingesta ES la capa de IO;
    un candado ahí no protege nada y agrega ruido.
  - Umbral aspiracional (p.ej. 500 líneas) que obligue a partir módulos ya
    aprobados: churn sin bug; el candado es contra la DERIVA futura.

## 事前確認

- 事項: external-send — `git push` de los commits de candados a la rama `orbit-03/phase-2` (entran al PR #12 existente)
  理由: los candados cierran la phase 2 dentro de su mismo PR; cubierto por el preapproval v2 vigente de ORBIT 03 (git push + PR por phase, hasta 2026-09-21)
  scope: Phase 1 / Tasks 1.1-1.3
- (sin secret-read; sin operaciones destructivas)
