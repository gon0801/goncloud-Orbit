# REPUTACION 01 — Plan formal del módulo Reputación

Versión: 1.4, 2026-09-07 — plan oficial (harness-plan). Redacción del lead
(sustituye el brief `plans/brief-reputacion-01-deepseek.md`, conservado como
antecedente). Atiende cross-review (2 majors + 4 minors) y validación
multi-perspectiva (4 validadores + síntesis, REQUEST_CHANGES atendido).
D1–D7 cerradas por el dueño (D6: tope $10 USD/mes; recurrencia la fija 0.2
para caber en el tope).
Estado: **aprobado por el dueño 2026-09-07**; **acta 0.5 aprobada
2026-09-08** (Fase A liberada: A.1→A.2→A.4→A.5→A.6; A.3 bloqueada).
No autoriza gasto (corridas Apify: requieren 0.2 + go explícito, tope $10 USD/mes), deploy ni escrituras. Pre-requisito PR #186: cumplido
(mergeado). Fase 0 cerrada: E/0.1–E/0.5 en `docs/evidencia/reputacion-01/`.

Origen: AUTO-09 (`docs/PENDIENTES-AUTOMATIZACION.md`, mergeado vía PR #186),
Módulo 3 de
`docs/traspaso/MODULOS-AVANZADOS.md` (fuente verbatim, no contrato sellado),
tablas reales del sistema anterior (ver Hechos base).
Precedencia: contrato del proyecto (AGENTS.md, CONTEXTO.md) → este plan →
Módulo 3. Lo que contradiga CONTEXTO pierde.
`team_validation_mode`: subagent (Producto/datos, Arquitectura, Seguridad/QA,
Escéptico + síntesis, 2026-09-07; veredicto REQUEST_CHANGES, 12 hallazgos
atendidos en v1.4). Validación: `plans/reputacion-01-validacion.md`.
Memoria project-scoped: sin `docs/specs/planes` ni harness-mem; `.harness/`
local untracked. Baseline de calidad: ruff + pre-commit + CI quality existen
(verificación en A.7).

## Hechos base (verificados por el lead contra el backup el 2026-09-07; salidas en el brief antecedente; no re-derivar)

Competitive intel traía reviews por **scraping Apify**, no por API oficial
(TRASPASO-1 §1.4). Backup `competitive-2026-08-22.db`:

- `product_reviews` (163): texto completo (`title, content`, 162/163),
  `reviewer, verified, helpful_votes, review_date, language, images_json,
  review_url`. MX 86, US 33, MeLi 44. URLs `amazon.com.mx/review/…`.
- `review_snapshots` (30,450): `(marketplace, marketplace_id, rating,
  review_count, snapshot_date)`. Amazon solo hasta **2026-06-02** (el scraping
  murió 2.5 meses antes del apagón); MeLi hasta 2026-08-22.
- `meli_seller_reputation` (123): diario vía API oficial, 2026-04-14 →
  2026-08-22. `meli_questions` (13): texto + `suggested_answer` por IA con
  flujo humano (referencia para v2).
- `pending_pause_reviews` era cola humana de pausas: nada que ver.

Lección sellada: el scraping se rompe; fuente ausente = estado explícito
(Sin verificar), jamás cero. Keys ya en secrets de Orbit (ORBIT 02): Apify,
Keepa, Anthropic, SP-API, token MeLi. Sidebar lista Reputación como próxima
(`app/templates/base.html:57`). Alertas: digest Telegram en `app/notifica.py`.

## Resultado y límites

Purpose: ver la salud reputacional por publicación y enterarse antes de que
duela, sin confundir falta de fuente con buena reputación.

Entrega A: snapshots rating/count, reviews con texto, reputación vendedor MeLi,
preguntas MeLi en lectura, alertas y pantalla `/reputacion`. Una fuente no
verificada queda como ampliación abierta, nunca bloquea el resto.

Quedan fuera salvo ampliación acordada: acciones automáticas sobre campañas o
pujas; respuestas automáticas (v2 con checkpoint humano); detección de reviews
falsas; competidores/discover; Buy Box intradía y Account Health salvo fuente
oficial barata (si no, v2). Cero escrituras a Amazon/MeLi en v1.

## Decisiones (cerradas 2026-09-07; acta formal en 0.5)

Decisiones del dueño 2026-09-07 (D6: tope $10 USD/mes; recurrencia la fija
0.2 para caber en el tope). Acta formal en 0.5:
`docs/evidencia/reputacion-01/0.5/confirmacion.md`.

| ID | Pregunta | Propuesta | Cierre 2026-09-07 |
|---|---|---|---|
| D1 | Cadencia de ingesta | Diaria ~09:30 UTC (tras optimizador 08:40) + bajo demanda por CLI | Aprobada condicionada a 0.2: la recurrencia final debe caber en el tope D6 (0.2 entrega tabla recurrencia×cobertura) |
| D2 | Alcance | Solo listings propios vinculados (bridge con mapa Odoo) + items MeLi propios; ASIN sueltos fuera | Aprobada como propuesta |
| D3 | Umbrales de alerta | rating < 4.2; review nueva 1★ (**solo si A.3**; si no, la alerta usa rating/count/reclamos); claims MeLi a nivel vendedor sube >1pt vs semana previa (Amazon excluido v1); caída rating ≥0.3 en 7d; alerta solo en flanco (cambio de estado); ventanas recalculadas según recurrencia | Aprobada; granularidad por alerta y ventanas en acta 0.5 |
| D4 | Canal de alerta | Líneas nuevas en el digest Telegram existente, sin canal nuevo | Aprobada como propuesta |
| D5 | Preguntas MeLi v1 | Solo lectura + conteo de pendientes (pendiente = UNANSWERED + ventana, en acta 0.5); borrador IA = v2 | Aprobada como propuesta |
| D6 | Presupuesto Apify | Tope USD/mes + recurrencia del scraping; **sin D6 no hay corridas con costo** | Cerrada: tope **$10 USD/mes**; recurrencia la fija 0.2 para caber en el tope (si ni la cobertura mínima cabe, D6 se reabre con el dueño); A.3 sigue bloqueada hasta 0.2 |
| D7 | Texto visible en UI | Completo con truncado ("ver más"), reviewer visible (dato público) | Aprobada como propuesta |

## Convenciones de evidencia y pruebas

DoD = criterio binario de terminado. `E/<task>/` abrevia
`docs/evidencia/reputacion-01/<task>/`: SQL, salidas sin secretos, contratos,
reporte de pruebas y capturas. `pytest_focal` = archivo de test trabajado con
`PYTHONPATH=. .venv/bin/python -m pytest -q`. Cada regresión demuestra rojo
contra el código previo y verde con el nuevo. Suite completa en CI del PR, no
duplicada local. Sondas de Fase 0 las corre el **lead** (son prod/secrets);
implementadores no acceden a goncloud, Amazon ni AppFlowy.

## Etapas y tareas

### Fase 0 — Verificación de fuentes y contrato

Purpose: saber qué fuente da qué dato, a qué costo, antes de prometer v1.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Inventario de alcance (lead): listings propios con mapa Odoo (fuente concreta o Por probar), ASIN MX/US, items MeLi | E/0.1 con SELECT y conteos por mercado; IDs ambiguos/omitidos reportados, nunca rellenados; cero mutaciones | - | cc:DONE |
| 0.2 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Apify (lead): 2-3 actores candidatos, cobertura amazon.com.mx/com, shape, costo por corrida | E/0.2 con actor elegido + corrida mínima (max_items/pages acotados) + costo real + tabla recurrencia×cobertura que cabe en $10 + muestra scrubbed; no verificada → ampliación abierta, v1 sigue con Keepa+MeLi | - | cc:DONE |
| 0.3 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda MeLi (lead): reputación seller (seller_id vía `GET /users/me`, no el histórico), preguntas, opiniones; endpoints, permisos, shape | E/0.3 asigna verificada/no_verificada con motivo POR endpoint + shape JSON del comando + muestra real scrubbed | - | cc:DONE |
| 0.4 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Keepa + Account Health SP-API (lead): rating/historial, salud de cuenta si es oficial y barata | E/0.4 con fuente, grano, costo y muestra real; veredicto por fuente; costo >0 entra en D6; lo no verificado queda fuera de v1 declarado | - | cc:DONE |
| 0.5 | [stage:planificacion] [lane:gate] [tdd:skip:docs-contract] Cerrar D1–D7, API/pantalla, migración, cron y reversa | Acta E/0.5 (ratifica D1–D7; fija recurrencia Apify dentro del tope $10 USD/mes; granularidad por alerta, ventanas, latencia, propiedad y concurrencia) + contrato API/pantalla/migración/cron/reversa; A.3 bloqueada hasta 0.2 | 0.1–0.4 | cc:DONE |

### Sondas candidatas (0.2–0.4; a confirmar en la sonda)

El DoD de cada sonda exige endpoint/comando final + muestra scrubbed.
Candidatos para que el lead arranque:

- 0.2 Apify: 2-3 actores candidatos en Apify Store (reviews Amazon, cobertura
  amazon.com.mx/com); corrida mínima con 1 ASIN propio y max_items/pages
  acotados; registrar shape y costo real + tabla recurrencia×cobertura vs tope
  D6. Actor exacto por identificar (el código viejo no está en el repo).
- 0.3 MeLi: `GET /users/me` (seller_id real; el 135734858 anterior es solo
  referencia), `GET /users/{id}` (reputación),
  `GET /questions/search?seller_id=…` o `?item=…&status=UNANSWERED` (parámetro
  real `item=`/`seller_id=`, no `item_id=`),
  `GET /reviews/items/{item_id}` (candidato, confirmar vigencia en la sonda).
- 0.4 Keepa: `GET api.keepa.com/product` (key de secrets; dominio MX/US según
  docs; `stats` con reviewCount/historial). SP-API Account Health: candidato,
  solo si es oficial y barata. Costo y cuota declarados (cero o entra en D6).

### Fase A — Ingesta, alertas y pantalla

Purpose: dato fresco visible y alertas que avisan, tolerantes a fuente ausente.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] Migración expansiva + modelos append-only (snapshots, reviews, reputación MeLi, preguntas) | pytest_focal aplica en PG temporal; UNIQUEs de idempotencia; GRANTs por rol; `tests/test_schema.py` la parsea; número final contra HEAD al aplicar (≥0024) con orden vs B.6/F2 en E/A.1 | 0.5 | cc:DONE |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta snapshots oficiales (Keepa/MeLi) + CLI + cron | append-only con observed_at; corrida en UNA transacción con rollback (test: corte a mitad deja cero filas); re-corrida = cero duplicados; toda excepción/log por `app/redaction.py` (test con key falsa ausente en salida); CLI manual OK; cron registrado; E/A.2 | A.1,0.3,0.4 | cc:DONE |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta texto Apify (solo si 0.2 verificó y la recurrencia cabe en el tope D6; si no, cierra como ampliación con evidencia) | texto completo + dedupe por external id; aborta y registra si excede costo/cobertura (max_items acotado); costo por corrida registrado; E/A.3 | A.1,0.2,0.5 | cc:TODO |
| A.4 | [stage:implementacion] [lane:gate] [tdd:required] Reputación vendedor + preguntas MeLi (lectura) | snapshots diarios; preguntas append-only con estado; cliente MeLi solo-GET default-deny (test revienta ante POST/PUT/DELETE); cero envíos; E/A.4 | A.1,0.3 | cc:DONE |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] Evaluación pura de alertas (D3, granularidad por tipo: qué fuente dispara cada alerta) + líneas en digest | fixtures 1★ (solo si A.3)/<4.2/spike/caída disparan SOLO en flanco (cambio de estado); sin datos no dispara; fixture sin-texto no rompe; digest solo imprime si hay alertas; E/A.5 | A.2,A.4,0.5 | cc:DONE |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] API GET + pantalla /reputacion + sidebar (Reputación deja "pronto") | UI verificada en navegador; textContent para texto externo (+ snippet/images_json: solo enlaces https amazon/MeLi o sin enlace; imágenes fuera v1 o allowlist); escape según parse_mode del digest + tests; CSP sin inline; ausencia visible; no rompe sin texto; capturas E/A.6 | A.2,A.5 | cc:DONE |
| A.R | [stage:revision] [lane:gate] [tdd:skip:revision] Revisión independiente de A | APPROVE sobre SHA; fuentes, idempotencia, XSS, secreto-fuera-de-logs; sin bloqueantes; E/A.R | A.6 | cc:DONE |
| A.7 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] CI, integración y despliegue | ruff/pre-commit + suite CI verdes; backup; migración; smoke GET; cron en DEPLOY.md; E/A.7 | A.R | cc:DONE |

