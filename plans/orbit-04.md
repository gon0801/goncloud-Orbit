# ORBIT 04 — PR2 optimizador: APPLY con topes, veto y harvest real

> **Propósito**: el motor escribe a Amazon POR PRIMERA VEZ. Traducción del PR2
> del diseño v2 al mundo Orbit (el stack viejo contra el que se escribió murió
> en ORBIT 02: adaptive/bid_cache/motor-flags son obsoletos — las REGLAS
> viajan, la mecánica se traduce). Validación: panel de 5 perspectivas
> (Product/Architecture/Security/QA/Skeptic, 30 hallazgos incorporados) +
> rondas de cross-review por delante. ORBIT 04 entrega TODO con la escalera en
> `shadow` (cero escrituras); ORBIT 05 es el cutover (flip a live + rampa),
> con criterio sellado: 2 semanas de shadow (~2026-09-07) + recálculo manual +
> **veto operativo y probado por el dueño** (prerequisito nuevo).

## Decisiones SELLADAS (header manda sobre las tareas)

1. **HÍBRIDO con ventana de veto** (decisión del dueño 2026-08-24, CAMBIO
   CONSCIENTE del sello "live automático sin approval queue" del diseño v2;
   razón registrada: caso arras — el motor negativizaría su término core con
   116 clicks / 0 ventas y el dueño quiere poder arreglar el listing en vez de
   cortar). Los BIDS aplican automático en su ciclo; los CORTES
   (pause/negative/harvest) entran a una cola con ventana de veto.
   **Perímetro blindado (Skeptic)**: SOLO cortes pasan por la cola — bids
   jamás; el default al VENCER es APLICAR (el silencio no bloquea: lo
   contrario resucita el proposal-only que murió con backlog en cero); ninguna
   señal/gate futuro puede colgarse de la cola.
2. **Ventana de veto: 48h**, y el reloj NO se detiene si el dashboard o
   Telegram están caídos (costo aceptado con razón: pausar el reloj por infra
   = fail-closed del lado equivocado; mitigación = notificación al ENCOLAR +
   48h dan un día entero de margen).
3. **Veto DURABLE con vencimiento**: vetar bloquea ese término/entidad+kind
   por 30 días (default, editable al vetar); al vencer, el motor puede
   re-proponer con datos frescos. Jamás one-shot (inutilizable: re-vetar cada
   ciclo) ni permanente por default (blocklist que se pudre).
4. **Estado del veto = tabla NUEVA `apply_queue`** (migración 0002), patrón
   `harvest_job`: nace `pending_veto` por trigger; transiciones selladas
   `pending_veto → released | vetoed | expired_released`; `vetoed_at/by`;
   ÚNICO PARCIAL "un item en vuelo por (platform, ad_entity_id, kind,
   search_term)" — sin él, el ciclo siguiente encola duplicados (la unicidad
   de `decision` es por ciclo, no global). `decision` es append-only y
   `decision_application` es "intento contra Amazon": ninguna de las dos puede
   ser la cola. Transiciones ATÓMICAS (`UPDATE … WHERE estado='pending_veto'`:
   veto y vencimiento compiten y gana exactamente uno — patrón del claim del
   lock).
5. **Skip nuevo `veto_pendiente`** en el vocabulario del orquestador: si un
   corte de la entidad E está en cola, el ciclo NO genera otra decisión de
   corte sobre E. Frescura envejecida declarada: un corte vencido se aplica
   con ventana de hasta D-12/D-13 — la madurez del trigger no se rompe; costo
   aceptado.
6. **AMBAS plataformas a la vez** (decisión del dueño; no cutover gradual por
   plataforma). Presupuesto de revisión humana de 48h: 2 plataformas × 4
   kinds.
7. **Rampa corta con topes por config**: día 1 = 10 bids / 2 pauses /
   5 negatives / 2 harvests POR DÍA Y PLATAFORMA. Claves selladas
   `ads_apply_cap_<platform>_<kind>` en `config_version` (append-only);
   **fail-closed: clave ausente → cap 0** (cero applies), jamás un default.
   Duplicación **MANUAL** cada 48h sanas (unánime del panel: si el motor
   calcula su propio tope, el candado es decorativo) — insert de config por el
   lead con OK del dueño. "48h sanas" = cero `verify_ok=false` sin resolver +
   cero `harvest_job` failed + spot-check del digest OK. Criterio de éxito de
   la rampa: "avanza sin incidentes", NO "backlog en cero en 1 semana" (los
   caps de cortes + veto hacen esa promesa incumplible; la lentitud es
   diseño).
