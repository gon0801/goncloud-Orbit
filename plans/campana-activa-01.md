# CAMPAÑA ACTIVA 01 — el motor solo toca campañas y ad groups ENABLED

> **Propósito**: el primer ciclo live de ORBIT 05 (2026-09-02, ciclos 33 US /
> 34 MX) aplicó 2 de sus 20 bids dentro de campañas **PAUSED** (decisión 1989
> en `USPerNog - Category Phrase - US` 3918 y 2104 en `AGM2M - Auto Discovery -
> MX` 165 — la campaña que el dueño dejó "fuera del piloto"). No mueve dinero
> (una campaña pausada no sirve) pero quema quota (2/20 cupos del día 1) y
> deja bids alterados que el dueño no pidió si un día reactiva la campaña.
> Causa: `cycle._gates_entidad` solo mira el `status` de la hoja
> (keyword/product_target) o del ad group; **nunca el de la campaña**, y para
> las hojas tampoco el del ad group. Este plan cierra ese hueco en los DOS
> momentos en que el motor toca Amazon: al decidir (ciclo) y al liberar la
> cola de cortes 48h después.
>
> Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > `docs/APPLY.md` > este plan.
> **No cambia umbrales, ni caps, ni la escalera, ni el esquema** (cero
> migraciones). Un solo PR.
>
> **Reparto** (`CLAUDE.md` global): **GLM implementa 1.1-1.3** (lógica del
> motor: gates y cola). El lead escribió este plan con la base viva, revisa
> la entrega contra `origin/master` + reviewer fresco + bots, **despliega al
> contenedor y corre la verificación en vivo (1.4)**. Cross-review: 1 ronda
> (codex) sobre el PR; 2ª SOLO si la 1ª halla severidad alta; jamás 3ª.
>
> **Reloj**: el cron de las 08:40/08:41 UTC corre TODOS los días con el
> código del contenedor. Mientras esto no esté desplegado, cada ciclo live
> puede volver a gastar cupos en campañas pausadas (hoy: 10 US + 2 MX de las
> 123 decisiones cayeron ahí). Prioridad alta; el deploy es del lead.

## Reglas de proceso para GLM (NO negociables)

1. **Rama desde `origin/master`** (`git fetch origin && git switch -c
   campana-activa-01 origin/master`), nunca desde el master local. Antes del
   PR: `git log origin/master..HEAD` lista SOLO los commits de este plan.
2. **Prohibido tocar producción**: cero `ssh goncloud`, cero `docker exec`
   al contenedor, cero SELECT a la base viva, cero AppFlowy. Las mediciones
   de regla 8 ya están en este plan (§Estado medido); la corrida real es del
   lead.
3. **TDD con log rojo** (regla 9 de `docs/CONTEXTO.md`): cada test nuevo se
   corre PRIMERO contra el código viejo y el fallo se pega en la sección
   "Decisiones y evidencia" de este plan (el `assert` exacto que rompió, no
   "falló"). Un test que pasa igual sin el fix NO cuenta.
4. **Local: solo el archivo que tocas** (`pytest tests/test_cycle.py -q -k
   campana`, etc.). **La batería completa corre UNA vez, en CI**, al abrir el
   PR (`.github/workflows/quality.yml` levanta Postgres 16 y exporta
   `ORBIT_TEST_DSN`). Sin Postgres local, los tests de ciclo/cola se
   SKIPEAN y el log rojo no existe → levanta uno igual al de CI:
   `docker run -d --name orbit-test-pg -e POSTGRES_USER=orbit -e
   POSTGRES_PASSWORD=orbit -e POSTGRES_DB=postgres -p 5432:5432 postgres:16`
   y `export ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`.
5. `ruff check --fix . && ruff format . && pre-commit run --all-files` antes
   de cada commit; **jamás `--no-verify`**. Sin acentos en el código.
6. **Decisiones escritas ANTES del código** en §"Decisiones y evidencia"
   (patrón ORBIT 06). Si algo del plan no cuadra con el código real, se
   escribe ahí y se PARA a preguntar al lead — no se "interpreta".
7. DoD de TODAS las tareas de GLM: marker de su fila a `cc:完了 [resumen]` +
   **una línea en `docs/CHAT-CONTEXT.md`** en lenguaje de negocio (el candado
   de frescura del CI la exige cuando el PR toca un marker).

## Estado medido que origina este plan (regla 8, lead, 2026-09-02 16:30 UTC, `orbit_read`)

| Medición | Resultado |
|---|---|
| Hojas (keyword/product_target) `ENABLED` cuya campaña NO está `ENABLED` | **US 299, MX 10** (campaña `PAUSED`, ad group `ENABLED` en todos) |
| Ad groups `ENABLED` cuya campaña NO está `ENABLED` | **US 37, MX 3** |
| Hojas `ENABLED` en ad group NO `ENABLED` con campaña `ENABLED` | **0** (hoy no ocurre; el gate igual se implementa: mañana puede) |
| Decisiones del ciclo 33 (US, live) en campaña `PAUSED` | 10 de 76 |
| Decisiones del ciclo 34 (MX, live) en campaña `PAUSED` | 2 de 47 |
| Bids APLICADOS en campaña `PAUSED` | 2 de 20: decisión 1989 (US 3918, 0.99→0.74 USD) y 2104 (MX 165, 13.64→10.23 MXN) |
| `ad_entity_state.synced_at` de campañas/grupos | 06:45 UTC diario (`ingest structure`); guarda del ciclo `MAX_EDAD_SYNC` = 48h (`app/optimizer/windows.py:136`) |
| Cola de cortes live | 0 filas (el gate de liberación no tiene casos vivos hoy) |

Esperado tras el deploy (si la estructura no cambia): el ciclo siguiente
reporta `skips.entidad.campana_no_enabled` ≈ **299 US / 10 MX** y
`skips.termino.campana_no_enabled` = la suma de términos de esos 37/3 grupos;
`estado_no_enabled` BAJA y mucho: como el gate de campaña precede al de
estado, las hojas PAUSED de campañas PAUSED también pasan a
`campana_no_enabled` (medido el 2026-09-03: 1597 US / 3115 MX; la
primera versión de esta nota decía lo contrario y era un error de cuenta).

## Decisiones selladas (diseño)

- **D1 · Orden de gates** (`cycle._gates_entidad`, de afuera hacia adentro):
  goal de campaña (`sin_goal`/`goal_disabled`/`goal_mode_off`) → **campaña
  `ENABLED`** (`campana_no_enabled`, NUEVO) → **ad group `ENABLED`**
  (`grupo_no_enabled`, NUEVO; solo para hojas — para el propio ad group su
  estado ya es el gate `estado_no_enabled` existente) → estado propio
  (`estado_no_enabled`) → cooldown 7d. Un ancestro **sin fila de state
  cuenta como no ENABLED** (regla 3: ausencia = fuera; misma semántica que
  hoy tiene la hoja).