A.3 no bloquea A.5/A.6: sin texto, la pantalla muestra snapshots/alertas y el
texto como Sin verificar. A.6 tolera cualquier combinación de fuentes caídas.

## Propiedad de archivos y concurrencia

| Tasks | Archivos previstos | Restricción |
|---|---|---|
| 0.1–0.5 | E/0.x, este plan | Lead corre sondas; dueño cierra D1–D7 |
| A.1 | `migrations/00XX_reputacion.sql` (número se fija al aplicar contra HEAD, ≥0024; orden documentado vs B.6/F2), `tests/test_reputacion_migracion.py` | No editar migraciones existentes |
| A.2,A.3,A.4 | `app/reputacion.py` (ingesta), `app/cli.py` (subcomando según sus convenciones), `tests/test_reputacion.py` | Un solo editor a la vez en `app/reputacion.py`; orden sugerido A.2→A.4→A.3 |
| A.5 | `app/reputacion_alertas.py` (puro, nuevo), `app/notifica.py` (solo líneas aditivas al digest), tests propios | No cambiar formato existente del digest; lectores de `review_event` (0027: N filas por review) con DISTINCT ON por review (fila mas reciente por `observed_at`); Grok XR-1 R2-2: hoy no hay lectores, A.5 es duena de este invariante |
| A.6 | `app/api_reputacion.py` (GET, reusa `app/api_common.py`), `app/ui.py` (ruta), `app/templates/reputacion.html` (+js/css), `app/templates/base.html` (Reputación deja "pronto"; chip Reviews se retira, la pantalla incluye pestaña reviews — objetable en 0.5), `tests/test_ui_reputacion.py` | No concurrente con otro editor de `base.html`/`ui.py` |
| A.R/A.7 | Evidencia, `docs/DEPLOY.md`, `docs/CHAT-CONTEXT.md` | Reviewer solo lectura; lead integra y despliega |