8. **Quota**: clave `ads_optimizer:<platform>:<kind>` en `apply_quota_state`
   (cierra el residual 1 del diseño v2); se consume al APLICAR (no al
   decidir) y **ANTES de cada HTTP de mutación** (backstop: un bug desbocado
   queda topado al cap del día); cap agotado a mitad de lote → el resto espera
   a mañana SIN error, orden FIFO por `queued_at`.
9. **Cliente de ESCRITURA = módulo separado** de `app/ads/client.py` (que
   sigue read-only), con: allowlist default-deny de mutaciones (update bid,
   pause/resume, create/delete negative exact, create/delete keyword — y NADA
   más, espejo de `MutationNotAllowedError`); constructor que exige
   `modo_confirmado='live'` re-resuelto POR DECISIÓN (`goal.mode` +
   `modo_desde_settings` — jamás `inputs.modo`/`cycle.mode`: residual sellado
   de cycle.py, ahora tarea con test); sin singleton/factory global; misma
   redacción de secretos (los bodies auditables van a
   `decision_application.platform_ack` / `decision.inputs`, JAMÁS a logs).
   Candados de import (test_architecture): el motor puro NO importa el write
   client ni el apply; SOLO el apply importa el write client. Residual honesto
   declarado: las credenciales LWA son las mismas con scope de escritura — la
   separación read/write es de código, no de credencial (Amazon no da tokens
   read-only).
10. **Secuencia de apply sellada** (por mutación): decisión commiteada →
    consumir quota (commit) → HTTP → readback → sellar terna
    `confirmed_at + verify_ok + platform_ack` JUNTA (CHECKs del schema).
    Divergencia (`verify_ok=false`): reintento con tope (3), y `platform_ack`
    acumula los acks como LISTA JSONB con timestamps (la evidencia de la
    divergencia anterior NO se pisa). El apply corre DENTRO del ciclo de las
    08:40/08:41 (cron propio = Reject); bids decide→aplica en el mismo ciclo;
    cortes vencidos se aplican en el primer ciclo tras `queued_at + 48h`.
11. **Regla 7 verificable — tabla de reversas** (cada una implementada Y
    testeada ANTES, en el mismo PR que su mutación): bid → restaurar
    `old_value` congelado; pause → resume; negative → delete; keyword harvest
    → delete (reversa del harvest parcial: PRIMERO delete del negativo).
12. **Harvest**: fases `pending → negative_created → exact_created → done` +
    reconciliación al INICIO del ciclo contra Amazon VIVO (regla 10 — jamás
    contra el cache `ad_entity`; requiere ampliar el allowlist del read client
    con `/sp/negativeKeywords/list`: tarea explícita con su test de
    superficie). Matriz de reconciliación completa en el brief (POST voló y
    crash antes del UPDATE → avanzar sin re-POST; reintentos consumen quota;
    match por keyword_text+match_type normalizados). **Fallo definitivo en
    `negative_created` → REVERSA AUTOMÁTICA del negativo + alerta Telegram**
    (decisión del dueño: cero estados invertidos silenciosos).
13. **Bid del harvest** (traducción A3, resuelve la contradicción con el
    schema): `decision.new_value` congela `goal.harvest_default_bid` (lo
    decidido); al APLICAR se consulta el bid SUGERIDO de Amazon (decisión del
    dueño 4.3; endpoint a verificar EN VIVO — regla 8 — antes de fijar tests),
    se CLAMPEA a [floor, ceiling] del goal y queda como evidencia en
    `platform_ack`; sin sugerencia → `harvest_default_bid` (jamás un número
    inventado, regla 3).
14. **Goals de harvest** (decisiones del dueño 2026-08-24): MX = destino
    `AC - Category Exact - MX` ad group 553629449717842, fallback **10.00
    MXN**; US = DOS familias con goal por campaña fuente: AU2 (destino
    `AU2 - Category Exact - US`, id 3926) y USPerNog (destino
    `USPerNog - Category Exact - US`, id 3919), fallback **1.00 USD** cada
    una; A1U queda FUERA por ahora (skip `harvest_sin_config` visible =
    comportamiento correcto). El dueño REACTIVA las dos Exact en la consola
    (Amazon no revive ARCHIVED; estas están PAUSED) y el sync las trae; los
    ad groups destino se resuelven en la ejecución del seed. Alta de familias
    futuras = UN goal nuevo por config, cero código (procedimiento en el
    brief).
15. **Gracia de reactivación manual (7d)**: solo el caso DETECTABLE — el
    motor pausó (apply verificado) y el sync ve ENABLED → no re-pausar por
    7d. Un ENABLED manual de algo que el motor nunca tocó es invisible
    (`ad_entity_state` no tiene historia): residual declarado, fiel al
    residual 3 del diseño v2.
