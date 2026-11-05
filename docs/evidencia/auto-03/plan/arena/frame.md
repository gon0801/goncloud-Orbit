# Arena AUTO-03: marco (Phase A del skill arena, dentro de architect Phase B)

Fecha: 2026-11-04. Lead: sesión de Claude Code en la Mac. Base: `origin/master` (el checkout local `resumen-ads-04-diseno` en `5adf1be0` solo difiere en `app/meli/catalogo.py` y `migrations/0075_meli_sku_alias.sql`).

## Artefacto que produce cada candidato

Un **plan de construcción de AUTO-03** en el formato que valida `motor valida` (ver "Formato"), más una **rationale** según `~/.claude/skills/architect/references/rationale-template.md`. El plan es el artefacto que correrá el motor (`autopiloto: motor`): bloques, filas con `Acepta`, `prueba-real`, dependencias, despliegue con receta propia y los supuestos de política que la spec dejó abiertos, resueltos como supuestos explícitos (el motor no le pregunta nada a David; un plan es autorización completa).

El **diseño** ya está sintetizado en `docs/superpowers/specs/2026-10-24-decisiones-producto-design.md` (dos candidatos + comparación + síntesis en `docs/evidencia/auto-03/diseno/`). Los candidatos de esta arena NO rediseñan la política económica; sí deben **corregir la spec donde el código la contradice** (lista consolidada en `docs/evidencia/auto-03/plan/ground/00-explicacion.md`, sección Gotchas) y decidir la **secuencia de construcción**.

## Grounding (leer completo antes de escribir)

1. `docs/superpowers/specs/2026-10-24-decisiones-producto-design.md` (spec).
2. `docs/evidencia/auto-03/plan/ground/00-explicacion.md` (explicación sintetizada + gotchas consolidados).
3. `docs/evidencia/auto-03/plan/ground/01-ciclo-y-cascada.md`, `02-cola-retiro-autorizacion.md`, `03-datos-producto-margen.md` (hallazgos con archivo:línea).
4. `docs/evidencia/auto-03/diseno/lectura-2026-10-25.md` (datos reales: 7 productos MX y 3 US con margen; Ads por producto desde 2026-08-08).
5. `docs/CONTEXTO.md` (reglas 1-10) y `AGENTS.md` (pruebas solo en 4 casos; revisor; CI).
6. Formato del motor: `~/dev/goncloud-motor/skill/motor/SKILL.md` y `~/dev/goncloud-motor/motor/plan.py` (docstring + validaciones). `.motor.toml` de Orbit: `requiere = ["ssh-goncloud"]`, sin receta de despliegue por omisión → un bloque `release` trae `despliegue: desplegar=<ruta> checklist=<ruta> rollback=<ruta>` con scripts que el propio plan crea (la receta manual vive en `docs/DEPLOY.md` §"Servicio app": `git archive origin/master … | ssh goncloud tar -xf -`, `docker compose up -d --no-deps --build app`, `/health`; migraciones en §"Aplicar migraciones").
7. Ejemplo de plan reciente (estilo de filas, no formato motor): `plans/resumen-ads-multicanal.md`.

## Rúbrica (la usa el pick en Phase D; los candidatos solo ven la tarea, pero no está prohibido que la lean)

| # | Criterio | Qué se gradúa |
|---|---|---|
| R1 | Fidelidad al código | Cada fila nombra firmas, tablas, CHECKs y módulos vigentes según el ground; cero APIs inventadas; cada contradicción spec↔código del ground queda resuelta en el plan (corrige la spec o la obedece y dice por qué). |
| R2 | Secuencia verificable, riesgo creciente | Cada bloque deja algo comprobable con datos reales antes del siguiente; nada toca dinero ni Amazon sin reversa construida y probada antes (regla 7); escalera off→shadow→live con interruptor propio; la medición de elegibilidad real ocurre ANTES de prometer cobertura. |
| R3 | Un dueño, un camino | Pausas por `apply_cola` + `retiro_anuncios` sin segundo escritor ni segundo encolador; colapso/madurez de métricas de producto reutiliza `evaluacion_catalogo` o lo sustituye (no lo duplica); una sola fuente de margen; peldaño nuevo con todos sus consumidores migrados en el mismo bloque. |
| R4 | Pruebas y revisión proporcionales | Pruebas nuevas solo en los 4 casos de AGENTS.md (dinero, escritura productiva, máquinas, falla real), una regresión demostrada fallando; `Acepta` ejecutables que salen 0 en el SHA; `prueba-real` de solo lectura contra producción. |
| R5 | Válido para el motor y autónomo | `motor valida` pasa; bloques humanos solo `login\|2fa\|captcha\|pago`; ningún "pedir go al dueño"; las decisiones de política abiertas en la spec se fijan como supuestos explícitos y reversibles (interruptor apagado por omisión), no como preguntas. |
| R6 | Carga del implementador | Bloques del tamaño de un PR revisable; archivos exactos; respeta `MAX_LINEAS_MODULO=900` y la allowlist; sin narrativa ni diario; ≤ ~250 líneas de plan. |

## Runners

`architect runners` en `~/.claude/pstack-models.md`: `opus, sonnet` → runner A en `opus`, runner B en `sonnet` (effort alto). Para garantizar dos formas estructuralmente distintas (architect: "design it twice"), cada runner recibe una **semilla de forma** distinta; puede desviarse si argumenta que la semilla es peor, pero debe nombrar la alternativa rechazada:

- **A — rebanadas por capacidad:** primero todo lo del target por producto (lectura, peldaño, migración del CHECK, consumidores, pantalla) hasta live; después todo lo de la pausa por producto (kind nuevo o reuso de `pause`, cola, retiro, reversa) hasta live.
- **B — sombra completa primero:** primero el camino end-to-end en sombra (evaluación persistida en TX3, pantalla, replay) para target Y pausa sin ningún efecto; medir elegibilidad real; después encender efectos uno a uno (target, luego pausa) con sus reversas.

## Salidas

- Runner A: `docs/evidencia/auto-03/plan/arena/candidatos/a/plan.md` y `.../a/rationale.md`.
- Runner B: `docs/evidencia/auto-03/plan/arena/candidatos/b/plan.md` y `.../b/rationale.md`.
- Cross-judge (Phase C): el pool `arena cross-judge pool: opus, sonnet` es toda familia Claude, igual que el padre; por regla del skill se omite el juez y el lead elige base solo (Phase D), dejando constancia.
- Síntesis: `plans/decisiones-producto-01.md` (plan final) + `docs/evidencia/auto-03/plan/sintesis.md` (nota de síntesis: base, injertos, rechazos, verificación con `motor valida`).
