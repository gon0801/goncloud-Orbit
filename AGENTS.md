# Orbit

Orbit optimiza campañas de Amazon Ads (Sponsored Products) con decisiones explícitas y auditables sobre cuánto pujar, qué pausar y qué cosechar. Opera para Amazon MX, Amazon US y Mercado Libre bajo el régimen mexicano de plataformas, donde las retenciones de ISR son costo de primer orden y Amazon las entrega sin `order_id`, por lo que se prorratean.

El roadmap en `docs/traspaso/MODULOS-AVANZADOS.md` suma cinco módulos: Repricing con inventario, Campañas (MeLi Ads solo como propuestas), Reputación, Promociones y Envíos. El sistema nace desde cero con base de datos nueva y reemplaza a `goncloud-MCP-2` sin reutilizar código viejo. Solo migra credenciales y datos verificados. Lee primero `docs/CONTEXTO.md` y `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md`.

Muse y los agentes que leen `AGENTS.md` ignoran `CLAUDE.md` en este repo. Toda regla que deba aplicar a todos los agentes va aquí también.

## Reglas de comportamiento

- Responde siempre en español, directo al grano y sin relleno.
- Entrega código completo y listo para usar, sin omisiones como `...`, escrito directo en los archivos con las herramientas.
- Entiende el código antes de cambiarlo. Prefiere patrones existentes, haz cambios mínimos y nunca inventes APIs ni credenciales.
- Ejecuta las comprobaciones que pide el plan aprobado en cada bloque, incluyendo la columna `Acepta` cuando exista. Amplíalas si el cambio toca un comportamiento sin cobertura. La suite completa solo es obligatoria cuando David o el plan aprobado la piden.
- Corrige cualquier regresión que introduzca tu cambio. Si hay dudas sobre el origen de un fallo relevante, compáralo contra la rama base en el mismo entorno. Un fallo previo se anota en el PR.

## Stack

- Python 3.12 o superior, FastAPI en `app/main.py`, PostgreSQL 16 en Docker enlazado en `127.0.0.1:5432` por túnel SSH, `psycopg` 3 y `httpx`. Sin Redis ni colas.
- El despliegue corre en el servidor `goncloud` (`ssh goncloud`) junto a `bridge` y `accounting`, que no se tocan. El procedimiento vive en `docs/DEPLOY.md` y la rama principal es `master`.

## Estructura

```
app/          # main.py (API), db.py (connect), redaction.py (secretos), ads/ (cliente Amazon READ-ONLY)
migrations/   # numeradas (0001 = esquema inicial); las *_reversa_* y las que salta verify/Launch.md no van en deploy; no son re-ejecutables
tests/        # suite pytest
tools/        # scripts operativos (fabrica_campanas.py como CLI del motor en app/fabrica_*.py, compara_target_margen.py, etc.)
verify/       # mapa de la app y pruebas e2e; receta de arranque en verify/Launch.md
docs/         # CONTEXTO.md, DATABASE.md, DEPLOY.md y traspaso/ (fuentes verbatim)
plans/        # manifest.json marca el plan ORBIT NN activo, sus tareas y su DoD
```

## Archivos principales

- `docs/CONTEXTO.md` y `docs/traspaso/ADS_OPTIMIZER_V2_DESIGN.md` contienen reglas y umbrales.
- `migrations/0001_initial.sql` fija el esquema inicial, `app/ads/client.py` contiene el guard de solo lectura y `plans/manifest.json` marca el plan activo.

## Comandos

```bash
uv sync                                        # dependencias en .venv/
uv run --frozen python -m pytest -q            # suite local; ORBIT_TEST_DSN apunta al PostgreSQL de prueba
ruff check --fix app tests tools && ruff format app tests tools  # lint y formato (line-length 100)
pre-commit run --all-files                     # gitleaks y conflictos
```

En Windows, el entorno virtual está en `.venv/Scripts/python.exe`. Sin PostgreSQL local, las pruebas que dependen de base de datos se saltan por diseño. Si un push se cuelga por credenciales, corre `gh auth setup-git`. La revisión local de secretos usa Gitleaks mediante pre-commit.

## Reglas de diseño innegociables

Estas reglas aplican a todo el código y su detalle vive en `docs/CONTEXTO.md`. Los invariantes de tiempo se implementan en triggers con UTC fijado, nunca en restricciones CHECK.

1. Una decisión tiene un solo camino y un solo dueño. Lo que no está en ese camino no se construye.
2. Un número tiene una sola fuente.
3. Un dato faltante se representa como `None` o como fila no escrita, nunca como constante inventada.
4. Todo monto lleva `(valor, moneda)` por esquema usando `NUMERIC(14,4)` y el ENUM `MXN`/`USD`. Está prohibido usar `float`.
5. Las métricas son append-only por `(entidad, metric_date, observed_at)`. Corregir un dato significa insertar una fila nueva.
6. Cortar o pausar exige datos con al menos 10 días de maduración. El día en curso se descarta.
7. Ninguna acción irreversible se enciende sin tener su reversa implementada antes.
8. Antes de definir un invariante que dependa de datos reales, corre el `SELECT` que confirma su forma en producción.
9. Toda prueba de regresión debe fallar primero contra el código anterior.
10. Se concilia contra la fuente externa, no contra la consistencia interna.

## Convenciones de código