16. **AUTH de escritura**: token estático con entropía real en `secrets/`
    (0600, montado ro, `register_secret` para redacción), comparación
    `secrets.compare_digest`, SOLO por header (jamás query string), exigido en
    TODO endpoint de escritura (veto, goals write; desbloquea la Phase 3 de
    dashboard-01). Rotación manual documentada en DEPLOY.md. Roles DB: el
    veto humano escribe como `app_admin`; **test de privilegio negativo:
    `app_decide` NO puede marcar `vetoed`** (el motor no se veta a sí mismo).
17. **Telegram entra a ORBIT 04 como parte del MECANISMO de veto** (no
    nice-to-have): notificación al ENCOLAR cortes ("N cortes entran en
    ventana, vencen el <fecha>") + digest mínimo de lo aplicado por ciclo. El
    digest rico (resumen económico) sigue en consolidación.
18. **UI mínima de veto DENTRO de ORBIT 04** (rompe la dependencia circular
    con dashboard-01 Phase 3): pantalla con cortes pendientes + botón vetar
    (con la auth de 16), en el dashboard existente.
19. **`optimizer_cycle.applied_count`** lo escribe el apply; invariante con
    test: cuadra contra `decision_application` verificadas del ciclo.
20. **`HAY_MODULO_APPLY=True`** al integrar, con el candado S6 demostrado
    fallando (regla 9): escalera en `shadow` → CERO HTTP de mutación (mock
    que registra cualquier intento). Dicho en voz alta: tras ORBIT 04, UN
    insert de config (`ads_optimizer_mode='live'`) enciende escrituras reales
    — por eso el DoD del cierre verifica escalera en `shadow`.
21. **Smoke de primera mutación real** (pre-cutover, regla 8/10 de
    escritura): no hay sandbox de Amazon Ads — tarea checkpoint con escalera
    `live` + goals de plataforma en `shadow` + UN goal de campaña (elegida
    por el dueño) en `live` con cap 1: un bid ±0.01 → readback → REVERSA →
    readback, para probar la forma real del ack (207 multi-status, ids)
    ANTES de fijar los tests del readback. Autorización del dueño en la
    ejecución.
22. **Deploy**: env files separados por servicio + usuario no privilegiado
    del contenedor (pre-nota obligatoria de ORBIT 03).
23. **Superficie amigable de goals** (requisito del dueño registrado en la
    pre-nota): subcomando CLI (`app.cli goals set`) + endpoints write de
    goals con la auth de 16 — target ACoS por plataforma y por campaña sin
    psql. La UI completa de settings sigue siendo dashboard-01 Phase 3 (se
    desbloquea con esto).

## Reject (con razón, no silencio)

- **Canary pct**: redundante — rampa por kind/plataforma + veto + caps día 1
  cubren su función con menos maquinaria; un gate "registrado pero cableado
  después" es la enfermedad del stack viejo (el gate que nunca disparó en
  220,494 decisiones).
- **Cron propio del apply**: segundo scheduler sin necesidad (la ventana de
  veto opera a granularidad diaria); la enfermedad de los 147 jobs.
- **`cross_motor_cut_guard`**: no existe otro motor escritor en Orbit.
- **Reserva PAUSE dentro de cap compartido**: superada por caps separados por
  kind; documentado para que nadie la "recupere".
- **Items 3-4 del checklist de cutover del diseño v2** (motor-flags /
  adaptive / bid_motor / dayparting): stack apagado en ORBIT 02. Sobrevive
  SOLO el backup pre-cutover: dump `ad_entity_state` + `/sp/keywords/list` +
  `/sp/negativeKeywords/list` + `/sp/targets/list`.
- **Auth más allá del token estático** (OAuth/JWT/sesiones/multi-usuario):
  sobre-ingeniería para mono-operador tras WireGuard (residual ya aceptado en
  dashboard-01).
- **Apply para MeLi**: escritura bloqueada a nivel cuenta (proposal-only).
- **Rampa automática**: el motor subiéndose su propio tope anula el candado.

unknowns declarados (`not_observed != absent`): endpoint y shape real del bid
sugerido de Amazon (regla 8 en vivo antes de sus tests); shape real del ack de
mutación (207 multi-status — el smoke 21 lo fija); ad groups destino de las
Exact de US (se resuelven post-reactivación del dueño); forma del payload de
negativeKeywords/list (regla 8 al ampliar el allowlist).