Prohibido tocar: `app/fabrica*`, `app/evaluacion*`, ingesta Ads, motores,
cron/jobs existentes (el nuevo no pisa horarios). Migración posterior a 0023 (número contra HEAD al aplicar).

## Aceptación verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | Publicación con reviews | Texto completo (solo si A.3), rating, verified, fecha; reviewer visible | API + captura |
| AC2 | Publicación sin fuente | Sin verificar visible; no 0, no bloquea el resto | Captura |
| AC3 | Fuente caída a media ingesta | Falla explícita, rollback total (cero filas parciales), re-corrida idempotente | Test + log |
| AC4 | Review nueva 1★ (solo si A.3) / rating < D3 | Alerta en digest con enlace a la publicación (latencia: digest o CLI same-day, según acta 0.5) | Test + digest |
| AC5 | Texto con `<script>` / HTML raro | Neutralizado por textContent; CSP sin inline intacto | Test rojo/verde |
| AC6 | Re-corrida de ingesta | Cero duplicados (UNIQUEs), observed_at distingue re-observación | Test |
| AC7 | Pregunta MeLi nueva | Visible con estado pendiente; cero envíos automáticos | API + captura |
| AC8 | Sidebar | Reputación es enlace a /reputacion; Repricing sigue "pronto" | Captura |

