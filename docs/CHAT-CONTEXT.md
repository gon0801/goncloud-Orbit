# Orbit — contexto para Claude Chat

> Archivo mantenido por la sesión lead de Claude Code: se actualiza al cierre
> de cada phase. Si la fecha de abajo se ve vieja, pide al dueño que haga
> "Sync now" en el Project o pregúntale el estado antes de asumir.

**Última actualización: 2026-08-27 — ORBIT 04 task 2.5 CERRADA: el probe
real se ejecutó (2026-08-26, autorización del dueño, campaña sacrificable
amazon_us) con 4/4 formas en neto cero (evidencia out/smoke-apply-20260826.log,
ledger probe ids 1-20) y los shapes quedaron fijados contra la API VIVA —
las hipótesis adivinadas estaban mal y se corrigieron (regla 8 cumplida a
contrapelo): el bid viaja como NÚMERO JSON (no string), los enums son UPPER
(matchType EXACT/NEGATIVE_EXACT, state ENABLED/PAUSED), los deletes v3 van
por POST /sp/{recurso}/delete con filtro de ids (DELETE con body da 403),
el readback es por LIST (el GET directo da 403) y los acks son 207 con
success/error. Tests de readback de 2.1-2.3 re-sellados contra esos shapes;
cross-review del dueño (codex+qwen) cerrada (readback paginado, 207
verificados campo por campo, constantes del pause a una fuente). Residuo
verificado y limpio: los 4 términos basura zzsmokeprobe* quedaron ARCHIVED
(en Amazon delete=archivar; el último se archivó con ledger probe id 21).
Rama orbit-04/2-5-probe-shapes, PR DRAFT #31 apilado sobre #29 (3.2).

Previo (2026-08-26): ORBIT 04 Phase 3 EN CURSO: 3.1 lista
(rama orbit-04/3-1-auth-escritura, PR DRAFT — el lead revisa). Auth de
escritura: token estático SOLO-header (x-orbit-token, compare_digest,
register_secret, fail-closed 503 sin secret/DSN, query string no
autentica), ConexionEscritura (ORBIT_DSN_ADMIN), POST /veto (actor,
vence_el editable default 30d, 409 en applying/terminal, 404 inexistente)
y POST /reversa/{bid,pause,negative} vía apply.reversa_manual
(negative_id SIEMPRE del ledger; una reversa por decisión — 409 "ya
revertida"), pantalla /cortes (pendientes + vencimiento + botón vetar, JS
estático CSP-self, XSS testeado), candados OpenAPI = lista sellada 3 GET +
4 POST con auth-dependency introspectada, docstring api.py corregido
(/run Reject permanente), rotación de token en DEPLOY.md (verifica con
queue_id inexistente — jamás muta). Ciclo adversario por tocar auth: 6
hallazgos; ADV-2/3/4 ARREGLADOS con tests en rojo primero; ADV-1
DECLARADO (orbit_test quedó miembro ADMIN OPTION de app_* en el cluster
para tests locales por túnel — REVOKE documentado en DEPLOY.md, 4.1
resuelve); declarado también: reversas necesitan además membresía
app_decide (GRANT apply_attempt es solo decide, NOTA en DEPLOY.md, wiring
en 4.1). 65 tests nuevos/focused en verde contra PG16 real por túnel;
batería completa en el CI del PR. Siguiente: 3.2 goals amigables (CLI +
endpoint, una implementación), 3.3 app/notifica.py.

