# DASHBOARD 01 — Brief de implementación (contrato fino)

> Contrato del **dashboard del optimizador** (ORBIT 16, plan `plans/dashboard-01.md`).
> Este documento es el contrato FINO: responde, sin ambigüedad y punto por punto,
> cada decisión sellada del header del plan (checklist 1:1 en la sección 1). El
> plan y `docs/CONTEXTO.md` (diseño v2 incluido) mandan sobre este brief; la
> implementación de cada endpoint se sella con su test (regla 8: forma real del
> dato verificada contra la base viva antes de fijar los tests).
>
> Alcance de este bloque (1.1–1.3): el brief (1.1), la cascada con procedencia
> en `app/optimizer/goals.py` (1.2) y el módulo `app/api_dashboard.py` con las
> series temporales (1.3). Los endpoints de campañas, feed y salud (1.4, 1.5) se
> contratan aquí y se implementan en sus tareas.

## 1. Checklist 1:1 contra las decisiones selladas del header

Cada fila referencia la decisión del header de `plans/dashboard-01.md` y dice
dónde y cómo la cumple este dashboard. Verificable punto por punto.

| # | Decisión sellada (header del plan) | Cumplimiento en este brief / código |
|---|------------------------------------|--------------------------------------|
| 1 | **Moneda (regla 4)**: series y tablas en moneda NATIVA por plataforma; CERO agregación cross-currency; conversión futura solo vía `fx_resolve` | §3.1/§3.2: cada serie lleva `moneda` (USD para amazon_us, MXN para amazon_mx, fuente `PLATAFORMAS_MONEDA` de `app/optimizer/bid.py` — la misma del motor). Ningún endpoint suma ni presenta totales que mezclen monedas; el feed (1.4) lleva `value_currency` por fila y NO existe total al pie. Toda conversión futura pasa por `fx_resolve` (sección 1 de DATABASE.md), jamás por una tasa de la app. |
| 2 | **Grano (anti-doble-conteo)**: `ads_metric_observation` mezcla filas de campaña/keywords/targets; toda serie o agregado filtra por `kind` EXPLÍCITO (columna de `ad_entity`, vía JOIN — no existe en la tabla de métricas). Resumen y por-campaña: SOLO `kind='campaign'`. Sellado con test regla 9 | §3.1/§3.2: ambas queries de serie hacen `JOIN ad_entity e ON e.id = v.ad_entity_id WHERE e.kind = 'campaign'`. Test anti-doble-conteo en `tests/test_api_dashboard.py` (fixture con fila campaign + fila keyword del mismo día → la serie usa SOLO campaign; demostrado fallando contra el SQL sin el filtro, regla 9). |
| 3 | **Ventanas**: series diarias en UTC, rango default [D-30, D-1]; el día en curso EXCLUIDO (vintage parcial, regla 6); días D-8..D-1 marcados como "inmaduros" (atribución madura 5–8d, costo hasta D+15) | §3.1/§3.2: default [D-30, D-1] UTC relativo a hoy; `hasta` se recorta a D-1 (el día en curso jamás se sirve, ni con `hasta` explícito); cada fila lleva `inmaduro: true/false` para [D-8, D-1] y la respuesta declara `ventana_inmaduros`. La fecha "hoy" es UTC (`_hoy_utc` en `app/api_dashboard.py`, inyectable en tests). |
| 4 | **NULL y ceros (regla 3)**: métrica NULL = hueco visible "sin dato", JAMÁS un 0 pintado; ACoS con revenue=0 y cost>0 = "sin ventas" (∞), nunca división ni cero engañoso | §3.6: cost/ad_revenue/clicks `null` = hueco (fecha sin fila O métrica NULL en alguna campaña del día — agregado envenenado con `bool_and`, mismo criterio que `windows.py`). `sin_ventas: true` cuando `ad_revenue == 0` conocido → `acos: null`. Jamás se emite 0 por dato faltante. |
| 5 | **Procedencia del target (regla 2)**: CINCO peldaños — goal de campaña, goal de plataforma, setting de config, cache del estado, default 55 — expuestos por `goals.py` (variante valor+peldaño), JAMÁS reimplementados en la capa web | `app/optimizer/goals.py` (1.2): `cascada_target_acos_con_procedencia(...) -> (valor, peldaño)` con los cinco nombres exactos `goal_campana`, `goal_plataforma`, `setting_plataforma`, `cache_estado`, `default`. Compatible con el camino del motor (cero cambio de comportamiento; la clave del setting sale de `clave_target_plataforma()` — `ads_target_acos_pct_amazon_us` / `_amazon_mx`). La pantalla Campañas (1.4) la REUTILIZA, no la reimplementa. |
| 6 | **Reuso (reglas 1-2)**: los helpers que ya existen en `app/api.py` (`_parse_notes` de formato mixto, SQL de último ciclo por plataforma, serialización de dinero como STRING) se EXTRAEN y comparten con `app/api_dashboard.py` — nunca dos copias | `app/api_common.py` (creado en 1.3): `_dec_str` (dinero→string) extraído de `api.py` y compartido. `_parse_notes` y el SQL de último ciclo se suman al mismo módulo en 1.5 (salud), con los tests de 3.2 intactos. La conexión de lectura `_conexion_lectura` SIGUE en `api.py` (el test de superficie 3.2 la introspecciona como `api._conexion_lectura` y parchea `api.connect`; moverla rompería ese candado): `api_dashboard.py` reutiliza el tipo `ConexionLectura` importándolo de `app.api`. |
| 7 | **Dinero en JSON = STRING** en todos los endpoints nuevos (patrón sellado de 3.2). Para las gráficas (que exigen números): la conversión string→número ocurre EN el cliente (JS parsea el string de la API) — representación intermedia documentada; el backend jamás emite floats de dinero | §3.6: todo monto (cost, ad_revenue, targets, floor/ceiling, bids, valores de decisión) viaja como string NUMERIC tal cual sale ("363.1400"). El cliente gráfica con `Number(string)`. El backend no emite floats de dinero en NINGÚN endpoint nuevo. |
| 8 | **Paginación del feed por CURSOR** (`id <` último visto, DESC): offset sobre una tabla append-only produce huecos/duplicados entre páginas | §3.4 (`/decisiones`, 1.4): paginación por cursor `id <`, ORDER BY estable `id DESC`, `next_cursor` + `has_more`. Prohibido limit/offset en el feed. |
| 9 | **Salud**: snapshot del último ciclo por plataforma + histórico 14d | §3.5 (`/salud`, 1.5): `ultimo_ciclo` (snapshot) + `historico_14d` + `skips` por motivo. |
| 10 | **Feed — límite declarado**: solo entidades que SÍ decidieron; skips agregados por motivo (así los persiste `notes`, sin id de entidad) | §5: el feed es SOLO decisiones persistidas (una fila por decisión). Los skips NO son filas de feed: van agregados por motivo en `/salud` (desde `optimizer_cycle.notes`, vocabulario del orquestador). |
| 11 | **Motivos → español**: dict que IMPORTA las constantes `MOTIVO_*` | §3.4: `motivo_es` sale de un dict en la capa de presentación que IMPORTA `MOTIVO_*` de `app/optimizer/bid.py`/`hygiene.py` (y los del orquestador para `/salud`); motivo desconocido → fallback sin crash. |
| 12 | **XSS (dos contextos)**: Jinja2 con autoescape VERIFICADO para HTML; los datos hacia JS de gráficas pasan EXCLUSIVAMENTE por `\|tojson`; header CSP `default-src 'self'`; `Cache-Control: no-store` | 1.6 (UI): autoescape verificado con test regla 9 (payload `<script>` en search_term escapado en HTML — demostrado fallando con autoescape off — y payload en datos de gráfica neutralizado por tojson); CSP y no-store por middleware; cero hosts externos. |
| 13 | **Regla 8**: antes de fijar los tests de cada endpoint, correr el SELECT de forma real contra la base viva (por túnel) y anotar la evidencia | §7: evidencia YA corrida por el lead contra la base viva (2026-08-24 UTC); es la base de los fixtures de los tests de 1.3–1.5. Sin SQL vivo en este bloque (el lead ya lo corrió). |
| 14 | **`app/api_dashboard.py` desde el inicio** (api.py va en 349 líneas) | 1.3: módulo nuevo `app/api_dashboard.py`; `api.py` NO se engorda (sus helpers se extraen a `app/api_common.py` si hacen falta). |
| 15 | **Dependencias**: Jinja2 pinneada en pyproject + uv.lock commiteado (patrón de 1.1 de ORBIT 03); la lib de gráficas vendoreada con versión, licencia y hash documentados en el brief | §6: candidatos uPlot / Chart.js single-file (unknown hasta 1.6); cuando 1.6 elija, se vendorea (versión+licencia+hash en esta sección), cero CDN. Jinja2 pinneada en pyproject + uv.lock en 1.6. |
| 16 | **Exposición VPN = PR separado** (Phase 2), único cambio de red, revertible, con sign-off del dueño. Residual aceptado: la VPN demuestra "no público", no "solo dueño" (server mono-operador) | §4: Phase 1 SOLO túnel SSH (nada publicado); Phase 2 publica en la IP de la interfaz WG (jamás 0.0.0.0; allowlist exacta `{127.0.0.1, <IP-WG>}` en el candado de compose), PR separado con sign-off explícito. |
| 17 | **Goals MUTABLES (corrección de ronda 2)**: `ads_optimizer_goal` es mutable POR SCHEMA; la historia se reconstruye vía `config_version` + `decision.inputs` congelados. La edición de Phase 3 es UPDATE del goal + fila NUEVA de config | §3.3: la pantalla Campañas muestra el goal como ESTADO VIVO (enabled/floor/ceiling/mode), no como historia; la escritura (Phase 3, bloqueada por ORBIT 04) es UPDATE del goal (tocando `updated_at` explícito) y fila nueva de `config_version` cuando toque config — jamás "fila nueva de goal". Este brief no contrata escritura. |
| 18 | Acceso pedido por el dueño: por su VPN WireGuard (compu/cel), patrón de sus demás apps del server; registro `ORBIT 16` en EHV Tasks | §4 + `plans/dashboard-01.md`: registro y acceso. El tracker lo mantiene el lead; este bloque no toca AppFlowy. |

