# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07)

Fecha: 2026-09-15 UTC. Estado: **diseño aprobado por el dueño en diálogo el
2026-09-15** (decisiones literales en S1); spec sellado para el plan
`plans/repricing-01.md`. Base de lectura `origin/master` `0617328`.

Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md` (M1 /
AUTO-07) > `docs/superpowers/specs/2026-09-08-margen-estimado-design.md` y su
acta `docs/evidencia/margen-estimado-01/0.3/acta.md` (contrato del margen
estimado) > `docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1 (criterio del
traspaso) > este spec > el plan.

## Objetivo y límites

Que cada producto con un goal de margen fijado por el dueño **llegue a ese
margen por precio**, y que el precio baje **solo** cuando el producto está
perdiendo ventas, nunca por debajo del goal. El motor protege margen; la Buy
Box se observa y se avisa, no se persigue.

Lo que este spec NO cubre y queda declarado, no rellenado con ceros: FBM,
Mercado Libre, promociones, kits, decisiones por inventario (se lee, no
decide), elasticidad estimada, modos «agresivo/equilibrado» del traspaso.

## S1. Decisiones del dueño (cerradas 2026-09-15, textos literales)

| # | Decisión | Texto literal |
|---|---|---|
| 1 | Objetivo principal: proteger margen; Buy Box deseable, no a cualquier precio | «A, proteger margen» |
| 2 | Subir precio cuando el margen no alcanza el goal | «quisiera subir precios si el margen no esta llegando al goal» |
| 3 | Goal de margen **por producto**, fijado por el dueño | «b» (goal por producto) |
| 4 | El margen que se compara es el **estimado** (precio, costo, comisiones, retenciones), no el observado tras Ads | «A, estimado» |
| 5 | Si el precio del goal queda arriba de la competencia, sube igual; la Buy Box se avisa | «A» |
| 6 | Baja **solo si se están perdiendo ventas**, nunca abajo del goal | «baja solo si se estan perdiendo ventas» |
| 7 | «Perder ventas» = unidades de los **últimos 15 días** contra el **promedio de los 60 días previos** | «por unidades las unidades venidas de los ultimos 15 dias» · «promedio de los 60 dias previos» |
| 8 | Aplicación **automática dentro de límites**, con sombra primero | «a» |
| 9 | Alcance: **Amazon MX y US**; MeLi y FBM fuera | «b» |
| 10 | Enfoque: motor por goal con **precio resuelto** (no escalera ciega); fases 0 (US), A (MX), B (US) | «si, la 1 y ese orden» |
| 11 | **Ningún silencio**: todo producto no evaluado se registra con motivo y se muestra | «si un dia falta un dato ... tiene que mostrarlo claramente no puede fallas en silencio» |
| 12 | El repricing tiene **su propia cuota**, separada de las de Ads | «tiene su propia cuota» |

Umbrales propuestos por el lead y aceptados como **valores de config, no
constantes**: caída de ventas ≥ 40%; escalón máximo por movimiento +10%; un
cambio por producto por semana; tope diario de cambios por plataforma = 5;
freno tras 3 cambios en la misma dirección sin acercarse al goal; aviso tras 3
días seguidos sin evaluar; reintento de escritura una vez por día, aviso y
freno al tercer día. El dueño los ajusta en `config_version`.

## S2. Fuentes: una por número, todas existentes

| Número | Fuente (única) | Frescura exigida | Si falta |
|---|---|---|---|
| Precio propio, Buy Box (precio, dueño, `is_own`), precio más bajo, ofertas | `spapi_price_observation` (pase diario de Pricing v0, 05:00 UTC) | fila del día UTC | `precio_sin_observar` |
| Contribución estimada al precio actual y sus componentes `P, I, C, F, L, R` | Estimación por venta FBA MX (`estimacion_*`, acta 0.3): oferta ≤ 6 h, costo vigente de la corrida `ok` del día, FX exacto o ≤ 7 días, cotización de fees ligada a esa oferta | la del escenario vigente del día | el motivo que ya declara la estimación (`oferta_desactualizada`, `costo_no_vigente`, `fx_ausente`, `fee_ausente`…) |
| Unidades vendidas por producto y día | `ledger_event` `kind = 'sale'` (`quantity`, `product_id`, `event_date`), ligado a la publicación por `listing.product_id` | ledger fresco del día (08:15 UTC) | `ventas_sin_dato` |
| Publicación (SKU, ASIN, `product_type`) | `listing` + `spapi_listing_estado_observation` | fila del día | `listing_sin_estado` |
| Inventario | `spapi_inventario_observation` | solo lectura; no decide en esta versión | — |
| Goal de margen, modo y límites | `precio_goal` (nueva, S3) y `config_version` | vigente en la fecha | `sin_goal` → no se evalúa |

