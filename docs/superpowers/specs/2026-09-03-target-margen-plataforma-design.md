# ORBIT 06 Fase 2 — target de ACoS derivado del margen neto medido por plataforma

**Estado: APROBADO por el dueño (2026-09-03).** Literales del dueño en §1.
Precedencia: `docs/CONTEXTO.md` (reglas 1-10) > este spec > `plans/orbit-06.md`.

## 1. Decisiones literales del dueño (2026-09-03)

- Arranque: «ok hay que arrancar el diseño de orbit 06 fase 2».
- Fracción del margen para publicidad, pregunta guiada con cuatro opciones
  (mitad / tres cuartos / todo / distinto por país): **«La mitad (utilidad)»**.
- Diseño completo (camino A, §3-§9): **«ok va asi»**.
- Pregunta del dueño que fija un requisito: «ese margen se va a ir ajustando
  automaticamente no? no es estatico, si cambian [COGS] si suben precios etc»
  → §6 (frescura) y §7 (lag declarado) son obligatorios, no opcionales.

## 2. Estado medido que origina el diseño (regla 8, lead, 2026-09-03, base viva)

Ledger de 90 días maduros (`event_date` en `[D-105, D-15)`), todo en MXN
(el ledger de US también reporta en MXN; el costo está en MXN: la razón es
adimensional, **sin FX**). Cobertura de costo: MX 260/263 ventas, US 154/154.

| | amazon_mx | amazon_us |
|---|---|---|
| Venta | 318,066 | 394,406 |
| Cargos de Amazon SIN publicidad (referral, envío, FBA, IVA/ISR retenido, refunds, otros) | −33.6 % | −48.9 % (envío solo: −21.0 %: guía cross-border ~454 MXN + cargo Amazon ~94 MXN por pedido; refunds 12/154 pedidos) |
| COGS | 26.2 % | 15.2 % |
| **Margen neto antes de publicidad** | **40.2 %** | **35.9 %** |
| Publicidad (`fee_type = ads`) sobre venta total | 10.4 % | 22.3 % |
| Target vigente del motor | 20 (setting, manual) | 20 (setting, manual) |

Hechos que cambian el planteamiento original de la 2.1:

1. **La contribución por campaña es uniforme** (`v_contribucion_entidad`:
   ≈ 0.68-0.70 MX, ≈ 0.78-0.81 US en TODAS las campañas con ingreso): un
   target por campaña no discrimina nada hoy. La palanca real es por
   plataforma.
2. **El 20 manual ≈ la mitad del margen** (20/40 MX, 20/36 US): la decisión
   del dueño («la mitad») deja los números casi donde están (≈ 20.1 MX /
   ≈ 17.9 US). El valor del cambio no es mover pujas hoy: es que el target
   quede **medido, con procedencia y auto-ajustable**.
3. `v_contribucion_entidad` sigue **no decisoria** (D1.bis de
   `docs/MARGEN-ENTIDAD.md`, intacto). La fuente del target es el **ledger
   por plataforma**: dinero real (venta, cargos, costo) sin supuestos de
   atribución ni `listing_price`, y sin el par con/sin halo que en US
   invierte el signo (−1,318 vs +5,101 USD).

## 3. D1 · Fuente y fórmula — vista `v_target_margen_plataforma`

Una fila por `platform` (solo `amazon_mx`, `amazon_us`; sellado 9: MeLi
fuera). Ventana fija de **90 días maduros**: `event_date >= hoy − 105` y
`event_date < hoy − 15` (madurez D-15 del ledger, hallazgo 0.6: el costo
madura al día 15). `hoy` = `CURRENT_DATE` UTC en el momento del ciclo.