Previo (2026-08-26): ORBIT 04 Phase 2: implementación
COMPLETA (5/5 tareas construidas y mergeables, rama orbit-04/phase-2, PR
DRAFT sin mergear — el dueño revisa) pero ACEPTACIÓN PENDIENTE: la corrida
real del probe 2.5 es acto del dueño y los tests de readback se sellan
contra los shapes reales en esa corrida: el apply integrado. (2.1) app/apply.py: re-resolución por decisión (escalera + goal,
JAMÁS inputs.modo), quota atómica con cap de config, ledger PRE-HTTP con
tope-3, readback con GET sellado, cache con LO LEÍDO, reversa de bid.
(2.2) app/apply_cola.py: encola cortes (invariante corte↔cola), skip
veto_pendiente por clave de efecto, liberación FIFO con re-validación de
evidencia FRESCA al reloj de liberación (contrato cross-plan CORTES 01),
descarte pre-cobro, filas released reintentadas al día siguiente.
(2.3) app/apply_harvest.py: harvest_job nace AL LIBERAR, reconciliación
viva por identidad completa (señuelo en otro ad group no engaña), bid
sugerido clampeado con intención pre-POST (endpoint NO pineado: v2
retirado, v3 exige SigV4 — fail-open al default sellado; regla 8
documentada), reversas keyword-primero. (2.4) fase de apply DENTRO del
lock en corre_ciclo: heartbeat + ownership-check pre-HTTP con aborto
fail-closed, guard status=running en el cierre, HAY_MODULO_APPLY=True
(escalera shadow sigue = cero HTTP, testeado tras el flip). (2.5)
tools/smoke_apply.py CONSTRUIDO y NO ejecutado (doble autorización +
campaña solo por config; corrida real = dueño). Review de fase con
adversario: 11 hallazgos (1 crítico: TX4 sin commit invisible por
autocommit en tests — corregido con test SIN autocommit), 8 fixes
aplicados y aprobados por el reviewer. Cross-review ordenada por el dueño
(codex+grok+qwen en paralelo): 6 altas reales (tope-3 contando reversas,
UniqueViolation al reusar job, reversa rompiendo keyword-primero, fases
de harvest avanzando sin ids del ack, GET sin capturar abortando el
barrido, cierre job+cola sin transacción) + medias — 13 fixes aplicados.
Suite 550 passed con batería DB real (túnel). Previo (2026-08-25): ORBIT 04 Phase 1 CERRADA (3/3 tareas,
rama orbit-04/phase-1, PR de fase): (1.1) docs/APPLY.md, el contrato fino de
PR2 — máquina de estados de la cola de cortes con clave de efecto y ventana
de veto 48h, ledger de intentos, quota sellada con mapeo config↔motor,
matriz de reconciliación, tabla de reversas y checklist de cutover; spec
deltas en CONTEXTO.md y DATABASE.md. (1.2) migración 0002_apply.sql: tabla
apply_queue (nace pending_veto, transiciones exactas por trigger, veto exige
admin por schema, fila shadow JAMÁS sale de vetoed|discarded, único parcial
NULLS NOT DISTINCT por clave de efecto), ledger apply_attempt (sello una
sola vez), reactivacion_manual, sellos de apply_quota_state (fila del día
solo desde config, used creciente, día UTC de la base), fases de
harvest_job, applied_cycle_id y GRANTs por columna; test_schema parsea 0002
y test_apply_schema ejercita el DoD en DB (CI). (1.3) app/ads/write.py:
cliente de escritura allowlist default-deny (10 mutaciones exactas, scope
sellado por instancia, moneda verificada pre-HTTP, 429 reintenta sin
recobro / 5xx no reintenta, constructor fail-closed exige modo live, candado
de imports) + negativeKeywords/list verificado EN VIVO (regla 8, 200 en US y
MX). Suite local 359 passed / 78 DB-skips; batería DB completa en el CI del
PR. Siguiente: Phase 2 (el apply integrado: 2.1 núcleo, 2.2 cola, 2.3
harvest, 2.4 integración al ciclo, 2.5 probe autorizado). Previo (2026-08-24): Phases 1–3 en master; 4.1 y 4.2 hechas
en goncloud (servicio `app` en 127.0.0.1:8010 + 3 crons aditivos, profundidad
diaria D-31..D-1). 4.3 EJECUTADA por el dueño: escalera global en shadow,
targets ACoS 20 (mx) y 20 (us — presión máxima elegida a sabiendas), TODAS
las campañas activas vía goals de plataforma; harvest sin bid fijo por
decisión (el bid sugerido de Amazon llega con el apply de PR2 — regla 3:
jamás inventar el número). 4.4 VALIDADA por el dueño: primer shadow real corrido (133 decisiones,
us 124 / mx 9), spot-check completo y verificación adversarial TRIPLE
(codex, grok y qwen: 133/133 limpias, skips cuadrando al entero).
ORBIT 03 COMPLETO (17/17 tareas): el optimizador decide EN SOMBRA todos
los dias (crons 06:45/07:10/08:40-08:41 UTC) sobre todas las campanas
activas, con cero capacidad de escribir a Amazon. El reloj de las 2
semanas de shadow para el cutover (ORBIT 05) corre desde el 2026-08-24.
ORBIT 16 Phase 1 CERRADA (7/7 tareas): dashboard de LECTURA en master
y desplegado — 4 pantallas (Resumen con gráficas, Campañas con estado
y procedencia del target, Decisiones por cursor, Salud), acceso por
túnel ssh al 8010. El dueño validó el smoke 1.7 (gráficas OK bajo CSP
estricta; pidió y recibió la columna de estado de campaña; se le
explicó con datos vivos por qué el motor negativiza sus términos
"arras": 116 clicks / 0 ventas en ventana madura — la decisión
estratégica listing-vs-negative queda anotada para el apply). La
review en cadena (lead + reviewer fresco + kimi/codex/grok + bots)
atrapó y cerró bugs reales del bloque 2. Phase 2 CERRADA: el dashboard
también se ve por la VPN WireGuard del dueño (cel y compu, validado
por él) — bind adicional en la IP wg0, allowlist EXACTA de mapeos
sellada por candado, sign-off del dueño antes de aplicar. Queda solo
Phase 3 (settings de escritura, bloqueada por ORBIT 04). CORTES 01
CERRADO (5/5): los cortes NEGATIVE y PAUSE ahora usan umbral de clicks
ADAPTATIVO por producto (evidencia del ad group 90d: expected_clicks ×
1.5, piso max con legacy) y NEGATIVE además piso de COSTO adaptativo
(AOV del producto × 1.0, respaldos 45 USD/600 MXN) — nacido del caso
arras y calibrado con 57 anotaciones del dueño (regla nueva 21/22 con
su instinto vs 0/22 de la vieja); contrafactual final: 28 cortes
legacy → 1 con el paquete. Los términos PRODUCTO-DIFERENTE se cortan
por la otra vía: lista curada por AI con aprobación del dueño
(CORTES 02, sembrada). Shadow valida la regla nueva ~2 semanas antes
del cutover. Siguiente proyecto grande: ORBIT
04 (PR2: el apply con topes). Este archivo tiene candado de frescura:
el CI exige actualizarlo en cada PR que cierre tareas.**

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
  backfill histórico completo: 95 días de métricas, ~65 de terms, us y mx),
  y todo lo siguiente (mergeado 2026-08-24): la capa de ventanas de datos, el
  motor de decisión puro (bids, hygiene, goals) con todos los umbrales
  sellados y testeados, 3.1 el orquestador del ciclo (claim atómico del
  lock con TTL y heartbeat, envelope que se sella en todos los caminos,
  skips estructurados en notes, decisiones con inputs congelados y golden
  replay que las reproduce exactas), 3.2 la API de solo lectura
  (`/api/ads-optimizer/{status,audit,goals}`, GET como `orbit_read`, con
  watermarks de la misma fuente del motor y notes de formato mixto
  tolerado) y 3.3 el CLI `python -m app.cli {ingest,cycle}` (envoltorio
  delgado: el ciclo usa el mismo claim/job_key del cron y `ingest` delega
  a los pipelines de `app/ads/`); candados anti-monolito activos
  (complejidad, fronteras de imports, tamaño de módulos, frescura de este
  archivo).
- **Phase 4 (PR #15, en cierre)**: 4.1 servicio `app` vivo en el server
  (`/health` OK, puerto solo loopback, `secrets/` 0600 intactos), 4.2
  tres crons diarios (accounting intacto), 4.3 seed del dueño ejecutado
  (shadow, targets 20/20) y 4.4 primer shadow real VALIDADO (133
  decisiones, triple verificación adversarial limpia). Falta solo el
  merge (4.5).
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
app/cycle.py ← orquestador del ciclo: ventanas → motor → tabla decision (auditoría)
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
  re-tira D-31..D-1 (sello 4.2: tope de un request = 31d, cubre las
  columnas 30d y el mínimo D-8..D-1 de terms) y la bitemporalidad lo hace
  seguro.
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
