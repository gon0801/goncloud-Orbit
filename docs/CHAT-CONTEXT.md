# Orbit — contexto para Claude Chat

> Archivo mantenido por la sesión lead de Claude Code: se actualiza al cierre
> de cada phase. Si la fecha de abajo se ve vieja, pide al dueño que haga
> "Sync now" en el Project o pregúntale el estado antes de asumir.

**Última actualización: 2026-08-23 — Phase 1 y Phase 2 de ORBIT 03 completas
y revisadas; Phase 3 (orquestador) en implementación.**

## Qué es Orbit

Sistema nuevo desde cero que optimiza **Amazon Ads (Sponsored Products)** para
un negocio que vende en **Amazon MX y US** (y opera también Mercado Libre)
bajo el régimen mexicano de plataformas tecnológicas. Decide, con reglas
explícitas y auditables: **cuánto pujar, qué pausar, qué términos negativizar
y qué harvestear**. Reemplaza a un sistema viejo (`goncloud-MCP-2`, apagado el
2026-08-22) que murió por monolito: 62 módulos, 206 flags y 147 jobs para 3
decisiones. Orbit arranca en modo **shadow**: decide y registra todo, pero NO
escribe nada a Amazon hasta pasar validación humana (el "apply" llega en PR2).

## Estado actual (se actualiza por phase)

- **Hecho y en master**: toda la ingesta (cliente HTTP read-only con redacción
  de secretos, sync de estructura, pipeline de métricas y search terms,
  backfill histórico completo: 95 días de métricas, ~65 de terms, us y mx).
- **Hecho en el PR #12 (rama de Phase 2)**: la capa de ventanas de datos y el
  motor de decisión puro (bids, hygiene, goals) con todos los umbrales
  sellados y testeados; candados anti-monolito activos (complejidad,
  fronteras de imports, tamaño de módulos).
- **En curso**: 3.1 el orquestador del ciclo (une ventanas + motor y escribe
  la auditoría). Siguen: 3.2 API de lectura, 3.3 CLI, y Phase 4 (deploy,
  crons, seed humano de goals, primer shadow real).
- **Datos reales ya en la base viva** (Postgres en el server `goncloud`):
  5,897 entidades, ~22,000 observaciones de métricas, ~6,900 de search terms.

## Cómo se construye (roles)

- **GLM** (otra sesión de IA) implementa las tareas del plan.
- **Claude Code (Fable, sesión lead)** revisa cada tarea con un reviewer de
  contexto fresco, verifica contra la base viva, y mantiene tracker y docs.
- **Cross-reviews** con otras IAs (Codex, Grok, Kimi, CodeRabbit, Greptile)
  por tarea; tope duro de 2 rondas.
- **Gon (el dueño)** decide QUÉ se construye, aprueba planes, y tiene dos
  checkpoints humanos en Phase 4: elegir campañas piloto (4.3) y el
  spot-check manual de ≥20 decisiones del primer shadow (4.4).
- Un PR por phase; nada llega a master sin CI verde y reviews atendidas.
- Registro de trabajo: fila `ORBIT 03` en AppFlowy (EHV Tasks).

## Arquitectura (mapa de carpetas)

```
app/
├── redaction.py   ← ningún secreto sale en logs/errores
├── db.py          ← conexión a Postgres (DSN redactado)
├── ads/           ← ESTACIÓN 1: hablar con Amazon (única capa con internet)
│   ├── client.py     cliente READ-ONLY (sin capacidad física de mutar campañas)
│   ├── structure.py  catálogo: campañas, ad groups, keywords, targets
│   └── reports.py    métricas y search terms → base de datos
└── optimizer/     ← ESTACIÓN 2: pensar (PURO, sin internet ni base — por test)
    ├── windows.py    única puerta a la base: ventanas de datos colapsadas
    ├── bid.py        decisiones de puja y pause
    ├── hygiene.py    negative exact y harvest
    └── goals.py      metas por campaña/plataforma, modo efectivo, cooldown
app/cycle.py (en construcción) ← orquestador: ventanas → motor → auditoría
```

Flujo de una vía: Amazon → `ads/` → Postgres → `windows.py` → motor →
tabla `decision` (auditoría) → API/CLI de lectura.

## Reglas de diseño selladas (resumen de docs/CONTEXTO.md)

1. Una decisión, un camino, un dueño — no se construye lo que no decide.
2. Un número, una fuente.
3. Dato faltante = None y la fila no se escribe; JAMÁS una constante inventada.
4. Todo dinero lleva (valor, moneda); mezclar monedas es imposible por schema.
5. Métricas append-only bitemporales; el motor colapsa a la última observación.
6. Cortes (pause/negative/harvest) exigen datos con ≥10 días de maduración.
7. Nada irreversible sin su reversa implementada antes.
8. Verificar la forma real del dato en producción antes de testear invariantes.
9. Toda prueba de regresión se demuestra fallando contra el código anterior.
10. Conciliar contra la fuente externa, no contra consistencia interna.

