# ORBIT 04 — PR2 optimizador: APPLY con topes, veto y harvest real

> **Propósito**: el motor escribe a Amazon POR PRIMERA VEZ. Traducción del PR2
> del diseño v2 al mundo Orbit (el stack viejo contra el que se escribió murió
> en ORBIT 02: adaptive/bid_cache/motor-flags son obsoletos — las REGLAS
> viajan, la mecánica se traduce). Validación: (a) panel de 5 perspectivas
> (30 hallazgos, v1); (b) ronda 1 de cross-review codex + qwen (codex: 9
> altas + 3 medias; qwen: 2 altas + medias/bajas — sus puntos 1-5 verbatim se
> perdieron por un tail corto del lead, reconstruidos del cierre y
> convergentes con codex; declarado). Todo incorporado en este v2. ORBIT 04
> entrega TODO con la escalera en `shadow` — cero escrituras fuera del smoke
> autorizado de 4.3; ORBIT 05 es el cutover (flip a live + rampa), con
> criterio sellado: 2 semanas de shadow (~2026-09-07) + recálculo manual +
> **veto operativo y probado por el dueño** (prerequisito nuevo).

## Decisiones SELLADAS (header manda sobre las tareas)

1. **HÍBRIDO con ventana de veto** (decisión del dueño 2026-08-24, CAMBIO
   CONSCIENTE del sello "live automático sin approval queue" del diseño v2;
   razón registrada: caso arras — el motor negativizaría su término core con
   116 clicks / 0 ventas y el dueño quiere poder arreglar el listing en vez
   de cortar). Los BIDS aplican automático en su ciclo; los CORTES
   (pause/negative/harvest) entran a la cola con ventana de veto.
   **Perímetro blindado (Skeptic)**: SOLO cortes pasan por la cola — bids
   jamás; el default al VENCER es APLICAR (el silencio no bloquea); ninguna
   señal/gate futuro puede colgarse de la cola.
2. **Ventana de veto: 48h**, y el reloj NO se detiene si dashboard/Telegram
   caen (costo aceptado: pausar el reloj por infra = fail-closed del lado
   equivocado; mitigación = notificación al ENCOLAR + 48h de margen).
3. **Veto DURABLE con vencimiento**: vetar crea un BLOQUEO por clave
   `(platform, ad_entity_id, kind, search_term)` con vencimiento 30 días
   (default, editable al vetar); mientras el bloqueo esté vigente el motor no
   re-decide NI re-encola esa clave; al vencer puede re-proponer con datos
   frescos. El bloqueo vive en la fila `vetoed` de la cola (su `vence_el` es
   consultable) — el gate del orquestador consulta EN-VUELO y BLOQUEOS
   VIGENTES juntos. Jamás one-shot ni permanente por default.
4. **`apply_queue` (migración 0002) — máquina de estados COMPLETA** (ronda 1:
   la v1 no tenía estados post-liberación y el índice en-vuelo era
   contradictorio): `pending_veto → vetoed | released`;
   `released → applying → applied | failed` (terminales: `vetoed`, `applied`,
   `failed`). Nace `pending_veto` por trigger; transiciones selladas por
   trigger de UPDATE (no solo INSERT); `vetoed_at/by`, `vence_el`,
   `request_payload JSONB` (la INTENCIÓN durable, ver 10). ÚNICO PARCIAL de
   en-vuelo SOLO sobre estados NO terminales, **`NULLS NOT DISTINCT`** (los
   pause llevan search_term NULL: sin esto, dos pauses de la misma entidad
   no chocan — ronda 1). `decision` es append-only y `decision_application`
   es "intento contra Amazon": ninguna puede ser la cola. Transiciones
   atómicas (`UPDATE … WHERE estado='…'`): veto vs vencimiento compiten y
   gana exactamente uno.
