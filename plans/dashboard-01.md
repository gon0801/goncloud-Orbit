# Plans — DASHBOARD 01: dashboard del optimizador (lectura, VPN y settings)

> Purpose: darle al dueño OJOS sobre la sombra (tipo Perpetua, pero con la
> aritmética de cada decisión a la vista) y, después, la perilla de settings.
> Pedido del dueño 2026-08-24: gráficas de spend/revenue/ACoS, info de
> campañas, decisiones explicadas, y poder editar el target ACoS. Acceso:
> por su VPN WireGuard (compu/cel), patrón de sus demás apps del server.
> Registro: fila `ORBIT 16 — Dashboard del optimizador` en EHV Tasks
> (`In progress` al arrancar 1.1; `Done` con notas completas al cerrar el
> plan — mandato de AGENTS.md).
>
> Validación en TRES rondas (tope cerrado — sin más re-reviews): (a) 5 perspectivas subagent (4 majors + 4
> minors) y (b) cross-review paralela codex+qwen sobre el documento v1
> (7 altas + 6 medias + 4 bajas). TODO incorporado abajo; los hallazgos
> clave de la ronda 2: goals son MUTABLES por schema (la 3.1 v1 lo
> contradecía), el grano de las series duplicaría dinero sin filtro por
> kind, las procedencias de la cascada son CINCO, la aritmética de NULL y
> revenue=0 no estaba sellada, el XSS aplicaba también al contexto JS de
> las gráficas, y el vintage de los últimos días debe MARCARSE en las
> gráficas (trampa de los tres relojes).
>
> Spec delta: `docs/CONTEXTO.md` gana la sección "Módulo dashboard"
> (aplicada por 1.1 en su mismo PR); el contrato fino vive en el brief
> `docs/DASHBOARD.md` (task 1.1). Precedencia: CONTEXTO/diseño v2 mandan.
>
> Decisiones selladas de este plan (lead + 2 rondas de validación):
> - **Moneda (regla 4)**: series y tablas en moneda NATIVA por plataforma;
>   CERO agregación cross-currency; conversión futura solo vía `fx_resolve`.
> - **Grano (anti-doble-conteo)**: `ads_metric_observation` mezcla filas de
>   campaña, keywords y targets — toda serie/agregado filtra por `kind`
>   EXPLÍCITO (columna de `ad_entity`, vía JOIN — no existe en la tabla de
>   métricas) (Resumen y por-campaña: solo `kind='campaign'`; las hojas
>   duplicarían el dinero). Sellado con test regla 9.
> - **Ventanas**: series diarias en UTC, rango default [D-30, D-1]; el día
>   en curso EXCLUIDO (vintage parcial, regla 6). Los días D-8..D-1 se
>   muestran MARCADOS como "inmaduros" (la atribución madura en 5-8d y el
>   costo hasta D+15 — mostrar sin marcar miente, ocultar pierde utilidad).
> - **NULL y ceros (regla 3)**: métrica NULL = hueco visible "sin dato",
>   JAMÁS un 0 pintado; ACoS con revenue=0 y cost>0 = "sin ventas" (∞),
>   nunca división ni cero engañoso.
> - **Procedencia del target (regla 2)**: CINCO peldaños — goal de campaña,
>   goal de plataforma, setting de config, cache del estado, default 55 —
>   expuestos por `goals.py` (variante valor+peldaño), JAMÁS reimplementados
>   en la capa web.
> - **Reuso (reglas 1-2)**: los helpers que ya existen en `app/api.py`
>   (`_parse_notes` de formato mixto, SQL de último ciclo por plataforma,
>   serialización de dinero como STRING) se EXTRAEN y comparten con
>   `app/api_dashboard.py` — nunca dos copias.
> - **Dinero en JSON = STRING** en todos los endpoints nuevos (patrón
>   sellado de 3.2). Para las gráficas (que exigen números): la conversión
>   string→número ocurre EN el cliente (JS parsea el string de la API) —
>   representación intermedia documentada; el backend jamás emite floats
>   de dinero (afinación grok r2).
> - **Paginación del feed por CURSOR** (`id <` último visto, DESC): offset
>   sobre una tabla append-only produce huecos/duplicados entre páginas.
> - **Salud**: snapshot del último ciclo por plataforma + histórico 14d.
> - **Feed — límite declarado**: solo entidades que SÍ decidieron; skips
>   agregados por motivo (así los persiste `notes`, sin id de entidad).
> - **Motivos → español**: dict que IMPORTA las constantes `MOTIVO_*`.
> - **XSS (dos contextos)**: Jinja2 con autoescape VERIFICADO para HTML; los
>   datos hacia JS de gráficas pasan EXCLUSIVAMENTE por `|tojson`; header
>   CSP `default-src 'self'`; `Cache-Control: no-store`.
> - **Regla 8**: antes de fijar los tests de cada endpoint, correr el SELECT
>   de forma real contra la base viva (por túnel) y anotar la evidencia.
> - **`app/api_dashboard.py` desde el inicio** (api.py va en 349 líneas).
> - **Dependencias**: Jinja2 pinneada en pyproject + uv.lock commiteado
>   (patrón de 1.1 de ORBIT 03); la lib de gráficas vendoreada con versión,
>   licencia y hash documentados en el brief.
> - **Exposición VPN = PR separado** (Phase 2), único cambio de red,
>   revertible, con sign-off del dueño. Residual ACEPTADO con razón: la VPN
>   demuestra "no público", no "solo dueño" — aceptable porque TODOS los
>   peers WG son dispositivos del dueño (server mono-operador); si algún
>   día se agregan peers de terceros, la Phase 3 ya tendrá auth propia.
> - **Goals MUTABLES (corrección de ronda 2)**: `ads_optimizer_goal` es
>   mutable POR SCHEMA (únicos parciales por campaña/plataforma; la
>   historia se reconstruye vía `config_version` + `decision.inputs`
>   congelados — así lo sella DATABASE.md). La edición de Phase 3 es
>   UPDATE del goal + fila NUEVA de config cuando toque config; jamás
>   "fila nueva de goal".
>
> unknowns declarados (`not_observed != absent`): IP exacta de la interfaz
> WG y su alcanzabilidad solo-túnel (2.1 la verifica con evidencia); lib de
> gráficas final (1.6 la elige: candidatos uPlot/Chart.js single-file, la
> más chica que cubra líneas y barras); forma real de los datos (regla 8,
> por endpoint antes de sus tests).