**Unknowns declarados** (`not_observed != absent`, del header): IP exacta de la
interfaz WG y su alcanzabilidad solo-túnel (2.1 la verifica con evidencia); lib
de gráficas final (1.6 la elige: candidatos uPlot / Chart.js single-file, la
más chica que cubra líneas y barras); forma real de los datos por endpoint
(regla 8, ya corrida para las series en §7; 1.4/1.5 la corren antes de sus
tests).

## 2. Las cuatro pantallas

Stack de la UI (1.6): Jinja2 server-rendered, lib de gráficas vendoreada, cero
CDN, responsive, CSP `default-src 'self'`, `Cache-Control: no-store`, datos a
JS solo por `|tojson` (decisión 12). Cada pantalla consume UNO de los
endpoints de la sección 3.

### 2.1 Resumen
Series diarias de spend / revenue / ACoS **por plataforma** (amazon_us,
amazon_mx): moneda NATIVA, grano `kind='campaign'`, rango [D-30, D-1] UTC, día
en curso excluido, días D-8..D-1 marcados como inmaduros, NULL = hueco visible.
Fuente: `GET /api/dashboard/series/plataforma` (§3.1).

### 2.2 Campañas
Tabla 30d por campaña: métricas colapsadas (mismo grano campaign, misma ventana
e inmadurez), **target efectivo CON procedencia de 5 peldaños** (decisión 5, vía
`goals.py` 1.2) y estado del goal (enabled / floor / ceiling / mode). Cada fila
lleva su moneda; sin total al pie que mezcle monedas (regla 4).
Fuente: `GET /api/dashboard/campanas` (§3.3, 1.4).