```
-- VENTAS de la ventana, partidas por si su COGS es conocido (regla 3: una
-- venta sin costo NO se cuenta con costo cero; se saca de AMBOS lados).
ventas_ventana   = kind = 'sale' AND event_date ∈ [D-105, D-15)
cogs_i           = cost_amount × quantity  (costo vigente a la fecha de la venta,
                   MISMA moneda que la venta; en otra moneda o ausente → la venta
                   es NO CUBIERTA, jamás se convierte ni se rellena)
venta_total      = Σ amount de ventas_ventana
venta_cubierta   = Σ amount de ventas_ventana con cogs_i conocido
cogs             = Σ cogs_i
cobertura        = venta_cubierta / venta_total          -- POR MONTO, no por conteo

-- CARGOS. Los que traen order_id pertenecen a SU venta (aunque el cargo caiga
-- fuera de la ventana); los de plataforma sin order_id se prorratean.
cargos_con_orden = Σ amount WHERE kind IN ('fee','refund','withholding')
                     AND coalesce(fee_type,'') <> 'ads'
                     AND order_id ∈ (order_id de ventas_ventana CUBIERTAS)
                     -- SIN filtro de fecha propio: la ventana la fija su venta
cargos_sin_orden = Σ amount WHERE kind IN ('fee','refund','withholding')
                     AND coalesce(fee_type,'') <> 'ads' AND order_id IS NULL
                     AND event_date ∈ [D-105, D-15)
cargos_prorrateados = cargos_sin_orden × cobertura

margen_neto_pct  = 100 × (venta_cubierta + cargos_con_orden + cargos_prorrateados
                          − cogs) / venta_cubierta        -- los cargos son negativos
dias_con_venta   = count(DISTINCT event_date) de ventas_ventana
ledger_fresco_at = max(observed_at) del ledger de la plataforma
fees_sin_tipo    = count(*) WHERE kind = 'fee' AND fee_type IS NULL
                            AND event_date ∈ [D-105, D-15)
```

Columnas: `platform, ventana_desde, ventana_hasta, venta_total,
venta_cubierta, cargos_con_orden, cargos_sin_orden, cogs, cobertura,
dias_con_venta, fees_sin_tipo, margen_neto_pct, ledger_fresco_at, moneda` (`moneda` = la única `amount_currency` de la ventana; si hay mezcla
→ **NULL fail-loud**, mismo patrón que `v_entidad_inerte.moneda`).
Cualquier condición de §5 deja `margen_neto_pct` **NULL** (regla 3: hueco,
jamás cero), y también lo deja NULL `fees_sin_tipo > 0`: un cargo de
publicidad que llegue sin clasificar se restaría del margen Y se cobraría en
el ACoS del motor — doble castigo sobre la misma puja (fail-loud, mismo
patrón que la mezcla de moneda; medido 2026-09-04: 0 de 1,226 fees sin tipo).
La vista NO conoce la fracción ni el target: solo mide.

`v_margen_plataforma` (0001) **no se reusa**: no tiene ventana, no separa la
publicidad de los demás cargos y su `margen_contribucion` exige cobertura
100 %. Se deja intacta (superficie de lectura).

## 4. D2 · Fracción y target derivado

`target_derivado_pct = fraccion × margen_neto_pct`, con `fraccion` en
`config_version.settings` bajo la clave
**`ads_target_fraccion_margen_<platform>`** (string decimal, como los demás
settings; hoy `"0.5"` para ambas plataformas por decisión literal del dueño).
Se cambia como los caps: `config_version` nueva, append-only, `app_admin`.
Clave **ausente** → el peldaño no aplica (§5): esa ausencia ES el
interruptor de la fase. Clave **presente pero inválida** (no numérica, NaN,
<= 0, > 1) → **ValueError ruidoso que tumba el ciclo**, idéntico a
`target_desde_settings` ("config CORRUPTA, no dato faltante": camuflarla de
ausente dejaría al motor decidiendo con un target que nadie configuró,
regla 3). Un `"0,5"` con coma no puede quedar en silencio.

## 5. D3 · Peldaño nuevo en la cascada de targets

Cascada sellada de `app/optimizer/goals.py`, hoy: `goal_campana →
goal_plataforma → setting_plataforma → cache_estado → default`. Pasa a
**SEIS** peldaños con nombre EXACTO nuevo `margen_plataforma`:

```
goal_campana → goal_plataforma → margen_plataforma → setting_plataforma → cache_estado → default
```

