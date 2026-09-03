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
| 1.1 | **GLM — Regla A' en `bid.py`**: constante `MOTIVO_BANDA_MENOS_25_CERO_VENTAS = "banda_menos_25_cero_ventas"`; `decide_bid` gana `expected_clicks: Decimal \| None = None`; función pura `_factor_cero_ventas(bids, expected_clicks, cost_min)`; motivo del resultado = el nuevo cuando esa regla dispara, si no `_MOTIVO_BANDA[factor]`; `cycle._procesa_decisora` pasa `expected_clicks=corte_pause.expected_clicks`; `replay._replay_bid` lee `inputs.corte.expected_clicks`; `apply._PRIORIDAD_BANDA` con el motivo nuevo en prioridad 0; `api_dashboard.MOTIVOS_ES_DECISIONES` con su etiqueta; `docs/CONTEXTO.md` (umbrales sellados) con la regla. Guía en §1.1. `[tdd:required]` | Tests puros ROJOS contra master en `tests/test_optimizer_bid.py`: dispara en el borde exacto (clicks == expected, cost == cost_min) con factor −0.25 y motivo nuevo; NO dispara con clicks = expected − 1, con cost < cost_min, con `expected_clicks=None`, con orders=None o ad_revenue=None (siguen −12%/otro); PAUSE gana cuando aplica; replay: fila histórica sin la clave rejuega igual (golden intacto) y una decisión nueva congela/replayea el motivo nuevo (`tests/test_cycle.py`); logs rojos en §Decisiones | - | cc:TODO |
| 1.2 | **GLM — Vista `v_entidad_inerte` + guarda `entidad_inerte`**: migración `migrations/0013_entidad_inerte.sql` (vista + `COMMENT ON VIEW` + `GRANT SELECT` con el mismo patrón de roles de `0006`), `cycle.MOTIVO_ENTIDAD_INERTE = "entidad_inerte"`, `_SQL_INERTES` + lectura en TX2 + skip en `_procesa_decisora` (D3), etiqueta en `MOTIVOS_ES_SALUD`, `docs/DASHBOARD.md` (lista de motivos), `docs/DATABASE.md` (la vista). Guía en §1.2. `[tdd:required]` | Test de la vista (`_db_temporal` + migración 0013 aplicada en el fixture) ROJO contra master: 3 hojas sembradas (sin impresiones 20 d con gasto 90 d → `gasto_sin_ventas`; sin impresiones y sin nada → `peso_muerto`; con impresiones hace 3 d → NO aparece) y N contado desde el watermark, no desde `now()`; test de ciclo ROJO: una hoja con métricas solo antiguas (ventana completa) decidía un bid y ahora es `skips.entidad.entidad_inerte = 1`, una con impresiones recientes sigue decidiendo; pglast de `_SQL_INERTES`; logs rojos | - | cc:完了 Vista + guarda entidad_inerte (PR #133, CI verde) |

## Phase 2 — Superficie y herramienta [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.3 | **DeepSeek — `/inertes` + digest**: `api_dashboard.py` `GET /inertes` (lee `v_entidad_inerte`: items + totales por plataforma y clasificación; regla 22: la UI consume el endpoint); `ui.py` `GET /inertes` + `templates/inertes.html` (tabla: plataforma, campaña, ad group, keyword/target, clasificación, días sin impresiones, gasto/ventas 90 d; textos ESCAPADOS; sin JS inline; enlace en `base.html`); `notifica.digest_ciclo`: línea `entidades sin trafico (saltadas): N` SOLO si `skips.entidad.entidad_inerte` existe en el resumen. Guía en §1.3. `[tdd:required]` | Tests ROJOS: endpoint con filas sembradas devuelve el shape y los totales; página renderiza y escapa `<script>` en `keyword_text`; digest sin la clave NO imprime la línea y con la clave sí; CI verde | 1.2 | cc:TODO |
| 1.4 | **GLM — `tools/archiva_inertes.py` + reversa + ledger**: migración `migrations/0014_keyword_archivo_manual.sql` (tabla ledger, D5); herramienta con `--plataforma`, `--clasificacion peso_muerto` (default), `--min-dias-sin-impresiones 30`, `--limite N`, dry-run por defecto, `--acepto-mutacion-real --esperado N --go "<literal>"`, `--reponer <lote>`; solo `kind='keyword'`; una línea JSON por mutación (scrub); reconciliación final. Guía en §1.4. `[tdd:required]` | Tests ROJOS con `httpx.MockTransport` (patrón `tests/test_reactiva_campanas.py`): plan desde la vista; `--esperado` distinto → aborta sin HTTP; entidad no ENABLED en el LIST previo → se salta con nota; ledger ANTES del HTTP; readback `ARCHIVED` → `applied`; readback ≠ → `failed` y el lote se detiene; `--reponer` recrea con el `matchType` del ledger y sella `repuesto_*`; `test_architecture` sigue verde (sin `app.ads.write`); logs rojos | 1.2 | cc:TODO |
| 1.5 | **Lead — producción**: review de cada PR + cross-review codex (1 ronda); migraciones 0013/0014 por el runbook (`-1`, backup del schema antes); deploy al contenedor (DEPLOY.md: `git archive origin/master`, md5, `up --build`, `Recreated`); **contrafactual** read-only sobre los últimos ciclos live: cuántas decisiones pasan a `entidad_inerte` y cuántas −12% pasan a −25% (esperado ≈57/103 y ≈1); verificar el ciclo siguiente (`notes.skips.entidad_inerte`, motivos nuevos en `/salud` y en el feed); primer lote de archivo SOLO con go literal del dueño; AppFlowy. `[tdd:skip:ops]` | SELECTs del ciclo post-deploy en la evidencia; contrafactual entregado al dueño; `/inertes` visible; AppFlowy Done con evidencia | 1.1-1.4 | cc:TODO |

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
        None, cost_min="40", expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_gasto_bajo_el_piso_sigue_menos_12():
    r = _decide(
        _bids(cost=Decimal("39.99"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None, cost_min="40", expected_clicks="120",
    )
    assert (r.motivo, r.factor) == ("banda_menos_12", Decimal("-0.12"))


def test_cero_ventas_sin_expected_clicks_no_aplica():
    """Grupo sin evidencia (expected None): regla 3, nada inventado -> -12%."""
    r = _decide(
        _bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=0),
        None, cost_min="40", expected_clicks=None,
    )
    assert r.motivo == "banda_menos_12"


def test_cero_ventas_orders_o_revenue_desconocidos_no_aplica():
    r1 = _decide(_bids(cost=Decimal("45"), ad_revenue=Decimal("0"), clicks=200, orders=None), None, cost_min="40", expected_clicks="120")
    r2 = _decide(_bids(cost=Decimal("45"), ad_revenue=None, clicks=200, orders=0), None, cost_min="40", expected_clicks="120")
    assert r1.motivo != "banda_menos_25_cero_ventas"
    assert r2.motivo != "banda_menos_25_cero_ventas"


def test_pause_gana_sobre_la_regla_de_cero_ventas():
    """Al 1.5x (umbral_pause) con cost >= piso en la ventana de CORTES manda el
    PAUSE (sin cambio): la regla nueva vive DESPUES del pause."""
    r = _decide(
        _bids(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        _cortes(cost=Decimal("60"), ad_revenue=Decimal("0"), clicks=180, orders=0),
        cost_min="40", expected_clicks="120", umbral_pause=180,
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