### 2.3 Decisiones
Feed del ciclo por CURSOR (decisión 8) con explicación en español (decisión 11)
construida desde `decision.inputs` congelados. El target mostrado se lee de
`inputs.target_acos_pct_usado`, **NUNCA de `inputs.goal.target_acos_pct`**
(es NULL cuando ganó el default — afinación grok r2 del plan). Límite declarado
(decisión 10): solo entidades que SÍ decidieron.
Fuente: `GET /api/dashboard/decisiones` (§3.4, 1.4).

### 2.4 Salud
Snapshot del último ciclo por plataforma + histórico 14d + skips agregados por
motivo (vocabulario del ORQUESTADOR: `cycle.py` — sin_goal, goal_disabled, …;
el que vive en `notes.skips`; distinto del `MOTIVO_*` de bid/hygiene del feed).
Fuente: `GET /api/dashboard/salud` (§3.5, 1.5).

## 3. Contratos de endpoints

Prefijo común: `/api/dashboard`. **Solo GET en toda la superficie** (candado:
introspección OpenAPI en `tests/test_api_dashboard.py`; CERO escrituras en
PR1). Conexión como rol de lectura (`ORBIT_DSN_READ` → `orbit_read` →
`app_read`); sin DSN → 503 fail-closed (mismo patrón que `api.py`). Dinero
siempre STRING (regla 4 / decisión 7).

