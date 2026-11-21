# Orbit

Orbit es un sistema construido desde cero con base de datos nueva. Reemplaza al stack anterior de Amazon Ads (`goncloud-MCP-2`, cancelado el 2026-08-21). No reutiliza código viejo y solo migra credenciales y datos verificados.

Lee primero `docs/CONTEXTO.md` para revisar las reglas de diseño, las trampas del dominio, el alcance de migración y las fases. Las fuentes verbatim viven en `docs/traspaso/`, donde `ADS_OPTIMIZER_V2_DESIGN.md` fija las reglas y umbrales base.

El plan activo está en `plans/manifest.json` e indica quién lo implementa y quién lo revisa.

## Cómo trabajar con David

Habla conmigo en español claro. Toma mi petición y el plan que haya aprobado como guía para el trabajo. Usa los modelos, cuentas y destinos acordados para ese encargo.

Hazte cargo del resultado. Decide por tu cuenta lo que te haya delegado y comprueba que el resultado funciona donde voy a usarlo. Elige comprobaciones proporcionales al cambio.

Si algo falla, averigua por qué, corrígelo y continúa. Si un intento repite el mismo fallo, cambia de enfoque usando lo que aprendiste. El número de intentos, por sí solo, no decide cuándo parar.

Si falta algo que solo yo pueda aportar, o hace falta una decisión fuera del encargo, avanza con el resto y dime qué ocurrió, qué intentaste y qué decisión concreta necesitas. Al cerrar, dime qué quedó hecho, cómo lo comprobaste y qué sigue pendiente.

## Grafo de código (graphify)

El proyecto mantiene un grafo de dependencias y relaciones entre archivos en `graphify-out/`.

Reglas de uso:

- Cuando exista `graphify-out/graph.json`, corre `graphify query "<pregunta>"` antes del primer grep, búsqueda o lectura de archivo para dudas del código. Incluye esta misma regla en el prompt de cualquier subagente que explore código. Lee archivos directos solo en las líneas que el grafo o una lectura previa ya ubicaron. Usa `graphify path "<A>" "<B>"` para relaciones y `graphify explain "<concepto>"` para conceptos puntuales.
- Si existe `graphify-out/wiki/index.md`, úsalo para navegación general en lugar de recorrer fuentes a mano.
- Lee `graphify-out/GRAPH_REPORT.md` solo para revisiones generales de arquitectura o cuando `query`, `path` y `explain` no alcancen.
- Después de modificar código, corre `graphify update .` para actualizar el grafo. Solo analiza AST y no tiene costo de API.
- Un `graphify-out/graph.json` commiteado se reescribe completo en cada reconstrucción, por lo que un diff grande en un PR es normal. No lo marques como ruido ni sugieras saltar la reconstrucción.

## Mapa de pantallas (verify-orbit)

- Antes de modificar o verificar pantallas del dashboard, lee `.cursor/skills/verify-orbit/SKILL.md` y la ficha correspondiente en `.cursor/skills/verify-orbit/features/<pantalla>.md`.
- Si cambias selectores, filtros o flujos visibles, actualiza la ficha en `features/` y comprueba la pantalla con `.cursor/skills/verify-orbit/helpers/orbit-verify` usando `launch`, `doctor`, `drive-*` y `cleanup`.