Regla 2 de Orbit aplicada: el motor **no** recalcula margen ni costo ni FX;
consume la estimación tal cual y su resultado se guarda con la referencia al
escenario y a la cotización de fees que usó (reproducible, S5).

## S3. Goal de precio por producto (`precio_goal`)

Tabla append-only con vigencia, patrón de `config_version`/`sku_cost`:

```
precio_goal(id, listing_id, platform, margen_goal_pct NUMERIC(6,4) CHECK (0 < x < 1),
            mode precio_mode ('shadow'|'live'), valid_from DATE, valid_to DATE NULL,
            creado_por TEXT, go_literal TEXT NULL, created_at)
UNIQUE (listing_id, platform, valid_from); un solo vigente por (listing, platform).
```

- **Sin fila vigente no hay decisión.** No hay default por plataforma ni
  herencia: es la decisión 3 del dueño y la regla 3 de Orbit (dato faltante =
  `None`, la fila no se escribe).
- `margen_goal_pct` se expresa sobre el ingreso sin IVA (`I`), igual que la
  contribución estimada (acta 0.3: 42.50 MXN sobre `I = 100` es 42.50%).
- `mode` por producto con la ceremonia de los goals de Ads (PR #283):
  **subir a `live` exige go literal**, bajar a `shadow` no. Sembrar o cambiar
  goals solo por `tools/precio_goal.py` (dry-run, huella del conjunto,
  `--esperado`, `--acepto-mutacion-real`, `--go`), solo `app_admin`, cero
  Amazon; un `UPDATE` crudo queda prohibido por el candado de escritor único
  de `tests/test_architecture.py`.
- Un producto puede tener goal en MX y otro en US; son filas distintas.

## S4. Reglas de decisión (lo que el implementador no decide)

Cada corrida diaria evalúa cada `(listing, platform)` con goal vigente y
produce **exactamente una** fila de resultado (S5): `subir`, `bajar`,
`mantener`, `no_evaluado(motivo)`, `goal_inalcanzable(motivo)` o `frenado(motivo)`.

1. **Insumos completos o nada.** Si cualquier fuente de S2 falta o está
   vencida, el resultado es `no_evaluado` con el motivo exacto. Nunca se
   sustituye un componente por una constante ni por el valor de ayer.
2. **Margen actual.** `m_actual = contribución / I` del escenario vigente al
   precio observado hoy. Si el precio observado y el precio de la oferta del
   escenario difieren, manda el más reciente y se registra la discrepancia
   (`precio_divergente`) sin decidir ese día.
3. **Subir** si `m_actual < goal − tolerancia` (tolerancia 0.5 pp, config).
   Precio objetivo `P*` = el precio de publicación (con IVA en MX) al que la
   contribución estimada iguala el goal, con la **misma fórmula del acta**:
   `contribución = I − C − F − L − R`, `I = P / (1 + iva)`, `R = r_isr · I`,
   `L = 0` (FBA), `F = fee(P)`. Como `F` se cotiza por precio, se resuelve en
   dos pasos: (a) `P₀` cerrando con `F` modelado como `ref · P + fijo`, donde
   `ref` y `fijo` salen de `fee_details` de la cotización vigente; (b)
   cotización real de Product Fees a `P₀` (append-only, ligada a la oferta) y
   recálculo; si `|m(P₁) − goal| > tolerancia`, un tercer paso y se detiene.
   El movimiento del día es `min(P*, P_actual · (1 + escalón_max))`,
   redondeado al centavo hacia arriba.
4. **Bajar** solo si `m_actual > goal` **y** `perdiendo_ventas = true` (regla
   5). Un solo movimiento a `P_goal` (el precio exacto del goal, resuelto
   como en 3), nunca menor. Si `P_goal ≥ P_actual` no hay bajada.
5. **Perdiendo ventas.** `u15 = Σ quantity` de los 15 días completos hasta
   ayer; `u60 = Σ quantity` de los 60 días anteriores a esa ventana.
   `perdiendo = u15 < (u60 / 60 · 15) · (1 − caída_pct)`. Exige **75 días de
   historia** de la publicación (regla 6 de Orbit: edad mínima del dato) y
   `u60 > 0`; si no, la señal es `sin_dato` y no dispara nada. La caída se
   guarda con sus dos números para auditoría.
6. **Buy Box.** Se registra `buy_box_is_own` y el precio de la Buy Box antes
   y después de cada cambio. Perderla **no** frena ni revierte (decisión 5);
   genera el aviso `buy_box_perdida` en flanco (S7).
7. **Límites** (config): escalón máximo por movimiento; **un cambio por
   producto cada 7 días** medido contra el último cambio `confirmado` o
   `pendiente`; tope diario por plataforma bajo la **cuota propia**
   `motor = 'precio'` en `apply_quota_state` (decisión 12); un cambio
   `no_confirmado` también consume cuota.
8. **Freno por no convergencia.** Tres cambios consecutivos en la misma
   dirección sin que `|m_actual − goal|` disminuya → `frenado(no_converge)`,
   aviso, y el producto no se mueve hasta que el dueño reponga el goal
   (nueva fila en `precio_goal`, que reinicia el conteo).
9. **Goal inalcanzable.** `P* > 2 · P_actual`, `P* ≤ C` (costo mal cargado),
   o cotización que no concilia (`TotalFeesEstimate ≠ Σ FinalFee`) →
   `goal_inalcanzable(motivo)`, sin movimiento, aviso.
10. **Sombra.** En `shadow` se calcula y registra todo (precio objetivo,
    cuenta, motivo) con `aplicado = false`; no se llama a Amazon para
    escribir. La cotización de fees sí se hace (es lectura) para que la
    sombra sea fiel.

## S5. Persistencia, identidad y reproducibilidad

- `precio_decision` (append-only, una por `(listing, platform, decision_date)`):
  resultado, `m_actual`, `goal`, `P_actual`, `P_objetivo`, `P_aplicado`,
  componentes `I, C, F, L, R` usados, `escenario_id`, `fee_observation_id`,
  `u15`, `u60`, `perdiendo`, `buy_box_is_own`, `mode`, `motivo`, `config_version_id`.
  Con esa fila cualquiera reproduce la cuenta sin volver a consultar Amazon.
- `precio_cambio` (append-only): `decision_id`, `precio_antes`, `precio_despues`
  (`money_amount` + `currency`, regla 4), `enviado_at`, `estado`
  (`pendiente` → `confirmado` | `no_confirmado` | `error`), `readback_precio`,
  `readback_at`, `error_code`, `es_reversa BOOLEAN`, `reversa_de BIGINT NULL`.
  **Nunca se edita el precio en `listing`** desde el motor: `listing` lo sigue
  refrescando su ingesta; la verdad del motor es `precio_cambio`.
- Idempotencia: la corrida del día para un `(listing, platform)` que ya tiene
  `precio_decision` no vuelve a decidir; un cambio `pendiente` bloquea
  cualquier cambio nuevo del mismo listing hasta resolverse.

## S6. Escritura en Amazon y reversa (regla 7: la reversa antes)

- **Camino único de escritura**: `app/spapi/precio_write.py`, sobre el
  cliente SP-API existente (`construir_ruta_listings(seller_id, sku)`):
  `PATCH /listings/2021-08-01/items/{sellerId}/{sku}` con `productType` (de
  `spapi_listing_estado_observation`) y un parche `replace` sobre
  `purchasable_offer[...].our_price[...].schedule[...].value_with_tax` para el
  marketplace. **La forma exacta del parche se sella con una sonda** sobre un
  producto controlado (fila 0.4 del plan), no se asume del doc.
- Orden inmutable por cambio: (1) fila `precio_cambio` `pendiente` con
  `precio_antes` = precio observado hoy; (2) PATCH; (3) readback por
  `GET listings item` (offers) y, al día siguiente, por
  `spapi_price_observation`; `confirmado` si coincide al centavo, si no
  `no_confirmado` sin reintento automático.
- **Reversa** = un `precio_cambio` con `es_reversa = true` y
  `precio_despues = precio_antes` del cambio revertido, mismo camino y mismo
  readback. Existe y se prueba con ids reales **antes** de que el primer
  cambio real salga (fila 0.4). Dos disparadores: (a) el motor, cuando un
  cambio queda `no_confirmado` con precio leído distinto del esperado y del
  anterior (estado inconsistente); (b) `tools/precio_reversa.py` por lote,
  dry-run + huella + go, para deshacer los últimos N días.
- **Errores de API**: `error` con código; un reintento por día; al tercer día
  consecutivo `frenado(api_error)` y aviso. Nunca ráfagas.
- **Nada de escrituras masivas sin ceremonia**: el tope diario de cuota es el
  candado; además, el primer encendido a `live` se limita a 3–5 productos
  elegidos por el dueño y se mide 14 días antes de ampliar (S8).

## S7. Visibilidad y avisos (decisión 11: ningún silencio)

- `/precios` (server-rendered como `/cortes`): (a) productos con goal:
  margen de hoy, goal, precio actual, precio objetivo, modo, último cambio y
  su estado; (b) lo que el motor hizo o habría hecho hoy, una frase por fila
  («subí de 116.00 a 127.60 MXN para llegar al goal de 30%; hoy vas en 24.1%»);
  (c) **no evaluados del día con su motivo** en palabras («costo vencido desde
  el 12-sep»). Cero filas de sombra se ven igual que las reales, marcadas.
- `/api/dashboard/salud` → bloque `precios` **dentro de
  `plataformas.<plataforma>`**: `evaluados`, `movidos`, `sombra`,
  `no_evaluados` por motivo, `frenados`, `goal_inalcanzable`, cuota
  `{used, cap, fuente}`.
- Telegram, sender nuevo en `app/notifica.py`, **en flanco por producto, una
  vez por racha** (patrón A.6 / `notifica_spapi_fallo`): `no_evaluado` 3 días
  seguidos, `goal_inalcanzable`, `no_confirmado`, `frenado`, `buy_box_perdida`
  tras un cambio propio. Fail-silent: un fallo del aviso no tumba la corrida.

## S8. Fases, encendido y aceptación

- **Fase 0 — Márgenes US.** Sellar en el spec de márgenes la política fiscal
  de `amazon_us` (sin IVA/ISR: `I = P`, retenciones = lo que Finances muestre
  como cargo recurrente o nada, declarado), FX MXN→USD para `C` con
  `fx_resolve` (par inverso solo si existe fuente; si no, `fx_ausente`), y la
  cotización de fees US con la misma API. Sale como acta 0.4 de
  `margen-estimado-01` y libera `amazon_us` para la estimación. **Sin esto,
  Fase B no arranca**; el motor MX no depende de ella.
- **Fase A — Motor FBA MX**: migración (`precio_goal`, `precio_decision`,
  `precio_cambio`, enum, GRANTs por columna, cuota `precio`), reglas puras
  con banco de pruebas, escritura + reversa con sonda real, `/precios` +
  `/salud` + avisos, herramientas selladas de goals y reversa, cron diario
  09:30 UTC (después del ledger de 08:15 y fuera del ciclo de Ads 08:40),
  sombra en producción, revisión independiente con catálogo de mutantes,
  encendido de 3–5 productos con go, medición a 14 días.
- **Fase B — US**: mismos caminos con la política de la Fase 0; sombra,
  encendido con go.
- **Aceptación verificable** (por fase, en el plan): cada regla de S4 con su
  test rojo-primero y su mutante; dry-run de las herramientas no escribe;
  la reversa deshace con readback; `/salud` cuenta lo mismo que la base;
  ningún camino a Amazon fuera de `precio_write.py` (candado de
  arquitectura); un día sin insumos produce N filas `no_evaluado` y cero
  llamadas de escritura.

## Divergencias con el traspaso, declaradas

`docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1 pide estrategias «match/beat
competitor», «Buy Box oriented», «inventory aware», «time-based» y modos
agresivo/equilibrado/conservador. Por decisión del dueño (S1 #1, #5, #6) este
motor tiene **una** estrategia: margen goal con bajada solo por pérdida de
ventas. Las demás quedan fuera de alcance; no son deuda, son decisión. El
historial completo, el floor por margen, el anti-thrashing y el dry-run
obligatorio del traspaso sí están (S4 #7, S5, S6).

Lecciones del sistema viejo que este spec convierte en regla: la fórmula que
ignoraba comisiones y dejó 46 días sin propuestas (aquí `F` siempre viene de
una cotización real y su ausencia bloquea, no calla); el paso de «sustain»
que midió −12% (aquí no hay pasos heurísticos: el precio objetivo se resuelve
y el escalón es un tope, no una regla); el stop-loss prometido que nunca
corrió (aquí la reversa se prueba con ids reales antes del primer cambio).