- `goal_campana` con **target explícito** pisa siempre (override manual por
  campaña: lanzamientos, defensa). `goal_plataforma` con target explícito
  también pisa.
- **Semántica de un goal de campaña con `target_acos_pct` NULL** (hallazgo
  A2 de la cross-review; el spec era ambiguo y hoy hay DOS goals así en
  producción: ids 6 y 7, campañas 3909 y 3926, creados para floor/ceiling).
  Regla sellada: `margen_plataforma` **se comporta exactamente como
  `setting_plataforma`** — es su reemplazo medido, no un peldaño de goal.
  Un goal de campaña sin target bloquea `goal_plataforma` (semántica
  existente, intacta) pero **NO** bloquea `margen_plataforma`: la cascada
  sigue bajando igual que hoy baja hasta el setting. Consecuencia
  deliberada: esas dos campañas pasan del 20 manual al derivado, que es el
  objetivo de la fase.
- **Plumbing** (A2b): las dos funciones de cascada siguen **puras**. El
  orquestador (`app/cycle.py`) lee la vista y el `ultimo` de `notes` UNA vez
  por ciclo y por plataforma, calcula el candidato del peldaño (derivado ->
  clamp de banda -> paso máximo) y se lo pasa ya resuelto a ambas variantes
  como un parámetro nuevo `target_margen: Decimal | None` (None = el
  peldaño no aplica; el motivo viaja aparte para `notes`). Ninguna cascada
  abre conexión ni conoce `platform`: el candado de pureza de
  `tests/test_architecture.py` no se toca, y la equivalencia motor-dashboard
  se prueba pasando el MISMO `target_margen` a las dos.
- Las DOS variantes (`cascada_target_acos` del motor y
  `cascada_target_acos_con_procedencia` del dashboard) ganan el peldaño; el
  test de equivalencia existente se extiende (regla 2: un número, una
  fuente). El nombre `margen_plataforma` entra al vocabulario cerrado de
  procedencias del dashboard y a `MOTIVOS_ES_SALUD`/etiquetas.
- El peldaño **se abstiene** (devuelve None y la cascada sigue al setting)
  SOLO por datos inválidos: `margen_neto_pct` NULL (incluye moneda mezclada
  y `fees_sin_tipo > 0`); `cobertura < 0.95` **por monto**;
  `dias_con_venta < 60`; `venta_cubierta <= 0`; fracción ausente;
  `ledger_fresco_at < hoy − 3 días` (ledger rancio, §6). Cada abstención
  deja su motivo en `notes.target.motivo_abstencion` (vocabulario cerrado:
  `sin_margen`, `cobertura_baja`, `ventana_corta`, `sin_fraccion`,
  `ledger_rancio`) y una línea en el digest. **Nunca cae a cero**; cae al
  setting de hoy.
- **La banda [10, 45] CLAMPEA, no abstiene** (enmienda por la cross-review
  de kimi, hallazgo A1 alta): un `target_derivado_pct` fuera de la banda se
  recorta al extremo, y solo después se aplica el paso máximo. Abstenerse
  por banda creaba un precipicio: con un derivado de 45.2 el target caía de
  golpe al setting (20) — 25 puntos en un ciclo, disparando la banda -25 %
  en masa —, y al día siguiente, con 44.9, volvía a ~44.8: latiguazo diario
  de pujas reales por un artefacto de frontera. El caso simétrico era peor:
  un margen realmente colapsado (derivado 9) abstenía y dejaba al motor
  pujando con el setting manual justo cuando más urgía recortar. Con clamp
  no hay salto en ningún borde, y la protección contra un dato corrupto la
  da el paso máximo (0.5/ciclo) más el aviso: **cada ciclo cuyo derivado
  cae fuera de la banda emite línea de digest** (`derivado_fuera_de_banda`
  con el valor crudo), aunque el target aplicado sea el clampeado.