### 3.1 Series por plataforma — `GET /api/dashboard/series/plataforma`  *(1.3)*

| Param | Tipo | Requerido | Default / Validación |
|-------|------|-----------|----------------------|
| `platform` | enum `amazon_us` \| `amazon_mx` | sí | 422 fuera del vocabulario (solo plataformas del optimizador) |
| `desde` | date `YYYY-MM-DD` | no | `hoy - 30d` (D-30) |
| `hasta` | date `YYYY-MM-DD` | no | `hoy - 1d` (D-1); si pide el día en curso o más, se RECORTA a D-1 (sellado) |
| — | — | — | `desde > hasta` → 422; ventana > 366 días → 422 |

`hoy` = fecha UTC actual (`_hoy_utc`); nunca se sirve el día en curso, ni con
`hasta` explícito. La respuesta declara SIEMPRE el rango efectivo servido.

Respuesta 200 (ejemplo real de la evidencia, amazon_us):
```json
{
  "plataforma": "amazon_us",
  "moneda": "USD",
  "desde": "2026-07-25",
  "hasta": "2026-08-23",
  "ventana_inmaduros": {"desde": "2026-08-16", "hasta": "2026-08-23"},
  "series": [
    {"fecha": "2026-07-25", "cost": "12.3400", "ad_revenue": "45.6700", "clicks": 12, "acos": "27.02", "sin_ventas": false, "inmaduro": false},
    {"fecha": "2026-08-22", "cost": "66.6300", "ad_revenue": "0.0000", "clicks": 5,  "acos": null, "sin_ventas": true,  "inmaduro": true},
    {"fecha": "2026-08-23", "cost": null,       "ad_revenue": null,      "clicks": null, "acos": null, "sin_ventas": false, "inmaduro": true}
  ]
}
```

Semántica sellada de cada fila (sección 3.6): spine completo del rango; fecha
sin fila → valores `null` (hueco visible), jamás 0; `ad_revenue == 0` →
`sin_ventas: true` y `acos: null` (∞), nunca división ni 0 engañoso.

### 3.2 Series por campaña — `GET /api/dashboard/series/campana`  *(1.3)*

| Param | Tipo | Requerido | Validación |
|-------|------|-----------|------------|
| `ad_entity_id` | int ≥ 1 | sí | 404 si no existe; 422 si existe pero `kind != 'campaign'` (el grano es explícito); 422 si su plataforma no tiene moneda sellada (solo amazon) |
| `desde` / `hasta` | date | no | igual que §3.1 |