5. **Skip `veto_pendiente` POR CLAVE, no por entidad** (ronda 1: bloquear la
   entidad entera serializaría los negatives de un ad group a uno por
   ventana): el ciclo no re-decide una clave con item en vuelo o bloqueo
   vigente; claves distintas de la misma entidad avanzan en paralelo.
   Frescura envejecida declarada: un corte vencido se aplica con ventana de
   hasta D-12/D-13 — costo aceptado.
6. **AMBAS plataformas a la vez** (dueño). Presupuesto de revisión humana de
   48h: 2 plataformas × 4 kinds.
7. **Rampa corta con topes por config**: día 1 = 10 bids / 2 pauses /
   5 negatives / 2 harvests POR DÍA Y PLATAFORMA — la unidad es la
   **OPERACIÓN LÓGICA** (un harvest = 1 unidad aunque sean 2 HTTPs; las
   REVERSAS de seguridad están EXENTAS de quota: la seguridad no se bloquea
   — ronda 1). Claves selladas `ads_apply_cap_<platform>_<kind>` en
   `config_version`; **fail-closed: clave ausente → cero applies,
   implementado como AUSENCIA de fila del día** (el CHECK del schema exige
   cap > 0: jamás una fila con cap 0 — ronda 1). Duplicación MANUAL cada 48h
   sanas (insert de config por el lead con OK del dueño). **"48h sanas" con
   semántica de resolución** (ronda 1): cero `verify_ok=false` NUEVOS en la
   ventana sin reintento exitoso posterior ni resolución anotada en ORBIT 04,
   y cero `harvest_job` failed NUEVOS en la ventana — ventana móvil, un
   incidente histórico resuelto no bloquea para siempre. Criterio de éxito:
   "la rampa avanza sin incidentes", NO "backlog en cero en 1 semana".
8. **Quota sellada en el SCHEMA, no solo en código** (ronda 1: con los grants
   actuales `app_decide` puede inventarse el cap o decrementar `used` — el
   backstop sería decorativo; DATABASE.md ya reconoce el hueco): la 0002
   agrega a `apply_quota_state` trigger de INSERT que exige `cap` == valor
   vigente en `config_version` para esa clave (fila del día solo nace de la
   config), trigger de UPDATE que solo permite `used` CRECIENTE, y
   vocabulario cerrado de claves `ads_optimizer:<platform>:<kind>` (cierra el
   residual 1 del diseño v2). Se consume al APLICAR (no al decidir), ANTES
   del HTTP; cap agotado a mitad de lote → **cortes** esperan (FIFO por
   `queued_at`); **bids NO se reintentan jamás** (ronda 1: un bid no aplicado
   en su ciclo queda DESCARTADO — la decisión fresca del ciclo siguiente lo
   supera; re-aplicar un bid viejo pisaría la decisión nueva y dispararía
   cooldown extemporáneo).
9. **Cliente de ESCRITURA = módulo separado** de `app/ads/client.py` (que
   sigue read-only): allowlist default-deny de mutaciones (update bid,
   pause/resume, create/delete negative exact, create/delete keyword — nada
   más), **payloads de UN solo objeto** (los endpoints son bulk/207: sin este
   sello, un HTTP consumiría 1 quota y aplicaría N cambios — ronda 1);
   **mutaciones no idempotentes JAMÁS auto-retry** (espejo del
   `idempotent=False` sellado del read client: fallo ambiguo → no reintenta,
   la reconciliación resuelve — ronda 1); constructor exige
   `modo_confirmado='live'` re-resuelto POR DECISIÓN (`goal.mode` +
   `modo_desde_settings` + **`enabled` + existencia del goal** — ronda 1: un
   goal deshabilitado durante la ventana debe frenar el corte encolado;
   jamás `inputs.modo`/`cycle.mode`); sin singleton; redacción heredada (los
   bodies auditables van a `platform_ack`/`inputs`, jamás a logs). Candados
   de import: el motor puro NO importa write/apply; SOLO el apply importa
   write. Residual declarado: credenciales LWA únicas con scope de escritura
   — la separación read/write es de código, no de credencial.