- **Paso máximo**: `target_aplicado = clamp(target_derivado, ultimo − 0.5,
  ultimo + 0.5)` puntos por ciclo (≈ 3.5 por semana natural; el promedio de
  90 días se mueve mucho menos). `ultimo` = `notes.target.aplicado` del
  último ciclo **live** `done` de la misma plataforma cuyo peldaño fue
  `margen_plataforma`; si no existe (primer ciclo), `ultimo` = el target que
  la cascada habría dado sin el peldaño (el setting), así la entrada es
  gradual desde 20 y no un salto. Sin tabla nueva: la memoria es el propio
  `optimizer_cycle.notes`.

## 6. D4 · Frescura obligatoria (requisito literal del dueño)

Medido 2026-09-03: `ledger_event` última ingesta **2026-08-31** (una sola
corrida por plataforma), `fx_rate` última **2026-08-28**; los costos sí se
refrescan a diario (`refresh_costos.sh`, 07:30 UTC). Sin cron, el margen se
congela y el target «auto-ajustable» sería mentira. Tarea de infra ANTES
del código del motor: `ingest ledger` e `ingest fx` leen el MISMO snapshot
SQLite de accounting que ya usa `refresh_costos.sh` a diario (07:30 UTC);
se extiende ese script para correr los tres pipelines con el mismo snapshot
(flags exactos según `docs/DEPLOY.md` y `app/cli.py`, regla 8), ANTES del
ciclo de 08:40 y DESPUÉS del sync de accounting (`sync_ads_to_ledger.py`,
cada 6 h); un pipeline caído no tumba a los otros.
La guarda `ledger_rancio` (§5) es el candado de código que detecta si el
cron muere.

## 7. Lag declarado (para el dueño; va al digest y a `/salud`)

Una venta entra al cálculo 15 días después de ocurrir; la ventana es de 90
días, así que un cambio de costo/precio/envío se refleja a la mitad en ~6
semanas y por completo en ~3 meses; el paso máximo suaviza además el
target. No es un defecto: es el reloj honesto de un promedio maduro.

## 8. D5 · Freeze y replay

El ciclo ya congela `target_acos_pct_usado` en `decision.inputs`; se añade
`target_procedencia` (nombre del peldaño) y, cuando el peldaño es
`margen_plataforma`, el snapshot `{margen_neto_pct, fraccion, cobertura,
ventana_desde, ventana_hasta, target_derivado, target_aplicado}`.
`reproduce()` sigue leyendo SOLO `target_acos_pct_usado` → las decisiones
históricas replayean idénticas **por construcción** (golden: la suite de
replay no cambia). `notes.target` del ciclo lleva el mismo snapshot por
plataforma (fuente del paso máximo y de `/salud`).

## 9. D6 · Superficie

- `/salud`: por plataforma, target vigente + procedencia + margen medido +
  fracción + ventana + edad del ledger + cobertura por monto; etiqueta
  española del peldaño. Además el ratio **`ad_revenue` de la ventana sobre
  `venta_total`** (hallazgo A7): el margen se mide sobre la venta TOTAL de
  la plataforma mientras el target se aplica contra el ACoS, que es
  `cost / ad_revenue` (solo lo atribuido a anuncios). La equivalencia "la
  mitad del margen" vale para la mezcla ads/orgánico de hoy (publicidad =
  10.4 % MX / 22.3 % US de la venta total); si esa mezcla se mueve mucho, el
  mismo derivado deja de significar lo mismo. Supuesto declarado, con su
  medidor a la vista: no se corrige en código, se vigila.
- Digest (Telegram): línea cuando el peldaño se abstiene (con motivo),
  cuando el derivado cae fuera de la banda (valor crudo), y cuando el target
  aplicado **acumula** un cambio >= 1 punto desde la última línea emitida.
  El acumulado es obligatorio (hallazgo A8): como el paso máximo es 0.5 por
  ciclo, un umbral "cambio >= 1 punto respecto al ciclo anterior" **no
  dispara nunca** y el target podría derivar 3.5 puntos por semana en
  silencio. El ancla del acumulado (`notes.target.ultimo_avisado`) viaja en
  `notes` junto al resto.