- **D2 · Fuente = el cache `ad_entity_state`** (sync diario 06:45 UTC,
  guarda de 48h ya existente). NO se agrega un LIST fresco de campañas a
  Amazon: el ciclo ya lo hace por lote en `_SQL_DECISORAS`/`_SQL_GRUPOS`
  (cero queries nuevas por entidad) y en la cola basta una query al cache
  por fila liberada. Residual declarado: una campaña pausada por el dueño
  DESPUÉS del sync de las 06:45 y ANTES del ciclo de las 08:40 sigue
  contando como ENABLED ese día.
- **D3 · Gate también al liberar la cola** (`apply_cola._revalida`, ANTES
  del dispatch por kind y por tanto ANTES del LIST fresco de la hoja, del
  cobro de quota y del claim): campaña o ad group no ENABLED → **discard
  PRE-claim** con motivo `campana_no_enabled` / `grupo_no_enabled` (mismo
  vocabulario de strings que el ciclo; `apply_cola` define sus propias
  constantes porque `cycle` importa de `apply_cola`, no al revés). Aplica a
  pause, negative y harvest (para negative/harvest la "fila" ES el ad group:
  su propio status es el gate de grupo). Invariante intacto: un descarte
  jamás ocurre después de cobrar quota.
- **D4 · Harvest destino**: sin cambio. `docs/APPLY.md` §11a ya exige destino
  `ENABLED` por DoD del seed; NO se agrega gate de código al destino en este
  plan (YAGNI; residual declarado).
- **D5 · Dashboard**: los dos motivos nuevos entran a `MOTIVOS_ES_SALUD`
  (`app/api_dashboard.py`) — sin traducción `/salud` mostraría el id crudo.
- **D6 · Las 2 mutaciones ya aplicadas en campañas pausadas NO se revierten
  en este plan**: es decisión del dueño (tarea 1.5 del lead, vía
  `POST /reversa/bid`, que exige `applied_cycle_id` y ambas lo tienen).
- **D7 · Sin migración, sin cambio de esquema, sin cambio de config.**