## Secuencia de despliegue y reversa

1. Backup verificable; migración expansiva (compatible hacia atrás).
2. Ingesta manual por CLI antes de encender el cron.
3. Cron diario solo tras ingesta manual sana.
4. Reversa: deshabilitar cron, conservar datos y pantalla (v1 es
   solo-lectura: no hay mutaciones que deshacer). Smoke GET productivo.

## Confirmación operativa previa por fase

Este plan no aprueba gasto, deploy ni corridas con costo. Sonda 0.2 + go
explícito del dueño (dentro del tope D6 de $10 USD/mes) habilitan A.3;
nada antes.

| Asunto/operación | Motivo | Scope y límites |
|---|---|---|
| SELECT Orbit + lectura snapshots bridge | Inventario 0.1 | Solo lectura |
| Credenciales Apify/Keepa/MeLi/SP-API vía ORBIT_SECRETS_DIR | Sondas 0.2–0.4 | Rutas mínimas; nunca imprimir valores ni copiar al repo |
| GETs oficiales (MeLi, Keepa, SP-API) | Sondas e ingesta A.2/A.4 | Lectura; ningún endpoint de escritura |
| Corridas Apify con costo | Texto de reviews | **Fuera de alcance automático** hasta 0.2 + go explícito (tope D6: $10 USD/mes) |
| git push, PR y lectura de CI | Revisión y calidad | Desde origin/master; sin force push ni --no-verify |
| Backup, migración, cron, deploy | Publicar v1 recuperable | Lead, secuencia anterior; no servicios vecinos |