- `docs/CONTEXTO.md`: la cascada pasa a seis peldaños con el literal del
  dueño (DoD de la 2.1).

## 10. D7 · Entrada (reemplaza el DoD original de la 2.2)

El criterio «solo entra si AUMENTA la tasa de acción útil» no aplica: con
fracción 0.5 el target derivado ≈ el manual y la diferencia de decisiones es
casi nula por diseño. Criterio nuevo, sellado con el dueño:

1. Replay golden intacto (§8).
2. **Un ciclo sombra comparado**: sobre las mismas entradas del ciclo del
   día, la tabla de decisiones con el target manual vs el derivado (cuántas
   cambian de banda, cuáles, y los dos targets), con el `SELECT` en la
   evidencia. Un solo ciclo basta (enmienda de cadencia del dueño,
   2026-09-03: «un test es suficiente»).
3. **Go literal del dueño** con los números en la mano → `config_version`
   nueva con `ads_target_fraccion_margen_*` = "0.5" (sin la clave, el
   peldaño no existe: ese es el interruptor).

## 10.bis Adjudicación de la cross-review (kimi, 1 ronda, 2026-09-04)

Revisión adversaria del DISEÑO antes de implementarlo (la ronda que permite
`CLAUDE.md`; no habrá segunda: los bloqueantes se resolvieron aquí). Veredicto
de kimi: *implementable con correcciones*, 4 bloqueantes. Adjudicación del
lead, con medición en la base viva donde aplicaba:

| # | Hallazgo | Veredicto del lead | Dónde quedó |
|---|---|---|---|
| A1 alta | La banda [10,45] **abstenía** en vez de clampear: precipicio de 25 puntos y latiguazo diario de pujas reales; y con el margen realmente colapsado el motor se quedaba en el setting manual justo cuando urgía recortar | **ACEPTADO**, es un fallo real del diseño del lead | §5: la banda clampea; la abstención queda solo para datos inválidos; línea de digest cuando el derivado sale de la banda |
| A2 alta | Cascada: (a) el spec no fijaba si un goal de campaña con target NULL bloquea el peldaño nuevo — hay **2 goals así en producción** (ids 6 y 7); (b) `cascada_target_acos` es pura y no puede leer vista ni `notes` con su firma actual | **ACEPTADO** las dos partes | §5: `margen_plataforma` se comporta como `setting_plataforma` (un goal sin target NO lo bloquea); el orquestador resuelve el candidato y lo pasa como `target_margen` a ambas cascadas, que siguen puras |
| A3 media-alta | COGS de cobertura parcial: las ventas sin costo aportaban venta sin costo — el cero disfrazado de dato que la regla 3 prohíbe — inflando margen y pujas; y la cobertura se medía por CONTEO | **ACEPTADO**. Medido: cobertura por monto 98.22 % MX / 100 % US; el sesgo vale **0.47 puntos de margen** en MX hoy | §3: cobertura por MONTO; el margen se calcula solo sobre la venta cubierta, con los cargos de plataforma prorrateados |
| A4 media | Fracción presente pero inválida se tragaba en silencio, contra el patrón sellado de `target_desde_settings` | **ACEPTADO** | §4: ausente = interruptor; presente e inválida = `ValueError` |
| A5 media | Refunds cuya venta original cae fuera de la ventana restan sin su ingreso | **ACEPTADO**. Medido: **4 de 6** refunds MX y **11 de 29** US son huérfanos (−1,588 MXN y −6,814 MXN: 1.7 % de la venta US) | §3: los cargos con `order_id` pertenecen a SU venta (sin filtro de fecha propio); los de plataforma sin `order_id` se prorratean |
| A6 media | `fee_type` NULL se contaba como cargo no-publicitario: un cargo de ads sin clasificar se restaría del margen Y se cobraría en el ACoS (doble castigo) | **ACEPTADO** como seguro barato. Medido: **0 de 1,226** fees sin tipo hoy | §3: `fees_sin_tipo > 0` deja el margen NULL (fail-loud, como la mezcla de moneda) |
| A7 media | Denominadores distintos: margen sobre venta total, ACoS sobre revenue atribuido | **ACEPTADO como supuesto declarado**, no como corrección de fórmula: por peso de ingreso anunciado la cuenta es coherente; lo que cambia con la mezcla es la equivalencia | §9: se publica el ratio `ad_revenue / venta_total` en `/salud` para vigilarlo |
| A8 media | El digest exigía un salto >= 1 punto que el paso máximo (0.5) **nunca** produce: la deriva sería invisible | **ACEPTADO** | §9: umbral sobre el cambio ACUMULADO desde el último aviso |
| A9 baja | "Replay idéntico por construcción": kimi no pudo verificarlo | **VERIFICADO por el lead y descartado**: `app/optimizer/replay.py` no importa `goals` ni llama a la cascada; sus dos caminos leen `inputs["target_acos_pct_usado"]` (líneas 94 y 154). Los dos call sites de la cascada (`cycle.py` 1209 y 1308) están en `_procesa_decisora` y `_procesa_grupo`: ambos camino vivo | Sin cambio; el DoD 2.3(e) lo sella con un test |
| A10-A13 bajas | `ultimo` viejo tras pausas; `ledger_fresco_at` mide ingesta y no datos nuevos; ISR retenido tratado como costo; `CURRENT_DATE` de la vista vs el ciclo | **ACEPTADOS como declaraciones** | Van al COMMENT de la vista y al docstring: el invariante "`observed_at` solo avanza con filas nuevas", el ISR como decisión consciente y conservadora, y que la evidencia auditada es el freeze de `ventana_desde/hasta`, no la vista |