## Phase 1 — El gate en los dos momentos [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **Gate de ancestros al DECIDIR** (`app/cycle.py`): constantes `MOTIVO_CAMPANA_NO_ENABLED = "campana_no_enabled"` y `MOTIVO_GRUPO_NO_ENABLED = "grupo_no_enabled"`; `_SQL_DECISORAS` gana `sg.status AS status_grupo, sc.status AS status_campana` (LEFT JOIN a `ad_entity_state` del ad group y de `ag.parent_id`); `_SQL_GRUPOS` gana `sc.status AS status_campana`; `_gates_entidad` recibe `ancestros: tuple[tuple[str, str \| None], ...]` ((motivo, status) de afuera hacia adentro) y los evalúa entre el gate de goal y el de estado propio (D1); los dos call sites desempacan las columnas nuevas y pasan sus ancestros; docstring del módulo (bala ELEGIBILIDAD) actualizado. Guía exacta en §1.1. `[tdd:required]` | Test `test_gate_campana_y_grupo_no_enabled` en `tests/test_cycle.py` demostrado ROJO contra master (KeyError `campana_no_enabled` + la hoja de la campaña pausada DECIDE un bid) y verde con el fix; los goldens/tests existentes intactos (pglast de `_SQL_DECISORAS`/`_SQL_GRUPOS` incluido); log rojo pegado en §Decisiones | - | cc:完了 [2026-09-02, GLM. Test test_gate_campana_y_grupo_no_enabled ROJO contra master (KeyError campana_no_enabled; log en §Decisiones) y verde con el fix; tests/test_cycle.py completo 37 passed (goldens + pglast intactos). Commit fix(cycle) ef0f535] |
| 1.2 | **Gate de ancestros al LIBERAR la cola** (`app/apply_cola.py`): constantes espejo, `_SQL_ANCESTROS` (status del grupo y de la campaña de la fila, resolviendo hoja vs ad group con `CASE`), `_revalida_ancestros(conn, fila)` y su llamada como PRIMER paso de `_revalida` (antes del dispatch por kind); docstring de `libera_vencidos` paso 2 lo cita. Seeds: `_semilla` de `tests/test_apply_cola.py` y de `tests/test_apply_harvest.py` dan state `ENABLED` a la campaña (hoy no la tienen — sin eso TODA la cola se descartaría con el gate nuevo: es el rojo que demuestra que el gate muerde). Guía exacta en §1.2. `[tdd:required]` | Tests `test_libera_descarta_pause_en_campana_pausada` y `test_libera_descarta_negative_en_grupo_pausado` en `tests/test_apply_cola.py` ROJOS contra master (motivo `ya_no_califica`/mutación HTTP en vez del discard) y verdes con el fix: fila `discarded` con `discard_motivo` exacto, **cero HTTP de mutación, cero fila en `apply_quota_state`**; suite de cola y harvest verde con las seeds corregidas; log rojo en §Decisiones | 1.1 | cc:完了 [2026-09-02, GLM. Seeds de cola/harvest con state de campaña (67 passed antes del gate); 2 tests ROJOS contra master (['ya_no_califica'] != ['campana_no_enabled'/'grupo_no_enabled'], logs en §Decisiones) y verdes con el fix: discarded con motivo exacto, cero HTTP, cero quota; suites cola+harvest 69 passed. Desviación D-GLM-3 (as conn). Commit fix(apply_cola) c67def7] |
| 1.3 | **Superficie y docs**: `MOTIVOS_ES_SALUD` con los dos motivos (D5) + test puro en `tests/test_api_dashboard.py`; `docs/DASHBOARD.md` (lista de motivos del orquestador, ~línea 251) y `docs/APPLY.md` (una bala en la sección de la cola/re-validación: el gate de ancestros al liberar) al día; línea en `docs/CHAT-CONTEXT.md`; markers 1.1-1.3 a `cc:完了`; PR abierto contra `master` con CI verde. `[tdd:required]` | Test del diccionario ROJO contra master (KeyError) y verde; docs citados en el diff; CI batería completa verde; candado de frescura verde | 1.2 | cc:完了 [2026-09-02, GLM. Test del diccionario ROJO (KeyError) y verde; MOTIVOS_ES_SALUD con los 2 motivos; DASHBOARD.md y APPLY.md al día; línea en CHAT-CONTEXT; PR #123 abierto contra master con los 3 commits; CI batería completa verde; fusionado por el lead tras la cross-review] |
| 1.4 | **Lead — revisión, deploy y verificación en vivo**: review del PR contra `origin/master` + reviewer fresco + cross-review codex (1 ronda); merge; deploy al contenedor por el runbook `docs/DEPLOY.md` (imagen nueva, md5 de `app/` vs master, respaldo del `app/` anterior); verificación DENTRO del contenedor de que `_gates_entidad` nuevo está vivo; esperar el ciclo del cron (no forzar `/run`) y comparar `notes.skips` contra §Estado medido (≈299 US / 10 MX `campana_no_enabled`); `/salud` muestra la etiqueta. `[tdd:skip:ops]` | SELECT del ciclo post-deploy con los contadores nuevos en la evidencia; cero decisiones en campañas no ENABLED (`SELECT` de conciliación por `parent_id`); AppFlowy anotado | 1.3 | cc:完了 [2026-09-02: revisión del lead contra la base viva (todas las campañas/ad groups tienen fila de state → esperado exacto 299 US / 10 MX); CodeRabbit (8 comentarios) + codex (2 hallazgos, veredicto «mergeable con fixes») adjudicados en §Cross-review; conflicto de CHAT-CONTEXT con #124 resuelto por el lead; #123 fusionado. Deploy 2026-09-02 17:48 UTC (go «deploy hoy»; respaldo app.bak-predeploy-20260902, md5 35/35 = master 4ec812d, contenedor Recreated, verificado dentro). **Verificación del ciclo 2026-09-03 (35 US 08:40:01 / 36 MX 08:41:02, live, done)**: `skips.entidad.campana_no_enabled` = **1597 US / 3115 MX** y `skips.termino.campana_no_enabled` = 92 US / 100 MX; `grupo_no_enabled` 0 (medido: no hay hojas ENABLED en grupos pausados de campañas activas). Conciliación de contadores: `estado_no_enabled` bajó de 1339→41 (US) y 3224→119 (MX): el gate de campaña precede al de estado, así que TODA hoja de campaña pausada (esté pausada o activa) cuenta ahora como `campana_no_enabled` — la suma 41+1597 = 1339+299 (US) y 119+3115 = 3224+10 (MX) cuadra exacta con lo medido; la nota «estado_no_enabled NO baja» de §Estado medido era un error de cuenta del lead (corregido aquí). **Cero decisiones en campañas o grupos no ENABLED** (SELECT de conciliación por `parent_id`: 57 US + 36 MX, todas ENABLED/ENABLED; ayer 10+2 en PAUSED). Decisiones bajaron de 76/47 a 57/36; 10+10 aplicadas (`apply_attempt` 50-69 `normal` ok, readback = bid a centavos, 0 divergentes, 0 failed), ninguna repetida de ayer (`cooldown_7d` 9+9), quota 10/10, cola live 0, `/salud` muestra la etiqueta nueva. AppFlowy anotado] |
| 1.5 | **Lead + dueño — las 2 mutaciones ya aplicadas en campañas pausadas** (decisiones 1989 y 2104): el dueño decide conservar o revertir; si revierte, `POST /reversa/bid` por decisión (ledger + readback), evidencia en `plans/orbit-05.md` 1.5. `[tdd:skip:checkpoint-humano]` | Decisión literal del dueño registrada; si hubo reversa: readback = bid original, ledger sellado | - | cc:完了 [2026-09-02: decisión literal del dueño "dejalas asi" — las 2 pujas (1989 US 0.99→0.74 USD, 2104 MX 13.64→10.23 MXN) se conservan; sin reversa; registrado en CHAT-CONTEXT] |
| 1.6 | **GLM — gate de ancestros en la RECONCILIACIÓN + orden de contadores con veto pendiente** (hallazgos codex r1, §Cross-review): (a) `apply.reconcilia_bids` (reintento divergente `_reintento_divergente`), `apply_harvest.reconcilia_harvest` (jobs en vuelo) y `_reconcilia_negativas` reintentan mutaciones de intentos SIN sello (crash a mitad de un apply) sin consultar campaña/ad group → ANTES de cualquier reintento HTTP, comprobación de ancestros con la misma semántica de D1/D3 (cache `ad_entity_state`; una sola función compartida por `ad_entity_id`, reusada por `apply_cola._revalida_ancestros`); si falla: sellar el intento como `fallo:ancestro_no_enabled`, cerrar fila/job como `failed` (una fila `applying` NO puede pasar a `discarded`), sin HTTP ni cuota; (b) `cycle._procesa_decisora` / `_procesa_grupo`: el chequeo `bloqueadas` (veto_pendiente) va DESPUÉS de goal/ancestros/estado, y un grupo gateado cuenta TODOS sus términos con el motivo del ancestro (hoy los bloqueados se descuentan antes). Mismas reglas de proceso que 1.1-1.3. `[tdd:required]` | Regresiones ROJAS contra master: bid, negative y harvest en reconciliación con campaña `PAUSED` y con ancestro SIN fila de state → cero HTTP de mutación, intento sellado, `failed`; ciclo con veto pendiente + campaña pausada → `campana_no_enabled` (no `veto_pendiente`) y contador del grupo = todos sus términos; suites completas verdes en CI; logs rojos en §Decisiones | 1.4 | cc:完了 [2026-09-03, GLM. Funcion UNICA apply.gate_ancestros (D-GLM-5, reusada por apply_cola y los 3 reconciliadores); 6 regresiones ROJAS contra master (logs en §Decisiones) y verdes con el fix; camino feliz (5) = goldens existentes con seed ENABLED, verdes; suites apply/apply_harvest/cycle/apply_cola/cycle_apply/architecture 180 passed local; ancestro de harvest = ORIGEN, fila released se descarta PRE-claim (D-GLM-8); veto_pendiente ahora DESPUES de los gates (D-GLM-9). PR #136] |

## Guía de implementación (GLM)

Las rutas son exactas contra `origin/master` a `3559437`. Si un número de
línea no cuadra, manda el nombre del símbolo, no la línea.

### 1.1 — `app/cycle.py`

**Archivos**: modificar `app/cycle.py` (constantes ~L205-211; `_SQL_DECISORAS`
~L387; `_SQL_GRUPOS` ~L398; `_gates_entidad` ~L1062; `_procesa_decisora`
unpack ~L1098 y llamada ~L1108; término unpack ~L1189 y llamada ~L1203;
docstring del módulo ~L46-52). Test: `tests/test_cycle.py`.

**Paso 1 — el test (rojo primero).** Pegar al final de `tests/test_cycle.py`
(los helpers `_db_temporal`, `_siembra_maestra`, `_entidad`, `_estado`,
`_siembra_kw_bid`, `_siembra_terminos`, `_corre`, `DECIDED_AT` y el alias
`w` de `app.optimizer.windows` ya existen en ese archivo):

```python
@pytest.mark.skipif(
    _postgres_obligatorio_ausente(),
    reason="sin Postgres utilizable en ORBIT_TEST_DSN/localhost:5432",
)
def test_gate_campana_y_grupo_no_enabled():
    """CAMPAÑA ACTIVA 01 (regla 9): una hoja ENABLED dentro de una campaña
    PAUSED, de una campaña SIN state o de un ad group PAUSED NO decide, y los
    terminos del ad group de una campaña pausada tampoco. Antes del fix el
    ciclo decidia (y en live APLICABA) bids en campañas pausadas: ciclos 33/34
    del 2026-09-02, 12 decisiones y 2 aplicadas (1989 y 2104)."""
    with _db_temporal("orbit_ciclo_campana") as (conn, _c):
        ids = _siembra_maestra(conn)  # campaña 9001 ENABLED: sigue decidiendo igual
        run_id = conn.execute("SELECT id FROM ingest_run LIMIT 1").fetchone()[0]
        synced = DECIDED_AT - dt.timedelta(hours=4)

        # P: campaña PAUSED, ad group y keyword ENABLED (el caso real del 2026-09-02)
        camp_p = _entidad(conn, "amazon_us", "campaign", "9701")
        ag_p = _entidad(conn, "amazon_us", "ad_group", "9711", parent=camp_p)
        kw_p = _entidad(
            conn, "amazon_us", "keyword", "9721", parent=ag_p, match_type="EXACT", keyword_text="p"
        )
        _estado(conn, camp_p, synced_at=synced, status="PAUSED")
        _estado(conn, ag_p, synced_at=synced)
        _estado(conn, kw_p, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
        _siembra_kw_bid(conn, run_id, kw_p)
        _siembra_terminos(conn, run_id, ag_p)

        # G: campaña ENABLED, ad group PAUSED, keyword ENABLED
        camp_g = _entidad(conn, "amazon_us", "campaign", "9702")
        ag_g = _entidad(conn, "amazon_us", "ad_group", "9712", parent=camp_g)
        kw_g = _entidad(
            conn, "amazon_us", "keyword", "9722", parent=ag_g, match_type="EXACT", keyword_text="g"
        )
        _estado(conn, camp_g, synced_at=synced)
        _estado(conn, ag_g, synced_at=synced, status="PAUSED")
        _estado(conn, kw_g, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
        _siembra_kw_bid(conn, run_id, kw_g)

        # N: campaña SIN fila de state (regla 3: ausencia = fuera), resto ENABLED
        camp_n = _entidad(conn, "amazon_us", "campaign", "9703")
        ag_n = _entidad(conn, "amazon_us", "ad_group", "9713", parent=camp_n)
        kw_n = _entidad(
            conn, "amazon_us", "keyword", "9723", parent=ag_n, match_type="EXACT", keyword_text="n"
        )
        _estado(conn, ag_n, synced_at=synced)
        _estado(conn, kw_n, synced_at=synced, current_bid=Decimal("1.00"), bid_currency="USD")
        _siembra_kw_bid(conn, run_id, kw_n)

        n_terminos_p = len(w.terminos_cortes(conn, ag_p, DECIDED_AT).terminos)
        assert n_terminos_p > 0  # la siembra de terminos del grupo pausado es real

        res = _corre(conn)
        assert res.status == "done"
        skips = json.loads(res.notes)["skips"]
        assert skips["entidad"]["campana_no_enabled"] == 2  # kw_p y kw_n
        assert skips["entidad"]["grupo_no_enabled"] == 1  # kw_g
        assert skips["termino"]["campana_no_enabled"] == n_terminos_p
        con_decision = {
            r[0]
            for r in conn.execute(
                "SELECT ad_entity_id FROM decision WHERE cycle_id = %s", (res.cycle_id,)
            )
        }
        # ninguna hoja gateada decide; el grupo de la campaña pausada tampoco
        assert con_decision.isdisjoint({kw_p, kw_g, kw_n, ag_p})
        # la campaña ENABLED de la fixture maestra sigue decidiendo igual que antes
        assert ids["kw_bid"] in con_decision
```

**Paso 2 — correrlo contra master y pegar el rojo** en §Decisiones:
`PYTHONPATH=. pytest tests/test_cycle.py -q -k campana_y_grupo`. Esperado:
`KeyError: 'campana_no_enabled'` (y, si se comenta esa línea, el `isdisjoint`
rompe porque `kw_p` decide un bid). Si pasa en verde, el test no discrimina:
PARAR y avisar.

**Paso 3 — constantes** (junto a `MOTIVO_ESTADO_NO_ENABLED`, ~L208):

```python
MOTIVO_ESTADO_NO_ENABLED = "estado_no_enabled"
# CAMPAÑA ACTIVA 01: ancestros no ENABLED (o sin fila de state) sacan a la
# hoja/grupo del motor ANTES de mirar su propio estado. Fuente: el cache
# ad_entity_state (sync diario; guarda de 48h del ciclo).
MOTIVO_CAMPANA_NO_ENABLED = "campana_no_enabled"
MOTIVO_GRUPO_NO_ENABLED = "grupo_no_enabled"
```

**Paso 4 — SQL.** `_SQL_DECISORAS` queda:

```python
_SQL_DECISORAS = """
SELECT k.id, k.parent_id AS ad_group_id, ag.parent_id AS campaign_id,
       s.current_bid, s.bid_currency, s.status, s.acos_target,
       sg.status AS status_grupo, sc.status AS status_campana
  FROM ad_entity k
  JOIN ad_entity ag ON ag.id = k.parent_id AND ag.kind = 'ad_group'
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = k.id
  LEFT JOIN ad_entity_state sg ON sg.ad_entity_id = ag.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = ag.parent_id
 WHERE k.platform = %s::platform AND k.kind IN ('keyword', 'product_target')
 ORDER BY k.id
"""
```

y `_SQL_GRUPOS`:

```python
_SQL_GRUPOS = """
SELECT ag.id, ag.parent_id AS campaign_id, s.status, sc.status AS status_campana
  FROM ad_entity ag
  LEFT JOIN ad_entity_state s ON s.ad_entity_id = ag.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = ag.parent_id
 WHERE ag.platform = %s::platform AND ag.kind = 'ad_group'
 ORDER BY ag.id
"""
```

(Ambas siguen en la tupla literal de nombres que parsea pglast en
`tests/test_cycle.py` ~L1270: no tocarla.)

**Paso 5 — `_gates_entidad`** completo (reemplaza la función):

```python
def _gates_entidad(
    conn: psycopg.Connection,
    goals: tuple[g.Goal | None, dict[int, g.Goal]],
    *,
    campaign_id,
    entidad_id: int,
    status,
    ancestros: tuple[tuple[str, str | None], ...],
    decided_at: dt.datetime,
) -> tuple[g.Goal | None, str | None]:
    """Cascada de gates del orquestador (orden sellado, ver docstring del
    modulo): goal de campaña -> ancestros ENABLED (CAMPAÑA ACTIVA 01: campaña
    y, para hojas, ad group; `ancestros` = ((motivo, status), ...) de afuera
    hacia adentro) -> estado propio -> cooldown. None = elegible. Un ancestro
    sin fila de state (status None) tambien queda fuera (regla 3)."""
    goal_plataforma, por_campana = goals
    goal, motivo = _porta_goal_campana(por_campana, goal_plataforma, campaign_id)
    for motivo_ancestro, status_ancestro in ancestros:
        if motivo is None and status_ancestro != "ENABLED":
            motivo = motivo_ancestro
    if motivo is None and status != "ENABLED":
        motivo = MOTIVO_ESTADO_NO_ENABLED  # None (sin state) tambien queda fuera
    if motivo is None and g.en_cooldown(conn, entidad_id, ahora=decided_at):
        motivo = MOTIVO_COOLDOWN_7D
    return (goal, motivo)
```

**Paso 6 — call site de hojas** (`_procesa_decisora`, ~L1098 y ~L1108):

```python
    (
        entidad_id,
        ad_group_id,
        campaign_id,
        current_bid,
        bid_currency,
        status,
        acos_cache,
        status_grupo,
        status_campana,
    ) = fila
    ...
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=entidad_id,
        status=status,
        ancestros=(
            (MOTIVO_CAMPANA_NO_ENABLED, status_campana),
            (MOTIVO_GRUPO_NO_ENABLED, status_grupo),
        ),
        decided_at=decided_at,
    )
```

**Paso 7 — call site de términos** (~L1189 y ~L1203):

```python
    grupo_id, campaign_id, status, status_campana = fila
    ...
    goal, motivo = _gates_entidad(
        conn,
        goals,
        campaign_id=campaign_id,
        entidad_id=grupo_id,
        status=status,
        ancestros=((MOTIVO_CAMPANA_NO_ENABLED, status_campana),),
        decided_at=decided_at,
    )
```

El resto de `_procesa_decisora` y del camino de términos NO cambia (el
`skips_termino[motivo] += len(terminos.terminos)` existente ya cuenta el
motivo nuevo). `contadores.skips_*` son `Counter`: no hace falta registrar
las claves.

**Paso 8 — docstring del módulo** (bala ELEGIBILIDAD, ~L46-52): después de
`goal.mode 'off' -> 'goal_mode_off';` insertar:
`campaña (o, para hojas, ad group) sin state o status != ENABLED ->
'campana_no_enabled' / 'grupo_no_enabled' (CAMPAÑA ACTIVA 01: el cache de
estructura, no un LIST; un ancestro pausado hace moot todo lo de abajo);`
y en "Orden de gates" dejar: campaña (goal) → ancestros → estado → cooldown.

**Paso 9 — verde y commit.** `PYTHONPATH=. pytest tests/test_cycle.py -q`
(TODO el archivo, no solo `-k`: los goldens y el pglast deben seguir
verdes). `ruff check --fix . && ruff format .`. Commit:
`fix(cycle): gate de campaña y ad group ENABLED antes de decidir (CAMPAÑA ACTIVA 01 · 1.1)`.

### 1.2 — `app/apply_cola.py`

**Archivos**: modificar `app/apply_cola.py` (constantes ~L112-119; SQL junto
a `_SQL_PADRE` ~L217; función nueva antes de `_revalida` ~L650; `_revalida`;
docstring de `libera_vencidos` paso 2 ~L790). Tests:
`tests/test_apply_cola.py` (seed `_semilla` ~L169-221 + 2 tests nuevos),
`tests/test_apply_harvest.py` (seed `_semilla` ~L152-181).

**Paso 1 — seeds** (verde, sin cambio de comportamiento; se hace ANTES para
que el rojo de abajo sea SOLO del gate):

- `tests/test_apply_cola.py` `_semilla`: `for entidad in (camp, ag, kw, kw2):`
  (hoy `(ag, kw, kw2)`). Docstring: añadir "campaña con state (CAMPAÑA
  ACTIVA 01: la cola exige campaña y grupo ENABLED al liberar)".
- `tests/test_apply_harvest.py` `_semilla`: `for entidad in (camp, ag, kw):`
  (hoy `(ag, kw)`), misma nota en el docstring.

Correr `PYTHONPATH=. pytest tests/test_apply_cola.py tests/test_apply_harvest.py -q`:
verde (nada cambió aún).

**Paso 2 — los tests (rojo primero).** Al final de `tests/test_apply_cola.py`
(helpers `_db_temporal`, `_semilla`, `_decision_corte`, `_encola_fila`,
`_payload_pause`, `_payload_negative`, `_handler_cortes`, `_aplicador`,
`_mutaciones`, `libera_vencidos` ya existen ahí; importar las dos constantes
nuevas junto a `MOTIVO_VENDIO_EN_VENTANA` en el `from app.apply_cola import`
del archivo):

```python
def test_libera_descarta_pause_en_campana_pausada():
    """CAMPAÑA ACTIVA 01 (regla 9): un pause vencido cuya CAMPAÑA se pauso
    durante la ventana de veto es moot -> discard PRE-claim con motivo
    'campana_no_enabled': cero HTTP de mutacion, cero quota. Antes del fix
    la re-validacion solo miraba el estado vivo de la HOJA y la regla: aqui
    (sin evidencia fresca) descartaba con el motivo EQUIVOCADO
    ('ya_no_califica') y, con evidencia que califica, el pause SALIA."""
    with _db_temporal("orbit_cola_campana") as (conn, _c):
        ids = _semilla(conn)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s",
            (ids["camp"],),
        )
        dec = _decision_corte(conn, ids["ciclo_dec"], ids["config"], ids["kw"], "pause")
        q = _encola_fila(conn, dec, ids["kw"], "pause", payload=_payload_pause("7201"))
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=ids["ahora"],
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.descartadas == [MOTIVO_CAMPANA_NO_ENABLED]
        assert res.aplicadas == 0 and res.fallidas == 0
        assert _mutaciones(vistos) == []
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", MOTIVO_CAMPANA_NO_ENABLED)
        assert conn.execute("SELECT count(*) FROM apply_quota_state").fetchone()[0] == 0


def test_libera_descarta_negative_en_grupo_pausado():
    """CAMPAÑA ACTIVA 01: para negative/harvest la fila ES el ad group; su
    propio status PAUSED es el gate de grupo -> 'grupo_no_enabled' ANTES de
    re-evaluar la regla (antes del fix salia 'ya_no_califica' por falta de
    observaciones frescas: motivo equivocado para un grupo apagado)."""
    with _db_temporal("orbit_cola_grupo") as (conn, _c):
        ids = _semilla(conn)
        conn.execute(
            "UPDATE ad_entity_state SET status = 'PAUSED' WHERE ad_entity_id = %s",
            (ids["ag"],),
        )
        dec = _decision_corte(
            conn, ids["ciclo_dec"], ids["config"], ids["ag"], "negative", term="zapato blanco"
        )
        q = _encola_fila(
            conn,
            dec,
            ids["ag"],
            "negative",
            term="zapato blanco",
            payload=_payload_negative("7101", "7001", "zapato blanco"),
        )
        handler, vistos = _handler_cortes()

        res = libera_vencidos(
            conn,
            "amazon_us",
            ahora=ids["ahora"],
            aplicador=_aplicador(conn, handler, ids["ciclo_ejec"]),
        )

        assert res.descartadas == [MOTIVO_GRUPO_NO_ENABLED]
        assert _mutaciones(vistos) == []
        fila = conn.execute(
            "SELECT estado, discard_motivo FROM apply_queue WHERE id = %s", (q,)
        ).fetchone()
        assert fila == ("discarded", MOTIVO_GRUPO_NO_ENABLED)
        assert conn.execute("SELECT count(*) FROM apply_quota_state").fetchone()[0] == 0
```

**Paso 3 — rojo contra master**: `PYTHONPATH=. pytest tests/test_apply_cola.py -q -k "campana_pausada or grupo_pausado"`.
Esperado: `ImportError` de las constantes; con las constantes stub agregadas
(paso 4 sin el resto), AMBOS tests fallan en `res.descartadas ==` porque el
código viejo descarta con `['ya_no_califica']` (motivo equivocado: no hay
evidencia fresca sembrada; con evidencia que califica, el pause/negative
SALDRÍA a Amazon). Pegar ambos en §Decisiones.

**Paso 4 — constantes** (junto a `MOTIVO_REACTIVACION_MANUAL`, ~L119):

```python
# CAMPAÑA ACTIVA 01: espejo de cycle.MOTIVO_CAMPANA/GRUPO_NO_ENABLED (cycle
# importa de este modulo, no al reves): un corte cuya campaña o ad group dejo
# de estar ENABLED durante la ventana de veto es moot -> discard PRE-claim.
MOTIVO_CAMPANA_NO_ENABLED = "campana_no_enabled"
MOTIVO_GRUPO_NO_ENABLED = "grupo_no_enabled"
```

**Paso 5 — SQL** (junto a `_SQL_PADRE`):

```python
# CAMPAÑA ACTIVA 01: status (cache) del ad group y de la campaña de la fila.
# Hoja (pause): el grupo es su padre; ad group (negative/harvest): el grupo
# es la propia fila. LEFT JOIN: sin fila de state = NULL = fuera (regla 3).
_SQL_ANCESTROS = """
SELECT sg.status, sc.status
  FROM ad_entity e
  JOIN ad_entity g ON g.id = CASE WHEN e.kind = 'ad_group' THEN e.id ELSE e.parent_id END
  LEFT JOIN ad_entity_state sg ON sg.ad_entity_id = g.id
  LEFT JOIN ad_entity_state sc ON sc.ad_entity_id = g.parent_id
 WHERE e.id = %s
"""
```

**Paso 6 — función y enganche** (antes de `_revalida`):

```python
def _revalida_ancestros(conn: psycopg.Connection, fila: FilaCola) -> str | None:
    """CAMPAÑA ACTIVA 01: gate de campaña y ad group ENABLED al LIBERAR, con
    el cache de estructura (sync diario; guarda de 48h del ciclo) — no un
    LIST fresco de campañas (residual declarado en el plan). Corre ANTES del
    dispatch por kind: sin LIST de la hoja, sin cobro, sin claim. Entidad sin
    fila o ancestro sin state -> fuera (regla 3)."""
    fila_anc = conn.execute(_SQL_ANCESTROS, (fila.ad_entity_id,)).fetchone()
    status_grupo, status_campana = fila_anc if fila_anc is not None else (None, None)
    if status_campana != "ENABLED":
        return MOTIVO_CAMPANA_NO_ENABLED
    if status_grupo != "ENABLED":
        return MOTIVO_GRUPO_NO_ENABLED
    return None
```

y en `_revalida`, como primera línea del cuerpo:

```python
    motivo = _revalida_ancestros(conn, fila)
    if motivo is not None:
        return motivo
    if fila.kind == "pause":
        ...
```

Docstring de `_revalida`: añadir "El gate de ancestros (campaña/ad group
ENABLED en el cache) va PRIMERO para todos los kinds." Docstring de
`libera_vencidos`, paso 2: "re-validacion PRE-claim (gate de ancestros
CAMPAÑA ACTIVA 01 + evidencia FRESCA ...)".

