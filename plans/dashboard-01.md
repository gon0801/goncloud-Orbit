# Plans — DASHBOARD 01: dashboard del optimizador (lectura, VPN y settings)

> Purpose: darle al dueño OJOS sobre la sombra (tipo Perpetua, pero con la
> aritmética de cada decisión a la vista) y, después, la perilla de settings.
> Pedido del dueño 2026-08-24: gráficas de spend/revenue/ACoS, info de
> campañas, decisiones explicadas, y poder editar el target ACoS. Acceso:
> por su VPN WireGuard (compu/cel), patrón de sus demás apps del server.
> Registro: fila `ORBIT 16 — Dashboard del optimizador` en EHV Tasks.
>
> Validado por revisión de 5 perspectivas (team_validation_mode: subagent —
> Product/Architecture/Security/QA/Skeptic, 2026-08-24): 4 majors + 4 minors,
> TODOS incorporados a las tareas de abajo (moneda declarada, procedencia de
> la cascada como subtarea propia, test XSS de `search_term`, candado de
> puertos con test exacto + smoke negativo, Salud con alcance fijado, 1.2
> partida por superficie, Reject de reverse-proxy, exposición VPN como PR
> separado).
>
> Spec delta: `docs/CONTEXTO.md` gana la sección "Módulo dashboard"
> (aplicada por 1.1 en su mismo PR); el contrato fino vive en el brief
> `docs/DASHBOARD.md` (task 1.1). Precedencia: CONTEXTO/diseño v2 mandan;
> el dashboard es capa de LECTURA sobre lo ya sellado.
>
> Decisiones selladas de este plan (del lead, con la validación):
> - **Moneda (regla 4)**: todas las series y tablas van en moneda NATIVA por
>   plataforma (USD para us, MXN para mx), sin agregados cross-currency;
>   cualquier conversión futura pasa EXCLUSIVAMENTE por `fx_resolve` (hoy
>   fuera de alcance: `fx_rate` está vacía a propósito).
> - **Procedencia del target (regla 2)**: el peldaño ganador de la cascada
>   sale de `goals.py` (variante que devuelve valor + peldaño), JAMÁS de una
>   reimplementación en la capa web.
> - **Salud**: snapshot del último ciclo por plataforma + histórico de 14
>   días de `optimizer_cycle` (la tabla es chica; cubre el reloj del shadow).
> - **Feed de decisiones — límite declarado**: solo muestra entidades que SÍ
>   decidieron; los skips son agregados por motivo (así los persiste
>   `optimizer_cycle.notes`, sin id de entidad). "¿Por qué la campaña X no
>   se movió?" se responde en Salud por conteo de motivos, no por entidad
>   (mejora candidata para PR2 si el dueño la pide con uso real).
> - **Motivos → español**: la capa de presentación IMPORTA las constantes
>   `MOTIVO_*` del motor (dict constante→plantilla); nada de strings a mano.
> - **`app/api_dashboard.py` desde el inicio** (el api.py actual va en 349
>   líneas; el split de emergencia a mitad de PR se evita naciendo partido).
> - **Exposición VPN = PR separado** (Phase 2): primero el dashboard se USA
>   por túnel SSH; el único cambio de red del plan viaja solo, chico y
>   revertible, con sign-off explícito del dueño.
>
> unknowns declarados (`not_observed != absent`): IP exacta de la interfaz
> WG del server y su alcanzabilidad SOLO-túnel (2.1 la verifica con
> evidencia); tamaño real de la lib de gráficas vendoreada (1.6 la elige:
> candidatos uPlot/Chart.js single-file, la más chica que cubra líneas y
> barras).