Respuesta 200 (el contrato de `series` es idéntico a §3.1):
```json
{
  "ad_entity_id": 42,
  "nombre": "SP - Manual - Girasoles",
  "plataforma": "amazon_mx",
  "moneda": "MXN",
  "desde": "2026-07-25",
  "hasta": "2026-08-23",
  "ventana_inmaduros": {"desde": "2026-08-16", "hasta": "2026-08-23"},
  "series": [
    {"fecha": "2026-08-22", "cost": "363.1400", "ad_revenue": "3262.0600", "clicks": 116, "acos": "11.13", "sin_ventas": false, "inmaduro": true}
  ]
}
```
`nombre` es `ad_entity.name` (nullable por schema) → `null` si la entidad no
tiene nombre; el contrato lo tolera (regla 3: faltante = null, no cadena vacía).

### 3.3 Resumen de campañas — `GET /api/dashboard/campanas`  *(1.4)*

| Param | Tipo | Requerido | Validación |
|-------|------|-----------|------------|
| `platform` | enum `amazon_us` \| `amazon_mx` | no | 422 fuera del vocabulario; sin filtro → ambas plataformas |

Respuesta 200:
```json
{
  "items": [
    {
      "ad_entity_id": 42,
      "nombre": "SP - Manual - Girasoles",
      "plataforma": "amazon_mx",
      "moneda": "MXN",
      "metricas_30d": {"cost": "4231.5500", "ad_revenue": "18876.2100", "clicks": 1320, "acos": "22.42", "sin_ventas": false, "inmaduro": true},
      "target_efectivo": {"valor": "25.00", "peldano": "goal_plataforma"},
      "goal": {"enabled": true, "floor": "0.1000", "ceiling": "2.5000", "mode": "shadow", "scope": "platform"}
    }
  ]
}
```
- `metricas_30d`: misma semántica de grano/ventana/NULL/dinero-string que §3.1/§3.2
  (ventana fija [D-30, D-1]; `inmaduro` = el agregado incluye días D-8..D-1).
- `target_efectivo.peldano` ∈ exactamente `{goal_campana, goal_plataforma, setting_plataforma, cache_estado, default}` (función de 1.2, REUTILIZADA; valor `str` de Decimal). La clave es `peldano` (convención del repo: sin acentos en el código).
- `goal` es el estado VIVO del goal RESUELTO (`resuelve_goal`: campaña > plataforma; decisión 17): `enabled`, `floor`, `ceiling`, `mode`, `scope`; `null` si no hay goal (regla 3). `target_acos_pct` del goal NO se expone como target efectivo (eso es la cascada), solo via `target_efectivo`.
- Cada fila lleva su `moneda`; **NO existe total al pie que sume filas de monedas distintas** (regla 4 — test anti-mezcla).

### 3.4 Feed de decisiones — `GET /api/dashboard/decisiones`  *(1.4)*

| Param | Tipo | Requerido | Validación |
|-------|------|-----------|------------|
| `cursor` | int | no | paginación por cursor: `id < cursor` DESC (decisión 8); sin cursor = primera página |
| `limit` | int | no | default 50, max 200 (tope duro, patrón `/audit`) |
| `platform` | enum | no | filtro |
| `kind` | enum `decision_kind` | no | 422 fuera del vocabulario (vocabulario cerrado, patrón `/audit`) |

Respuesta 200:
```json
{
  "items": [
    {
      "id": 9001,
      "cycle_id": 12,
      "ad_entity_id": 42,
      "nombre": "SP - Manual - Girasoles",
      "plataforma": "amazon_mx",
      "kind": "bid",
      "decided_at": "2026-08-22T12:00:00Z",
      "search_term": null,
      "target_acos_pct_usado": "25.00",
      "motivo_es": "ACoS sobre 1.15x del target: -12%",
      "old_value": "1.0000",
      "new_value": "0.8800",
      "value_currency": "MXN"
    }
  ],
  "next_cursor": 8991,
  "has_more": true
}
```
- `target_acos_pct_usado` se lee de `inputs.target_acos_pct_usado`; **jamás de
  `inputs.goal.target_acos_pct`** (NULL cuando ganó el default — afinación grok r2;
  en producción los goals de plataforma no traen target y leer el goal mostraría
  null en TODAS las decisiones).