## Alcance puntuado (harness-plan, validación 2026-09-07)

Juicio de alcance, no de rentabilidad. Required entra a v1; Recommended entra
si su sonda verifica; Optional solo si es gratis; Reject queda fuera de v1.

| Alternativa | Veredicto | Motivo |
|---|---|---|
| Snapshots rating/count (Keepa, MeLi API) | Required | Núcleo de v1; fuentes oficiales |
| Reputación seller + preguntas MeLi (lectura) | Required | API oficial, cero costo marginal |
| Texto Apify | Recommended | Solo si 0.2 verifica actor + cabe en D6 |
| Account Health SP-API | Optional | Solo si es oficial y barata en 0.4 |
| Respuestas IA, acciones automáticas, fakes, competidores, Buy Box intradía | Reject v1 | Requieren contrato/reversa propios o son otro sistema |

## Especificación (Spec skip reason)

No se crea spec nuevo hoy: las fuentes 0.2–0.4 no están verificadas y nada es
implementable hasta 0.5; fijar el contrato ahora prometería cobertura y APIs
sin evidencia. Base vigente: Módulo 3 (verbatim, no contrato) + este plan. El
contrato (API/pantalla, shapes, migración, cron, reversa) se fija en 0.5 con
sondas reales. Si 0.5 contradice este plan, el plan se enmienda, no la sonda.

## Inicio de una sesión de ejecución

- 0.1–0.4 son sondas del lead (prod/secrets); implementadores no las piden.
- A.1–A.7 se piden tras 0.5, en orden de Depends (sugerido A.2→A.4→A.3).
- La entrada al manifest se commitea al aprobar 0.5, no antes.
- D6 se reabre con el dueño si 0.2 demuestra que ni la cobertura mínima cabe
  en $10 USD/mes.

## Manifest y siguientes

Snippet para el lead al aprobar (no commitear antes de 0.5):

```json
{"name": "reputacion-01", "path": "plans/reputacion-01.md", "description": "REPUTACION 01 — reputación v1 solo-lectura: snapshots rating/count, reviews con texto, reputación y preguntas MeLi, alertas Telegram y pantalla /reputacion (AUTO-09)"}
```

v2 candidato (otro plan/brief): borrador IA de respuestas con checkpoint
humano, Account Health, Buy Box, detección de fakes. Acciones sobre campañas
por reputación exigen su propio contrato con reversa.