- Usa español en comentarios, docstrings, pruebas y documentación, sin acentos en el código y siguiendo Ruff.
- No guardes secretos en el repositorio. Se cargan desde `ORBIT_SECRETS_DIR` y todo error o log pasa por `app/redaction.py`.
- El cliente de Amazon Ads aplica guard default-deny. Permite `GET` siempre y `POST` solo en `/reporting/reports`, rutas literales v3 `/list` de lectura y `/sp/targets/bid/recommendations` de lectura. Su interfaz exacta es `get`, `list_objects`, `recommend_bids`, `create_report`, `get_report` y `download`.
- La base de datos usa roles LOGIN por servicio (`orbit_ingest`, `orbit_decide`, `orbit_read` y `orbit_admin`). Cada invariante nuevo del esquema lleva su prueba y las decisiones de arquitectura van en `COMMENT ON` y docstrings.
- Los cambios de los planes entran por PR a `master`.

## Cómo trabajar con David

Habla conmigo en español claro. Toma mi petición y el plan que haya aprobado como guía para el trabajo. Usa los modelos, cuentas y destinos acordados para ese encargo.

Hazte cargo del resultado. Decide por tu cuenta lo que te haya delegado y comprueba que el resultado funciona donde voy a usarlo. Elige comprobaciones proporcionales al cambio.

Si algo falla, averigua por qué, corrígelo y continúa. Si un intento repite el mismo fallo, cambia de enfoque usando lo que aprendiste. El número de intentos, por sí solo, no decide cuándo parar.

Si falta algo que solo yo pueda aportar, o hace falta una decisión fuera del encargo, avanza con el resto y dime qué ocurrió, qué intentaste y qué decisión concreta necesitas. Al cerrar, dime qué quedó hecho, cómo lo comprobaste y qué sigue pendiente.

## Revisión de PRs, tope de rondas y continuidad nocturna

Esta política aplica en todos los repositorios de David y prevalece sobre cualquier plan, diseño, runbook o criterio previo que pida rondas infinitas o «cero esquinas reproducibles».

- **Revisores:** `ai-review` en GitHub (informativo) más el revisor asignado al encargo (cualquier modelo distinto al implementador; por ejemplo Astra u otro). Devin no cuenta como compuerta ni abre rondas.
- **Qué sí bloquea (P0/P1):** rompe lo pedido o la aceptación del bloque en uso real, falla de seguridad o fuga de secretos, o corrupción/pérdida de dinero o datos en operación normal o ante un fallo simple común.
- **Qué NO bloquea (P2 — va a notas o seguimiento, nunca frena el merge):** esquinas que requieren 2 o más fallos o carreras encadenadas, defensas hipotéticas fuera de alcance, o preferencias de estilo/estructura.
- **Tope de 3 rondas + 1 relevo fuerte:** los hallazgos de `ai-review` y del revisor asignado se atienden juntos en las mismas rondas (máximo 3).
  - Si al cerrar la ronda 3 **no quedan P0/P1 reales**, se anotan los P2 restantes y se integra.
  - Si tras la ronda 3 **sigue abierto un P0/P1 real**, se lanza automáticamente **1 solo relevo con contexto limpio y el modelo más fuerte disponible distinto al implementador y al revisor** (prioridad orientativa: Opus / Neon / Astra según disponibilidad), con un tope de **2 rondas** y aplicando `principle-attack-the-premise` para atacar la raíz en lugar de parchar.
  - Si incluso el relevo deja un **P0/P1 real abierto**, **NO se mergea roto ni se abren más rondas**: se estaciona el PR en **Draft** con resumen para David y el coordinador continúa automáticamente con el siguiente bloque o tarea independiente para no frenar la noche.
- **Skills de `pstack` por disparador (cuando estén disponibles en el entorno, nunca en cambios triviales):**
  - `architect`: antes de codear cambios complejos (dinero, concurrencia, migraciones, máquinas de estado o varios módulos).
  - `principle-attack-the-premise`: en el relevo o si un mismo P0/P1 se repite en ronda 2.
  - `fix-ci` y `fix-merge-conflicts`: cuando un PR tenga CI rojo o conflictos.
  - `show-me-your-work` / `ketchup`: en corridas nocturnas o desatendidas para dejar resumen claro de lo integrado y lo estacionado en Draft.
  - No crear loops tipo `babysit` ni watchers de LLM: la vigilancia externa la realiza Timbre + Agent Orchestrator.

## Grafo de código y mapa de pantallas

- Cuando exista `graphify-out/graph.json`, corre `graphify query "<pregunta>"` antes del primer grep, búsqueda o lectura de archivo para dudas del código, e incluye esta regla en todo prompt de subagente que explore código. Usa `graphify path "<A>" "<B>"` para relaciones y `graphify explain "<concepto>"` para conceptos puntuales. Después de modificar código, corre `graphify update .` para mantener el grafo al día.
- Antes de modificar o verificar pantallas del dashboard, lee `.cursor/skills/verify-orbit/SKILL.md` y la ficha correspondiente en `.cursor/skills/verify-orbit/features/<pantalla>.md`. Si cambias selectores, filtros o flujos visibles, actualiza la ficha en `features/` y comprueba la pantalla con `.cursor/skills/verify-orbit/helpers/orbit-verify` usando `launch`, `doctor`, `drive-*` y `cleanup`.