## Phase 1 — Dashboard de LECTURA por túnel [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Brief `docs/DASHBOARD.md`: las 4 pantallas (Resumen: series diarias spend/revenue/ACoS por plataforma — moneda NATIVA, grano `kind='campaign'`, rango [D-30,D-1] UTC, días D-8..D-1 marcados inmaduros, NULL=hueco; Campañas: tabla 30d + target efectivo con PROCEDENCIA de 5 peldaños + estado del goal; Decisiones: feed por cursor con explicación en español desde `inputs` (el target mostrado se lee de `inputs.target_acos_pct_usado`, NUNCA de `inputs.goal.target_acos_pct` que es NULL cuando ganó el default — afinación grok r2); Salud: snapshot + histórico 14d + skips por motivo) con TODAS las decisiones selladas del header, contratos de endpoints, modelo de acceso y límite del feed. Spec delta a CONTEXTO.md. Marcar `ORBIT 16` In progress. `[tdd:skip:docs-brief]` | El brief responde SIN ambigüedad cada decisión sellada del header de este plan (checklist 1:1 contra la lista del header, verificable punto por punto); CONTEXTO.md actualizado; CI verde | - | cc:完了 [a9d2c04] |
| 1.2 | `app/optimizer/goals.py`: variante de la cascada que devuelve `(valor, peldaño)` con los CINCO peldaños (goal_campana, goal_plataforma, setting_plataforma, cache_estado, default), reutilizada por dashboard y compatible con el camino del motor (cero cambio de comportamiento). `[tdd:required]` | Tests: cada uno de los 5 peldaños gana con su fixture y reporta su nombre; los tests existentes de 2.4 pasan SIN tocarse; demostrado fallando (regla 9) | 1.1 | cc:完了 [28b5ade] |
| 1.3 | `app/api_dashboard.py` (módulo nuevo): endpoints GET de series temporales (por plataforma y por campaña) implementando los sellos: colapso bitemporal (regla 5), `kind='campaign'` explícito, [D-30,D-1] UTC, NULL como null (no 0), revenue=0 → ACoS null con flag "sin_ventas", dinero como STRING. ANTES de fijar tests: SELECT de forma real contra la base viva (regla 8, evidencia anotada). `[tdd:required]` | Tests: colapso (dos obs misma fecha → la última); ANTI-DOBLE-CONTEO demostrado fallando (fixture con fila campaign + fila keyword del mismo día → la serie usa SOLO campaign, regla 9); NULL≠0; sin_ventas; día en curso excluido; dinero string; superficie OpenAPI COMPLETA solo-GET | 1.1 | cc:完了 [f136e28] |
| 1.4 | Endpoints GET de resumen de campañas (métricas 30d colapsadas + target efectivo CON procedencia vía 1.2 + goal enabled/floor/ceiling) y feed de decisiones por CURSOR (join nombres — `ad_entity.name` nullable, término, motivo en español importando `MOTIVO_*`). Reuso obligatorio de helpers de api.py (extraídos a módulo compartido si hace falta). `[tdd:required]` | Tests: procedencia en los 5 peldaños; name NULL no revienta; motivo desconocido → fallback sin crash; cursor estable con inserción concurrente simulada (páginas sin duplicados ni huecos); ANTI-MEZCLA de monedas en el listado (cada fila lleva su currency y NO existe total al pie que sume USD+MXN — regla 4); regla 8 antes de los tests | 1.2, 1.3 | cc:完了 [35ee5dd] |
| 1.5 | Endpoint GET de salud: snapshot último ciclo por plataforma + histórico 14d + watermarks — REUTILIZANDO `_parse_notes` y el SQL de último ciclo de api.py (extracción compartida, no copia). Los motivos de skips usan el vocabulario del ORQUESTADOR (`cycle.py`: sin_goal, goal_disabled, etc. — es el que vive en `notes.skips`), distinto del `MOTIVO_*` de bid/hygiene que usa el feed de decisiones: DOS diccionarios de traducción, cada uno importando su fuente. Regla 8 (SELECT vivo) aplica a este endpoint igual que a 1.3/1.4. `[tdd:required]` | Tests: notes mixto; ciclo failed/degraded visible con motivo; histórico acotado 14d; los tests de 3.2 existentes siguen intactos tras la extracción | 1.3 | cc:完了 [fac5c47] |
| 1.6 | UI Jinja2 server-rendered (dep pinneada en pyproject + uv.lock; AUTOESCAPE verificado), lib de gráficas VENDOREADA (versión+licencia+hash en el brief; cero CDN), responsive, CSP `default-src 'self'`, `Cache-Control: no-store`. Datos a JS SOLO vía `|tojson`. Las 4 pantallas. `[tdd:required]` | Tests: 4 rutas → 200 con marcador único; XSS regla 9 en DOS contextos (search_term con `<script>` escapado en HTML — demostrado fallando con autoescape off — Y payload en datos de gráfica neutralizado por tojson); cero hosts externos en el HTML; headers presentes; uv.lock commiteado | 1.4, 1.5 | cc:完了 [b1d243a] |
| 1.7 | Uso real: smoke por túnel SSH desde la compu del dueño (4 pantallas con los datos vivos del shadow), feedback anotado en AppFlowy, cierre del PR de Phase 1 (CI verde, batería completa). `[tdd:skip:smoke-de-uso]` | El dueño vio el dashboard con datos reales y su feedback quedó en la fila ORBIT 16; PR de Phase 1 mergeado | 1.6 | cc:完了 [901db15] |