10. **Secuencia de apply sellada — INTENCIÓN DURABLE ANTES DEL HTTP** (ronda
    1: la v1 permitía una mutación real sin rastro si el proceso caía entre
    HTTP y registro): por mutación, TODO esto commiteado ANTES del primer
    byte a Amazon: (a) claim atómico de la cola (`released → applying`) para
    cortes; (b) `decision_application` INSERT con `attempted_at`; (c)
    `request_payload` con la intención EXACTA (para harvest incluye el bid
    efectivo a escribir — ronda 1: si el crash llega tras consultar el
    sugerido, la reconciliación debe conocer la intención, no adivinarla);
    (d) consumo de quota. Luego HTTP → readback → sellar terna
    `confirmed_at + verify_ok + platform_ack` JUNTA. Divergencia
    (`verify_ok=false`): reintento con tope 3 (test: NO existe cuarto
    intento — ronda 1) y `platform_ack` acumula acks como lista JSONB. Un
    `applying` huérfano (crash) lo resuelve la reconciliación del ciclo
    siguiente contra Amazon VIVO — y esto aplica a los negatives NORMALES
    igual que a harvest (ronda 1: el POST ambiguo de un negative fuera de
    harvest duplicaba o se perdía en silencio; ahora todo corte pasa por la
    cola con estado durable + reconciliación). El apply corre DENTRO del
    ciclo 08:40/08:41; bids decide→aplica en el mismo ciclo; cortes vencidos
    en el primer ciclo tras `queued_at + 48h`, con **re-check del estado
    vivo antes de mutar** (ronda 1: deriva durante la ventana — entidad ya
    pausada a mano / negativo ya existente = `applied` con ack "ya estaba",
    sin gastar HTTP de mutación ni quota).
11. **Regla 7 — tabla de reversas** (cada una implementada Y testeada ANTES,
    mismo PR que su mutación): bid → restaurar `old_value`; pause → resume;
    negative → delete; harvest PARCIAL (solo negativo creado) → delete del
    negativo; harvest COMPLETO → **delete de la keyword PRIMERO, luego el
    negativo** (ronda 1: al revés, un fallo intermedio deja el término
    fluyendo por origen Y cosechado en destino = doble costo).
12. **Harvest**: fases `pending → negative_created → exact_created → done` +
    reconciliación al INICIO del ciclo contra Amazon VIVO (regla 10; amplía
    el read client con `/sp/negativeKeywords/list` — tarea con test de
    superficie). **La 0002 SELLA las transiciones de `harvest_job` por
    trigger de UPDATE** (ronda 1: hoy el trigger solo cubre INSERT y
    `app_decide` puede saltar fases ilegalmente). Matriz de reconciliación
    completa en el brief; reintentos consumen quota; match por
    keyword_text+match_type normalizados. Fallo definitivo en
    `negative_created` → REVERSA AUTOMÁTICA + alerta Telegram (dueño).
13. **Bid del harvest**: `decision.new_value` congela `harvest_default_bid`
    (lo decidido); al aplicar se consulta el bid SUGERIDO de Amazon (dueño
    4.3; endpoint verificado EN VIVO — regla 8 — y **la tarea que lo
    verifique define por CUÁL cliente viaja y amplía el guard que
    corresponda con su test** si la forma real es un POST no-list — ronda
    1), se CLAMPEA a [floor, ceiling], se PERSISTE como intención en
    `request_payload` ANTES del POST (ver 10) y queda en `platform_ack`; sin
    sugerencia → `harvest_default_bid` (regla 3).
14. **Goals de harvest** (dueño 2026-08-24): MX = `AC - Category Exact - MX`
    ad group 553629449717842, fallback **10.00 MXN**; US = familias AU2
    (destino `AU2 - Category Exact - US`, id 3926) y USPerNog (destino
    `USPerNog - Category Exact - US`, id 3919), fallback **1.00 USD**; A1U
    FUERA por ahora (skip visible = correcto). El dueño REACTIVA las dos
    Exact en consola (están PAUSED); ad groups destino se resuelven en el
    seed. Alta de familias futuras = un goal por config, cero código
    (procedimiento en el brief).