## Phase 1 — Dashboard de LECTURA por túnel [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Brief `docs/DASHBOARD.md`: las 4 pantallas (Resumen: tendencias diarias spend/revenue/ACoS por plataforma, moneda NATIVA — regla 4 sellada arriba; Campañas: tabla con métricas 30d colapsadas + target EFECTIVO con PROCEDENCIA + estado del goal; Decisiones: feed paginado con explicación en español desde `inputs` congelados; Salud: snapshot último ciclo + histórico 14d + skips agregados por motivo), contratos de los endpoints nuevos, modelo de acceso (Phase 1 túnel, Phase 2 VPN), y el límite declarado del feed. Spec delta a `docs/CONTEXTO.md` (sección Módulo dashboard) en el mismo PR. `[tdd:skip:docs-brief]` | El brief responde moneda, alcance de Salud y límite del feed SIN ambigüedad (los 3 huecos de la validación); CONTEXTO.md actualizado; CI verde | - | cc:TODO |
| 1.2 | `app/optimizer/goals.py`: variante de la cascada que devuelve `(valor, peldaño)` — peldaño ∈ {goal_campana, setting_plataforma, cache_estado, default} — reutilizada por dashboard Y por el camino existente (el motor sigue consumiendo el valor; cero cambio de comportamiento). Regla 2: la precedencia vive en UN lugar. `[tdd:required]` | Tests: cada peldaño gana con su fixture y reporta su nombre; el camino existente del motor NO cambia (mismos resultados en los tests de 2.4 sin tocarlos); demostrado fallando (regla 9) | 1.1 | cc:TODO |
| 1.3 | `app/api_dashboard.py` (módulo NUEVO — el presupuesto de 900 nace respetado): endpoints GET de series temporales por día (spend/revenue/ACoS por plataforma y por campaña, desde las tablas bitemporales COLAPSADAS — regla 5: `v_metric_latest` / `DISTINCT ON` sellados; moneda nativa, CERO agregación cross-currency) montado en `app/main.py`. Guard solo-GET ampliado al router nuevo. `[tdd:required]` | Tests: serie correcta con fixture bitemporal (dos observaciones misma fecha → usa la última); test de que NINGUNA query suma monedas distintas (espejo del sello del schema); test de superficie: el OpenAPI COMPLETO sigue solo-GET (cierra de paso el minor declarado de 3.2) | 1.1 | cc:TODO |
| 1.4 | Endpoints GET de resumen de campañas (métricas 30d + target efectivo CON procedencia vía 1.2 + goal enabled/floor/ceiling) y de decisiones enriquecidas (join con nombres de entidad, término, paginado; motivo en español importando `MOTIVO_*`). `[tdd:required]` | Tests: procedencia correcta por fixture en los 4 peldaños; campaña sin nombre (targets NULL) no revienta el join; motivo desconocido → fallback genérico sin crash (regla 3: jamás inventar); paginación estable | 1.2, 1.3 | cc:TODO |
| 1.5 | Endpoint GET de salud: snapshot del último ciclo por plataforma (tolerando `notes` de formato MIXTO — JSON y texto "rastro", residual declarado de 3.1) + histórico 14d de `optimizer_cycle` + watermarks (importando las constantes de `windows.py` — regla 2, patrón de 3.2). `[tdd:required]` | Tests: notes mixto no revienta; ciclo `failed`/`degraded` visible con su motivo; histórico ordenado y acotado a 14d | 1.3 | cc:TODO |
| 1.6 | UI servida por la MISMA app: Jinja2 server-rendered (AUTOESCAPE verificado explícitamente), lib de gráficas VENDOREADA en `app/static/` (cero CDN; la más chica que cubra líneas+barras), responsive para cel, `Cache-Control: no-store`. Las 4 pantallas del brief. `[tdd:required]` | Tests: las 4 rutas → 200 con marcador único de cada pantalla; **test XSS (regla 9): un `search_term` con `<script>` en el feed se renderiza ESCAPADO — demostrado fallando con autoescape apagado** (hallazgo Security de la validación: los términos vienen de búsquedas reales de compradores); cero requests a hosts externos en el HTML generado | 1.4, 1.5 | cc:TODO |
| 1.7 | Uso real: smoke por túnel SSH desde la compu del dueño (las 4 pantallas con los datos vivos del shadow), feedback de primera impresión anotado, y cierre del PR de Phase 1 (CI verde, batería completa). `[tdd:skip:smoke-de-uso]` | El dueño vio el dashboard con datos reales y su feedback quedó en AppFlowy; PR de Phase 1 mergeado | 1.6 | cc:TODO |