- `nombre` viene del JOIN a `ad_entity.name` (nullable → `null`, no revienta).
- `motivo_es`: dict de traducción que IMPORTA las constantes `MOTIVO_*` de
  `app/optimizer/bid.py`/`hygiene.py`; motivo desconocido → fallback SIN crash
  (se devuelve el id crudo del motivo, jamas se pierde información).
- `old_value`/`new_value`/`value_currency` pueden ser `null` (trampa real: los
  pause traen los tres NULL — el CHECK del esquema solo exige moneda en kinds
  que mueven dinero): el feed los renderiza null sin inventar 0 ni crashear.
- `search_term` solo en kinds de término (NULL en los demás, CHECK del esquema);
  es texto libre del comprador → el vector XSS que la UI de 1.6 debe escapar.
- `value_currency` por fila; sin total al pie (regla 4).
- Cursor estable bajo inserción concurrente simulada (páginas sin duplicados ni
  huecos) — DoD de 1.4.

### 3.5 Salud — `GET /api/dashboard/salud`  *(1.5)*

Respuesta 200:
```json
{
  "plataformas": {
    "amazon_us": {
      "watermark": "2026-08-22",
      "synced_at": "2026-08-23T00:46:00Z",
      "ultimo_ciclo": {"id": 5, "mode": "shadow", "status": "done", "started_at": "2026-08-22T00:46:00Z", "finished_at": "2026-08-22T00:50:00Z", "decisions_count": 124, "applied_count": 0, "notes": {"skips": {"entidad": {"estado_no_enabled": 3200}}, "decisiones": {"bid": 124}}},
      "historico_14d": [{"cycle_id": 5, "fecha": "2026-08-22T00:46:00Z", "status": "done", "decisions_count": 124}, {"cycle_id": 4, "fecha": "2026-08-21T00:47:00Z", "status": "degraded", "decisions_count": 0, "motivo": "Watermark de la plataforma vencido"}],
      "skips": {"entidad": {"estado_no_enabled": {"count": 3200, "motivo_es": "Entidad sin estado o no habilitada"}}, "termino": {"asin_like": {"count": 84, "motivo_es": "Termino ASIN-like: se salta siempre"}}}
    }
  }
}
```
- `watermark`/`synced_at`: las MISMAS fuentes del motor (v_metric_latest y
  ad_entity_state.synced_at, constantes de `windows.py`) — regla 2.
- `ultimo_ciclo` REUTILIZA `_parse_notes` (formato mixto JSON / texto `rastro: …`)
  y el SQL de último ciclo de `api.py` (extraídos a `app/api_common.py`, no
  copiados — decisión 6).
- `historico_14d`: los últimos 14 ciclos de la plataforma (acotado), con
  `status` y, cuando aplique, el `motivo` en español (degradado/failed visible
  con motivo; de `notes.motivo_skip` — guarda_watermark/synced_at/sin_datos — o
  el texto del notes).
- `skips`: los contadores del notes del ORQUESTADOR (`cycle.py`: sin_goal,
  goal_disabled, campana_no_enabled, grupo_no_enabled, estado_no_enabled,
  entidad_inerte (BIDS 01: hoja sin impresiones en 14d desde el watermark),
  cooldown_7d, escalera_off, … + los
  `MOTIVO_*` de bid/hygiene que el orquestador importa a sus contadores) con su
  traducción `motivo_es`; DOS diccionarios de traducción (este y el de §3.4),
  cada uno importando su fuente (decisión 11); motivo desconocido → fallback sin
  crash.

### 3.6 Reglas transversales de las series (selladas)

1. **Colapso bitemporal (regla 5)**: las series SIEMPRE leen `v_metric_latest`
   (última observación por `(ad_entity, metric_date)`), jamás la tabla cruda.
   Dos observaciones de la misma fecha → gana la última por `observed_at`.
2. **Grano explícito**: `JOIN ad_entity` + `WHERE e.kind = 'campaign'` en AMBAS
   series (el `kind` vive en `ad_entity`, no en la observación); las hojas
   (keyword/product_target) duplicarían el dinero (evidencia §7: 63.96 = 24.94 +
   39.02).