**Paso 7 — verde y commit.** `PYTHONPATH=. pytest tests/test_apply_cola.py tests/test_apply_harvest.py -q`
(archivos completos). `ruff check --fix . && ruff format .`. Commit:
`fix(apply_cola): gate de campaña y ad group ENABLED al liberar la cola (CAMPAÑA ACTIVA 01 · 1.2)`.

### 1.3 — superficie, docs y PR

**Paso 1 — test (rojo primero)** al final de `tests/test_api_dashboard.py`:

```python
def test_motivos_salud_traducen_los_gates_de_ancestros():
    """CAMPAÑA ACTIVA 01: los dos motivos nuevos del orquestador tienen
    traduccion en /salud (sin ella la pantalla mostraria el id crudo)."""
    from app import cycle as ciclo
    from app.api_dashboard import MOTIVOS_ES_SALUD

    assert MOTIVOS_ES_SALUD[ciclo.MOTIVO_CAMPANA_NO_ENABLED].startswith("Campana")
    assert MOTIVOS_ES_SALUD[ciclo.MOTIVO_GRUPO_NO_ENABLED].startswith("Ad group")
```

`PYTHONPATH=. pytest tests/test_api_dashboard.py -q -k ancestros` → `KeyError`.

**Paso 2 — diccionario** (`app/api_dashboard.py`, `MOTIVOS_ES_SALUD`, tras
`MOTIVO_ESTADO_NO_ENABLED`):

