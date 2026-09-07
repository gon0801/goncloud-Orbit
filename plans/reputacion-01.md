# REPUTACION 01 — Plan formal del módulo Reputación

Versión: 1.2, 2026-09-07. Redacción del lead (sustituye el brief
`plans/brief-reputacion-01-deepseek.md`, conservado como antecedente).
Atiende cross-review (2 majors + 4 minors). D1–D5 y D7 cerradas por el dueño;
D6 abierta hasta 0.2 (requiere costos).
Estado: **aprobado por el dueño 2026-09-07**. Nada es implementable hasta 0.5.
No autoriza gasto (D6), deploy ni escrituras. Pre-requisito PR #186: cumplido
(mergeado). Pendiente: sondas 0.1–0.4 y acta 0.5.

Origen: AUTO-09 (`docs/PENDIENTES-AUTOMATIZACION.md`, mergeado vía PR #186),
Módulo 3 de
`docs/traspaso/MODULOS-AVANZADOS.md` (fuente verbatim, no contrato sellado),
tablas reales del sistema anterior (ver Hechos base).
Precedencia: contrato del proyecto (AGENTS.md, CONTEXTO.md) → este plan →
Módulo 3. Lo que contradiga CONTEXTO pierde.
`team_validation_mode`: lead + 1 cross-review del plan antes de implementar.

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

## Decisiones (abiertas, cierran en 0.5 con acta)

Decisiones del dueño 2026-09-07 (acepta propuestas salvo D6, que requiere
costos de 0.2). Acta formal en 0.5:
`docs/evidencia/reputacion-01/0.5/confirmacion.md`.

| ID | Pregunta | Propuesta | Cierre 2026-09-07 |
|---|---|---|---|
| D1 | Cadencia de ingesta | Diaria ~09:30 UTC (tras optimizador 08:40) + bajo demanda por CLI | Aprobada como propuesta |
| D2 | Alcance | Solo listings propios vinculados (bridge con mapa Odoo) + items MeLi propios; ASIN sueltos fuera | Aprobada como propuesta |
| D3 | Umbrales de alerta | rating < 4.2; review nueva 1★ (**condicionada a A.3**: sin texto, la alerta usa solo rating/count/reclamos); claims sube >1pt vs semana previa; caída rating ≥0.3 en 7d | Aprobada como propuesta |
| D4 | Canal de alerta | Líneas nuevas en el digest Telegram existente, sin canal nuevo | Aprobada como propuesta |
| D5 | Preguntas MeLi v1 | Solo lectura + conteo de pendientes; borrador IA = v2 | Aprobada como propuesta |
| D6 | Presupuesto Apify | Tope USD/mes + recurrencia del scraping; **sin D6 no hay corridas con costo** | ABIERTA: sin tope aprobado; A.3 bloqueada hasta que 0.2 dé costos y el dueño fije tope + recurrencia |
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
| 0.1 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Inventario de alcance (lead): listings propios con mapa Odoo, ASIN MX/US, items MeLi | E/0.1 con SELECT y conteos por mercado, IDs ambiguos/omitidos; cero mutaciones | - | cc:TODO |
| 0.2 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Apify (lead): actor vigente, cobertura amazon.com.mx/com, shape, costo por corrida | E/0.2 con doc + muestra scrubbed + costo; no verificada → ampliación abierta, v1 sigue con Keepa+MeLi | - | cc:TODO |
| 0.3 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda MeLi (lead): reputación seller, preguntas, opiniones; endpoints, permisos, shape | E/0.3 asigna verificada/no_verificada con motivo por endpoint; muestra scrubbed | - | cc:TODO |
| 0.4 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Keepa + Account Health SP-API (lead): rating/historial, salud de cuenta si es oficial y barata | E/0.4 con fuente, grano, costo y muestra; lo no verificado queda fuera de v1 declarado | - | cc:TODO |
| 0.5 | [stage:planificacion] [lane:gate] [tdd:skip:docs-contract] Cerrar D1–D7, API/pantalla, migración, cron y reversa | Acta E/0.5 (ratifica D1–D5/D7, cierra D6 con valores tras 0.2) + contrato API/pantalla/migración/cron/reversa; sin D6 no hay A.3 con costo | 0.1–0.4 | cc:TODO |

### Sondas candidatas (0.2–0.4; a confirmar en la sonda)

El DoD de cada sonda exige endpoint/comando final + muestra scrubbed.
Candidatos para que el lead arranque:

- 0.2 Apify: identificar actor vigente en Apify Store (reviews Amazon, cobertura
  amazon.com.mx/com); corrida mínima con 1 ASIN propio; registrar shape y costo.
  Actor exacto por identificar (el código viejo no está en el repo).
- 0.3 MeLi: `GET /users/{user_id}` (reputación; seller anterior 135734858),
  `GET /questions/search?item_id=…&status=UNANSWERED`,
  `GET /reviews/items/{item_id}` (candidato, confirmar vigencia en la sonda).
- 0.4 Keepa: `GET api.keepa.com/product` (key de secrets; dominio MX/US según
  docs; `stats` con reviewCount/historial). SP-API Account Health: candidato,
  solo si es oficial y barata.

### Fase A — Ingesta, alertas y pantalla

Purpose: dato fresco visible y alertas que avisan, tolerantes a fuente ausente.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] Migración expansiva + modelos append-only (snapshots, reviews, reputación MeLi, preguntas) | pytest_focal aplica en PG temporal; UNIQUEs de idempotencia; GRANTs por rol; `tests/test_schema.py` la parsea; E/A.1 | 0.5 | cc:TODO |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta snapshots oficiales (Keepa/MeLi) + CLI + cron | append-only con observed_at; re-corrida = cero duplicados; CLI manual OK; cron registrado; E/A.2 | A.1,0.3,0.4 | cc:TODO |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta texto Apify (solo si 0.2 verificó **y** D6 cerró; si no, cierra como ampliación con evidencia) | texto completo + dedupe por external id; costo por corrida registrado; E/A.3 | A.1,0.2,0.5 | cc:TODO |
| A.4 | [stage:implementacion] [lane:gate] [tdd:required] Reputación vendedor + preguntas MeLi (lectura) | snapshots diarios; preguntas append-only con estado; cero envíos; E/A.4 | A.1,0.3 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] Evaluación pura de alertas (D3) + líneas en digest | fixtures 1★ (solo si A.3)/<4.2/spike/caída disparan; sin datos no dispara; digest solo imprime si hay alertas; E/A.5 | A.2,0.5 | cc:TODO |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] API GET + pantalla /reputacion + sidebar (Reputación deja "pronto") | UI verificada en navegador; textContent para texto externo; CSP sin inline; ausencia visible; no rompe sin texto; capturas E/A.6 | A.2,A.5 | cc:TODO |
| A.R | [stage:revision] [lane:gate] [tdd:skip:revision] Revisión independiente de A | APPROVE sobre SHA; fuentes, idempotencia, XSS, secreto-fuera-de-logs; sin bloqueantes; E/A.R | A.6 | cc:TODO |
| A.7 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] CI, integración y despliegue | ruff/pre-commit + suite CI verdes; backup; migración; smoke GET; cron en DEPLOY.md; E/A.7 | A.R | cc:TODO |

