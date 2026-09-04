# BIDS 01 — cero ventas baja −25% (relativo al producto) + entidades sin tráfico fuera del motor, reportadas y archivables

> **Propósito**: cerrar los dos huecos de diseño que la verificación
> adversarial triple de ORBIT 05 (tarea 2.1, 2026-09-02) vio EN VIVO: (1) una
> palabra que gasta y no vende nada solo puede bajar −12% por día; (2) el
> motor gasta cupos ajustando palabras que Amazon no sirve desde hace meses.
> **Contrato**: `docs/superpowers/specs/2026-08-26-banda-cero-ventas-design.md`
> (sección «Decisión del dueño (2026-09-03) — SELLADO» manda; precedencia
> spec > este plan). Decisiones literales del dueño: *"si que puedan bajar
> 25%"* (regla relativa a los clics esperados por venta, con gasto ≥ piso de
> pausa) y *"las palabras sin tráfico no se tendrían que ver … y ver si subir
> bid o eliminar o algo"* → guarda + reporte por causa + herramienta de
> archivo por lote con reversa. Opción B: no por ahora.
>
> Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > la spec > este plan. **No
> cambia caps, escalera, cola de cortes ni umbrales de pausa/negative.** Un
> PR por tarea. Cross-review: 1 ronda (codex) por PR de código; 2ª SOLO si la
> 1ª halla severidad alta; jamás 3ª.
>
> **Reparto** (`CLAUDE.md` global): **GLM** 1.1, 1.2, 1.4 (motor, vista y
> herramienta que muta Amazon); **DeepSeek** 1.3 (superficie de lectura
> server-rendered + línea del digest; sin ssh/push/tracker, el lead cierra);
> **lead** 1.5 (migraciones y deploy en producción, contrafactual, AppFlowy).
>
> **Reloj**: 1.1 y 1.2 son independientes y pueden ir en paralelo; 1.3 y 1.4
> dependen de la vista (1.2). Ningún cambio llega a producción sin 1.5.

## Reglas de proceso para GLM y DeepSeek (NO negociables)

1. **Rama desde `origin/master`** (`git fetch origin && git switch -c
   bids-01-<tarea> origin/master`); antes del PR, `git log origin/master..HEAD`
   lista SOLO los commits de la tarea.
2. **Prohibido tocar producción**: cero `ssh goncloud`, cero `docker exec`,
   cero SELECT a la base viva, cero AppFlowy. Las mediciones de regla 8 ya
   están en la spec y aquí; la corrida real, las migraciones y el deploy son
   del lead (1.5).
3. **TDD con log rojo** (regla 9): cada test nuevo corre PRIMERO contra el
   código viejo y el fallo exacto se pega en §«Decisiones y evidencia» de
   este plan. Un test que pasa igual sin el fix NO cuenta.
4. **Local: solo el archivo de tests que tocas**; **la batería completa corre
   UNA vez en CI** al abrir el PR (`.github/workflows/quality.yml` levanta
   Postgres 16 con `ORBIT_TEST_DSN`). Sin Postgres local: `docker run -d
   --name orbit-test-pg -e POSTGRES_USER=orbit -e POSTGRES_PASSWORD=orbit -e
   POSTGRES_DB=postgres -p 5432:5432 postgres:16` y `export
   ORBIT_TEST_DSN=postgresql://orbit:orbit@localhost:5432/postgres`.
5. `ruff check --fix . && ruff format . && pre-commit run --all-files` antes
   de cada commit; **jamás `--no-verify`**. Sin acentos ni ñ en el código
   (comentarios, docstrings, tests): `CAMPANA`, `campana`, `dueno`.
6. **Decisiones escritas ANTES del código** en §«Decisiones y evidencia». Si
   algo del plan no cuadra con el código real, se escribe ahí y se PARA a
   preguntar al lead; no se interpreta.
7. DoD de TODAS las tareas: marker de su fila a `cc:完了 [resumen]` + **una
   línea en `docs/CHAT-CONTEXT.md`** en lenguaje de negocio (el candado de
   frescura del CI la exige cuando el PR toca un marker).
8. Módulos nuevos ≤ 900 líneas (`tests/test_architecture.py`); `cycle.py` y
   `apply_cola.py` ya están en la allowlist de tamaño: cambios mínimos ahí.

## Estado medido que origina este plan (regla 8, lead, 2026-09-03, `orbit_read`)

| Medición | Resultado |
|---|---|
| Decisiones de bid de los ciclos live 33 (US) / 34 (MX), 2026-09-02 | 76 / 47; **103 de cero ventas** (39 MX, 64 US); clicks mediana 7 MX / 2 US |
| De esas 103, con `window_end` de hace > 30 días (inertes) | 57 (7 MX, 50 US) |
| Regla A' (clicks ≥ `expected_clicks` del grupo y cost ≥ piso de pausa) | **1** habría bajado −25% (US 1994 `arras for wedding ceremony`: 111 clicks vs 92 esperados, 87.30 USD, 0 ventas) |
| `expected_clicks` mediana (grupos con evidencia) | 47 MX / 90 US; grupos con evidencia: 28/39 MX, 13/64 US de las cero-ventas |
| Hojas ENABLED (campaña y grupo ENABLED) sin impresiones en 14 d / 30 d | MX 183 / 172 de 272; US 170 / 167 de 247 |
| De las sin impresiones 30 d: con ventas 90 d / gasto sin ventas 90 d / nada en 90 d | MX 0 / 7 / 165; US 0 / 33 / 134 |
| Último `metric_date` en `v_metric_latest` | 2026-08-30 (ingesta D-1 con maduración; por eso N se cuenta desde el watermark) |

Esperado tras 1.5: en un ciclo como el 33/34, ~57 decisiones dejan de
existir (`entidad_inerte`) y liberan cupo para las que sí sangran; ~1
decisión al día pasa de −12% a −25%.

## Decisiones selladas (diseño)

- **D1 · Regla A'** en el motor puro (`app/optimizer/bid.py`), evaluada
  DESPUÉS del PAUSE (que sigue mandando al 1.5×) y ANTES de las bandas:
  `orders == 0 ∧ ad_revenue == 0 ∧ expected_clicks ≠ None ∧ clicks ≥
  expected_clicks ∧ cost ≥ cost_min` → factor `FACTOR_BAJA_FUERTE` (−0.25)
  con motivo NUEVO `banda_menos_25_cero_ventas`. Comparaciones Decimal
  exactas; `None` en cualquier insumo = la regla no aplica (regla 3).
  `expected_clicks` viene de `cortes.umbral_corte(evidencia, "pause")`
  (`UmbralResuelto.expected_clicks`, ya congelado como string en
  `inputs.corte.expected_clicks`); `cost_min` es el piso de pausa que ya
  recibe `decide_bid` (`PAUSE_COST_MIN[platform]`, congelado en
  `inputs.corte.cost_min_usado`). Replay: lee `inputs.corte.expected_clicks`;
  fila sin la clave → `None` → rejuega como antes.
- **D2 · Vista `v_entidad_inerte`** (migración `0013`) = ÚNICA fuente de
  «inerte» para el ciclo, la página, el digest y la herramienta. N = 14 días
  contados desde el **watermark de la plataforma** (`max(metric_date)` de
  `v_metric_latest` por plataforma, misma consulta que `windows.py`). Solo
  hojas (`keyword`/`product_target`) con `status='ENABLED'` propio, del ad
  group y de la campaña (cache `ad_entity_state`). Clasificación sobre 90 d
  (desde el watermark): `con_ventas_previas` / `gasto_sin_ventas` /
  `peso_muerto`.
- **D3 · Guarda en el ciclo**: `_procesa_decisora` consulta el conjunto de
  inertes de la plataforma UNA vez por ciclo (en TX2, junto a
  `evidencia_ad_groups`) y salta la hoja con motivo `entidad_inerte` ANTES de
  `windows.ventanas_entidad` (ahorra las consultas). No aplica al camino de
  términos (`_procesa_grupo`): negativos/harvest son sobre términos con dato.
- **D4 · Reporte**: `GET /api/dashboard/inertes` (lee la vista) + página
  `/inertes` (Jinja, escapado, sin JS inline) + línea en `digest_ciclo` SOLO
  si la clave existe en `notes.skips` (regla 3). Nada muta.
- **D5 · Herramienta de archivo** sigue el precedente de
  `tools/reactiva_campanas.py`: corre DENTRO del contenedor por el lead,
  HTTP propio con el sello v3 (POST `/sp/keywords/delete` con
  `keywordIdFilter`; readback por POST `/sp/keywords/list`), NO importa
  `app.ads.write` (candado de `test_architecture`). Ledger propio
  `keyword_archivo_manual` (migración `0014`) con la identidad completa para
  la REVERSA `--reponer <lote>` (POST `/sp/keywords` con `matchType` del
  ledger, shape de `write.crear_keyword_exacta`). Fail-closed: `--esperado N`
  debe igualar el conteo del plan; ledger ANTES del HTTP; readback ≠
  ARCHIVED → fila `failed` y el lote se detiene.
- **D6 · Sin cambio de caps/escalera/cola.** Las decisiones −25% nuevas
  compiten por el cupo con la prioridad sellada (`apply._PRIORIDAD_BANDA`:
  el motivo nuevo entra con prioridad 0, la de −25%).