15. **Gracia de reactivación manual (7d)**: solo el caso DETECTABLE (el
    motor pausó con apply verificado y el sync ve ENABLED → no re-pausar
    7d). Lo invisible (ENABLED manual de algo nunca tocado) queda residual
    declarado — fiel al residual 3 del diseño v2.
16. **AUTH de escritura**: token estático (entropía real, `secrets/` 0600
    ro, `register_secret`), `secrets.compare_digest`, SOLO header, exigido
    en TODO endpoint de escritura (veto, goals write; desbloquea dashboard-01
    Phase 3). Rotación manual en DEPLOY.md. Roles: veto humano = `app_admin`;
    test de privilegio negativo: `app_decide` NO puede vetar. **`/run` =
    Reject formal** (ronda 1: el docstring de api.py lo prometía para PR2):
    el disparo manual sigue siendo el CLI por ssh; la tarea 3.1 corrige el
    docstring — un endpoint HTTP que dispara ciclos es superficie de ataque
    sin beneficio sobre el CLI.
17. **Telegram como parte del MECANISMO** (módulo nuevo `app/notifica.py`,
    patrón fail-silent con warning — es código nuevo, no reuso — ronda 1):
    notificación al ENCOLAR ("N cortes entran en ventana, vencen el
    <fecha>") + digest mínimo de aplicados por ciclo + alerta de harvest
    failed. El digest rico sigue en consolidación.
18. **UI mínima de veto DENTRO de ORBIT 04**: pantalla con cortes pendientes
    + vencimiento + botón vetar (vencimiento del bloqueo editable), con la
    auth de 16.
19. **`applied_count` atribuible** (ronda 1: los cortes se aplican en un
    ciclo POSTERIOR al que decidió y el envelope viejo ya está cerrado): la
    0002 agrega `decision_application.applied_cycle_id` (FK a
    `optimizer_cycle`); `applied_count` del envelope = applications selladas
    con `applied_cycle_id` = ese ciclo; el digest reporta por ciclo
    EJECUTOR. Invariante con test.
20. **`HAY_MODULO_APPLY=True`** al integrar, con el candado S6 regla 9:
    escalera `shadow` → CERO HTTP de mutación (mock registra, demostrado
    fallando sin el gate). Tras ORBIT 04, UN insert de config enciende
    escrituras reales — el DoD del cierre verifica escalera en `shadow`.
21. **Smoke de primera mutación real** (pre-cutover; no hay sandbox de
    Amazon Ads): **script de smoke que usa el write client DIRECTO, fuera
    del motor** (ronda 1: el motor no puede producir un ±0.01 a voluntad —
    el clamp y el no-op de |Δ|<0.01 lo impiden; el script es herramienta de
    ensayo, no camino del motor), con autorización del dueño y campaña
    elegida por él: bid ±0.01 → readback → REVERSA → readback; fija la forma
    real del ack (207 multi-status, ids) antes de los tests del readback;
    todo devuelto y verificado al terminar.
22. **Deploy endurecido**: env files por servicio + usuario no privilegiado
    — **con el ajuste de permisos de `secrets/` declarado** (ronda 1: hoy
    `user: "0:0"` existe PORQUE secrets/ es root:600; non-root exige grupo
    dedicado + 640 o uid match, no solo cambiar el user del compose).
23. **Superficie amigable de goals** (pre-nota del dueño): `app.cli goals
    set` + endpoints write con la auth de 16 — target ACoS por
    plataforma/campaña sin psql; goal=UPDATE con `updated_at` explícito,
    config=fila nueva (sellado dashboard-01). Una sola implementación
    compartida CLI/endpoint.

## Reject (con razón, no silencio)

- **Canary pct**: redundante — rampa + veto + caps cubren su función; un gate
  "registrado pero cableado después" es la enfermedad del stack viejo (el
  gate que nunca disparó en 220,494 decisiones).
- **Cron propio del apply**: segundo scheduler sin necesidad; la enfermedad
  de los 147 jobs.
- **`/run` endpoint HTTP** (ronda 1): superficie de ataque sin beneficio
  sobre el CLI por ssh; el docstring de api.py que lo prometía se corrige en
  3.1.
- **`cross_motor_cut_guard`**: no existe otro motor escritor en Orbit.
- **Reserva PAUSE dentro de cap compartido**: superada por caps por kind.
- **Items 3-4 del checklist de cutover del diseño v2** (motor-flags /
  adaptive / bid_motor / dayparting): stack apagado en ORBIT 02. Sobrevive
  SOLO el backup pre-cutover: dump `ad_entity_state` + `/sp/keywords/list` +
  `/sp/negativeKeywords/list` + `/sp/targets/list`.
- **Auth más allá del token estático**: sobre-ingeniería para mono-operador
  tras WireGuard (residual aceptado en dashboard-01).
- **Apply para MeLi**: escritura bloqueada a nivel cuenta.
- **Rampa automática**: el motor subiéndose su propio tope anula el candado.
- **Retry automático de mutaciones no idempotentes** (ronda 1): duplica
  negatives/keywords en fallo ambiguo; la reconciliación es el camino.

unknowns declarados (`not_observed != absent`): endpoint y shape real del bid
sugerido (regla 8 en vivo; decide su cliente/guard); shape real del ack de
mutación (207 — el smoke 21 lo fija); ad groups destino de las Exact de US
(post-reactivación); payload de negativeKeywords/list (regla 8); soporte
`NULLS NOT DISTINCT` verificado contra el Postgres 16 del server.

## Phase 1 — Cola, quota sellada y cliente de escritura [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Brief `docs/APPLY.md`: contrato fino de TODO el header (checklist 1:1), máquinas de estado de `apply_queue` (con estados post-liberación) y `harvest_job`, matriz de reconciliación (incluye negatives normales y `applying` huérfanos), tabla de reversas (parcial vs completo), claves de config selladas, secuencia intención-durable→HTTP→readback→terna, semántica de "48h sanas" con resolución, procedimiento de alta de familia harvest y rotación de token. Spec delta a CONTEXTO.md ("Módulo apply"). Marcar `ORBIT 04` In progress. `[tdd:skip:docs-brief]` | Checklist 1:1 contra el header verificable punto por punto; CONTEXTO.md actualizado; CI verde | - | cc:TODO |
| 1.2 | Migración `0002_apply.sql`: `apply_queue` (estados completos, triggers de INSERT y UPDATE, único parcial en-vuelo NULLS NOT DISTINCT, `request_payload`, `vence_el`, `vetoed_at/by`) + **sellos de `apply_quota_state`** (fila del día solo desde config vigente, `used` creciente, vocabulario de claves) + **triggers de transición de `harvest_job`** + `decision_application.applied_cycle_id` + grants por rol. `[tdd:required]` | Tests de schema: transición ilegal revienta en LAS TRES tablas (demostrado fallando); dos pauses en vuelo de la misma entidad chocan (NULLS NOT DISTINCT); app_decide no puede inventarse cap ni decrementar used ni vetar (privilegio negativo); atomicidad veto-vs-vencimiento | 1.1 | cc:TODO |
| 1.3 | Cliente de ESCRITURA (`app/ads/write.py`): allowlist default-deny de mutaciones + reversas, payloads de UN objeto (bulk rechazado), NO auto-retry de mutaciones (espejo idempotent=False), constructor con `modo_confirmado` obligatorio, redacción heredada. Ampliar `LIST_REQUEST_TYPES` con negativeKeywords (regla 8 en vivo del payload). `[tdd:required]` | Tests: path fuera del allowlist rechazado; payload multi-objeto rechazado (demostrado fallando); mutación con fallo ambiguo NO reintenta; constructor sin modo revienta; test_architecture nuevo (motor puro no importa write/apply; SOLO apply importa write — demostrado fallando); superficie del read client actualizada con test | 1.1 | cc:TODO |

## Phase 2 — El apply [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Núcleo de bids (`app/apply.py`): re-resolver POR DECISIÓN escalera + `goal.mode` + `enabled` + existencia del goal, intención durable ANTES del HTTP (decision_application INSERT + quota, commiteados), secuencia sellada con terna junta, retry divergencia tope 3 con acks acumulados, bids no aplicados por quota = DESCARTADOS (jamás reintentados), `applied_count` por `applied_cycle_id`, reversa de bid. `[tdd:required]` | Tests: escalera shadow → CERO HTTP (regla 9); goal disabled a media ventana frena el corte; crash simulado entre commit-intención y HTTP deja rastro auditable y la reconciliación lo resuelve; NO existe cuarto intento (regla 9); bid descartado no se re-aplica al día siguiente; quota a mitad de lote → cortes esperan sin error; terna parcial revienta (CHECKs reales); applied_count cuadra por ciclo ejecutor; reversa de bid testeada | 1.2, 1.3 | cc:TODO |
| 2.2 | Cola de cortes: encolar (pause/negative/harvest) con notificación al ENCOLAR, skip `veto_pendiente` POR CLAVE, liberar vencidos (FIFO, caps por operación lógica, reversas exentas), claim `released→applying` atómico, re-check de estado vivo pre-mutación (ya-en-estado = applied "ya estaba"), aplicar con reversas, veto durable 30d por clave, gracia de reactivación (caso detectable). `[tdd:required]` | Tests: carreras (veto durante applying → "en vuelo"; veto vs vencimiento → gana uno); skip por clave demostrado fallando Y claves distintas de la misma entidad avanzan; bloqueo vigente impide re-decisión y al vencer re-propone; re-check evita HTTP y quota (demostrado fallando); gracia 7d; reversas de pause y negative testeadas | 2.1 | cc:TODO |
| 2.3 | Harvest real: fases + reconciliación al inicio del ciclo contra Amazon VIVO (matriz completa, incluye `applying` huérfanos de negatives normales), bid sugerido (regla 8 en vivo: endpoint, cliente y guard decididos y testeados) clampeado + persistido como intención pre-POST + fallback, fallo definitivo → reversa automática + alerta, orden de reversa completo (keyword→negativo) y parcial (negativo), reintentos consumen quota. `[tdd:required]` | Tests: cada celda de la matriz con fixture; clamp demostrado fallando; intención durable del bid efectivo sobrevive crash simulado; orden de reversa completo demostrado fallando (al revés dejaría doble costo); failed → alerta (mock) | 2.2 | cc:TODO |

## Phase 3 — Superficies: veto, goals y Telegram [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Auth de escritura (token secrets, compare_digest, solo header) + endpoint de veto (`app_admin`, rastro actor+timestamp, vencimiento editable) + pantalla mínima de veto en el dashboard + corrección del docstring de api.py (Reject de `/run`) + rotación del token en DEPLOY.md. `[tdd:required]` | Tests: sin token → 401 en TODA escritura; token por query string NO autentica; veto marca vetoed con actor y vence_el; XSS del término (regla 9, autoescape+tojson); privilegio negativo del rol; docstring sin la promesa de /run | 1.2 | cc:TODO |
| 3.2 | Superficie amigable de goals: `app.cli goals set` + endpoints write (target/enabled/floor/ceiling por plataforma y campaña) con auth de 3.1 — goal=UPDATE tocando `updated_at` explícito, config=fila nueva; UNA implementación compartida CLI/endpoint. Desbloquea dashboard-01 Phase 3. `[tdd:required]` | Tests: edición visible en el ciclo siguiente con rastro en inputs; UPDATE toca updated_at (demostrado fallando); sin auth rechazado; CLI y endpoint comparten camino (test de una sola implementación) | 3.1 | cc:TODO |
| 3.3 | `app/notifica.py` (módulo NUEVO, fail-silent con warning): notificación al encolar con vencimiento + digest mínimo por ciclo ejecutor + alerta de harvest failed. `[tdd:required]` | Tests con transporte mock: encolar N → 1 mensaje con vencimiento; ciclo con applies → digest; failed → alerta; cero secretos en mensajes; fallo de Telegram JAMÁS tumba el ciclo (demostrado fallando) | 2.2 | cc:TODO |

## Phase 4 — Deploy, seeds y ensayo real [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | Deploy endurecido: env files por servicio + usuario no privilegiado CON el esquema de permisos de `secrets/` resuelto (grupo dedicado + 640 o uid match — hoy user 0:0 existe porque secrets es root:600). `[tdd:required]` | Candados de compose/Dockerfile actualizados y demostrados fallando; servicio arriba non-root LEYENDO secrets; accounting/bridge intactos | Phase 3 | cc:TODO |
| 4.2 | Seeds (dueño reactiva las Exact de US ANTES): goals de harvest MX (10.00 MXN) y US AU2+USPerNog (1.00 USD, ad groups post-sync), caps día 1 (10/2/5/2 × plataforma), ventana 48h, vencimiento veto 30d — config_version append-only. `[tdd:skip:seed-config]` | Seeds verificados con SELECT en vivo (regla 8); goal_harvest_completo satisfecho; fail-closed probado: sin clave de cap NO nace fila del día y el apply no emite HTTP | 4.1 | cc:TODO |
| 4.3 | SMOKE de primera mutación real (autorización del dueño + campaña elegida por él): script de smoke con el write client DIRECTO (fuera del motor), bid ±0.01 → readback → REVERSA → readback; fija la forma real del ack; todo devuelto y verificado. `[tdd:skip:smoke-produccion]` | Evidencia completa en ORBIT 04 (request/ack/readback/reversa); estado final == inicial verificado; tests del readback ajustados a la forma real | 4.2 | cc:TODO |
| 4.4 | Cierre: backup pre-cutover (dump ad_entity_state + keywords/negativeKeywords/targets lists), CHAT-CONTEXT al día, PR final mergeado, `ORBIT 04` Done con notas completas. FLIP a live + rampa = ORBIT 05 (checklist en el brief; prerequisito: veto probado por el dueño). `[tdd:skip:cierre-docs]` | Backup guardado y verificado; CI verde; evidencia en AppFlowy; escalera en `shadow` verificada al cerrar | 4.3 | cc:TODO |

## 事前確認

- 事項: external-send — `git push` + `gh pr create`/merge (un PR por phase)
  理由: cada phase cierra con PR y batería completa en CI, patrón del repo
  scope: Phases 1-4 / todas las tareas
- 事項: external-send — llamadas READ-ONLY nuevas a Amazon Ads (negativeKeywords/list, bid recommendations) para regla 8 y reconciliación
  理由: DoD de 1.3 y 2.3 exigen la forma real del dato en vivo
  scope: Phase 1 / 1.3, Phase 2 / 2.3
- 事項: external-send — escritura APPEND-ONLY en la base Orbit (migración 0002 vía app_admin, seeds de config/goals, apply_queue en los ciclos)
  理由: la cola y los seeds SON el modo de operación; DDL solo la 0002 revisada
  scope: Phase 1 / 1.2, Phase 4 / 4.2
- 事項: external-send/destructive — MUTACIONES REALES a Amazon SOLO en 4.3 (1 bid ±0.01 con reversa, campaña elegida por el dueño, autorización explícita en el momento) y mensajes Telegram del mecanismo
  理由: DoD de 4.3 (forma real del ack); el resto corre con escalera en shadow = cero mutaciones
  scope: Phase 4 / 4.3, Phase 3 / 3.3
- 事項: destructive — deploy: rebuild del servicio app con env split + non-root + ajuste de permisos de secrets; crons intactos
  理由: DoD de 4.1 (pre-nota obligatoria de ORBIT 03)
  scope: Phase 4 / 4.1