A.3 no bloquea A.5/A.6: sin texto, la pantalla muestra snapshots/alertas y el
texto como Sin verificar. A.6 tolera cualquier combinación de fuentes caídas.

## Propiedad de archivos y concurrencia

| Tasks | Archivos previstos | Restricción |
|---|---|---|
| 0.1–0.5 | E/0.x, este plan | Lead corre sondas; dueño cierra D1–D7 |
| A.1 | `migrations/00XX_reputacion.sql` (número se fija al aplicar, posterior a 0022), `tests/test_reputacion_migracion.py` | No editar migraciones existentes |
| A.2,A.3,A.4 | `app/reputacion.py` (ingesta), `app/cli.py` (subcomando), `tests/test_reputacion.py` | Un solo editor a la vez en `app/reputacion.py`; secuencial A.2→A.3/A.4 |
| A.5 | `app/reputacion_alertas.py` (puro, nuevo), `app/notifica.py` (solo líneas aditivas al digest), tests propios | No cambiar formato existente del digest |
| A.6 | `app/api_reputacion.py` (GET), `app/ui.py` (ruta), `app/templates/reputacion.html` (+js/css), `app/templates/base.html` (Reputación deja "pronto"; chip Reviews se retira, la pantalla incluye pestaña reviews — objetable en 0.5), `tests/test_ui_reputacion.py` | No concurrente con otro editor de `base.html`/`ui.py` |
| A.R/A.7 | Evidencia, `docs/DEPLOY.md`, `docs/CHAT-CONTEXT.md` | Reviewer solo lectura; lead integra y despliega |

Prohibido tocar: `app/fabrica*`, `app/evaluacion*`, ingesta Ads, motores,
cron/jobs existentes (el nuevo no pisa horarios). Migración posterior a 0022.

## Aceptación verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | Publicación con reviews | Texto completo, rating, verified, fecha; reviewer visible | API + captura |
| AC2 | Publicación sin fuente | Sin verificar visible; no 0, no bloquea el resto | Captura |
| AC3 | Fuente caída a media ingesta | Falla explícita, cero filas parciales inventadas, re-corrida idempotente | Test + log |
| AC4 | Review nueva 1★ (solo si A.3) / rating < D3 | Alerta en digest con enlace a la publicación | Test + digest |
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

Este plan no aprueba gasto, deploy ni corridas con costo. D6 + go explícito
del dueño habilitan A.3; nada antes.

| Asunto/operación | Motivo | Scope y límites |
|---|---|---|
| SELECT Orbit + lectura snapshots bridge | Inventario 0.1 | Solo lectura |
| Credenciales Apify/Keepa/MeLi/SP-API vía ORBIT_SECRETS_DIR | Sondas 0.2–0.4 | Rutas mínimas; nunca imprimir valores ni copiar al repo |
| GETs oficiales (MeLi, Keepa, SP-API) | Sondas e ingesta A.2/A.4 | Lectura; ningún endpoint de escritura |
| Corridas Apify con costo | Texto de reviews | **Fuera de alcance automático** hasta D6 + go explícito |
| git push, PR y lectura de CI | Revisión y calidad | Desde origin/master; sin force push ni --no-verify |
| Backup, migración, cron, deploy | Publicar v1 recuperable | Lead, secuencia anterior; no servicios vecinos |

## Manifest y siguientes

Snippet para el lead al aprobar (no commitear antes de 0.5):

```json
{"name": "reputacion-01", "path": "plans/reputacion-01.md", "description": "REPUTACION 01 — reputación v1 solo-lectura: snapshots rating/count, reviews con texto, reputación y preguntas MeLi, alertas Telegram y pantalla /reputacion (AUTO-09)"}
```

v2 candidato (otro plan/brief): borrador IA de respuestas con checkpoint
humano, Account Health, Buy Box, detección de fakes. Acciones sobre campañas
por reputación exigen su propio contrato con reversa.