## Phase 2 — Exposición por VPN WireGuard [lane:gate] — PR SEPARADO

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Publicar el puerto TAMBIÉN en la IP de la interfaz WG (además de 127.0.0.1; JAMÁS 0.0.0.0). ANTES: verificar con evidencia (firewall/NAT + `ss -lntp`) que esa IP solo es alcanzable por el túnel. Candado `tests/test_compose_deploy.py::test_compose_ningun_puerto_en_todas_las_interfaces` a allowlist EXACTO `{127.0.0.1, <IP-WG>}` (set). DEPLOY.md actualizado. Residual "no-público ≠ solo-dueño" aceptado con razón (header). **Requiere sign-off explícito del dueño.** `[tdd:required]` | Candado demostrado fallando (regla 9) con `0.0.0.0` Y con IP pública sintética; smoke POSITIVO desde compu y cel por VPN; smoke NEGATIVO (inalcanzable fuera del túnel, con evidencia); accounting/bridge intactos; evidencia en ORBIT 16 | 1.7 | cc:完了 [1a9e7eb] |

## Phase 3 — Settings de ESCRITURA [lane:gate] — BLOQUEADA por ORBIT 04

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Con la auth propia de ORBIT 04: edición de settings respetando el schema sellado — **goal = UPDATE** (mutable por diseño, únicos parciales; historia vía `config_version` + `decision.inputs`) y **config de plataforma = fila NUEVA de `config_version`** (append-only por trigger); todo UPDATE de goal toca `updated_at` explícitamente en el SQL (no hay trigger que lo haga — historia falsa si se omite, afinación grok r2); pantalla de settings: target por plataforma, override por campaña (mostrando procedencia y a quién pisa), enabled, floor/ceiling; harvest config cuando el apply exista. CERO escritura antes de esa auth. `[tdd:required]` | Tests: sin credencial → rechazado; edición de goal = UPDATE visible en el ciclo siguiente con su rastro en `decision.inputs`; cambio de config = fila nueva (el trigger append-only lo exige); sign-off del dueño sobre la primera edición real; `ORBIT 16` → Done con notas completas al cerrar | ORBIT 04 | cc:TODO |