```python
    ciclo.MOTIVO_CAMPANA_NO_ENABLED: "Campana no habilitada (pausada/archivada o sin estado)",
    ciclo.MOTIVO_GRUPO_NO_ENABLED: "Ad group no habilitado (pausado/archivado o sin estado)",
```

**Paso 3 — docs**: `docs/DASHBOARD.md` (~L251) agrega `campana_no_enabled,
grupo_no_enabled` a la lista de motivos del orquestador; `docs/APPLY.md`
(sección de la cola / re-validación al liberar) una bala: "Gate de ancestros
(CAMPAÑA ACTIVA 01): campaña o ad group no ENABLED en el cache → discard
PRE-claim `campana_no_enabled`/`grupo_no_enabled`, antes del LIST fresco y
del cobro". `docs/CHAT-CONTEXT.md`: una línea fechada en lenguaje de negocio
("el motor ya no toca pujas ni cortes de campañas o ad groups pausados; el
2026-09-02 aplicó 2 de 20 bids en campañas pausadas — causa y fix").

**Paso 4 — markers** 1.1, 1.2 y 1.3 de este plan a `cc:完了 [resumen]`;
§Decisiones con los logs rojos. Commit:
`docs(campana-activa-01): etiquetas de /salud, docs y cierre 1.1-1.3`.

**Paso 5 — PR** contra `master` (título `fix(motor): el optimizador solo
toca campañas y ad groups ENABLED (CAMPAÑA ACTIVA 01)`), cuerpo con: causa,
D1-D7 en 5 líneas, los tres logs rojos, y el residual D2. Verificar
`git log origin/master..HEAD` = solo tus 3 commits. CI = la batería
completa; NO correrla local.

## Decisiones y evidencia (GLM escribe aquí ANTES del código)

**D-GLM-1 (entorno de test local, 2026-09-02):** la máquina de GLM no tiene
Docker ni Postgres; se instaló PostgreSQL 16 por Homebrew (rol `orbit/orbit`
superuser en localhost:5432, igual al contenedor del CI) para que
los tests de ciclo/cola NO skipeen y el log rojo exista. No es desviación del
diseño: el plan pide "un Postgres igual al de CI"; solo cambia el medio.

**D-GLM-2 (código viejo, verificación inicial):** contra `origin/master`
`a73afac`, `_gates_entidad` solo evalúa goal → estado propio → cooldown
(`app/cycle.py`), y `_SQL_DECISORAS`/`_SQL_GRUPOS` no traen status de
ancestros: el hueco descrito en la Causa existe tal cual. Sin desviaciones del
plan hasta ahora.

### Logs rojos (regla 9, corridos contra el código viejo antes de cada fix)

**1.1** — `PYTHONPATH=. pytest tests/test_cycle.py -q -k campana_y_grupo`:

```text
>           assert skips["entidad"]["campana_no_enabled"] == 2  # kw_p y kw_n
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           KeyError: 'campana_no_enabled'

tests/test_cycle.py:2131: KeyError
FAILED tests/test_cycle.py::test_gate_campana_y_grupo_no_enabled - KeyError: ...
1 failed, 36 deselected in 1.77s
```

**1.2** — `PYTHONPATH=. pytest tests/test_apply_cola.py -q -k "campana_pausada or
grupo_pausado"`. Primero con el import de las constantes nuevas (aun sin
definir en `app/apply_cola.py`), el rojo de colección:

```text
E   ImportError: cannot import name 'MOTIVO_CAMPANA_NO_ENABLED' from 'app.apply_cola' (/Users/dn/dev/goncloud-Orbit/app/apply_cola.py)
1 error in 0.17s
```

Luego con SOLO las constantes stub agregadas (paso 4 sin el resto), el rojo
que demuestra que el gate muerde — el código viejo descarta con el motivo
EQUIVOCADO en AMBOS tests:

```text
E           AssertionError: assert ['ya_no_califica'] == ['campana_no_enabled']
E           At index 0 diff: 'ya_no_califica' != 'campana_no_enabled'
E           AssertionError: assert ['ya_no_califica'] == ['grupo_no_enabled']
E           At index 0 diff: 'ya_no_califica' != 'grupo_no_enabled'
FAILED tests/test_apply_cola.py::test_libera_descarta_pause_en_campana_pausada
FAILED tests/test_apply_cola.py::test_libera_descarta_negative_en_grupo_pausado
2 failed, 25 deselected in 0.32s
```

**1.3** — `PYTHONPATH=. pytest tests/test_api_dashboard.py -q -k ancestros`:

```text
E       KeyError: 'campana_no_enabled'
FAILED tests/test_api_dashboard.py::test_motivos_salud_traducen_los_gates_de_ancestros
1 failed, 34 deselected in 1.45s
```

### Logs rojos 1.6 (regla 9, contra el codigo viejo, 2026-09-03)

Bids campana PAUSED y campana SIN state
(`pytest tests/test_apply.py -q -k gate_ancestros`, 2 failed):

```text
E       AssertionError: cero HTTP con ancestro no ENABLED: POST /sp/keywords/list
FAILED tests/test_apply.py::test_reconcilia_bids_campana_pausada_gate_ancestros
FAILED tests/test_apply.py::test_reconcilia_bids_campana_sin_state_gate_ancestros
```

Negative con ad group PAUSED (`pytest tests/test_apply_harvest.py -k gate_neg`):

```text
E           assert resumen.negativas_fallidas == 1 and resumen.negativas_confirmadas == 0
E           assert (0 == 1)
E            +  where 0 = ResumenReconciliacion(... negativas_confirmadas=1, negativas_fallidas=0 ...).negativas_fallidas
FAILED tests/test_apply_harvest.py::test_reconcilia_negative_grupo_pausado_gate_ancestros
```

Jobs en vuelo con campana de origen PAUSED (fila applying y fila released):

```text
E           assert 0 == 1
E            +  where 0 = ResumenReconciliacion(jobs_done=1, jobs_failed=0, ...).jobs_failed
FAILED tests/test_apply_harvest.py::test_reconcilia_harvest_campana_origen_pausada_cierra_job
FAILED tests/test_apply_harvest.py::test_reconcilia_harvest_campana_pausada_fila_released_descarta_pre_claim
```

Ciclo, veto pendiente + campana PAUSED (`pytest tests/test_cycle.py -k cuenta_el_ancestro`):

```text
E           assert skips["entidad"]["campana_no_enabled"] == 2  # kw_bid (bloqueada) y kw_pause
E           assert 1 == 2
FAILED tests/test_cycle.py::test_veto_pendiente_cuenta_el_ancestro_no_enabled
```

Camino feliz (5): los goldens existentes (`test_reconcilia_bids_get_igual_confirma`,
`test_reconcilia_bids_divergencia_reintenta_bajo_tope_y_falla`,
`test_matriz_applying_huerfano_negative_ausente_reintenta_bajo_tope`,
`test_reconcilia_cobra_quota_la_primera_vez...`) corren con campaña y grupo
ENABLED de la seed y siguen verde con el gate (verificados en verde, no rojos:
no cambian).

### Decisiones 1.6 (escritas ANTES del codigo, 2026-09-03)

**D-GLM-5 (hogar de la funcion compartida):** vive en `app/apply.py` como
`gate_ancestros(conn, ad_entity_id) -> str | None` (mismo SQL/semantica que
`apply_cola._SQL_ANCESTROS`: resuelve hoja vs ad group con CASE; sin fila de
state = fuera, regla 3). `apply.py` es el dueno del write client y tanto
`apply_cola` como `apply_harvest` ya lo importan, asi que el candado de
imports de `tests/test_architecture.py` (solo prohíbe IO en el motor puro)
no se toca. Las constantes `MOTIVO_CAMPANA/GRUPO_NO_ENABLED` pasan a
definirse en `app/apply.py`; `apply_cola` las re-exporta como alias (sus
tests y `cycle` las importan de ahi; cero cambio de vocabulario).

**D-GLM-6 (reconcilia_bids):** el gate va al TOPE del loop, antes incluso
del readback GET: sella `fallo:ancestro_no_enabled`, fallidas += 1, cero
HTTP de cualquier tipo. Con eso el reintento divergente queda cubierto por
construccion (la misma entidad ya fue gateada antes de que nazca la fila
nueva del ledger).

**D-GLM-7 (_reconcilia_negativas):** gate sobre `fila.ad_entity_id` (la
fila ES el ad group, semantica D3) antes de crear el cliente y de cualquier
LIST/POST: sella pendientes `fallo:ancestro_no_enabled`, fila
`applying` -> `failed`. `_reconcilia_pauses` NO se toca: no reintenta
mutaciones (solo LIST de estado y veredicto), el GET siempre esta permitido.

**D-GLM-8 (harvest, ancestro que aplica):** el ORIGEN. `job.ad_entity_id`
es el ad group de la fila (misma semantica D3 que la cola); el DESTINO del
goal sigue SIN gate de codigo (D4, residual declarado). En
`reconcilia_harvest` el gate corre tras el chequeo vetoed/discarded y ANTES
del cobro de quota/claim/HTTP: job -> `failed`, ledger pendiente sellado
`fallo:ancestro_no_enabled`; fila `applying` -> `failed`; fila `released` ->
`discarded` con `discard_motivo` (la maquina de estados de 0002 NO tiene
`released -> failed` y released sigue vetable: el discard PRE-claim es la
transicion legal y no quema quota). Cuenta en `jobs_failed` SIN alerta de
Telegram: campana pausada por el dueno es condicion esperada, no fallo
operativo (las demas alertas de job siguen igual).

**D-GLM-9 (orden de contadores):** en `_procesa_decisora` el chequeo
`bloqueadas` pasa a justo DESPUES de `_gates_entidad` (goal -> ancestros ->
estado -> cooldown) y antes de `inertes` (conserva el orden relativo
veto/inerte de hoy); en `_procesa_grupo` los gates corren primero y, con
motivo de ancestro, cuentan TODOS los terminos (incluidos los bloqueados);
solo despues del gate se filtran los bloqueados como `veto_pendiente`.
Solo cambian contadores de `notes.skips` (bala "Orden de gates" del
docstring del modulo).

**Seed (igual que 1.2):** `_semilla` de `tests/test_apply.py` gana state
ENABLED de campana y ad group (hoy no lo tienen; sin eso el gate nuevo
romperia los goldens de reconciliacion de bids). Se commita ANTES del gate,
en verde.

### Desviaciones del plan

**D-GLM-3 (tests 1.2):** el snippet del plan escribe
`with _db_temporal("orbit_cola_campana") as (conn, _c):`, pero el
`_db_temporal` de `tests/test_apply_cola.py` yields SOLO `conn` (a diferencia
del de `tests/test_cycle.py`, que yields la tupla). Se ajustó a
`as conn` en ambos tests; el cuerpo no cambia. Mismo ajuste NO aplicó a 1.1
(ahí sí es tupla).

**D-GLM-4 (markers):** 1.1/1.2 se marcan `cc:完了` en el commit de 1.3 junto
con este texto (regla 7 del plan: la línea de CHAT-CONTEXT y los markers van
en el cierre, un solo commit de docs como indica el paso 4 de §1.3).

## Cross-review de #123 (1 ronda: CodeRabbit + codex, 2026-09-02) — adjudicación del lead

| # | Fuente | Hallazgo | Severidad | Decisión |
|---|---|---|---|---|
| 1 | codex | La reconciliación post-crash reintenta PUT/POST sin gate de ancestros (`app/apply.py` ~595/636 `_reintento_divergente`, `app/apply_harvest.py` ~1178/1307) | alta (camino: crash a mitad de un apply + campaña pausada después; hoy 0 intentos sin sello) | ACEPTADO → tarea 1.6(a). No bloquea el deploy: el camino normal (decidir + liberar) ya está cerrado |
| 2 | codex | `bloqueadas` (veto_pendiente) se evalúa ANTES de los gates: una hoja/término en campaña pausada con veto pendiente cuenta como `veto_pendiente` | media (solo contadores; no muta) | ACEPTADO → tarea 1.6(b) |
| 3 | CodeRabbit | `_revalida_ancestros` devolvería `campana_no_enabled` si el `ad_entity` no existiera | menor | DESCARTADO: `apply_queue.ad_entity_id` tiene FK a `ad_entity` (0002) y las entidades no se borran (ARCHIVED) |
| 4 | CodeRabbit | Carrera entre `sync_structure` y `_SQL_CLAIM` (pide el predicado de ancestros en el claim) | "mayor" según el bot | DESCARTADO como residual declarado (D2): crons 06:45 y 08:40 UTC, ventana negligible; misma clase que el residual (a) de `libera_vencidos` |
| 5 | CodeRabbit | APPLY.md §3 documentaba claim ANTES de quota (pre-existente, el código cobra antes del claim) | menor | CORREGIDO en este PR |
| 6 | CodeRabbit | `Ñ` en comentarios/docstrings nuevos (convención: sin acentos en el código) | menor | CORREGIDO: token `CAMPANA ACTIVA 01` en los .py del PR (la Ñ venía del plan del lead) |
| 7 | CodeRabbit | Lint markdown del plan (`\|` en tabla, fences sin lenguaje) y marker 1.3 «pendiente CI» | menor | CORREGIDO |
| 8 | CodeRabbit | Pide test de términos de un ad group PAUSED con `grupo_no_enabled` | menor | DESCARTADO: por D1 el propio ad group gatea por `estado_no_enabled` (vocabulario existente); `grupo_no_enabled` es solo para hojas |

Veredicto codex: **mergeable con fixes**. Los fixes de código van en 1.6 con sus
regresiones. Tope de rondas alcanzado: no hay segunda ronda; lo que reste se
declara en §Residuales.

## Reject (con razón)

- **LIST fresco de campañas a Amazon en el ciclo o en la cola**: el cache se
  sincroniza a diario y el ciclo ya tiene guarda de 48h; una llamada extra
  por entidad viola "una decisión, un camino" y alarga la ventana de API.
- **Filtrar en SQL (`WHERE sc.status = 'ENABLED'`) en vez de un gate con
  contador**: haría invisibles las hojas en `notes.skips`; el spot-check
  humano y `/salud` viven de esos contadores (decisión 11 del dashboard).
- **Tratar ancestro sin state como ENABLED**: regla 3 (ausencia = fuera),
  misma semántica que ya tiene la hoja.
- **Revertir las 2 mutaciones dentro de este PR**: es decisión del dueño y
  mutación real; va por el camino sellado de reversa (1.5, lead).
- **Migración o cambio de esquema**: nada lo exige.

## Residuales declarados

1. Campaña pausada a mano DESPUÉS del sync de las 06:45 UTC y ANTES del ciclo
   de las 08:40: ese día cuenta como ENABLED (cache de ≤2h). Aceptado: el
   cron de estructura precede al ciclo por diseño (DEPLOY.md §Crons).
2. Destino de harvest: sigue exigido por DoD del seed (APPLY §11a), sin gate
   de código.
3. Las 2 mutaciones ya aplicadas en campañas pausadas quedan como las dejó el
   ciclo 33/34 por decisión del dueño (1.5, 2026-09-02, "dejalas asi"): si un
   día reactiva esas campañas, esas dos pujas arrancan 25% más bajas.
4. CERRADO por 1.6(a) (2026-09-03): la reconciliación post-crash ahora gatea
   campaña/ad group (apply.gate_ancestros) antes de cualquier HTTP de
   reintento.
5. CERRADO por 1.6(b) (2026-09-03): el veto pendiente se evalúa después de
   los gates; una entidad bloqueada en campaña/grupo apagado se cuenta con el
   motivo del ancestro.
6. Las pausas applying huérfanas (`_reconcilia_pauses`) no se gatean: solo
   LEEN estado por LIST (GET siempre permitido), jamás reintentan mutación.
7. El gate de los jobs en vuelo de harvest SIN fila de cola (`queue_id`
   None) cierra solo el job; sin fila no hay transición de cola que hacer.
8. Declarado (review del PR #136): un intento sin sello cuyo PUT/POST SÍ
   llegó a Amazon antes del crash se sella `fallo:ancestro_no_enabled` sin
   verificar el side effect (cero HTTP por diseño). Mientras la campaña está
   pausada el efecto es inerte; al re-activar, el cache de state y la
   re-decisión del ciclo absorben los bids y la identidad del término
   detecta el negativo (misma regla que las ambiguas de §6.1: cerrar y
   re-decidir).

## 事前確認

- 事項: external-send — `git push` + PR (GLM) y merge (lead)
  理由: patrón del repo; batería completa en CI
  scope: Phase 1 / 1.3-1.4
- 事項: destructive — deploy al contenedor de producción (lead, runbook DEPLOY.md, con respaldo del `app/` anterior)
  理由: sin deploy el cron sigue gastando cupos en campañas pausadas
  scope: Phase 1 / 1.4
- 事項: external-send — reversa de bids (`POST /reversa/bid`) SOLO si el dueño lo decide en 1.5
  理由: mutación real a Amazon; camino sellado de ORBIT 04
  scope: Phase 1 / 1.5