## Umbrales del optimizador (sellados; fuente: diseño v2)

| Decisión | Regla |
|---|---|
| PAUSE | orders=0 ∧ clicks≥25 ∧ cost≥ {us: 12 USD, mx: 200 MXN} |
| Bajar puja −25% | ACoS > 1.35×target (con orders≥1) |
| Bajar puja −12% | ACoS > 1.15×target |
| Subir puja +15% | ACoS < 0.85×target ∧ orders≥3 |
| NEGATIVE_EXACT | orders=0 ∧ clicks≥20 ∧ cost≥ {us: 8, mx: 130}; ASIN-like nunca |
| HARVEST | orders≥2 ∧ ACoS ≤ min(35%, target); exige config completa en el goal |

ACoS = cost / ad_revenue COMPLETO (halo incluido). Clamp por decisión
[−30%, +20%]; resultado dentro de [floor, ceiling] (defaults 0.10/2.50).
Target ACoS en cascada: goal → config global → cache del estado → default 55.
Precedencia: PAUSE gana a todo; −25 gana a −12. Modo: off→shadow→live, y en
PR1 'live' degrada a shadow (fail-closed).

## Trampas del dominio (nunca olvidarlas)

- **Tres relojes**: la venta atribuida madura en 5–8 días, el costo hasta el
  día 15, los fees a 15–30. Un ACoS de día 1 en MX sale ~1.5× peor que el real.
- **Halo**: 56–58% del ingreso atribuido es de OTROS SKUs. Es atribución de
  Amazon, no causalidad. Por eso el ACoS usa el revenue completo.
- **Monedas**: el sistema viejo mezcló MXN/USD (error de 18.66× a favor de
  "todo es rentabilísimo"). En Orbit es imposible por schema.
- **El día en curso** llega incompleto y etiquetado con su fecha: el cron
  re-tira D-8..D-1 y la bitemporalidad lo hace seguro.
- **La pregunta sin respuesta**: si la cuenta US gana o pierde depende del
  supuesto de halo (entre +1,671 y −2,238 USD en 91 días). Decisión pendiente
  antes de la fase margin-aware: acotar, holdout, o TACoS.

## Glosario rápido

- **Shadow**: el motor decide y registra, pero no toca Amazon.
- **Harvest**: promover un término de búsqueda ganador a keyword exact propia.
- **ASIN-like**: término que es un código de producto (B0 + 8 caracteres);
  jamás se negativiza.
- **Bitemporal**: cada métrica guarda el día del hecho Y el momento en que se
  observó; permite reconstruir "qué sabía el motor cuándo".
- **Watermark**: última fecha con datos por plataforma; si está vieja (>7d),
  el ciclo se salta esa plataforma.
- **Golden replay**: test que re-alimenta los inputs congelados de una
  decisión al motor y debe reproducirla idéntica — la garantía de que toda
  decisión es auditable.
- **Candados anti-monolito**: tests y linters que hacen imposible que el motor
  haga IO, que un módulo engorde sin decisión visible, o que la complejidad
  derive en silencio.

## Dónde vive todo

- **Código**: GitHub `gon0801/goncloud-Orbit` (master = lo aprobado; un PR
  abierto por phase en curso).
- **Base de datos viva**: Postgres en el server `goncloud` (Docker, solo
  localhost; acceso por túnel SSH).
- **Tracker**: AppFlowy (notion.goncloud.cc), grid EHV Tasks, fila
  `ORBIT 03 — PR1 optimizador: SHADOW completo (cero escrituras a Amazon)`.
- **Fuentes de verdad**: `docs/CONTEXTO.md` (reglas), `docs/traspaso/
  ADS_OPTIMIZER_V2_DESIGN.md` (umbrales), `docs/DATABASE.md` (schema),
  `plans/orbit-03.md` (plan y estado por tarea).

## Instrucciones para el asistente de chat

- Responde SIEMPRE en español (mexicano).
- Los umbrales y reglas de arriba están SELLADOS: cítalos tal cual, jamás
  inventes números ni "mejoras" de reglas — cambios de reglas son decisión
  del dueño y se hacen vía plan en el repo, no en el chat.
- Si el estado parece desfasado respecto a lo que el dueño cuenta, di que tu
  copia puede estar vieja y sugiérele hacer "Sync now" en el Project.
- Este Project es para PENSAR con el dueño (estrategia, dudas del negocio,
  entender decisiones del motor); la implementación vive en Claude Code.
