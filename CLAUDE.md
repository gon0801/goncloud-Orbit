# Orbit

Sistema **nuevo desde cero** (base de datos nueva) que reemplaza al stack de
motores de Amazon Ads (`goncloud-MCP-2`, cancelado 2026-08-21). No se reutiliza
código viejo; solo se migran credenciales y datos verificados.

**Leer primero:** `docs/CONTEXTO.md` — reglas de diseño innegociables, trampas
del dominio, qué se migra y qué no, fases. Las fuentes verbatim están en
`docs/traspaso/` (diseño v2 = fuente de verdad de reglas y umbrales).

El plan activo de Orbit está en `plans/manifest.json` y especifica quién lo implementa y lo revisa.

## Cómo trabajar con David

Habla conmigo en español claro. Toma mi petición y el plan que haya aprobado como guía para el trabajo. Usa los modelos, cuentas y destinos acordados para ese encargo.

Hazte cargo del resultado. Decide por tu cuenta lo que te haya delegado y comprueba que el resultado funciona donde voy a usarlo. Elige comprobaciones proporcionales al cambio.

Si algo falla, averigua por qué, corrígelo y continúa. Si un intento repite el mismo fallo, cambia de enfoque usando lo que aprendiste. El número de intentos, por sí solo, no decide cuándo parar.

Si falta algo que solo yo pueda aportar, o hace falta una decisión fuera del encargo, avanza con el resto y dime qué ocurrió, qué intentaste y qué decisión concreta necesitas. Al cerrar, dime qué quedó hecho, cómo lo comprobaste y qué sigue pendiente.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.

## Feature Map (`.cursor/skills/verify-orbit/`)

- Antes de tocar o verificar pantallas del dashboard, lee `.cursor/skills/verify-orbit/SKILL.md` y la ficha de la pantalla en `.cursor/skills/verify-orbit/features/<pantalla>.md`.
- Si cambias selectores, filtros o flujos visibles, actualiza su ficha en `features/` y comprueba la pantalla con `.cursor/skills/verify-orbit/helpers/orbit-verify` (`launch`, `doctor`, `drive-*`, `cleanup`).