## Phase 2 — Exposición por VPN WireGuard [lane:gate] — PR SEPARADO

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Publicar el puerto de la app TAMBIÉN en la IP de la interfaz WG del server (además de 127.0.0.1; JAMÁS 0.0.0.0). ANTES: verificar con evidencia que esa IP es alcanzable SOLO por el túnel (reglas de firewall/NAT + `ss -lntp`; un bind por-IP no basta si algún proxy la puentea). Candado `tests/test_compose_deploy.py::test_compose_ningun_puerto_en_todas_las_interfaces` actualizado a allowlist EXACTO `{127.0.0.1, <IP-WG>}` (set, no match laxo). DEPLOY.md con el modelo de acceso. **Este task relaja acotadamente un sello de PR1 y requiere sign-off EXPLÍCITO del dueño al aprobar este plan.** `[tdd:required]` | Candado demostrado fallando (regla 9) con `0.0.0.0` Y con una IP pública sintética; smoke POSITIVO desde un cliente VPN (compu y cel) y NEGATIVO (puerto inalcanzable desde fuera del túnel, con evidencia); accounting/bridge intactos | 1.7 | cc:TODO |

## Phase 3 — Settings de ESCRITURA [lane:gate] — BLOQUEADA por ORBIT 04

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Con la auth propia de ORBIT 04 (sellada en su Reject de PR1): endpoints write versionados (goal de campaña, config de plataforma — siempre filas NUEVAS, append-only) + pantalla de settings: target por plataforma, override por campaña (mostrando procedencia y a quién pisa), enabled, floor/ceiling; config de harvest cuando el apply exista. CERO escritura antes de esa auth. `[tdd:required]` | Tests de auth (sin credencial → rechazado), de versionado (cambio = fila nueva, jamás UPDATE), y de que el siguiente ciclo usa el valor nuevo; sign-off del dueño sobre la primera edición real | ORBIT 04 | cc:TODO |

## Priorización

- **Required**: 1.1–1.7 (la herramienta de revisión del shadow — su momento es AHORA, durante las 2 semanas del reloj), 2.1 (el acceso que el dueño pidió).
- **Recommended**: skips por entidad en el feed (si el uso real lo pide — hoy el límite está declarado); pantalla de harvest candidates cuando PR2 traiga el bid sugerido.
- **Optional**: gráficas de TACoS/margen (necesitan `fx_rate` + margen: fase margin-aware, ORBIT 06+).
- **Reject** (con razón):
  - SPA con framework/build de frontend (npm, bundlers): infra que la autopsia mata; server-rendered + una lib vendoreada cubre 4 pantallas.
  - Websockets/tiempo real: los datos cambian una vez al día (crons); recargar la página basta.
  - Multi-usuario/roles: server mono-operador detrás de VPN.
  - CDNs o cualquier asset externo: la app no depende de internet y un CDN caído no puede romper el dashboard.
  - Exposición a internet público: la VPN es el perímetro, punto.
  - **Reverse-proxy adicional (nginx/Caddy) "para TLS" frente a la IP WG**: WireGuard ya cifra extremo a extremo; proxy = infra innecesaria (hallazgo Skeptic).
  - Editar settings desde el chat del Project de claude.ai: el camino de escritura es la app con auth (ORBIT 04), no un LLM con acceso a la base.

## 事前確認

- 事項: external-send — `git push` de ramas + `gh pr create`/merge (un PR por phase: Phase 1 lectura, Phase 2 exposición VPN, Phase 3 settings)
  理由: cada phase cierra con PR y batería completa en CI, patrón del repo
  scope: Phases 1-3 / todas las tareas
- 事項: external-send — lecturas por ssh a goncloud para smokes (curl al dashboard por túnel, `ss -lntp`, verificación de firewall/NAT de la IP WG) y anotaciones de evidencia en AppFlowy
  理由: DoD de 1.7 y 2.1; solo lectura del estado del server, cero secretos
  scope: Phase 1 / 1.7, Phase 2 / 2.1
- 事項: destructive/estado — editar el compose de deploy del server para agregar el binding de la IP WG (aditivo: una línea de ports; los servicios db/bridge/accounting no se tocan) y `docker compose up -d --no-deps app`
  理由: DoD de 2.1 — es el único cambio de red del plan, en PR separado con sign-off del dueño
  scope: Phase 2 / 2.1
- (sin secret-read en todo el plan)