## Phase 1 — Cola, cliente de escritura y contratos [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Brief `docs/APPLY.md`: contrato fino de TODO el header (checklist 1:1), máquina de estados de `apply_queue` y `harvest_job`, matriz de reconciliación completa, tabla de reversas, claves de config selladas (caps + ventana + vencimiento veto), secuencia commit→quota→HTTP→readback→terna, procedimiento de alta de familia harvest nueva y de rotación del token. Spec delta a CONTEXTO.md (sección "Módulo apply"). Marcar `ORBIT 04` In progress. `[tdd:skip:docs-brief]` | Checklist 1:1 contra el header verificable punto por punto; CONTEXTO.md actualizado; CI verde | - | cc:TODO |
| 1.2 | Migración `0002_apply.sql`: tabla `apply_queue` (patrón harvest_job: nace `pending_veto` por trigger, transiciones selladas, único parcial en-vuelo, `vetoed_at/by`, vencimiento) + grants por rol (encolar/liberar = `app_decide`; `vetoed` = solo `app_admin`). `[tdd:required]` | Tests de schema: transición ilegal revienta; único parcial rechaza duplicado en vuelo; PRIVILEGIO NEGATIVO demostrado (app_decide no puede vetar, app_read nada); atomicidad veto-vs-vencimiento (dos UPDATE compiten, gana uno) | 1.1 | cc:TODO |
| 1.3 | Cliente de ESCRITURA (módulo nuevo `app/ads/write.py`): allowlist default-deny de mutaciones + reversas, constructor con `modo_confirmado` obligatorio, redacción heredada, cero uso fuera del apply. Ampliar `LIST_REQUEST_TYPES` del read client con negativeKeywords (regla 8 del payload en vivo). `[tdd:required]` | Tests: path fuera del allowlist → MutationNotAllowedError espejo; constructor sin modo confirmado revienta; test_architecture NUEVO: motor puro no importa write/apply y SOLO el apply importa write (demostrado fallando); superficie del read client actualizada con su test | 1.1 | cc:TODO |

## Phase 2 — El apply [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Núcleo de bids en módulo nuevo `app/apply.py`: re-resolver `goal.mode`+escalera POR DECISIÓN (jamás inputs.modo/cycle.mode — residual de cycle.py a tarea), quota `ads_optimizer:<platform>:<kind>` consumida ANTES del HTTP, secuencia sellada con terna junta, retry divergencia con `platform_ack` lista JSONB (tope 3), `applied_count` del envelope, reversa de bid. `[tdd:required]` | Tests: escalera shadow → CERO HTTP (mock registra, demostrado fallando sin el gate); quota agotada a mitad de lote → resto espera sin error; divergencia acumula acks sin pisar; terna junta pasa y parcial revienta (CHECKs reales); applied_count cuadra (invariante); reversa de bid testeada | 1.2, 1.3 | cc:TODO |
| 2.2 | Cola de cortes: encolar (pause/negative/harvest) con notificación Telegram al ENCOLAR, skip `veto_pendiente` en el orquestador, liberar vencidos (`queued_at+48h`, FIFO, respetando caps), aplicar con reversas listas (pause→resume, negative→delete), veto durable (bloqueo 30d editable por término/entidad+kind), gracia de reactivación (caso detectable, 7d). `[tdd:required]` | Tests: carreras Q1 (veto durante aplicación → gana uno; veto tras released → "en vuelo"); skip veto_pendiente demostrado fallando; durable: término vetado NO se re-encola por 30d y SÍ tras vencer; gracia: pause bloqueado 7d tras ENABLED manual post-apply propio; reversas testeadas | 2.1 | cc:TODO |
| 2.3 | Harvest real: fases + reconciliación al inicio del ciclo contra Amazon VIVO (matriz del brief completa), bid sugerido (endpoint verificado en vivo — regla 8) clampeado a [floor,ceiling] con fallback al default, fallo definitivo en `negative_created` → REVERSA AUTOMÁTICA + alerta, reintentos consumen quota. `[tdd:required]` | Tests: cada celda de la matriz de reconciliación con su fixture; clamp del sugerido demostrado fallando (fuera de rango pasaría crudo); reversa del parcial (primero delete negativo); failed → alerta enviada (mock) | 2.2 | cc:TODO |