Lo que kimi revisó y confirmó bien: la exclusión de la publicidad por
`fee_type` es el punto correcto y no hay doble conteo dentro de la vista; no
hay FX en la razón (MXN/MXN) y se rechaza convertir; la memoria en
`optimizer_cycle.notes` sin tabla nueva respeta la regla 2; no reutilizar
`v_margen_plataforma` ni tocar `v_contribucion_entidad` es lo correcto; la
entrada gradual desde el setting evita el salto inicial; madurez D-15 y
ventana de 90 días cumplen la regla 6.

## 11. Reject (con razón)

- **Target por campaña desde `v_contribucion_entidad`**: no discrimina
  (margen uniforme), señal sellada no decisoria, y el par con/sin halo se
  contradice en US. Queda como peldaño futuro (`margen_campana`, entre
  `goal_plataforma` y `margen_plataforma`) si el catálogo se diversifica.
- **Reusar `v_margen_plataforma` tal cual**: sin ventana ni separación de
  la publicidad (contaría el gasto de ads dos veces contra el ACoS).
- **Convertir moneda dentro de la razón**: innecesario (MXN/MXN) y abre la
  trampa del par invertido de `fx_resolve` (nota de sello de la 1.2).
- **Rellenar costo faltante / cobertura parcial con promedio**: trampa
  pagada (`sales_history.cogs`); cobertura < 95 % = abstenerse.
- **Tabla nueva de estado del target**: `optimizer_cycle.notes` ya es la
  memoria auditable; una tabla más es una fuente más (regla 2).
- **Mover el target sin tope**: un mes con devoluciones altas en US
  bajaría el target 5-8 puntos de golpe y el motor recortaría pujas en
  masa por ruido.

## 12. Reparto y proceso

- **Infra (2.2)**: cron diario de ledger + fx (Grok o lead; es ops).
- **GLM (2.3)**: migración 0015 (vista), peldaño en ambas cascadas, guardas,
  paso máximo, freeze, `notes.target`, `/salud`, digest, tests rojos
  (regla 9) y el SELECT de comparación sombra. Prohibido tocar producción;
  la corrida real es del lead.
- **Lead (2.4)**: review contra la base viva + CodeRabbit (1 ronda), deploy,
  ciclo sombra comparado, go del dueño, `config_version` con la fracción,
  verificación del primer ciclo con procedencia `margen_plataforma`,
  CONTEXTO sellado, AppFlowy.