3. **Spine de fechas**: la respuesta trae TODAS las fechas de [desde, hasta];
   fecha sin fila → `cost/ad_revenue/clicks/acos` en `null` (hueco visible),
   JAMÁS 0. El D-1 puede venir todo-null en la madrugada (no existe hasta el
   cron de las 07:10 UTC) — el spine es obligatorio.
4. **NULL ≠ 0 (regla 3)**: además del hueco por fila ausente, si ALGUNA campaña
   del día trae una métrica en NULL, el agregado de esa métrica ese día es
   `null` (envenenado con `bool_and`, mismo criterio que `windows.py`) — un
   agregado parcial jamás se disfraza de completo.
5. **ACoS**: `acos = cost / ad_revenue * 100` con Decimal exacto, 2 decimales,
   como STRING ("11.13"). `ad_revenue == 0` (conocido) → `acos: null` +
   `sin_ventas: true` (∞). `cost` o `ad_revenue` NULL → `acos: null` y
   `sin_ventas: false` (dato faltante, no "sin ventas").
6. **Dinero como STRING (regla 4 / decisión 7)**: `cost`/`ad_revenue` salen
   `str()` del NUMERIC(14,4) tal cual ("363.1400" — la escala es artefacto
   determinístico del NUMERIC de origen, mismo criterio que 3.2). `clicks` es
   entero (conteo, no dinero). El cliente parsea con `Number()` para graficar.
7. **Moneda por serie**: `moneda` en el sobre de la respuesta (fuente
   `PLATAFORMAS_MONEDA`); ninguna serie mezcla monedas (el trigger
   `metric_moneda_de_plataforma` sella el par plataforma→moneda en la base).
8. **Inmadurez**: `inmaduro: true` para fechas en [D-8, D-1] (relativo a hoy,
   independiente del rango pedido); la respuesta lo declara también en
   `ventana_inmaduros`.

## 4. Modelo de acceso

- **Phase 1 (ahora, lectura por túnel)**: el dashboard corre en el server
  `goncloud` bindeado a `127.0.0.1:<puerto>` (jamás 0.0.0.0 — candado
  `tests/test_compose_deploy.py`). El dueño lo alcanza por **túnel SSH** desde
  su compu: `ssh -L 127.0.0.1:<puerto_local>:127.0.0.1:<puerto_app> goncloud` y
  luego browser/curl a `http://127.0.0.1:<puerto_local>/…`. Cero cambios de red.
- **Phase 2 (PR separado, con sign-off del dueño)**: publicar el puerto TAMBIÉN
  en la IP de la interfaz WireGuard (aditivo en el compose; db/bridge/accounting
  intactos); ANTES se verifica con evidencia (firewall/NAT + `ss -lntp`) que esa
  IP solo es alcanzable por el túnel; candado a allowlist exacta
  `{127.0.0.1, <IP-WG>}` (set). Residual aceptado y declarado: la VPN demuestra
  "no público", no "solo dueño" (server mono-operador, todos los peers WG son
  del dueño; si algún día hay peers de terceros, la Phase 3 ya trae auth).
- **Phase 3 (bloqueada por ORBIT 04)**: auth propia + settings de escritura;
  CERO escritura antes de esa auth.

## 5. Límite declarado del feed

El feed (`/decisiones`) muestra **solo entidades que SÍ decidieron**: una fila
por decisión persistida en `decision`. Los skips NO son filas del feed (no
tienen id de entidad propio en el feed; el orquestador los persiste agregados
por motivo en `optimizer_cycle.notes`) — se ven en `/salud` (§3.5). El feed es
append-only en la práctica: la paginación es por CURSOR (`id <`, DESC) porque
offset sobre una tabla append-only produce huecos/duplicados entre páginas
(decisión 8). Sin filtros → `total` no se computa (patrón `/audit`: un
`count(*)` de la tabla append-only completa por request escala lineal con el
historial).

## 6. Librería de gráficas (elegida en 1.6)

**Elegida: Chart.js 4.4.9 single-file (UMD)**, vendoreada en
`app/static/vendor/chart.umd.min.js`.

