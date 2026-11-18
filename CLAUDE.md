# Orbit

Sistema **nuevo desde cero** (base de datos nueva) que reemplaza al stack de
motores de Amazon Ads (`goncloud-MCP-2`, cancelado 2026-08-21). No se reutiliza
código viejo; solo se migran credenciales y datos verificados.

**Leer primero:** `docs/CONTEXTO.md` — reglas de diseño innegociables, trampas
del dominio, qué se migra y qué no, fases. Las fuentes verbatim están en
`docs/traspaso/` (diseño v2 = fuente de verdad de reglas y umbrales).

Los planes de Orbit se entregan al motor como encargo (`motor director encargar`).

## Reglas de trabajo

Iguales en todos los repos de David. Si otro documento de este repo dice otra cosa sobre calidad,
candados, evidencia, rondas o pruebas, manda esto. Ninguna regla debe dejar un trabajo parado: si
una no se puede cumplir, se sigue y se anota por qué.

- Todo cambio entra por PR; nadie sube directo a la rama principal.
- Pruebas nuevas solo en 4 casos: dinero (precios, pujas, presupuestos, cobros), escribir datos de
  producción, instalar o borrar en las máquinas, y una falla que ya pasó en producción (una prueba,
  no una batería). Lo demás: basta comprobar que corre.
- Las pruebas que ya existen deben pasar antes de mergear. Si una ya falla igual en la rama
  principal, no frena: se anota en el PR.
- Revisa un modelo distinto del que escribió; si no está disponible, el siguiente que sí lo esté.
  Nunca se espera a una persona. Solo bloquea por seguridad, datos, el comportamiento pedido roto o
  uno de los 4 casos sin su prueba, siempre con el comando que lo reproduce. Lo demás va en notas.
- DeepSeek opina en el PR; el revisor decide. Un hallazgo Low nunca abre otra vuelta.
- Máximo 2 rondas con el mismo agente. Si lo que queda no es grave, se mergea y lo pendiente va en
  notas. Si queda algo grave, la 3.ª ronda la hace otro modelo más fuerte; solo se detiene si ese
  tampoco puede, y dice por qué.
- Un cambio grande que altera algo que ya corre en producción va detrás de un interruptor apagado
  y se enciende después.
- Los encargos o contratos para otro agente son cortos: qué debe pasar y cómo se comprueba.
- Nunca subas llaves ni contraseñas; no uses `--no-verify` sobre la revisión de secretos. Si marca
  algo que no es una llave, se agrega a `.gitleaksignore` con su razón y se sigue.
- No agregues compuertas (tamaño, lint, tipos, checklists, revisores extra, baterías, CI
  obligatorio) ni revisiones cruzadas sin que David lo pida.

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