## Priorización

- **Required**: 1.1–1.7 (la herramienta de revisión del shadow — su momento es AHORA), 2.1 (el acceso pedido).
- **Recommended**: skips por entidad en el feed (si el uso real lo pide); pantalla de harvest candidates cuando PR2 traiga el bid sugerido.
- **Optional**: gráficas TACoS/margen (necesitan `fx_rate` + margen: ORBIT 06+); `v_metric_mature` como toggle de vista madura.
- **Reject** (con razón):
  - SPA con framework/build de frontend: infra que la autopsia mata.
  - Websockets/tiempo real: los datos cambian una vez al día.
  - Multi-usuario/roles: mono-operador detrás de VPN (si eso cambia, Phase 3 ya trae auth).
  - CDNs o assets externos: un CDN caído no puede romper el dashboard.
  - Exposición a internet público: la VPN es el perímetro.
  - Reverse-proxy adicional "para TLS" frente a la IP WG: WireGuard ya cifra extremo a extremo.
  - Allowlist por peer WG / smoke por peer no autorizado: sobre-ingeniería para un server mono-operador cuyos peers son todos del dueño (residual documentado en el header).
  - Editar settings desde el chat de claude.ai: la escritura va por la app con auth, no por un LLM.

## 事前確認

- 事項: external-send — `git push` de ramas + `gh pr create`/merge (un PR por phase)
  理由: cada phase cierra con PR y batería completa en CI, patrón del repo
  scope: Phases 1-3 / todas las tareas
- 事項: external-send — lecturas por ssh a goncloud: SELECTs de forma real (regla 8, read-only), curl al dashboard por túnel, `ss -lntp`, verificación firewall/NAT de la IP WG, y anotaciones de evidencia en AppFlowy
  理由: DoD de 1.3-1.5 (regla 8), 1.7 y 2.1; solo lectura, cero secretos
  scope: Phase 1 / 1.3-1.7, Phase 2 / 2.1
- 事項: destructive/estado — editar el compose de deploy del server para agregar el binding de la IP WG (aditivo: una línea de ports; db/bridge/accounting intactos) y `docker compose up -d --no-deps app`
  理由: DoD de 2.1 — único cambio de red del plan, PR separado con sign-off del dueño
  scope: Phase 2 / 2.1
- (sin secret-read en todo el plan)