- **Versión:** 4.4.9 · **Licencia:** MIT · **SHA-256:**
  `BCE154080959C574BE0BB6B1A924FF32F08EBC6FF460C159171F51C53802C844`
  (descargada de jsdelivr/npm; el hash se verificó al vendorear y es la
  referencia del candado).
- **Por qué Chart.js y no uPlot** (candidato sellado del plan): el criterio
  era "la más chica que cubra líneas Y barras". El core de uPlot 1.6.32 NO
  cubre barras sin plugin, y el plugin (`uPlot.bars.js`) no se distribuye en
  el dist estándar del release/npm (verificado contra el repo en el tag
  1.6.32: dist solo trae el core) — no existe una descarga limpia y
  versionada del par core+plugin. Chart.js UMD cubre líneas y barras en UN
  archivo descargable limpio (~207 KB, MIT). Es el candidato que cumple el
  requisito funcional completo; la decisión queda declarada con su razón.
- Restricciones innegociables (siguen vigentes): **cero CDN** y cero hosts
  externos en el HTML (un CDN caído no puede romper el dashboard); los datos
  a JS pasan EXCLUSIVAMENTE por `|tojson` (decisión 12); el parseo
  string→número ocurre en el cliente (`Number()`, decisión 7); el HTML no
  lleva scripts inline de datos salvo el bloque `<script type="application/json">`
  con `|tojson`.

## 7. Evidencia regla 8 — forma real del dato (corrida por el lead, 2026-08-24 UTC)

Base de los fixtures de 1.3–1.5 (el lead ya corrió el SELECT contra la base
viva; este bloque NO necesita SQL vivo). Query base de la serie (rol
`orbit_read`):

```sql
SELECT e.platform, m.metric_date, m.metric_currency, SUM(m.cost),
       SUM(m.ad_revenue), SUM(m.clicks), COUNT(*)
  FROM v_metric_latest m JOIN ad_entity e ON e.id = m.ad_entity_id
 WHERE e.kind='campaign' AND m.metric_date BETWEEN D-6 AND D-1
 GROUP BY 1,2,3;
```

Forma real observada:

- amazon_mx/MXN, 2026-08-22 → cost `363.1400`, revenue `3262.0600`, clicks 116,
  18 campañas. amazon_us/USD, 2026-08-22 → cost `66.6300`, revenue `0.0000`
  (**sin_ventas es caso REAL, no hipotético**).
- NUMERIC(14,4) llega con 4 decimales SIEMPRE → string tal cual.
- NO existe fila campaign con dinero NULL (el gate de ingesta descarta filas
  todo-None): el hueco real es FILA AUSENTE. El D-1 UTC no existe hasta el cron
  de las 07:10 UTC → el spine de fechas es obligatorio y D-1 puede venir
  todo-null en la madrugada.
- **Doble conteo confirmado con producción** (amazon_us, 2026-08-20): campaign
  suma 63.96 USD y las hojas suman EXACTO lo mismo (keyword 24.94 +
  product_target 39.02) → SUM sin filtro de kind = 2×.
- Cobertura amazon_mx: 95 días continuos (2026-05-20 → 2026-08-22).

## 8. Archivos

| Archivo | Tarea | Qué es |
|---------|-------|--------|
| `docs/DASHBOARD.md` | 1.1 | Este brief (contrato fino). |
| `docs/CONTEXTO.md` | 1.1 | Spec delta: sección "Módulo dashboard" (apunta a este brief). |
| `app/optimizer/goals.py` | 1.2 | `cascada_target_acos_con_procedencia` (5 peldaños, valor+peldaño). |
| `app/api_common.py` | 1.3 | Helpers compartidos de la capa API (extracción sellada, decisión 6). |
| `app/api_dashboard.py` | 1.3 | Módulo nuevo: series por plataforma y por campaña (+ 1.4, 1.5). |
| `app/main.py` | 1.3 | Incluye el router `/api/dashboard`. |
| `tests/test_optimizer_goals.py` | 1.2 | Tests de los 5 peldaños (se agregan; los de 2.4 no se tocan). |
| `tests/test_api_dashboard.py` | 1.3 | Tests de series + superficie OpenAPI solo-GET + SQL pglast. |