## Phase 3 — Superficies: veto, goals y Telegram [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Auth de escritura (token en secrets, compare_digest, solo header) + endpoint de veto (`app_admin`, rastro actor+timestamp) + pantalla mínima en el dashboard: cortes pendientes con su vencimiento + botón vetar (con vencimiento del bloqueo editable). Rotación del token en DEPLOY.md. `[tdd:required]` | Tests: sin token → 401 en TODA escritura; token por query string NO autentica; veto marca vetoed con actor; XSS del término en la pantalla (regla 9, autoescape + tojson como en 1.6); privilegio negativo del rol | 1.2 | cc:TODO |
| 3.2 | Superficie amigable de goals: `app.cli goals set` + endpoints write (target ACoS por plataforma/campaña, enabled, floor/ceiling) con la auth de 3.1 — goal=UPDATE tocando `updated_at` explícito, config=fila nueva (sellado dashboard-01). Desbloquea formalmente dashboard-01 Phase 3. `[tdd:required]` | Tests: edición visible en el ciclo siguiente con rastro en inputs congelados; UPDATE toca updated_at (demostrado fallando); sin auth → rechazado; CLI y endpoint comparten el mismo camino (una implementación) | 3.1 | cc:TODO |
| 3.3 | Telegram del mecanismo: notificación al encolar + digest mínimo de aplicados por ciclo + alerta de harvest failed (token del env del server, fail-silent con warning — patrón existente). `[tdd:required]` | Tests con transporte mock: encolar N cortes → 1 mensaje con vencimiento; ciclo con applies → digest; failed → alerta; cero secretos en mensajes | 2.2 | cc:TODO |

## Phase 4 — Deploy, seeds y ensayo real [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | Deploy endurecido: env files separados por servicio + usuario no privilegiado del contenedor (pre-nota ORBIT 03) + rebuild. `[tdd:required]` | Candados de compose/Dockerfile actualizados y demostrados fallando; servicio arriba con usuario no-root; accounting/bridge intactos | Phase 3 | cc:TODO |
| 4.2 | Seeds (dueño reactiva las Exact de US en consola ANTES): goals de harvest MX (10.00 MXN) y US AU2+USPerNog (1.00 USD, ad groups resueltos post-sync), claves de caps día 1 (10/2/5/2 por plataforma), ventana 48h, vencimiento veto 30d — todo config_version append-only. `[tdd:skip:seed-config]` | Seeds verificados con SELECT en vivo (regla 8); goal_harvest_completo satisfecho; caps fail-closed probado (clave ausente → 0) | 4.1 | cc:TODO |
| 4.3 | SMOKE de primera mutación real (autorización del dueño + campaña elegida por él): escalera live + goals plataforma shadow + 1 goal campaña live cap 1 → bid ±0.01 → readback → REVERSA → readback; fijar los tests del ack con la forma real observada; TODO devuelto a shadow al terminar. `[tdd:skip:smoke-produccion]` | Evidencia completa en ORBIT 04 (request/ack/readback/reversa); escalera de vuelta en shadow verificada; tests del readback ajustados a la forma real | 4.2 | cc:TODO |
| 4.4 | Cierre: backup pre-cutover (dump ad_entity_state + keywords/negativeKeywords/targets lists), CHAT-CONTEXT al día, PR final mergeado, `ORBIT 04` Done con notas completas. El FLIP a live + rampa manual = ORBIT 05 (checklist en el brief; prerequisito: veto probado por el dueño). `[tdd:skip:cierre-docs]` | Backup guardado y verificado; CI verde; evidencia en AppFlowy; escalera en shadow al cerrar | 4.3 | cc:TODO |

## 事前確認

- 事項: external-send — `git push` + `gh pr create`/merge (un PR por phase)
  理由: cada phase cierra con PR y batería completa en CI, patrón del repo
  scope: Phases 1-4 / todas las tareas
- 事項: external-send — llamadas READ-ONLY nuevas a Amazon Ads (negativeKeywords/list, bid recommendations) para regla 8 y reconciliación
  理由: DoD de 1.3 y 2.3 exigen la forma real del dato en vivo
  scope: Phase 1 / 1.3, Phase 2 / 2.3
- 事項: external-send — escritura APPEND-ONLY en la base Orbit (migración 0002 vía app_admin, seeds de config/goals, apply_queue en los ciclos)
  理由: la cola y los seeds SON el modo de operación; DDL solo la migración 0002 revisada
  scope: Phase 1 / 1.2, Phase 4 / 4.2
- 事項: external-send/destructive — MUTACIONES REALES a Amazon SOLO en 4.3 (1 bid ±0.01 con reversa, cap 1, campaña elegida por el dueño, autorización explícita en el momento) y mensajes Telegram del mecanismo
  理由: DoD de 4.3 (forma real del ack); el resto de ORBIT 04 corre con escalera en shadow = cero mutaciones
  scope: Phase 4 / 4.3, Phase 3 / 3.3
- 事項: destructive — deploy: rebuild del servicio app con env split + non-root; crons intactos
  理由: DoD de 4.1 (pre-nota obligatoria de ORBIT 03)
  scope: Phase 4 / 4.1