## Phase 1 — Motor y vista [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | **GLM — Regla A' en `bid.py`**: constante `MOTIVO_BANDA_MENOS_25_CERO_VENTAS = "banda_menos_25_cero_ventas"`; `decide_bid` gana `expected_clicks: Decimal \| None = None`; función pura `_factor_cero_ventas(bids, expected_clicks, cost_min)`; motivo del resultado = el nuevo cuando esa regla dispara, si no `_MOTIVO_BANDA[factor]`; `cycle._procesa_decisora` pasa `expected_clicks=corte_pause.expected_clicks`; `replay._replay_bid` lee `inputs.corte.expected_clicks`; `apply._PRIORIDAD_BANDA` con el motivo nuevo en prioridad 0; `api_dashboard.MOTIVOS_ES_DECISIONES` con su etiqueta; `docs/CONTEXTO.md` (umbrales sellados) con la regla. Guía en §1.1. `[tdd:required]` | Tests puros ROJOS contra master en `tests/test_optimizer_bid.py`: dispara en el borde exacto (clicks == expected, cost == cost_min) con factor −0.25 y motivo nuevo; NO dispara con clicks = expected − 1, con cost < cost_min, con `expected_clicks=None`, con orders=None o ad_revenue=None (siguen −12%/otro); PAUSE gana cuando aplica; replay: fila histórica sin la clave rejuega igual (golden intacto) y una decisión nueva congela/replayea el motivo nuevo (`tests/test_cycle.py`); logs rojos en §Decisiones | - | cc:完了 Regla A' en el motor (PR #132, CI verde) |
| 1.2 | **GLM — Vista `v_entidad_inerte` + guarda `entidad_inerte`**: migración `migrations/0013_entidad_inerte.sql` (vista + `COMMENT ON VIEW` + `GRANT SELECT` con el mismo patrón de roles de `0006`), `cycle.MOTIVO_ENTIDAD_INERTE = "entidad_inerte"`, `_SQL_INERTES` + lectura en TX2 + skip en `_procesa_decisora` (D3), etiqueta en `MOTIVOS_ES_SALUD`, `docs/DASHBOARD.md` (lista de motivos), `docs/DATABASE.md` (la vista). Guía en §1.2. `[tdd:required]` | Test de la vista (`_db_temporal` + migración 0013 aplicada en el fixture) ROJO contra master: 3 hojas sembradas (sin impresiones 20 d con gasto 90 d → `gasto_sin_ventas`; sin impresiones y sin nada → `peso_muerto`; con impresiones hace 3 d → NO aparece) y N contado desde el watermark, no desde `now()`; test de ciclo ROJO: una hoja con métricas solo antiguas (ventana completa) decidía un bid y ahora es `skips.entidad.entidad_inerte = 1`, una con impresiones recientes sigue decidiendo; pglast de `_SQL_INERTES`; logs rojos | - | cc:完了 Vista + guarda entidad_inerte (PR #133, CI verde) |

## Phase 2 — Superficie y herramienta [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.3 | **DeepSeek — `/inertes` + digest**: `api_dashboard.py` `GET /inertes` (lee `v_entidad_inerte`: items + totales por plataforma y clasificación; regla 22: la UI consume el endpoint); `ui.py` `GET /inertes` + `templates/inertes.html` (tabla: plataforma, campaña, ad group, keyword/target, clasificación, días sin impresiones, gasto/ventas 90 d; textos ESCAPADOS; sin JS inline; enlace en `base.html`); `notifica.digest_ciclo`: línea `entidades sin trafico (saltadas): N` SOLO si `skips.entidad.entidad_inerte` existe en el resumen. Guía en §1.3. `[tdd:required]` | Tests ROJOS: endpoint con filas sembradas devuelve el shape y los totales; página renderiza y escapa `<script>` en `keyword_text`; digest sin la clave NO imprime la línea y con la clave sí; CI verde | 1.2 | cc:完了 /inertes + digest (PR #135, CI verde) |
| 1.4 | **GLM — `tools/archiva_inertes.py` + reversa + ledger**: migración `migrations/0014_keyword_archivo_manual.sql` (tabla ledger, D5); herramienta con `--plataforma`, `--clasificacion peso_muerto` (default), `--min-dias-sin-impresiones 30`, `--limite N`, dry-run por defecto, `--acepto-mutacion-real --esperado N --go "<literal>"`, `--reponer <lote>`; solo `kind='keyword'`; una línea JSON por mutación (scrub); reconciliación final. Guía en §1.4. `[tdd:required]` | Tests ROJOS con `httpx.MockTransport` (patrón `tests/test_reactiva_campanas.py`): plan desde la vista; `--esperado` distinto → aborta sin HTTP; entidad no ENABLED en el LIST previo → se salta con nota; ledger ANTES del HTTP; readback `ARCHIVED` → `applied`; readback ≠ → `failed` y el lote se detiene; `--reponer` recrea con el `matchType` del ledger y sella `repuesto_*`; `test_architecture` sigue verde (sin `app.ads.write`); logs rojos | 1.2 | cc:完了 Herramienta + ledger + reversa (PR #134: revisión del lead adjudicada + 4 cambios; CI decide) |
| 1.5 | **Lead — producción**: review de cada PR + cross-review codex (1 ronda); migraciones 0013/0014 por el runbook (`-1`, backup del schema antes); deploy al contenedor (DEPLOY.md: `git archive origin/master`, md5, `up --build`, `Recreated`); **contrafactual** read-only sobre los últimos ciclos live: cuántas decisiones pasan a `entidad_inerte` y cuántas −12% pasan a −25% (esperado ≈57/103 y ≈1); verificar el ciclo siguiente (`notes.skips.entidad_inerte`, motivos nuevos en `/salud` y en el feed); primer lote de archivo SOLO con go literal del dueño; AppFlowy. `[tdd:skip:ops]` | SELECTs del ciclo post-deploy en la evidencia; contrafactual entregado al dueño; `/inertes` visible; AppFlowy Done con evidencia | 1.1-1.4 | cc:WIP [**2026-09-03 21:32-21:40 UTC, lead** (adelantado del 09-05 por enmienda del dueño «más velocidad»). **Migraciones**: backup del schema (`backups/schema-pre-0013-0014-20260903.sql`), 0013 y 0014 aplicadas en transacción única ANTES del código (el ciclo lee `v_entidad_inerte`: sin la vista, TX2 revienta); verificado: vista y tabla creadas, GRANT ok, legible por `app_read` y por el servicio desde el contenedor, `ad_entity`/`ad_entity_state` sin cambio (18,426). Vista en prod: **349 hojas inertes** (MX 179 = 1 con_ventas_previas + 8 gasto_sin_ventas + 170 peso_muerto; US 170 = 38 + 132). **Deploy**: respaldo `app.bak-predeploy-20260903b`, `git archive origin/master` 9f60d7e, **md5 38/38 idénticos** antes de construir, `COPY app` no cacheado, contenedor **Recreated** (imagen 3673dd5→03379cf), health 200, `/api/dashboard/inertes` 200, `/inertes` 200, cero errores de arranque, puerto solo loopback+VPN, secrets 700. Verificado DENTRO del contenedor: `bid.MOTIVO_BANDA_MENOS_25_CERO_VENTAS` + `expected_clicks` en `decide_bid`, `cycle.MOTIVO_ENTIDAD_INERTE` + `_SQL_INERTES` sobre la vista, marcador `cero_ventas_expected_usado` en el freeze. **CONTRAFACTUAL (read-only, antes del deploy)**: de las 20 aplicadas el 09-03, **4 eran inertes** (cupos gastados en hojas sin tráfico); con la guarda, de 57 decisiones US quedan **13** y de 36 MX quedan **31**; la regla A' cambia **1** decisión de −12% a −25% (id 2107, US). Base sana tras el deploy: cola live 0, intentos sin sello 0, config 12, 4 goals live. **HALLAZGO DEL LEAD 2026-09-04: la herramienta de archivo NUNCA habia corrido contra una base real.** Al preparar el ensayo del primer lote revento con `psycopg.errors.IndeterminateDatatype: could not determine data type of parameter $1`: el primer parametro de `_SQL_PLAN` y `_SQL_EXCLUIDOS` (`%s IS NULL OR ...`) no tenia contexto de tipo. Causa raiz de por que nadie lo vio: **los 20 tests del archivo usan `_ConnFalsa`** — validan el plumbing de Python y JAMAS el SQL. Es exactamente la debilidad declarada en el CLAUDE.md global (tests que no discriminan). Arreglado con el cast (`%s::platform IS NULL`) + un test que ejecuta las DOS consultas contra Postgres de verdad, con parametro presente y con NULL; verificado por mutacion (quitar el cast lo pone rojo). **ENSAYO REAL (dry run, cero mutaciones)**: MX peso_muerto **160 keywords** candidatas (+4 product_target solo reportados), US peso_muerto **4** (+127 reportados), gasto_sin_ventas MX **6** y US **2**. Gasto de 90d de las inertes: MX 248 MXN y US 54 USD en `gasto_sin_ventas` (el peso_muerto no gasta nada: son 0). PENDIENTE: verificar el ciclo del 09-04 (`skips.entidad.entidad_inerte` y el motivo nuevo en el feed) y, con go literal del dueño, el primer lote de archivo] |

## Guía de implementación

Rutas contra `origin/master` ≥ `08ccaaf`. Si un número de línea no cuadra,
manda el nombre del símbolo.

### 1.1 — Regla A' (`app/optimizer/bid.py`, `app/cycle.py`, `app/optimizer/replay.py`, `app/apply.py`, `app/api_dashboard.py`)

**Paso 1 — tests (rojo primero)** en `tests/test_optimizer_bid.py`. El helper
`_decide(...)` gana `expected_clicks: str | None = None` y lo pasa como
`Decimal` (o `None`). `_bids(...)`/`_cortes(...)` ya existen.

```python
def test_cero_ventas_con_clics_esperados_y_gasto_sobre_piso_baja_25():
    """BIDS 01 (regla 9): orders=0 y ad_revenue=0 con clicks >= expected_clicks
    del grupo y cost >= cost_min -> -25% con motivo propio. Antes del fix el
    motor solo podia dar -12% a cero ventas (-25% exigia orders >= 1)."""
    r = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=120, orders=0),
        _cortes(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=120, orders=0),
        cost_min="40",
        expected_clicks="120",
    )
    assert r.kind == "bid"
    assert r.factor == Decimal("-0.25")
    assert r.motivo == "banda_menos_25_cero_ventas"
    assert r.new_value == Decimal("0.75")


def test_cero_ventas_un_click_bajo_los_esperados_sigue_menos_12():
    r = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=119, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_gasto_bajo_el_piso_sigue_menos_12():
    r = _decide(
        _bids(cost=Decimal("39.99"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_sin_expected_clicks_no_aplica():
    """Grupo sin evidencia (expected None): regla 3, nada inventado -> -12%."""
    r = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks=None,
    )
    assert r.motivo == "banda_menos_12"


def test_cero_ventas_orders_o_revenue_desconocidos_no_aplica():
    r1 = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=None),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    r2 = _decide(
        _bids(cost=Decimal("45"), ad_revenue=None, clicks=200, orders=0),
        None,
        cost_min="40",
        expected_clicks="120",
    )
    assert r1.motivo != "banda_menos_25_cero_ventas"
    assert r2.motivo != "banda_menos_25_cero_ventas"


def test_pause_gana_sobre_la_regla_de_cero_ventas():
    """Al 1.5x (umbral_pause) con cost >= piso en la ventana de CORTES manda el
    PAUSE (sin cambio): la regla nueva vive DESPUES del pause."""
    r = _decide(
        _bids(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        _cortes(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        cost_min="40",
        expected_clicks="120",
        umbral_pause=180,
    )
    assert r.kind == "pause"
```

(Si `_decide` no acepta `umbral_pause`, agrégalo con default `b.LEGACY_PAUSE`.
Revisa `_bids`/`_cortes`: si `clicks`/`orders` no son kwargs de `_agregado`,
extiende el helper — está en las primeras 80 líneas del archivo.)

Rojo esperado: `AssertionError` en el motivo (`'banda_menos_12' !=
'banda_menos_25_cero_ventas'`) y `TypeError: unexpected keyword argument
'expected_clicks'`. Pegar en §Decisiones.

**Paso 2 — motor.** En `bid.py`, junto a `_MOTIVO_BANDA`:

```python
MOTIVO_BANDA_MENOS_25_CERO_VENTAS = "banda_menos_25_cero_ventas"


def _factor_cero_ventas(
    agregado: AgregadoMetricas, expected_clicks: Decimal | None, cost_min: Decimal | None
) -> Decimal | None:
    """BIDS 01 (spec 2026-08-26 aprobada 2026-09-03, regla A'): gasto sin UNA
    venta habiendo alcanzado los clicks que en ese grupo cuesta una venta y
    el piso de costo de pausa -> -25%. Relativa al producto (expected_clicks
    de CORTES 01), jamas un numero absoluto. Cualquier insumo None = no aplica
    (regla 3). Se evalua DESPUES del pause y ANTES de las bandas."""
    if expected_clicks is None or cost_min is None:
        return None
    if agregado.orders is None or agregado.ad_revenue is None:
        return None
    if agregado.clicks is None or agregado.cost is None:
        return None
    if agregado.orders != 0 or agregado.ad_revenue != 0:
        return None
    if Decimal(agregado.clicks) >= expected_clicks and agregado.cost >= cost_min:
        return FACTOR_BAJA_FUERTE
    return None
```

`decide_bid(..., cost_min=None, expected_clicks: Decimal | None = None)`. En
el bloque de bandas (hoy `factor = _factor_banda(bids, target_acos_pct)`):

```python
        factor = _factor_cero_ventas(bids, expected_clicks, cost_min)
        motivo_banda = MOTIVO_BANDA_MENOS_25_CERO_VENTAS if factor is not None else None
        if factor is None:
            factor = _factor_banda(bids, target_acos_pct)
            motivo_banda = _MOTIVO_BANDA.get(factor) if factor is not None else None
```

y donde hoy se toma el motivo del resultado desde `_MOTIVO_BANDA[factor]`
(o `_MOTIVO_BANDA[factor_clamped]`), usar `motivo_banda`. El resto
(clamps, delta mínimo, dirección/magnitud) no cambia. Docstring de
`decide_bid`: una frase con la regla y su orden.

**Paso 3 — orquestador y replay.** `app/cycle.py` `_procesa_decisora`: en la
llamada a `bid.decide_bid` añadir `expected_clicks=corte_pause.expected_clicks`
(el freeze `inputs.corte.expected_clicks` ya existe: nada que congelar).
`app/optimizer/replay.py` `_replay_bid`:

```python
    expected = (
        _dec_de_json(corte.get("expected_clicks"))
        if corte is not None and corte.get("expected_clicks") is not None
        else None
    )
    return bid.decide_bid(..., cost_min=cost_min, expected_clicks=expected)
```

**Paso 4 — prioridad y etiqueta.** `app/apply.py`:
`_PRIORIDAD_BANDA = {"banda_menos_25": 0, "banda_menos_25_cero_ventas": 0,
"banda_menos_12": 1, "banda_mas_15": 2}` (importa la constante desde bid en
vez de literal si el módulo ya importa `bid`). `app/api_dashboard.py`
`MOTIVOS_ES_DECISIONES`: `bid.MOTIVO_BANDA_MENOS_25_CERO_VENTAS: "Cero ventas
con los clics de una venta y gasto sobre el piso: -25%"`. Test puro del
diccionario (patrón `test_motivos_salud_traducen_los_gates_de_ancestros`).

**Paso 5 — replay/golden en `tests/test_cycle.py`.** (a) Los goldens
existentes NO cambian (sus inputs históricos tienen ventas o no alcanzan
expected). (b) Test nuevo con `_db_temporal`: hoja con métricas de cero
ventas que superan `expected_clicks` de su grupo y cost ≥ piso → la decisión
persistida trae `motivo = banda_menos_25_cero_ventas` y `inputs.corte
.expected_clicks` no nulo; `ciclo.reproduce(inputs)` devuelve el mismo
`new_value`. Usa `_siembra_maestra` + un grupo con evidencia 3/60/14 (mira
`_siembra_terminos`/`_siembra_kw_bid` y cómo otros tests siembran evidencia
elegible; si no hay helper, siembra 60 clicks/3 órdenes/14 fechas en el
grupo y documenta en §Decisiones).

**Paso 6 — docs.** `docs/CONTEXTO.md`, sección de umbrales sellados: bala
«BIDS 01 (2026-09-03): cero ventas → −25% cuando clicks ≥ expected_clicks
del grupo y cost ≥ piso de pausa; antes −12%; pausa al 1.5× sin cambio».
Commit: `feat(bid): -25% para cero ventas relativo a los clics esperados por
venta (BIDS 01 · 1.1)`.

### 1.2 — Vista `v_entidad_inerte` + guarda (`migrations/0013_entidad_inerte.sql`, `app/cycle.py`)

**Paso 1 — migración** (patrón de cabecera y `COMMENT ON VIEW` de `0007`;
`GRANT SELECT` copiando la línea de roles de `migrations/0006_contribucion_entidad.sql:782`):

```sql
CREATE OR REPLACE VIEW v_entidad_inerte AS
WITH watermark AS (
  SELECT e.platform, max(v.metric_date) AS wm
    FROM v_metric_latest v JOIN ad_entity e ON e.id = v.ad_entity_id
   GROUP BY e.platform
), hojas AS (
  SELECT e.id, e.platform, e.kind, e.keyword_text, e.name, e.external_id,
         g.id AS ad_group_id, g.name AS ad_group_name,
         c.id AS campaign_id, c.name AS campaign_name
    FROM ad_entity e
    JOIN ad_entity g ON g.id = e.parent_id AND g.kind = 'ad_group'
    JOIN ad_entity c ON c.id = g.parent_id AND c.kind = 'campaign'
    JOIN ad_entity_state se ON se.ad_entity_id = e.id AND se.status = 'ENABLED'
    JOIN ad_entity_state sg ON sg.ad_entity_id = g.id AND sg.status = 'ENABLED'
    JOIN ad_entity_state sc ON sc.ad_entity_id = c.id AND sc.status = 'ENABLED'
   WHERE e.kind IN ('keyword', 'product_target')
), reciente AS (  -- N = 14 dias desde el watermark de SU plataforma (sellado)
  SELECT h.id, coalesce(sum(v.impressions), 0) AS impresiones_14d
    FROM hojas h JOIN watermark w ON w.platform = h.platform
    LEFT JOIN v_metric_latest v ON v.ad_entity_id = h.id AND v.metric_date > w.wm - 14
   GROUP BY h.id
), historia AS (  -- 90 dias desde el watermark
  SELECT h.id,
         coalesce(sum(v.cost), 0) AS gasto_90d,
         coalesce(sum(v.orders), 0) AS ordenes_90d,
         max(v.metric_date) FILTER (WHERE v.impressions > 0) AS ultima_impresion
    FROM hojas h JOIN watermark w ON w.platform = h.platform
    LEFT JOIN v_metric_latest v ON v.ad_entity_id = h.id AND v.metric_date > w.wm - 90
   GROUP BY h.id
)
SELECT h.*, w.wm AS watermark,
       hi.ultima_impresion,
       (w.wm - hi.ultima_impresion) AS dias_sin_impresiones,  -- NULL = nunca en 90d
       hi.gasto_90d, hi.ordenes_90d,
       CASE WHEN hi.ordenes_90d > 0 THEN 'con_ventas_previas'
            WHEN hi.gasto_90d > 0 THEN 'gasto_sin_ventas'
            ELSE 'peso_muerto' END AS clasificacion
  FROM hojas h
  JOIN watermark w ON w.platform = h.platform
  JOIN reciente r ON r.id = h.id AND r.impresiones_14d = 0
  JOIN historia hi ON hi.id = h.id;
```

Cuida los tipos reales (`impressions`/`cost`/`orders` pueden ser NULL:
`coalesce` en las sumas; `metric_date` es `date`). `COMMENT ON VIEW` con: N=14
desde el watermark (por qué no `now()`), fuente única (D2), que la ausencia
de fila = NO inerte. Test de la vista en `tests/test_schema.py` o archivo
nuevo `tests/test_entidad_inerte.py` (fixture `_db_temporal` + `SQL`, `SQL2`,
`SQL3` + el texto de `0013`): 3 casos del DoD, y el caso «watermark viejo»:
si el último `metric_date` de la plataforma es hace 10 días, una hoja con
impresiones hace 12 días NO es inerte (12 − 10 = 2 ≤ 14 desde el watermark).

**Paso 2 — ciclo.** `app/cycle.py`: constante `MOTIVO_ENTIDAD_INERTE =
"entidad_inerte"` (junto a los demás `MOTIVO_*`), SQL:

```python
_SQL_INERTES = """
SELECT id FROM v_entidad_inerte WHERE platform = %s::platform
"""
```

En TX2 (donde nace `evidencia_ad_groups = windows.ventanas_evidencia_ad_group(...)`):
`inertes = {f[0] for f in conn.execute(_SQL_INERTES, (platform,)).fetchall()}`
y pásalo a `_procesa_decisora` (parámetro nuevo `inertes: set[int]`). En
`_procesa_decisora`, justo después del `assert goal is not None` y ANTES de
resolver `corte_pause_por_grupo`/`ventanas_entidad`:

```python
    if entidad_id in inertes:
        # BIDS 01 (D3): sin impresiones en 14d desde el watermark -> el ajuste
        # seria inerte (Amazon no sirve la hoja); no gasta consultas ni cupo.
        contadores.skips_entidad[MOTIVO_ENTIDAD_INERTE] += 1
        tick()
        return
```

Docstring del módulo (bala ELEGIBILIDAD) + `MOTIVOS_ES_SALUD` («Entidad sin
trafico reciente (sin impresiones en 14 dias): sin ajuste») +
`docs/DASHBOARD.md` (lista de motivos) + `docs/DATABASE.md` (la vista). Añade
`_SQL_INERTES` a la tupla literal que parsea pglast en `tests/test_cycle.py`.
Test de ciclo (`_db_temporal` con 0013 aplicada en el fixture — agrégala al
fixture de `tests/test_cycle.py` igual que `SQL3`): hoja con `_siembra_kw_bid`
cuyas fechas terminan hace 40 días respecto al watermark → antes decidía
bid, ahora `skips.entidad.entidad_inerte == 1` y cero decisiones de esa hoja;
otra hoja con impresiones dentro de 14 d sigue decidiendo. Commit:
`feat(cycle): vista v_entidad_inerte y guarda entidad_inerte (BIDS 01 · 1.2)`.

### 1.3 — `/inertes` y digest (DeepSeek)

Patrón EXACTO a copiar: `app/api_dashboard.py::cortes` (endpoint que consume
una SQL con `dict_row` y devuelve `{"items": [...]}`) y `app/ui.py::pagina_cortes`
(+ `app/templates/cortes.html`). Endpoint `GET /api/dashboard/inertes`:
`SELECT ... FROM v_entidad_inerte ORDER BY platform, clasificacion,
gasto_90d DESC` → `{"totales": {plataforma: {clasificacion: n}}, "items":
[{plataforma, campana, ad_group, kind, texto (keyword_text o name),
external_id, clasificacion, dias_sin_impresiones (int|null),
ultima_impresion (iso|null), gasto_90d (str Decimal), ordenes_90d}]}`.
Página `/inertes`: tabla + resumen por plataforma/clasificación; `{{ }}`
escapa el texto (vector XSS: `keyword_text`); sin JS; enlace en la nav de
`base.html`. `notifica.digest_ciclo`: el resumen ya trae `skips`? Si no,
mira cómo llega `apply` al resumen y añade `skips.entidad.entidad_inerte`
por el mismo camino; línea `entidades sin trafico (saltadas): {n}` SOLO si
la clave existe. Tests: `tests/test_api_dashboard.py` (siembra 2 hojas
inertes de distinta clasificación sobre el fixture con 0013 y asserta shape y
totales), `tests/test_ui.py` (200, contiene la clasificación y escapa
`<script>`), `tests/test_notifica.py` (con/sin clave). Rojo primero.
Commit: `feat(dashboard): pagina /inertes y linea del digest (BIDS 01 · 1.3)`.

### 1.4 — `tools/archiva_inertes.py` + `migrations/0014_keyword_archivo_manual.sql` (GLM)

Lee ENTERO `tools/reactiva_campanas.py` (HTTP propio con sello v3, dry-run,
`--acepto-mutacion-real`, readback por `/list`, JSON por línea con scrub,
reconciliación) y `tests/test_reactiva_campanas.py` (MockTransport). Copia
ese esqueleto; NO importes `app.ads.write` (candado). Ledger (0014):

```sql
CREATE TABLE keyword_archivo_manual (
  id                 BIGSERIAL PRIMARY KEY,
  lote               TEXT        NOT NULL,          -- p.ej. 'inertes-2026-09-05'
  ad_entity_id       BIGINT      NOT NULL REFERENCES ad_entity(id),
  platform           platform    NOT NULL,
  campaign_external  TEXT        NOT NULL,
  ad_group_external  TEXT        NOT NULL,
  keyword_external   TEXT        NOT NULL,
  keyword_text       TEXT        NOT NULL,
  match_type         TEXT        NOT NULL,          -- EXACT | PHRASE | BROAD
  bid                NUMERIC(14,4),
  bid_currency       TEXT,
  clasificacion      TEXT        NOT NULL,
  dias_sin_impresiones INT,
  go_literal         TEXT        NOT NULL,          -- el texto del dueno
  intentado_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  ack                JSONB,
  readback_estado    TEXT,                          -- ARCHIVED esperado
  estado             TEXT        NOT NULL CHECK (estado IN ('planeado','applied','failed','repuesto')),
  repuesto_at        TIMESTAMPTZ,
  repuesto_external  TEXT,
  repuesto_ack       JSONB
);
COMMENT ON TABLE keyword_archivo_manual IS 'BIDS 01 (D5): ledger de archivos MANUALES de keywords inertes con la identidad completa para la reversa (--reponer). Regla 7.';
GRANT SELECT ON keyword_archivo_manual TO <roles de lectura del patron 0006>;
GRANT INSERT, UPDATE ON keyword_archivo_manual TO app_admin;
```

Flujo por keyword (`--acepto-mutacion-real`): (1) plan desde
`v_entidad_inerte` con los filtros; `len(plan) == --esperado` o aborta sin
HTTP; (2) LIST previo: estado vivo `ENABLED` o se salta con nota; (3) fila
`planeado` en el ledger (intención durable) + commit; (4) POST
`/sp/keywords/delete` `{"keywordIdFilter": {"include": ["<id>"]}}` (shape de
`write.borrar_keyword`, id como STRING, vendor v3 en Content-Type y Accept);
(5) readback LIST → `ARCHIVED` → `applied`; distinto → `failed` y **el lote se
detiene**; (6) línea JSON con request/ack/readback (scrub). `--reponer
<lote>`: por cada `applied`, POST `/sp/keywords` con `{adGroupId, campaignId,
keywordText, matchType (del ledger), state: ENABLED, bid}` (shape de
`write.crear_keyword_exacta`), readback por texto+match en el grupo, fila →
`repuesto` con el external nuevo. Requiere `ORBIT_DSN_ADMIN` para el ledger y
`ORBIT_DSN_READ` para el plan. Docs: `docs/DEPLOY.md` sección nueva
«Archivo manual de keywords inertes (BIDS 01)» con el runbook y la reversa,
al lado de «Limpieza de product ads muertos». Commit: `feat(tools):
archiva_inertes con ledger y reversa (BIDS 01 · 1.4)`.

## Decisiones y evidencia (GLM / DeepSeek escriben aquí ANTES del código)

### 1.3 — `/inertes` y digest (DeepSeek, rama bids-01-1-3)

- **D-1.3.1 · Plan-vs-código: CUADRA con 5 precisiones mínimas** (cada una
  con precedente explícito en el repo; si el lead discrepa se revierte en
  review):
  1. `api_dashboard.cortes` existe (`app/api_dashboard.py:824`, patrón
     `dict_row` + `{"items": [...]}`) y `ui.pagina_cortes`
     (`app/ui.py:340`) + `app/templates/cortes.html` existen: se copian
     tal cual. La UI consume el endpoint con la misma `ConexionLectura`
     (regla 22: ningún SQL en `ui.py`).
  2. Columnas de la vista verificadas en
     `migrations/0013_entidad_inerte.sql:63-80`: id, platform, kind,
     keyword_text, name, external_id, ad_group_id, ad_group_name,
     campaign_id, campaign_name, watermark (wm), ultima_impresion,
     dias_sin_impresiones, gasto_90d, moneda, ordenes_90d,
     clasificacion. Coinciden con el contrato de la tarea.
  3. `skips` NO viaja hoy al digest (verificado: `_fase_notifica` en
     `app/cycle.py:1595` arma el resumen solo con
     cycle_id/plataforma/modo/status/decisions_count/apply; los
     contadores viven en `cuerpo["skips"]` en el llamador
     `_corre_fases`, `app/cycle.py:1728`). Se lleva por el MISMO camino
     que `apply`: parámetro nuevo `skips` en `_fase_notifica` (default
     None: `tests/test_preflight_1_4.py:383` la llama con kwargs y debe
     seguir pasando) + `resumen["skips"]`, y `digest_ciclo` lee
     `skips.entidad.entidad_inerte`.
  4. Presupuesto de tamaño (`tests/test_architecture.py`, 900 líneas):
     `app/api_dashboard.py` tiene 849. El endpoint cabe en ~44 líneas
     (SQL 9 + endpoint ~35 con docstring de 3 líneas); se verifica que
     queda ≤ 900 corriendo ese test en local. El racional largo vive
     aquí y en el `COMMENT ON VIEW` de la 0013, no en el endpoint.
  5. Orden con NULLs: `gasto_90d` puede ser NULL (mezcla de monedas,
     fail-loud de la 0013; `DESC` solo pondría los NULL primero).
     `ORDER BY platform, clasificacion, gasto_90d DESC NULLS LAST, id`:
     NULLs al final e `id` como desempate estable.
- **D-1.3.2 · Shape del endpoint** (plan §1.3 + moneda por regla 4):
  `{"totales": {plataforma: {clasificacion: n}}, "items": [{plataforma,
  kind, texto (keyword_text o name), external_id, campana, ad_group,
  clasificacion, dias_sin_impresiones (int|null),
  ultima_impresion (iso|null), gasto_90d (str|null vía `_dec_str`),
  moneda (str|null), ordenes_90d (int)}]}`. `gasto_90d` NULL viaja null
  (regla 3, jamás 0); `ordenes_90d` es conteo (la vista lo coalescea,
  nunca null).
- **D-1.3.3 · Tests.** En `tests/test_api_dashboard.py` se aplica el
  texto de la 0013 sobre el fixture (precedente `test_cycle.py:SQL13`) y
  se extiende `_metrica` con `impressions`/`orders` (default None,
  precedente D-1.2.3, compatible hacia atrás); los ENABLED se siembran
  con `_estado_acos(..., None)`. Dos hojas inertes (`gasto_sin_ventas`
  con impresión vieja real + `peso_muerto`) + ancla de watermark + hoja
  con tráfico reciente que NO aparece; aserta shape y totales. En
  `tests/test_ui.py`: render de `inertes.html` con payload `<script>` en
  el texto (escapado) + ruta `/inertes` 200 por TestClient. En
  `tests/test_notifica.py`: digest puro sin la clave (no menciona la
  línea) y con `skips: {"entidad": {"entidad_inerte": N}}` (línea
  exacta).
- **D-1.3.4 · Digest.** Línea literal
  `entidades sin trafico (saltadas): N` (sin acentos: viaja al código).
  SOLO si existe `skips.entidad.entidad_inerte`; N es el conteo entero.
  `skips` ausente, no-dict o sin `entidad` = no se menciona (regla 3,
  sin KeyError).

**Rojo 1.3** (tests nuevos contra `origin/master`, antes del fix; Postgres
local 16 disponible, nada skipea):

```text
$ .venv/bin/python -m pytest tests/test_api_dashboard.py -q -k "inertes" \
    tests/test_ui.py -q -k "inertes" tests/test_notifica.py -q -k "inertes"
FAILED tests/test_api_dashboard.py::test_router_dashboard_expone_inertes_get
  E AssertionError: falta la ruta de inertes en el router
FAILED tests/test_api_dashboard.py::test_inertes_devuelve_shape_y_totales
  E AssertionError: {"detail":"Not Found"} / assert 404 == 200
FAILED tests/test_ui.py::test_ui_inertes_xss_texto_escapado
  E jinja2.exceptions.TemplateNotFound: 'inertes.html' not found
FAILED tests/test_ui.py::test_ui_inertes_200_con_clasificacion_y_escape
  E AssertionError: {"detail":"Not Found"} / assert 404 == 200
FAILED tests/test_notifica.py::test_digest_con_inertes_muestra_saltadas
  E AssertionError: assert 'entidades sin trafico (saltadas): 57'
    in '[Orbit] digest ciclo #2 amazon_us [live] — done\ndecisiones: 5'
5 failed, 3 passed (los 3 guards de regla 3 ya pasan: sin la clave nada se
menciona — el que discrimina es el positivo de cada archivo)
```

Poder discriminante: el 404 del endpoint prueba que la siembra (vista 0013
+ 2 hojas) es valida antes del fix — con el fix el mismo seed da 200 con
shape y totales (regla 9).

### 1.4 — `tools/archiva_inertes.py` + ledger (GLM, rama bids-01-1-4)

- **D-1.4.1 · Plan-vs-código: CUADRA con 4 adiciones mínimas, no se para**
  (cada una con precedente explícito en el repo; si el lead discrepa se
  revierte en review):
  1. El plan §1.4 no dice de dónde salen `bid`, `match_type` ni los
     externals de campaña/grupo. Código real: `bid` ←
     `ad_entity_state.current_bid` + `bid_currency` (`0001:644`, dominio
     `money_amount` = NUMERIC(14,4) + enum `currency`, con CHECK parejo
     `estado_bid_con_moneda` — regla 4 ya sellada); `match_type` ←
     `ad_entity.match_type` (NOT NULL para `keyword` por CHECK
     `ad_entity_keyword_coherente`, `0001:250`); externals por JOIN a
     `ad_entity` vía `ad_group_id`/`campaign_id` de la vista. La vista
     0013 trae lo demás (id, platform, kind, texto, clasificación, días,
     gasto+moneda 90d). Nada inventado.
  2. La migración suma `GRANT USAGE ON ALL SEQUENCES IN SCHEMA public`
     (precedente `0002:712`; `0001:1517` explica por qué: las identity
     columns no necesitan grant por separado pero se deja explícito).
     Sin esa línea el INSERT como `app_admin` revienta en el `nextval`
     del BIGSERIAL. El DDL del plan por lo demás va literal.
  3. `--reponer`: readback por `keywordIdFilter` con el id que trae el
     ack del create (criterio `_id_de_ack` de `apply_harvest.py:471`,
     reimplementado mínimo sin importar `write`/`apply` por el candado),
     verificando texto+match+grupo contra el ledger. `adGroupIdFilter`
     no lo usa NINGÚN camino sellado del repo (grep: cero hits en
     app/tools/tests): el plan pide "readback por texto+match en el
     grupo" y eso se cumple verificando esos 3 campos en el objeto del
     LIST, no por el nombre del filtro.
  4. `dias_sin_impresiones` NULL (= nunca impresionó en 90d, el caso más
     muerto) PASA el filtro `--min-dias-sin-impresiones` (se trata como
     infinito): excluirlo vaciaría el default `peso_muerto` (165 MX +
     134 US "nada en 90d" de la medición del plan). `bid` NULL en el
     state: se archiva igual (el DELETE no necesita bid); `--reponer`
     aborta esa fila fail-closed (el POST create exige bid; regla 3, no
     se inventa) y la fila queda `applied` declarándolo.
- **D-1.4.2 · Lote autogenerado.** El contrato no define flag `--lote`:
  `lote = 'inertes-YYYY-MM-DD'` (fecha UTC del día de la corrida);
  `--reponer <lote>` lo consume literal. `go_literal` = el `--go` tal
  cual.
- **D-1.4.3 · Disciplina del flujo (copia `reactiva_campanas.py`).**
  Dry-run por defecto (tabla del plan + excluidos). Mutación exige
  `--acepto-mutacion-real` + `--esperado N` (`len(plan) == N` o aborta
  SIN abrir HTTP ni token) + `--go` literal (se guarda en
  `go_literal`). LIST previo por keyword: vivo != ENABLED → SKIP con
  nota y sigue el lote (distinto de reactiva, que aborta: el plan aquí
  es un reporte vivo y archivar una ya-muerta sería pisar decisión
  ajena — se declara y se sigue). Fila `planeado` + commit ANTES del
  POST. POST `/sp/keywords/delete` `{"keywordIdFilter": {"include":
  ["<id>"]}}` (id STRING, vendor spkeyword v3 en Content-Type Y Accept).
  Readback LIST por id: ARCHIVED → `applied`; distinto (o POST que
  lanza) → `failed` y el lote SE DETIENE. Una línea JSON por mutación
  pasada por `scrub`; reconciliación final. DSNs: `ORBIT_DSN_READ`
  (plan) + `ORBIT_DSN_ADMIN` (ledger). Sin `app.ads.write`/`app.apply`
  (candado `test_architecture`); módulo ≤ 900 líneas.
- **D-1.4.4 · Tests (`tests/test_archiva_inertes.py`, patrón reactiva:**
  conn falsa + AdsClient falso + `httpx.MockTransport` para el POST de
  mutación y el token LWA — "sin HTTP" = transporte sin requests
  grabados). Casos: plan desde la vista con filtros (plataforma,
  clasificación, min-días con NULL que pasa, límite; `product_target`
  excluido con conteo); `--esperado` distinto → aborta sin HTTP; no
  ENABLED en LIST previo → skip con nota y sigue; ledger ANTES del
  HTTP (POST 500 → la fila `planeado` existe y queda `failed`);
  readback ARCHIVED → `applied`; readback distinto → `failed` + break
  (la 2ª keyword sin HTTP); `--reponer` recrea con el `matchType` del
  ledger (PHRASE en el test, para discriminar del EXACT fijo de
  `crear_keyword_exacta`) y sella `repuesto_*`; regla 4 (bid+moneda
  juntos, NULL parejo) y regla 3 (ningún NULL → 0). `test_architecture`
  lo corre el CI (batería completa una vez, regla 4 del plan).
- **D-1.4.5 · `--reponer` también es dry-run por defecto.** Crea en
  Amazon, así que exige `--acepto-mutacion-real` igual que archivar;
  sin el flag solo lista lo que repondría (filas `applied` del lote).
  `--esperado` / `--go` NO aplican a reponer: el anti-typo ahí es el
  nombre del lote (conjunto cerrado de filas `applied`) más el readback
  por id. Fallo en una fila de reposición (sin id en el ack, readback
  distinto, bid NULL) → se declara y el lote SE DETIENE igual que en
  archivar.

**Rojo 1.4** (tests nuevos contra `origin/master`, antes del fix;
el módulo no existe: la colección revienta — rojo honesto de archivo
nuevo; el poder discriminante de cada assert lo pinean el verde + la
pasada de mutación de D-1.4.6):

```text
$ .venv/bin/python -m pytest tests/test_archiva_inertes.py -q
E   ModuleNotFoundError: No module named 'archiva_inertes'
ERROR tests/test_archiva_inertes.py
1 error in 0.09s
```

- **D-1.4.6 · Poder discriminante verificado por mutación en local
  (sin commits, archivo restaurado después).** (1) `matchType` fijo a
  `"EXACT"` en el create → cae `test_reponer_recrea...` (el test siembra
  PHRASE del ledger); (2) gate de readback debilitado a `is None` →
  cae `test_main_readback_distinto_falla_y_detiene_el_lote` (un ENABLED
  ya no detiene). Verde final: `15 passed`. Tres fallos intermedios
  fueron míos, no del diseño (doble lectura de capsys, índice de params
  del INSERT, token antes de validar el lote en `--reponer` — este
  último sí mejoró el módulo: validación sin HTTP antes del token).

- **D-1.4.7 · Adjudicación grok (ronda única del kit: 2 ALTAS + 3 MEDIAS
  + 2 BAJAS).** Cada hallazgo verificado contra el código real ANTES de
  tocar nada; todos ciertos:
  1. ALTA: el CREATE de `--reponer` iba desnudo y `_mutate` envuelve por
     defecto ("objeto desnudo = 400", `write.py:568`). Fix: `_post` gana
     `envolver` (`"keywords"` en el create; el DELETE sigue desnudo,
     sello `borrar_keyword`) + test pineando el shape envuelto. El test
     viejo pineaba el shape roto: el cross-review mordió donde mis tests
     no (lección: el wire sellado se pineaba contra el probe, no contra
     mi suposición).
  2. ALTA: el runbook corría `python tools/...` dentro del contenedor
     pero la imagen solo trae `app/` (`Dockerfile:21`, compose sin
     montes). Fix: runbook por stdin (precedente `reactiva_campanas`)
     en DEPLOY y docstring.
  3. MEDIA (mitigada): reponer sin sello de fallo post-CREATE (fila
     queda `applied`, un reintento duplicaría). Mitigación mínima: los
     eventos de fallo llevan ack + id nuevo + objeto leído para
     conciliar a mano. RESIDUAL DECLARADO: reponer no es idempotente
     entre CREATE y sello; ante `lote_detenido` en reponer, verificar en
     consola lo ya creado antes de reintentar (lo opera el lead, 1.5).
  4. MEDIA: ledger sin CHECK parejo (regla 4 > plan por precedencia;
     precedente `estado_bid_con_moneda`). Fix: CONSTRAINT
     `archivo_bid_con_moneda` en 0014 + assert estático. Se mantiene
     `bid_currency TEXT` del plan (cambio mínimo).
  5. MEDIA: `list_objects` lanza en >=400 (`client.py:286`); el `!= 200`
     era muerto y el readback post-DELETE dejaba `planeado` colgado.
     Fix: `_readback_salvo` (try/except → None) en los 3 readbacks +
     test con `AdsApiError` real.
  6-7. BAJAS: línea de mutación sin ack (fix: `ack` en eventos
     `archivo`/`reponer` + test) y `--go ""` que autorizaba (fix: `if
     not args.go` + test).
  Tests tras adjudicar: 18 passed + 14 de `test_architecture`. Sin
  segunda ronda (tope del repo): los fix van en el commit de
  adjudicación y el lead decide si re-abre.

- **D-1.4.8 · Adjudicación GLM (ronda única del kit: 0 ALTAS, 2 MEDIAS +
  1 MEDIA/VERIFICAR + resto BAJAS; proceso colgado al final y terminado
  a mano, veredicto completo en archivo).** Veredictos:
  1. MEDIA (`_sella` sin try → `planeado` colgado): cierta en el caso
     DB-caída, pero la ventana es mínima y la recuperación es un UPDATE
     manual declarado (misma familia que el residual de D-1.4.7.3); el
     readback post-DELETE ya va por `_readback_salvo`. Sin código nuevo.
  2. MEDIA (readback único sin reintento): diseño sellado (igual que
     `reactiva_campanas` y que el plan §1.4); no se cambia. Cobertura
     operativa: DEPLOY trae la recuperación (`failed` con ARCHIVED en
     consola → UPDATE manual a `applied`).
  3. MEDIA/VERIFICAR (JOIN multiplica si state no es 1:1): REFUTADO con
     evidencia (`ad_entity_id BIGINT PRIMARY KEY`, `0001:645`).
  4. BAJA (lote compartido el mismo día): cierto y por diseño (el lote
     es la unidad); documentado en DEPLOY (un lote por día).
  5. BAJA (reponer sin dedup): misma familia que D-1.4.7.3, ya mitigada
     y con residual declarado; sin dedup por LIST de grupo porque
     `adGroupIdFilter` no tiene precedente sellado (D-1.4.1.3).
  6. BAJA/VERIFICAR (USAGE a tres roles): REFUTADO como desviación (el
     precedente `0002:712` otorga a los tres; se sigue el patrón).
  7. BAJA (match TEXT sin dominio): cierto; fix: CONSTRAINT
     `archivo_match_cerrado` en 0014 + assert estático.
  8. BAJA (`--go ""`): ya corregida en D-1.4.7 (grok la vio primero).
  9. BAJA (bid cuantizado en la reversa): por diseño (presentación
     sellada `write._bid_wire`; el ledger guarda el NUMERIC exacto).
  10. BAJA/VERIFICAR (tests fuera de su diff): artefacto de mi partición
      del diff por el tope de 60KB del kit; grok sí revisó los tests.
  11. BAJA/VERIFICAR (mapa de moneda duplicado): cierto; fix: test que
      pinea `MONEDA_POR_PLATAFORMA == dict(write.PLATAFORMA_MONEDA)`
      (import solo en tests, fuera del candado).
  Tests tras adjudicar: 19 passed + 14 de `test_architecture`.

- **D-1.4.9 · Revisión del lead (PR #134): 4 cambios antes del merge,
  sin nueva ronda.**
  1. `bid_currency TEXT` → `currency` (enum `0001:37`): regla 4 por
     schema, igual que `ad_entity_state.bid_currency`. El INSERT sigue
     con `%s` plano (sin cast): el precedente `_metrica` de
     `test_entidad_inerte.py` inserta `"USD"` string en columna
     `currency` y pasa en CI — unknown → enum coerciona en asignación.
  2. CHECKs de evidencia por estado (la tabla se vuelve append-only de
     hecho salvo el avance de su propia máquina):
     `archivo_evidencia_applied`: `applied` exige `ack` + `readback` no
     nulos; `archivo_evidencia_repuesto`: `repuesto` exige `repuesto_at`
     + `repuesto_external` + `repuesto_ack` (+ `ack`/`readback`
     heredados). `planeado`/`failed` sin requisitos (`failed` puede no
     tener readback si el POST lanzó). Los tres `_sella` del tool ya los
     cumplen (verificado a mano antes de codificar).
  3. Test de la migración contra Postgres real (fixture `_db_ledger`:
     0001 + texto de 0014; patrón `_db_inerte` + skipif de
     `test_schema`): filas válidas por estado + cada inválida revienta
     (`applied` sin ack, `repuesto` sin external, bid sin moneda, match
     fuera de dominio, moneda `'XXX'`). TDD honesto de dos pushes
     (precedente D-1.2.4, sin PG local): commit A solo-tests → rojo
     local (estáticos) + rojo CI (PG); commit B implementación → verde.
  4. `commit()` de la txn de lectura antes del bucle HTTP: en
     `_archivar` tras `_plan_inertes`, en `_reponer` tras el SELECT de
     filas — cierra snapshot/locks antes de la fase larga de red (las
     dos conns solo habían leído hasta ahí). Asserts con el contador
     `commits` de la conn falsa.

**Rojo 1.4-revisión-lead** (commit A solo-tests, local; el PG skipea
sin servidor y da rojo en CI):

```text
$ .venv/bin/python -m pytest tests/test_archiva_inertes.py -q
FAILED test_migracion_0014_crea_el_ledger_con_grants_y_estados
FAILED test_main_dry_run_imprime_el_plan_sin_http
FAILED test_reponer_sin_mutacion_es_dry_run
3 failed, 16 passed, 1 skipped in 2.39s
```
Los 3: la migración vieja trae `bid_currency TEXT` sin CHECKs de
evidencia y el tool no commitea la lectura. El PG skipeado corre en CI
contra la migración vieja (sus `pytest.raises` no muerden).

### 1.2 — Vista + guarda (GLM, rama bids-01-1.2)

- **D-1.2.1 · Plan-vs-código: CUADRA, sin parar.** `v_metric_latest` =
  última observación por (entidad, fecha) (`0001:1159`); `ad_entity`
  tiene `name`/`keyword_text`/`external_id`/`parent_id`, `ad_entity_state`
  el `status` cache, enum `platform` con `amazon_mx/amazon_us/meli`, enum
  de kind con los 5 valores; `_recorre_plataforma` lee
  `evidencia_ad_groups` en TX2 (`app/cycle.py:1316`) y `comunes` alimenta
  a AMBOS caminos; `MOTIVOS_ES_SALUD` importa `ciclo.MOTIVO_*`
  (`app/api_dashboard.py:221`); GRANT-patrón en `0006:782`
  (`TO app_read, app_ingest, app_decide, app_admin`); cabecera-patrón en
  `0007`; tupla pglast literal en `tests/test_cycle.py:1259`.
- **D-1.2.2 · `inertes` NO va en `comunes`.** `comunes` también alimenta a
  `_procesa_grupo` (camino de términos, D3: la guarda NO aplica ahí);
  pasarle `inertes` le exigiría un parámetro que no usa. Se lee el set en
  `_recorre_plataforma` (TX2, junto a la evidencia) y se pasa EXPLÍCITO
  solo a `_procesa_decisora` (`inertes: set[int]`).
- **D-1.2.3 · `_metrica` fija `impressions=None`.** El test de la vista
  necesita hojas CON impresiones recientes (caso "no aparece") y el de
  ciclo una hoja de control con impresiones en ventana. Se extiende
  `_metrica` en AMBOS archivos de test con `impressions=None` por
  default (compatible hacia atrás: todo lo sembrado antes sigue NULL).
  No se toca helper de producción: no hay.
- **D-1.2.4 · Sin Postgres ni Docker en el sandbox: el rojo corre en CI.**
  La regla 4 del plan ("local solo tu archivo, batería UNA vez en CI")
  no deja correr el rojo DB en local. TDD honesto con dos pushes: (1)
  commit SOLO tests → CI rojo (la vista no existe / la guarda no salta);
  log pegado aquí; (2) implementación → CI verde. Desviación declarada
  del "UNA vez": sin PG local no hay otro rojo real; un rojo simulado
  sería teatro (regla 9).
- **D-1.2.5 · Detalles SQL sellados.** `date - date = integer`
  (`dias_sin_impresiones`; NULL si nunca hubo impresión en 90d);
  `w.wm - 14` con `>` deja la ventana wm-13..wm = 14 fechas exactas;
  `platform = %s::platform` como el resto del módulo; GRANT copia la
  línea de `0006`; `COMMENT ON VIEW` explica N-desde-watermark (por qué
  no `now()`), fuente única D2 y ausencia-de-fila = NO inerte. Migración
  sin acentos (aunque `0001` trae alguno, el código va sin).
- **D-1.2.6 · Test de ciclo (discrimina).** Con ancla maestra (max
  08-19): hoja A con 10 fechas 07-20..07-29 (clicks 50, cost 50,
  impressions NULL) + hoja B de control 08-06..08-15 (igual pero
  impressions 100) en grupo/campaña nuevos (no tocan la evidencia
  131/9/30 de la maestra). Código viejo: A decide bid (−12%) → 6
  decisiones; código nuevo: A es `skips.entidad.entidad_inerte == 1`,
  B decide → 5 decisiones.
- **D-1.2.7 · Test de la vista (archivo nuevo
  `tests/test_entidad_inerte.py`).** Fixture `_db_temporal` propio
  (SQL+SQL2+SQL3+texto de `0013`): US con `gasto_sin_ventas`,
  `peso_muerto` y hoja reciente que NO aparece; MX con watermark viejo
  (max 08-09, impresiones 08-07 → NO inerte: 12−10=2 ≤ 14 desde el
  watermark, aunque desde `now()` parezca muerta) + inerte de control.
  Archivo nuevo en vez de `test_schema.py`: la vista es de BIDS 01, no
  del esquema sellado original.

**Rojo 1.2** (commit solo-tests, CI run 33713121511 en PR #133 draft;
ver commit de implementación para el verde):

```text
4 failed, 1030 passed, 1 skipped, 2 warnings in 181.17s
FAILED tests/test_cycle.py::test_ciclo_hoja_sin_impresiones_recientes_es_skip_entidad_inerte
  assert res.decisions_count == 5
  E  assert 6 == 5        <- el codigo viejo SI decide la hoja inerte (-12%):
  DISCRIMINA (regla 9)
FAILED tests/test_entidad_inerte.py::test_vista_clasifica_tres_casos_y_excluye_reciente
FAILED tests/test_entidad_inerte.py::test_vista_cuenta_desde_el_watermark_no_desde_now
FAILED tests/test_entidad_inerte.py::test_vista_exige_enabled_en_hoja_grupo_y_campana
  E  psycopg.errors.UndefinedTable: relation "v_entidad_inerte" does not exist
```
Nada más falló: el resto de la batería (1030) sigue verde sin la guarda.

- **D-1.2.8 · Omisión propia atrapada por el CI (no por el lead): el plan
  §1.2 SÍ pedía aplicar 0013 en el fixture de `test_cycle.py` y no lo
  hice en el commit de implementación → todo ciclo reventó con
  `UndefinedTable` (run 33713521180). Fix: `SQL13` en `_db_temporal` de
  `test_cycle.py` y de `test_cycle_apply.py` (el otro que corre
  `corre_ciclo` de verdad; el CLI lo mockea). Ningún otro fixture corre
  TX2. El plan estaba bien; el error fue mío al implementarlo.**
- **D-1.2.9 · Segunda oleada del mismo CI (run 33713841740, 26 fallos, una
  sola causa): las semillas viejas traen `impressions NULL` y la vista
  las marca inertes.** En producción eso no pasa (las mediciones del lead
  distinguen 183/172 con/sin impresiones: los reportes traen el número;
  NULL con clicks > 0 es artefacto del helper viejo que fijaba
  `impressions=None`). Fix honesto con la forma real del dato (regla 8):
  las siembras de hojas SERVIDAS llevan impressions > 0 (10x clicks) en
  `_siembra_kw_bid`, `_siembra_kw_pause`, `_siembra_guarda`, el helper de
  bloqueo (test_cycle.py) y el helper kw2 (test_cycle_apply.py); lo que
  modela ausencia sigue NULL (mi hoja inerte 1.2, `kw_solo_evidencia`).
  El freeze NO pineaba impressions (`app/cycle.py` no la menciona): los
  goldens no se tocan. Fixtures que corren ciclo y faltaban: también
  `SQL13` en `_db_con_rol_admin` (test_api_write, lo usa goals_write).
  Dashboard/notifica/preflight no corren TX2 con fixture propio sin la
  vista (usan los ya corregidos + maestra).

> **Revisión del lead (PR #133): dos MAJOR por reglas selladas + marker.**
> Se trabajan en esta rama antes del merge (#133 fusiona ANTES que #132).

- **D-1.2.10 · `impressions` NULL = desconocido, no cero (regla 3).**
  `coalesce(sum,0)=0` no distingue "sin filas en 14d" de "filas con NULL".
  Fix en `reciente`: `filas_14d = count(v.ad_entity_id)` (0 sin filas;
  el `count` ignora la fila NULL-extendida del LEFT JOIN) y `nulas_14d =
  count(v.ad_entity_id) FILTER (WHERE v.impressions IS NULL)` (OJO:
  `count(*)` filtrado contaría 1 con cero filas — por eso se cuenta la
  columna, no `*`). Inerte ⇔ `filas_14d = 0 OR (nulas_14d = 0 AND
  impresiones_14d = 0)`: con una sola observación reciente NULL la hoja
  sigue optimizándose (mejor ruido que callar una viva). Verificado por
  el lead: en producción hoy no hay NULLs recientes (0/893 MX, 0/470
  US) — el cambio no mueve ningún conteo vivo, solo sella la semántica.
  Tests viejos intactos (ninguno mete NULL reciente esperando inerte: la
  hoja A del test de ciclo y las de la vista tienen sus NULLs FUERA de
  la ventana). Test ROJO nuevo: hoja con observación reciente
  `impressions=NULL, clicks>0` NO aparece (+ control con `impressions=0`
  reciente que SÍ aparece).
- **D-1.2.11 · `gasto_90d` con `moneda` (regla 4).** `historia` expone
  `mon_min_90d/mon_max_90d` y la vista devuelve `moneda = (min si min =
  max, si no NULL)` y `gasto_90d = NULL si hay mezcla, si no la suma`
  (cero filas → 0, como antes: los tests de `peso_muerto` lo pinean). La
  clasificación usa las sumas CRUDAS (el lead: no depende de la moneda).
  Mezcla imposible por inserts normales (trigger `metric_moneda_sellada`):
  el test ROJO de mezcla deshabilita el trigger en la DB temporal (es
  dueño) y lo rehabilita; el de moneda única aserta `"USD"`.
  `ordenes_90d` no lleva moneda (conteo, no dinero).
- **D-1.2.12 · Marker 1.2** → «PR #133, CI verde».
- **D-1.2.13 · Cross-review grok (única ronda; codex sin cuota en su
  cuenta → bloqueado, y sin ALTA no hay segunda por tope): APRUEBA CON
  MENORES, 0 ALTA.** Menores 1.2 aplicados aquí (solo tests, sin delta
  de código: pinean comportamiento existente): (3) la hoja de gasto del
  DoD lleva `impressions=50` real en su fila 07-30 → pinea
  `dias_sin_impresiones == 17` (antes NULL; el camino `date - date` con
  impresión real no estaba cubierto) y la semilla ya es forma de
  reporte; (4) borde N en el test de watermark MX: impresión en wm−14
  (07-26) → inerte, en wm−13 (07-27) → viva (`>` vs `>=` quedaría
  atrapado). La BAJA 5 (control cero-explícito con cost 5 no es forma
  SP) se deja como test puro del predicado SQL: el camino vivo
  (`filas_14d=0`) lo cubre el test de ciclo.

**Rojo 1.2-revisión** (commit solo-tests c5ca327, CI run 33716113970):

```text
5 failed, 1031 passed, 1 skipped, 2 warnings in 102.03s
FAILED test_vista_clasifica_tres_casos_y_excluye_reciente
FAILED test_vista_cuenta_desde_el_watermark_no_desde_now
FAILED test_vista_exige_enabled_en_hoja_grupo_y_campana
FAILED test_vista_impressions_desconocido_no_es_cero
FAILED test_vista_mezcla_de_monedas_anula_gasto_y_moneda
  E  psycopg.errors.UndefinedColumn: column "moneda" does not exist
```
Los 5 son la columna nueva ausente (el SELECT la pide); la discriminación
semántica (nula-reciente ausente vs cero-explícito presente) la pinean los
mismos tests en verde + la tabla de verdad del predicado verificada en
local (`filas=0→inerte; nulas>0→activa; suma=0 conocida→inerte`).

### 1.1 — Regla A' (GLM, rama bids-01-1.1)

- **D-1.1.1 · Plan-vs-código: CUADRA, sin parar.** `decide_bid` ya recibe
  `cost_min` resuelto y congela `cost_min_usado`; `_corte_json` ya congela
  `expected_clicks` como string (`app/cycle.py:517`); `corte_pause` es
  `UmbralResuelto` con `.expected_clicks` (`app/optimizer/cortes.py:109`);
  el replay vive en `app/optimizer/replay.py::_replay_bid` (donde el plan
  dice); `_PRIORIDAD_BANDA` en `app/apply.py:454` y
  `MOTIVOS_ES_DECISIONES` en `app/api_dashboard.py:187` existen. Desviación
  menor YA PREVISTA por el plan (§1.1: "Si `_decide` no acepta
  `umbral_pause`..."): el helper `_decide` de `tests/test_optimizer_bid.py`
  no acepta `umbral_pause`/`expected_clicks`/`cost_min` como Decimal — se
  extiende con defaults (`umbral_pause: int | None = None` →
  `b.LEGACY_PAUSE`), no se para.
- **D-1.1.2 · La regla compara contra el piso RESUELTO.** `decide_bid`
  resuelve `costo_piso = cost_min or PAUSE_COST_MIN[platform]` antes de las
  bandas; `_factor_cero_ventas` recibe ese `costo_piso` (el MISMO que usó el
  pause, regla 2 — una sola fuente). La guarda `cost_min is None → None` se
  conserva dentro de la función pura por regla 3 (insumo faltante = no
  aplica), aunque el camino vivo nunca le pasa None.
- **D-1.1.3 · `clicks` es `int | None`** (`windows.AgregadoMetricas:175`):
  la comparación es `Decimal(agregado.clicks) >= expected_clicks`, exacta,
  inclusiva (>=), sin dividir — igual que el esqueleto del plan.
- **D-1.1.4 · `apply.py` NO importa `bid`** (solo `goals`; el write client
  sí): `_PRIORIDAD_BANDA` gana el literal
  `"banda_menos_25_cero_ventas": 0` sin import nuevo (diff mínimo; el
  vocabulario cerrado lo pinean los tests literales). Caso del plan
  ("si el módulo ya importa bid") no aplica.
- **D-1.1.5 · Etiqueta ES sin acento y con `clicks`.** El plan trae
  «clics»; el repo exige sin acentos/ñ en el código y los labels vecinos
  usan `clicks` ("Pausa: sin ventas con clicks y costo sobre el umbral"):
  `bid.MOTIVO_BANDA_MENOS_25_CERO_VENTAS: "Cero ventas con los clicks de
  una venta y gasto sobre el piso: -25%"`.
- **D-1.1.6 · Test de ciclo 1.1.** Grupo nuevo con evidencia elegible
  (72 clicks / 3 órdenes / 24 fechas → `expected_clicks = 24`) y hoja de
  cero ventas que en SU ventana (ancla propia) trae clicks/cost sobre los
  umbrales; donante de evidencia SIN state (precedente `kw_solo_evidencia`).
  Aserta `motivo = banda_menos_25_cero_ventas`,
  `inputs.corte.expected_clicks == "24"` y
  `ciclo.reproduce(inputs) == (kind, new_value, moneda)`. Los goldens
  viejos no cambian (sus hojas tienen ventas o no alcanzan expected).

**Rojo 1.1** (tests nuevos contra `origin/master`, antes del fix; ver
commit para el verde):

```text
$ .venv/bin/python -m pytest tests/test_optimizer_bid.py -q -k "cero_ventas or pause_gana"
E       TypeError: decide_bid() got an unexpected keyword argument 'expected_clicks'
tests/test_optimizer_bid.py:101: TypeError
FAILED test_precedencia_pause_gana_a_banda_menos_25  (usa _decide: el helper
  ya pasa expected_clicks; verde de nuevo tras el fix — no es regresion)
FAILED test_cero_ventas_con_clics_esperados_y_gasto_sobre_piso_baja_25
FAILED test_cero_ventas_un_click_bajo_los_esperados_sigue_menos_12
FAILED test_cero_ventas_gasto_bajo_el_piso_sigue_menos_12
FAILED test_cero_ventas_sin_expected_clicks_no_aplica
FAILED test_cero_ventas_orders_o_revenue_desconocidos_no_aplica
FAILED test_pause_gana_sobre_la_regla_de_cero_ventas
7 failed, 38 deselected in 0.20s

$ PYTHONPATH=. .venv/bin/python /tmp/rojo11.py   (regla 9: poder discriminante)
codigo viejo: bid banda_menos_12 -0.12 0.8800
DISCRIMINA: el codigo viejo da -12% donde el test 1 exige -25% + motivo nuevo
```

- **DESV-1.1.1 · El test 1 del plan (§1.1) NO CUADRA con el código real —
  PARADO a pregunta del lead (regla 6).** El plan pasa `_cortes(cost=45,
  clicks=120, orders=0)` SIN `umbral_pause`, o sea con el default vigente
  `LEGACY_PAUSE = 100`: `120 >= 100 ∧ 45 >= 40 ∧ orders == 0` → **PAUSE
  dispara** (`decide_bid` lo evalúa ANTES que las bandas, sellado). El test
  aserta `kind == "bid"` + motivo nuevo: **fallaría ANTES y DESPUÉS del
  fix** (el pause roba la decisión en ambos mundos) → test que no
  discrimina, viola la regla 9. El propio rojo esperado del plan
  (`'banda_menos_12' != 'banda_menos_25_cero_ventas'`) prueba que el autor
  quería el camino de BANDAS puro (con pause, el motivo viejo sería
  `pause_umbral`, no `banda_menos_12`). Corrección propuesta (mínima, fiel
  a la intención): test 1 con `cortes=None` — la ASIMETRÍA INTENCIONAL del
  motor (`bid.py`: cortes ausente NO impide bandas sobre bids) lo ampara, y
  los tests 2–5 del plan ya usan `cortes=None`. Con `cortes=None`: código
  viejo → `banda_menos_12` (rojo exacto del plan); código nuevo → `-25%`
  con motivo nuevo (verde). **Sin veredicto del lead no hay código.**
  (Tests 2–6 verificados a mano contra `bid.py`: con `cortes=None` dan el
  rojo/verde esperado; el test 6-pause con `umbral_pause=180` cuadra.)
- **Veredicto del lead (2026-09-03): `cortes=None`.** Se aplica la
  corrección propuesta; el test 1 queda en camino de bandas puro.
- **D-1.1.7 · El CI del PR (run 33712909099) dio `decisions_count == 6`
  (esperaba 5); el volcado mostró `(donante, bid, banda_menos_25)` y la
  hoja nueva en `-12%`. Causa (modelo mental mío, no bug del motor): las
  ventanas de DECISIÓN anclan en el `max(metric_date) DE LA ENTIDAD`
  (`_ventanas_metricas`, ORBIT 03 punto 8) — no en el watermark global
  (ese solo da frescura y la ventana de EVIDENCIA sí es global
  D-90..D-10). El donante (junio) decidía un −25% clásico en su propia
  ventana y mi hoja solo metía 17 clics en la suya (< 21). Fix del test:
  donante SIN state (aporta evidencia, skipea `estado_no_enabled`) y
  clics/costo de la hoja concentrados en su ventana propia (26 ≥ 24,
  42.00 ≥ 40; división exacta 72/3). Lección para sembrar: cada hoja
  decide en SU ancla; el watermark global solo ordena evidencia y
  frescura.**
- **D-1.1.11 · Cross-review grok (misma ronda única): 2 menores 1.1
  aplicados aquí (solo tests, sin delta de código): (1) el motivo nuevo
  entra a `test_orden_bids_prioridad_de_hemorragia_sellada` (banda 0,
  cost DESC; un typo en el literal caería al final); (2) el test
  pause-gana pinea `(kind, motivo)`. La BAJA 2b (4 guards pasarían sin
  `_factor_cero_ventas`) es por diseño: son guards, el que discrimina es
  el test 1 + ciclo + replay pre-BIDS (veredicto grok: APRUEBA CON
  MENORES, 0 ALTA).**

> **Revisión del lead (PR #132): 1 MAJOR (fidelidad del replay) + 2
> menores + marker + filas de secuencia.**

- **D-1.1.8 · Marcador congelado `cero_ventas_expected_usado` (regla
  4.4).** El lead midió 17 decisiones US que cambiarían (ej. 1994 del
  live 33): `expected_clicks` se congela desde CORTES 01 en TODAS las
  bids, así que leerlo en el replay contamina filas pre-BIDS. Fix mínimo
  (propuesta del lead, se adopta tal cual): `_pendiente_bid` congela
  ADEMÁS `inputs.corte.cero_ventas_expected_usado` (string Decimal o
  null = exactamente lo consumido; `expected_clicks` queda informativo)
  y `_replay_bid` pasa `expected_clicks` SOLO desde esa clave (ausente o
  null → None). Filas viejas sin la clave rejuegan −12% como persistido;
  filas nuevas la traen siempre (null = grupo sin evidencia = la regla
  no aplicó, fiel por construcción). Sin sentinel en `_corte_json`: el
  camino negative no consume decide_bid, así que `_pendiente_bid` añade
  la clave DESPUÉS de llamar a `_corte_json` (una línea; el freeze de
  negative queda idéntico). Test ROJO obligatorio (puro, corre en
  local): fila pre-BIDS con `expected_clicks` no nulo + cero ventas
  sobre umbrales, SIN la clave nueva → `reproduce` devuelve el
  persistido (−12%), no −25%.
- **D-1.1.9 · Menores.** (a) Pinear `r1`/`r2` completos en
  `test_cero_ventas_orders_o_revenue_desconocidos_no_aplica`:
  `r1 == ("bid", "banda_menos_12", -0.12)`,
  `r2 == (None, "acos_desconocido", None)` (verificado contra `bid.py`:
  revenue None → `ACOS_DESCONOCIDO`). (b) El test de ciclo distingue la
  lectura congelada: copia de `ins` con la CLAVE NUEVA en `"33"` (> 32
  clics de la hoja en su ventana) → `reproduce` devuelve `("bid", 0.88,
  "USD")` (−12%), no el −25%. (c) Marker 1.1 → «PR #132, CI verde».
- **D-1.1.10 · Filas de secuencia (aviso del lead en #132-4).** #133
  fusiona antes: con la guarda, la hoja kw (impressions NULL hasta
  07-27) sería inerte. Se siembran YA filas 08-06..08-12 con
  `impressions=10, clicks=0, cost=0` (cero aporte: expected sigue 24,
  72/3/31): pre-merge el test sigue en 5 (ancla propia pasa a 08-12;
  verificado en CI) y post-merge la hoja no es inerte. Requiere extender
  `_metrica` con `impressions=None` también en esta rama (igual que
  D-1.2.3; compatible hacia atrás).

**Rojo 1.1-revisión** (test puro obligatorio en local; DB en CI):

```text
$ .venv/bin/python -m pytest tests/test_cycle.py -q -k "pre_bids"
E  AssertionError: assert ('bid', Decimal('0.750000'), 'USD') == ('bid', Decimal('0.88'), 'USD')
E  At index 1 diff: Decimal('0.750000') != Decimal('0.88')
FAILED test_replay_pre_bids_con_expected_congelado_no_aplica_regla_nueva
```
El replay viejo lee `expected_clicks` (120 ≥ 120, 45 ≥ 40) y rejuega −25%
(0.75) sobre una fila persistida en −12% (0.88): exactamente las 17
decisiones que midió el lead. El pin `r1/r2` ya pasa (es pin, no rojo).

### 2.1 — Sellar la edad (rama bids-01-2-1)

- **D-2.1.1 · Columna, no derivación.** `ALTER TABLE ad_entity ADD COLUMN
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()` (migración 0017). El
  DEFAULT permanente cumple tres funciones: backfill de las existentes a
  la hora de la migración (una sola txn → un solo valor), red para seeds
  de test que insertan directo (nacen "vistas ahora", honesto), y red para
  cualquier escritor futuro que olvide la columna. El upsert de
  `structure.py` fija `first_seen_at = now()` SOLO en el INSERT; el
  `DO UPDATE SET name` ni la nombra → jamás se pisa por sync. Sin GRANTs
  nuevos (la columna hereda los de la tabla). COMMENT declara que el
  backfill es un PISO, no la verdad (las 133 sin métricas podrían ser más
  viejas).
- **D-2.1.2 · El filtro vive en el plan, con fecha UTC fijada.** `_SQL_PLAN`
  suma `AND e.first_seen_at <= (now() AT TIME ZONE 'UTC')::date - %s`
  (precedente `v_metric_mature`: UTC en la expresión, nunca
  `CURRENT_DATE` de sesión). N por CLI `--min-antiguedad-dias` (default
  30, mismo molde que `--min-dias-sin-impresiones`). La exclusión se
  REPORTA: `_SQL_EXCLUIDOS_JOVENES` cuenta keywords que pasan todo menos
  la edad, y el dry-run las imprime (una exclusión invisible es trampa de
  soporte). La línea del plan muestra `edad=Nd`.
- **D-2.1.3 · `--reponer` no filtra por edad** (trabaja del ledger, no de
  la vista). El filtro solo toca el camino de archivo.
- **D-2.1.4 · Tests contra Postgres de verdad** (proceso: la `_ConnFalsa`
  no ejecuta SQL). Tres rojos + backfill: (1) keyword inerte insertada
  "hoy" NO entra al plan (joven contada aparte); (2) con `first_seen_at`
  viejo SÍ entra; (3) sync real DOS veces (`sync_structure` con el mismo
  payload) deja `first_seen_at` idéntico; (4) migración sobre 0001 con
  fila previa → la fila amanece con `first_seen_at NOT NULL` (≈ hora de
  la migración, PISO). Estático: la 0017 parsea, trae COMMENT de piso y
  no nombra la columna en ningún UPDATE.

### 2.2 — Candado del dedupe + carrera del apply (rama bids-01-2-2)

- **D-2.2.1 · El brief cuadra con el código: NO se para.** Verificado en
  `origin/master` antes de programar: (1) `_SQL_KEYWORDS_CAMPANA`
  (`app/optimizer/hygiene.py:375-385`) lee `ad_entity` sin JOIN a
  `ad_entity_state` ni filtro de estado — la archivada está en el set;
  (2) `_SQL_MARCAR_ARCHIVADOS` (`app/ads/structure.py:213-222`) es
  `UPDATE ad_entity_state` (la fila de `ad_entity` se conserva) y filtra
  `e.kind = 'product_ad'` — ni cubre keywords; (3) `apply_harvest._identidad`
  (`app/apply_harvest.py:446-447`) salta `ARCHIVED` como ausente, sellado
  por el probe 2.5 en su docstring; (4) `keyword_archivo_manual` (0014)
  trae `platform/ad_group_external/keyword_text/match_type/estado/
  repuesto_at` — todo lo que el chequeo necesita, sin migración.
- **D-2.2.2 · Alcance de (a): candado a dos niveles.** Test de higiene:
  una EXACT archivada en destino SÍ sale en `keywords_campana_destino`.
  Test de ciclo: el término harvestable con su texto archivado NO se
  decide y cae `skips.termino.harvest_duplicado = 1`. El rojo se produce
  con la "mejora" literal que abriría el bucle (JOIN a `ad_entity_state`
  + `status = 'ENABLED'` en `_SQL_KEYWORDS_CAMPANA`) y el log muestra el
  harvest duplicado decidido. Docstring de la función con el porqué (la
  protección es ciega al estado A PROPÓSITO).
- **D-2.2.3 · Alcance de (b): el ledger manda donde se decide crear.** En
  `_paso_keyword`, tras el miss de `_identidad` y ANTES de bid/ledger/POST:
  si hay fila en `keyword_archivo_manual` con `platform` + mismo ad group
  + mismo texto + `match_type = 'EXACT'` (la identidad de `_identidad`;
  PHRASE archivada NO bloquea un EXACT, coherente con (a)) +
  `estado = 'applied'` + `repuesto_at IS NULL` → NO hay POST y se cierra
  con `_falla_job` y motivo nuevo. Comparación de texto con
  `lower+btrim` en SQL, coherente con la normalización del dedupe de
  decisión. `_identidad` y `_solo_en_otro_ad_group` NO se tocan (sellados
  para reconciliación).
- **D-2.2.4 · Cierre con `_falla_job`: declarado y terminal.** `failed`
  solo lo conducen las fases en vuelo (`fase IN (pending,
  negative_created, exact_created)`); nada re-conduce un `failed`, así que
  el cierre es estable y el chequeo lo hace idempotente ante reintentos
  manuales. La alerta de Telegram es lo correcto aquí (no ruido): archivar
  con un job en vuelo es anomalía operativa accionable. Motivo nuevo
  `archivado_en_vuelo` (constante `MOTIVO_ARCHIVADO_EN_VUELO`).
- **D-2.2.5 · Lo que NO se hace.** Sin cambios en `cycle.py` (la decisión
  ya está bien), sin migración, sin tocar el camino negative ni el
  readback. `repuesto_at` con valor = la reversa ya repuso → el chequeo
  NO bloquea (el harvest puede recrear tras reposición deliberada).

### Revisión del lead sobre el PR #155 (2026-09-04)

**Verificación del diagnóstico antes de revisar el código.** El hook
`context-docs-budget` que GLM declaró como ambiental falla IGUAL en
`origin/master` (`Executable python not found`: es la máquina del lead, no el
repo). Declaración honesta, confirmada.

**Auditoría del poder discriminante** (cuatro mutaciones, las cuatro
cazadas): (1) excluir archivadas del dedupe → rojo en DOS niveles
(`test_keywords_campana_destino_incluye_archivadas` unitario y
`test_ciclo_harvest_no_duplica_texto_archivado_en_destino` de ciclo completo);
(2) quitar el chequeo del ledger en el apply → rojo; (3) quitar la cláusula
`repuesto_at IS NULL` → rojo en el pin de la reposición deliberada; (4)
posición del chequeo verificada estáticamente: va DESPUÉS de `_identidad` y
de la rama de reconciliación, antes del POST. `_identidad` intacta.

**Adjudicación de CodeRabbit (2, ambos ACEPTADOS y corregidos por el lead):**

1. **Major · la carrera seguía abierta.** El chequeo filtraba
   `estado = 'applied'`, pero el archivador commitea **`planeado` ANTES del
   DELETE** y solo sella `applied` tras el readback: en esa ventana (el ida y
   vuelta HTTP) el harvest no veía nada y creaba el duplicado —justo lo que
   la tarea venía a cerrar—. **Arreglo**: bloquea CUALQUIER fila no repuesta,
   sin filtrar estado. Es seguro porque el chequeo solo se alcanza con
   `_identidad` ya fallida (la keyword no está viva en el destino): si el
   archivo se quedó en `planeado`/`failed` pero la keyword sigue viva,
   `_identidad` la encuentra y reconcilia sin llegar aquí. **De paso cierra
   el residual H3 de grok** (archivada en Amazon con el sello sin promover).
   El estado viaja al detalle del cierre. Regresión propia con fila
   `planeado`, verificada por mutación.
2. **Minor · guard fail-closed de Postgres** en el test nuevo de hygiene
   (`_postgres_obligatorio_ausente`), como el resto del archivo: sin driver,
   CI falla en vez de saltar en silencio.

Suite completa local tras las correcciones: **1158 passed**.

**Rojos 2.2** (contra `origin/master` + tests nuevos, antes del fix;
Postgres local):

```text
ROJO (a) — _SQL_KEYWORDS_CAMPANA con JOIN ad_entity_state + status='ENABLED':
>  assert harvests == [], "el texto archivado NO se vuelve a cosechar"
E  AssertionError: el texto archivado NO se vuelve a cosechar
E  assert [(2, 'harvest', 'buena yarda', None, Decimal('0.7500'), 'USD', ...)] == []
FAILED test_ciclo_harvest_no_duplica_texto_archivado_en_destino
(restaurado el SQL: 2 passed — el candado verdea)

ROJO (b) — sin chequeo del ledger (job negative_created + ARCHIVED en
Amazon + fila applied sin repuesto):
>  assert resumen.jobs_failed == 1 and resumen.jobs_done == 0
E  assert (0 == 1)
E  + where 0 = ResumenReconciliacion(jobs_done=1, jobs_failed=0, ...).jobs_failed
FAILED test_matriz_keyword_archivada_en_vuelo_cero_post_y_cierre_declarado
(jobs_done=1 = el POST duplico lo archivado; con el fix: failed +
alerta archivado_en_vuelo + cero POSTs)

PIN (b2) — sin `AND repuesto_at IS NULL`:
FAILED test_matriz_archivo_repuesto_no_bloquea_harvest
(restaurada la clausula: pasa — la reposicion deliberada NO bloquea)
```

## Cross-review del lote de archivo (grok, 2026-09-04) — VEREDICTO: NO CORRERLO

Pedida por el dueño antes de autorizar el lote real de 160 keywords.
Veredicto de grok: **«no correrlo»**. **El lead lo comparte y el lote queda
BLOQUEADO** hasta que se arreglen los puntos de abajo. Razón de fondo del
lead, además de los hallazgos: **el beneficio medido del lote es CERO pesos**
— las 160 `peso_muerto` gastaron **0** en 90 días (el gasto de las inertes
vive en `gasto_sin_ventas`: 248 MXN y 54 USD, que son 8 keywords, no 160).
Archivar es higiene, no ahorro; el riesgo, en cambio, es real e irreversible.

**H1 (alta) · `dias_sin_impresiones IS NULL` = "infinitamente muerta".**
`ad_entity` **no tiene `created_at`**, así que no hay forma de distinguir una
keyword recién creada de uso una que lleva años sin servir. Medido:
**133 de las 166 candidatas MX no tienen NI UNA fila de métricas jamás**;
las 33 con historia dejaron de imprimir el 2026-08-15. Hoy el riesgo es
teórico (`harvest_job` done = **0**: el harvest nunca ha corrido), pero se
vuelve trampa viva en cuanto aterrice ORBIT 05 · 2.3: harvest crea un EXACT
que convierte → a las 24 h es `peso_muerto | dias=None` → el tool lo archiva.

**H2 · VERIFICADO POR EL LEAD Y ACOTADO (grok lo exageró).** grok afirmó
que «el harvest recrea lo archivado» y cierra un bucle. Medido en el código:
**el camino de DECISIÓN ya está protegido**. El dedupe de harvest
(`hygiene.keywords_campana_destino` → `decide_hygiene.keywords_existentes` →
skip `harvest_duplicado`) lee `ad_entity` **sin filtrar por estado**, y una
keyword archivada CONSERVA su fila en `ad_entity` (el sync solo marca
`ad_entity_state`, y su `_SQL_MARCAR_ARCHIVADOS` ni siquiera cubre keywords:
es solo para `product_ad`). Así que el ciclo NO va a decidir un harvest nuevo
para un texto ya archivado. Lo que sí queda expuesto son dos cosas más
pequeñas:

- **(a) Carrera del camino de APLICACIÓN.** `apply_harvest._identidad` sí
  trata `ARCHIVED` como ausente (sellado por el probe 2.5, con razón para su
  caso). Un job de harvest YA en vuelo cuya keyword se archive entre la
  decisión y el apply volvería a hacer POST. Ventana estrecha pero real.
- **(b) La protección es ACCIDENTAL.** Nada documenta ni prueba que el
  dedupe deba seguir siendo ciego al estado. Una «mejora» razonable
  —excluir archivadas de `keywords_campana_destino`— abriría el bucle que
  grok describe. Sin un candado, esto se rompe solo con el tiempo.

**H3 (alta) · ventana post-HTTP sin sello.** El ledger inserta `planeado`,
sale el POST y luego sella `applied`/`failed`. Un kill, un timeout con el POST
ya enviado, o un LIST que aún no confirma dejan la fila sin `applied` — y
`--reponer` **solo lee `applied`**. Esa keyword queda archivada en Amazon y
fuera de la reversa.

**H4 (media-alta) · `--esperado N` fija el CONTEO, no el conjunto.** Entre el
ensayo y el go, `v_entidad_inerte` puede cambiar (watermark, ingesta, sync de
status): si salen 3 y entran 3, N sigue siendo 160 y se archivan OTRAS.

**H5 (alta) · `--reponer` NO es una reversa.** Amazon no des-archiva: el
`--reponer` **crea otra keyword** con `keywordId` NUEVO, **ENABLED** (gastando
desde el minuto uno), sin la historia de métricas ni el ranking interno de la
identidad muerta; `ad_entity.external_id` es inmutable, así que el motor sigue
apuntando al id muerto. Además `--reponer` no exige `--esperado` ni `--go`, y
su readback no verifica bid ni campaña. **Precedente del propio repo**:
`app/ads/archivar.py` repone product ads en **PAUSED**, justo para no empezar
a gastar. La "reversa" que el lead le describió al dueño es más débil de lo
anunciado: corregir esa descripción es parte del arreglo.

**H6 · falsa alarma, verificada por el lead.** grok leyó el árbol principal
(`/Users/dn/dev/goncloud-Orbit`, desactualizado porque el lead trabaja en el
worktree de papeleo) y vio el SQL sin el cast. En `origin/master` el cast
está (3 ocurrencias) desde el PR #151. El punto de fondo de grok sigue siendo
bueno: verificar QUÉ archivo entra al contenedor antes de un go.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 2.1 | **Sellar la edad de las entidades** (H1). **Medido por el lead 2026-09-04: hoy la edad es INDERIVABLE**, así que la redacción anterior de esta tarea («derivar de la primera métrica o del ingest_run») era irrealizable — `ad_entity` **no tiene ninguna columna de tiempo**; `ad_entity_state.synced_at` se **sobrescribe** en cada sync (3 fechas distintas en toda la tabla, la más vieja 2026-08-31: es «último visto», no «primero visto»); el historial de `ingest_run` de estructura solo llega al 2026-08-22; y **133 de las 166 candidatas MX no tienen NI UNA métrica** de la cual inferir nada. Trabajo real: migración que añade `ad_entity.first_seen_at timestamptz` **poblada en el INSERT y JAMÁS en el UPDATE** (el upsert de `structure.py` ya distingue la fila nueva con `(xmax = 0) AS es_nueva`), backfill honesto de las existentes a la fecha de la migración con un COMMENT que declare que es un piso, no la verdad; y la herramienta excluye del plan toda hoja con `first_seen_at` posterior a `hoy - N` (N configurable, default 30). Así el caso que de verdad importa —una keyword recién creada por harvest— queda protegido desde el día uno. `[tdd:required]` | Rojo: keyword insertada hoy por el sync NO entra al plan; una con `first_seen_at` viejo SÍ; el UPDATE del upsert no pisa `first_seen_at` (test que corre el sync dos veces y compara) | - | cc:TODO |
| 2.2 | **Candado del dedupe + carrera del apply** (H2 acotado por el lead): (a) test que CLAVA que `keywords_campana_destino` incluye keywords archivadas —es lo que hoy impide el bucle archivar→recrear, y hoy es accidental: nada lo documenta ni lo prueba—, con el porqué en el docstring; (b) `apply_harvest` no recrea una identidad con fila `applied` en `keyword_archivo_manual` sin `repuesto_at`, cerrando la carrera del job en vuelo. `[tdd:required]` | Rojo (a): quitar del dedupe las archivadas hace que el ciclo decida un harvest duplicado. Rojo (b): job en vuelo cuya keyword se archivó → cero POST y cierre declarado. Ambos verdes con el fix; suites de hygiene, cycle y apply_harvest completas | - | cc:完了 candado + carrera (PR, CI verde) |
| 2.3 | **Reconciliación del ledger** (H3): `--reconciliar` que cruza `planeado`/`failed` contra el LIST real de Amazon y promueve a `applied` lo que ya está `ARCHIVED`; `--reponer` lo incluye. `[tdd:required]` | Rojo: fila `failed` cuya keyword está ARCHIVED en Amazon se recupera y es reponible | - | cc:TODO |

### 2.3 — Reconciliación del ledger (rama bids-01-2-3; base origin/master sin 2.1: no la toca)

- **D-2.3.1 · `--reconciliar [--lote X]` solo lee Amazon y solo escribe el ledger.** Por cada fila `planeado`/`failed` (del lote dado o de todos): LIST por `keywordIdFilter`; si `state == ARCHIVED` → UPDATE a `applied`; si sigue viva (ENABLED/PAUSED/otra) → intacta (el DELETE no se aplicó: queda pendiente visible, re-correr el archivo la retoma); si el LIST no responde → intacta y se REPORTA. Nunca DELETE ni CREATE desde este camino. Es idempotente y re-corrible: solo mira pendientes.
- **D-2.3.2 · La promoción carga evidencia honesta.** El CHECK `archivo_evidencia_applied` exige `ack` + `readback_estado` NOT NULL: se fijan `readback_estado = 'ARCHIVED'` y `ack = {"fuente": "reconciliar", "state": "ARCHIVED", ...}` con lo leído del LIST. JAMÁS se fabrica el ack del DELETE: el ledger distingue "confirmado por readback del archivo" de "recuperado por reconciliación".
- **D-2.3.3 · `--reponer` con mutación real reconcilia su lote ANTES del SELECT `applied`.** Las filas recuperadas entran a la reversa en la misma corrida (H3 pedía exactamente eso: lo archivado fuera de la reversa). El dry-run de `--reponer` NO reconcilia: cero escrituras sin `--acepto-mutacion-real`. Si alguna fila del lote queda sin verificar (LIST caído), el reponer aborta fail-closed antes de crear nada.
- **D-2.3.4 · Tests con fakes + uno real.** El LIST va por `AdsClient` falso con colas por keyword (patrón del archivo) y el ledger por `_ConnFalsa`: rojo = fila `failed` + LIST ARCHIVED → `applied` y `_SQL_REPONER` la trae; viva → intacta; LIST caído → intacta + aborto. Un test contra Postgres de verdad ejecuta el UPDATE de promoción (el CHECK de evidencia muerde si falta ack/readback) y el `SELECT` de pendientes.
| 2.4 | **Autorizar por identidad, no por conteo** (H4): `--ids-file` o hash ordenado de `external_id` del ensayo; el go aborta si el conjunto cambió. `[tdd:required]` | Rojo: mismo N con un conjunto distinto → aborta | - | cc:TODO |

### 2.4 — Autorizar por identidad (rama bids-01-2-4; base origin/master sin 2.1/2.3: no las toca)

- **D-2.4.1 · Hash, no ids-file.** Huella = sha256 hex (completa, sin truncar) de `platform:external_id` ordenadas, unidas con `\n`. Se incluye la plataforma porque `external_id` solo es único con `(platform, kind)`: el mismo id numérico puede existir en MX y US. Un solo literal en el CLI, nada de archivos sueltos que se pierden entre el ensayo y el go.
- **D-2.4.2 · El dry-run publica la huella.** Se imprime `huella del conjunto: <hex>` y viaja en el evento `dry_run`: el dueño la pega en `--huella` del go. `--esperado N` SE MANTIENE (defensa en profundidad: conteo + conjunto).
- **D-2.4.3 · Verificación temprana y fail-closed.** En modo real `--huella` es obligatoria (ausente = aborta) y se compara ANTES de credenciales, token y cualquier HTTP: si difiere, aborta mostrando esperada vs calculada, sin haber tocado nada.
- **D-2.4.4 · Tests con fórmula local.** Helper del test que replica la fórmula (pin: si el módulo la cambia sin querer, caen); rojo = mismo N con conjunto distinto → `Abortar` con cero HTTP; los argv de mutación existentes se actualizan con su huella.
| 2.5 | **La reposición no gasta** (H5): `--reponer` crea en **PAUSED** (precedente `archivar.py`), exige `--go`, verifica bid y campaña en el readback, y registra el `external_id` nuevo. Documentar que el archivo de Amazon es **irreversible en la práctica**. `[tdd:required]` | Rojo: la repuesta nace PAUSED con bid y campaña verificados | - | cc:TODO |

### 2.5 — La reposición no gasta (rama bids-01-2-5; base origin/master sin 2.1/2.3/2.4: no las toca)

- **D-2.5.1 · PAUSED al crear, como `archivar.py`.** El CREATE de `--reponer` lleva `"state": "PAUSED"` (razón del precedente: repuesto en ENABLED empieza a gastar solo; que lo encienda un humano). El readback exige `state == PAUSED`.
- **D-2.5.2 · `--go` obligatorio en modo real.** D-1.4.5 decía que `--esperado`/`--go` no aplicaban a reponer; H5 lo revierte para `--go`: recrear keywords es mutación real y lleva el literal del dueño (el dry-run no lo exige). `--esperado` sigue sin aplicar (el conjunto lo fija el lote del ledger, no un conteo).
- **D-2.5.3 · Readback verifica bid y campaña.** Además de texto+match+grupo: `str(campaignId)` igual al del ledger (`campaignId` viene string o INT en el LIST: comparar por str, precedente `_obj_kw`/snapshot) y `Decimal(str(bid))` igual al bid del ledger cuantizado a 2 (el wire se envía así; conversión vía str, precedente `_bid_decimal`). Si el LIST no trae bid, no cuadra: fail-closed (creamos MANUAL con bid explícito; si Amazon no lo devuelve, no se sella).
- **D-2.5.4 · Docs honestos en la herramienta.** Docstring del módulo + help de `--reponer`: Amazon NO des-archiva; archivar es irreversible en la práctica y reponer CREA otra keyword PAUSED sin historia ni ranking. El `external_id` nuevo ya se registra (`repuesto_external`): se pinea con test.
- **D-2.5.5 · Tests con fakes.** Rojo: CREATE con ENABLED / sin `--go` pasa / readback con campaña o bid distintos sella. Verde: camino feliz PAUSED con bid+campaña verificados + `repuesto_external` registrado.

## Reject (con razón)

- **Umbral absoluto de clicks (15/25)**: rechazado por el dueño — sus
  productos necesitan ~120 clicks por venta; el umbral es relativo al
  producto (`expected_clicks`, CORTES 01).
- **Quitar `ORDERS_MIN_BAJA_FUERTE`**: cambiaría el mundo con ventas; la
  regla nueva vive aparte y con motivo propio.
- **Subida automática «de prueba» para inertes**: hoy 0 candidatas con
  historial; gastaría dinero a ciegas. Revivir es decisión humana desde el
  reporte.
- **Archivo automático**: archivar no tiene reversa en Amazon; solo por
  lote, con go literal y ledger para reponer.
- **Contar N desde `now()`**: con ingesta D-1 y maduración, marcaría inertes
  por retraso de datos; se cuenta desde el watermark.
- **Opción B (pause secundario)**: no por ahora; prueba de fuego empírica
  tras 2 semanas con A'+C.

## Residuales declarados

1. Product targets inertes solo se reportan (el cliente no tiene archivo de
   targets sellado); pausarlos a mano sigue disponible por consola.
2. La regla A' pega ~1 vez al día con los datos de hoy; si tras 2 semanas
   los cero-ventas siguen gastando de más, se evalúa B (spec).
3. `expected_clicks` exige grupo con evidencia 3/60/14: grupos nuevos o de
   rotación muy lenta nunca disparan A' (siguen −12%). Declarado, no se
   inventa un número.

## 事前確認

- 事項: destructive — migraciones 0013/0014 y deploy al contenedor (lead, runbook, backup del schema)
  理由: 1.5; sin deploy nada rige
  scope: 1.5
- 事項: destructive/external-send — archivo REAL de keywords en Amazon por lote (v3 delete = ARCHIVED), con reversa por ledger
  理由: decisión del dueño («herramienta de archivo por lote»); cada lote exige su go literal
  scope: 1.4 (herramienta) / 1.5 (primer lote)
- 事項: external-send — `git push` + PRs, línea en CHAT-CONTEXT, AppFlowy
  理由: patrón del repo
  scope: todas
