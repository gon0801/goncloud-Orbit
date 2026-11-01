# Orbit

Sistema **nuevo desde cero** (base de datos nueva) que reemplaza al stack de
motores de Amazon Ads (`goncloud-MCP-2`, cancelado 2026-08-21). No se reutiliza
código viejo; solo se migran credenciales y datos verificados.

**Leer primero:** `docs/CONTEXTO.md` — reglas de diseño innegociables, trampas
del dominio, qué se migra y qué no, fases. Las fuentes verbatim están en
`docs/traspaso/` (diseño v2 = fuente de verdad de reglas y umbrales).

Los planes de Orbit se corren con el motor (`autopiloto: motor`).

## Pruebas y revisiones
- Pruebas nuevas SOLO en 4 casos: dinero (pujas, precios, presupuestos, cobros), escribir datos de produccion
  (base del servidor, Amazon, Mercado Libre, Shopify, Odoo), instalar/borrar/deshacer en las maquinas, y una
  falla que ya paso en produccion (una prueba, no una bateria). Lo demas: basta comprobar que corre. Nunca
  pruebas de texto, de las pruebas ni mutantes. Un revisor no pide pruebas fuera de eso.
- Un revisor solo bloquea por seguridad, datos, el comportamiento pedido roto o un caso de los 4 sin su
  prueba, siempre con el comando que lo reproduce. Lo demas va en notas y se corrige en la misma ronda.
- Nunca subir contrasenas ni llaves a un repo.
## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
>>>>>>> 8f2d33a9 (chore: integrar Graphify (mapa de código consultable por agentes))
