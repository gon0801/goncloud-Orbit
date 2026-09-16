# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07 / ORBIT 09)

Version: 1.0, 2026-09-16 UTC. Estado: **plan sellado sobre el spec aprobado por
el dueño el 2026-09-15; cero implementación**. Base: `origin/master` `0617328`.
Spec: `docs/superpowers/specs/2026-09-15-repricing-01-design.md` (manda sobre
este plan). Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md`
> spec de márgenes y su acta 0.3 > spec de este plan > este plan.
Tracker: `ORBIT 09 — Módulo Repricing` y `AUTO-07 Repricing (plan formal)`.
`team_validation_mode: subagent` (cinco perspectivas sobre el borrador v0.1;
resultado en `docs/evidencia/repricing-01/plan-validacion.md`).
No hay fechas ni presupuestos en este plan. Ningún secreto en el repo.

## Resultado y límites

Cada producto con un goal de margen fijado por el dueño llega a ese margen
por precio, sube solo cuando le falta, baja solo cuando pierde ventas y nunca
abajo del goal. Todo cambio de precio queda en un ledger append-only con su
cuenta completa y su reversa probada antes del primer cambio real. Todo día en
que el motor no pudo evaluar un producto queda escrito con su motivo y se ve.

Fuera de alcance, declarado: FBM, Mercado Libre, promociones, kits, decisiones
por inventario, elasticidad, modos «agresivo/equilibrado» del traspaso.

## Decisiones ya cerradas (no se reabren)

Las doce de S1 del spec, con texto literal del dueño (2026-09-15). Resumen:
proteger margen; subir si el margen estimado no llega al goal por producto;
sube igual aunque rebase a la competencia (Buy Box se avisa); baja solo si las
unidades de 15 días caen contra el promedio de 60; automático dentro de
límites con sombra primero; MX y US; precio resuelto, no escalera; ningún
silencio; cuota propia.

Umbrales aceptados como **config** (`config_version.settings`), con sus
claves: `precio_caida_ventas_pct = 0.40`, `precio_escalon_max_pct = 0.10`,
`precio_dias_entre_cambios = 7`, `precio_cap_amazon_mx = 5`,
`precio_cap_amazon_us = 5`, `precio_tolerancia_pp = 0.5`,
`precio_freno_cambios = 3`, `precio_aviso_dias_sin_evaluar = 3`,
`precio_freno_dias_error = 3`. Tipo JSON número.

## Hechos verificados que condicionan el plan (2026-09-15, lectura de producción)

1. `spapi_price_observation` trae a diario precio propio, Buy Box (precio,
   dueño, `is_own`), precio mínimo y conteos para **342 ASINs MX y 176 US**.
2. La estimación por venta (`estimacion_*`) está desplegada **solo para FBA
   MX** (acta 0.3: FBM y US excluidos por contrato). 6 630 observaciones de
   oferta MX/FBA hasta el 2026-09-16. `fee_details` guarda el desglose de la
   cotización (referral + FBA), lo que permite modelar `F(P)` para resolver el
   precio objetivo (S4 #3).
3. Unidades por producto y día: `ledger_event` `kind='sale'` con `quantity` y
   `product_id`, fresco al día anterior (MX al 09-15, US al 09-14); la
   publicación se liga por `listing.product_id`. **No existe** tabla de
   líneas de orden SP-API: las unidades salen del ledger, no de Orders.
4. `spapi_listing_estado_observation` trae `seller_sku`, `asin` y
   `product_type` por publicación: los tres insumos del PATCH de precio.
5. **No existe camino de escritura de precios** en Orbit. El cliente SP-API
   ya valida rutas `listings` por `sellerId + sku` (`construir_ruta_listings`).
6. Cuotas: `apply_quota_state(motor, quota_date, used, cap)` admite un motor
   nuevo (`precio`) sin tocar los de Ads.
7. Avisos: `app/notifica.py` tiene el patrón de flanco por racha
   (`notifica_spapi_fallo`, A.6 de F2) y es fail-silent.
8. Sistema viejo (tracker, 2026-07/08): fórmula sin fees → 46 días sin
   propuestas; paso «sustain» → −12% medido; stop-loss prometido que nunca
   corrió. Cada una tiene su regla en el spec (Divergencias).

## Diseño (lo que el implementador no decide)

Está en el spec S2–S8. Invariantes que el revisor verifica en cada fila:

- **Un camino de escritura**: `app/spapi/precio_write.py` es el único módulo
  que llama al PATCH de listings; candado en `tests/test_architecture.py`.
- **Un escritor de goals**: `app/precio/goals_write.py`; `tools/precio_goal.py`
  solo pasa por él; `UPDATE precio_goal` crudo prohibido por el candado.
- **Append-only** en `precio_goal`, `precio_decision`, `precio_cambio`
  (triggers que rechazan `UPDATE`/`DELETE` salvo el `estado`/readback de
  `precio_cambio`, que solo avanza `pendiente → confirmado|no_confirmado|error`).
- **Dinero con moneda** (regla 4) en todas las columnas de precio.
- **Sin insumo, sin decisión**: `no_evaluado(motivo)` es una fila, nunca una
  ausencia; ninguna constante sustituye un dato.
- **Sombra fiel**: en `shadow` se cotiza y se resuelve igual; solo se omite el
  PATCH.
- **Reversa antes**: la fila A.4 (sonda con ids reales) precede a cualquier
  cambio real; D.2 no arranca sin E/A.4.
- **Cuota propia**: `motor = 'precio'`, cap por plataforma, `no_confirmado`
  consume.

## Etapas y tareas

| ID | Tarea | DoD (verificable) | Deps | Estado |
|---|---|---|---|---|
| 0.1 | [stage:verificacion] [lane:gate] [tdd:skip:sonda] **Inventario US para márgenes** (lead de datos, solo lectura): Product Fees `amazon_us` con una oferta FBA real (`Success`, `TotalFeesEstimate = Σ FinalFee`, `TaxAmount`); cargos recurrentes en Finances US (¿hay retención equivalente a ISR/IVA? ¿sales tax en el precio?); `fx_resolve` MXN→USD (¿existe el par inverso con fuente? si no, se declara `fx_ausente`); cobertura de `sku_cost` y ofertas US frescas ≤ 6 h. | `docs/evidencia/repricing-01/0.1/` con las sondas y sus respuestas literales; tabla de insumos US presente/ausente por componente `I, C, F, L, R` | — | cc:TODO |
| 0.2 | [stage:contrato] [lane:fast] [tdd:skip:contrato] **Acta 0.4 de `margen-estimado-01`: política fiscal US sellada por el dueño** (`I = P` sin IVA; `R` = lo que 0.1 demostró o 0 declarado con razón; `L = 0` FBA; FX de `C` por `fx_resolve` con fecha). Spec delta en el spec de márgenes; ampliación del universo a `amazon_us/fba`. | Acta con decisión literal del dueño; spec de márgenes con sección S9 «US FBA»; ninguna ausencia convertida en cero | 0.1 | cc:TODO |
| 0.3 | [stage:implementacion] [lane:gate] [tdd:required] **Estimación por venta para `amazon_us/fba`** (Muse): universo, normalización de 0.2, cotización de fees US ligada a la oferta, `fx_ausente` como motivo. | Rojo-primero: escenario US con oferta fresca produce contribución con los cinco componentes; sin FX → `null` con `fx_ausente`; sin cotización → `fee_ausente`; mutante que fije FX constante muere; readback en prod de N listings US con estimación | 0.2 | cc:TODO |
| A.0 | [stage:implementacion] [lane:gate] [tdd:required] **Migración `00NN_precio_goal.sql`** (Muse): enum `precio_mode`, tablas `precio_goal`, `precio_decision`, `precio_cambio` (S3/S5) con `money_amount + currency`, CHECK `0 < margen_goal_pct < 1`, un vigente por `(listing_id, platform)` (índice parcial `valid_to IS NULL`), triggers append-only, GRANTs por columna (`app_decide` inserta decisiones/cambios y avanza `estado`; `app_admin` inserta goals; `app_read` solo lee), semillas del DO que insertan y borran dentro de la transacción. | Rojo-primero con PostgreSQL real: `UPDATE precio_goal` rechazado; segundo goal vigente para el mismo `(listing, platform)` rechazado; `precio_cambio.estado` solo avanza; `app_read` no inserta; `docs/DATABASE.md` y `docs/DEPLOY.md` §«Migración 00NN» con backup del schema y verificación como lector | — | cc:TODO |
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] **`app/precio/goals_write.py` + `tools/precio_goal.py`** (Muse; patrón `tools/goals_modo_grupo.py` y `harvest_excepcion.py`): sembrar goal (`--listing/--sku --platform --goal-pct`), por lote desde CSV (`--csv`), cambiar `mode` (`--mode live` con ceremonia `--acepto-mutacion-real --esperado --huella --go`; `--mode shadow` sin ceremonia), cerrar goal (`valid_to`). Dry-run imprime resolución y huella del conjunto. Solo `app_admin`, cero Amazon. | Dry-run no escribe; `--go` escribe exactamente N filas; huella distinta aborta; `live` sin go rechazado; `shadow` sin go acepta; candado de escritor único ampliado a `precio_goal` pasa y **falla** con un `UPDATE` crudo sembrado | A.0 | cc:TODO |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] **Reglas puras** (Muse): `app/precio/reglas.py` (`decidir(insumos, cfg) -> Decision`, sin I/O), `app/precio/objetivo.py` (`precio_objetivo(componentes, goal, cotizar) -> Objetivo` con los pasos `P₀` modelado → cotización real → recálculo ≤ 3 pasos), `app/precio/ventas.py` (`senal_ventas(unidades_por_dia, hoy, cfg) -> Senal` con `u15`, `u60`, `perdiendo|sin_dato`). Banco de pruebas con fixtures de la acta (`P=116, I=100, C=40, F=15, R=2.5`). | Rojo-primero por cada regla de S4: (1) insumo faltante → `no_evaluado(motivo)`; (2) `precio_divergente` no decide; (3) `m < goal − tol` → `subir` con `P* = min(P*, P·1.10)` y cuenta completa; goal alcanzado en ≤ 3 cotizaciones; (4) `m > goal` sin pérdida → `mantener`; con pérdida → `bajar` a `P_goal` y nunca menor; (5) 74 días de historia → `sin_dato`; caída 39% no dispara, 40% sí; (6) Buy Box perdida no frena; (7) 6 días desde el último cambio → `mantener(cooldown)`; (8) 3 cambios sin acercarse → `frenado`; (9) `P* > 2P`, `P* ≤ C`, fees que no concilian → `goal_inalcanzable`; (10) `shadow` produce la misma decisión con `aplicado=false`. Catálogo de mutantes propio: uno por regla, todos muertos | A.0 | cc:TODO |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] **Escritura y reversa** (Muse): `app/spapi/precio_write.py` (`cambiar_precio(listing, precio, moneda) -> Cambio`: fila `pendiente` → PATCH `purchasable_offer` por `sellerId + sku + productType` → readback GET → `confirmado|no_confirmado`; `revertir(cambio_id) -> Cambio` con `es_reversa`; errores → `error` con código, sin reintento en la llamada); `tools/precio_reversa.py` por lote (`--desde <fecha>`, dry-run + huella + go). Candado: ningún otro módulo construye la ruta PATCH de listings. | Rojo-primero con cliente falso: orden fila→PATCH→readback; readback distinto → `no_confirmado` y **cero** reintentos; error 4xx/5xx → `error` con código; reversa escribe `precio_antes` y lee; dry-run de la herramienta no llama al cliente; candado de arquitectura falla con un PATCH sembrado fuera de `precio_write.py`; **forma del parche sellada por A.4**, hasta entonces marcada `pendiente_sonda` en el módulo | A.0 | cc:TODO |
| A.4 | [stage:verificacion] [lane:release] [tdd:skip:sonda] **Sonda de escritura y reversa con ids reales** (dueño con `!`, go literal; lead lee): un producto controlado elegido por el dueño en MX, cambio de +0.01 en el precio por `precio_write.py` con readback, y **reversa** por `tools/precio_reversa.py` con readback al precio original. Sella la forma exacta del parche (S6). | `docs/evidencia/repricing-01/A.4/`: los dos `precio_cambio` (`confirmado`, `es_reversa`), readbacks literales, y al día siguiente `spapi_price_observation` con el precio original; si el parche no funcionó, se corrige A.3 y se repite: **sin E/A.4 no hay D.2** | A.3 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] **Corrida diaria** (Muse): `app/precio/corrida.py` (`correr(platform, hoy) -> Resumen`: lee insumos de S2, llama `decidir`, escribe `precio_decision` para **todos** los goals vigentes, aplica solo `live` bajo cuota `precio`, avanza freno y reintento de errores), `app/cli.py precio --platform`, lock por plataforma, cron `40 9 * * *` en el crontab de `gon` con `flock` y log (`docs/DEPLOY.md`), `config_version` con las claves de umbrales. | Rojo-primero: día sin insumos → N filas `no_evaluado` y **cero** llamadas de escritura; cuota saturada → `mantener(cuota)` registrado; idempotencia: segunda corrida del día no decide ni escribe; `shadow` nunca llama a `cambiar_precio`; freno tras 3 días de `error`; línea de crontab pinzada por test como el vigilante | A.1, A.2, A.3 | cc:TODO |
| A.6 | [stage:implementacion] [lane:gate] [tdd:required] **Visibilidad y avisos** (Muse): `/precios` server-rendered (tres bloques de S7, frases en palabras, sombra marcada), bloque `precios` en `/api/dashboard/salud` **dentro de `plataformas.<p>`**, senders `notifica_precio_*` en flanco por racha (cinco casos de S7), fail-silent. | Rojo-primero: `/precios` 200 con los tres bloques y las frases con números reales del fixture; `/salud` cuenta lo mismo que `precio_decision` del día; cada aviso sale una vez por racha y calla al corregirse; un fallo del sender no tumba `correr` | A.5 | cc:TODO |
| R.1 | [stage:revision] [lane:gate] [tdd:required] **Revisión independiente** (kimi sobre un SHA concreto; lead audita): catálogo de mutantes del implementador (uno por regla de S4, uno por candado, dinero, cuota, reversa) re-ejecutado con base real; cero sobrevivientes o se cierran con test en el mismo PR. | `docs/evidencia/repricing-01/R.1/` (catálogo, re-mutación, APPROVE de kimi y del lead sobre el SHA) | A.6 | cc:TODO |
| D.1 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] **Despliegue en sombra MX** (dueño con `!`): backup del schema, migración en una transacción y verificación como lector, deploy por `git archive` + md5 + rebuild, cron instalado, `precio_goal` sembrado en `shadow` para los productos que el dueño elija (lote con go), **cinco corridas** en sombra leídas por el lead (propuestas, no evaluados por motivo, cuenta de tres productos reproducida a mano). | E/D.1 con SHA, salidas, y la lectura de las cinco corridas; `/precios` y `/salud` en producción con datos reales; ningún PATCH emitido (`precio_cambio` vacío salvo A.4) | R.1 | cc:TODO |
| D.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido de 3–5 productos MX** con go literal (`tools/precio_goal.py --mode live`), medición **14 días**: margen estimado antes/después, unidades 15d, Buy Box, cambios confirmados, avisos. | E/D.2 con los ids, los `precio_cambio` confirmados con readback, y la tabla de 14 días; decisión del dueño literal para ampliar o parar | D.1, A.4 | cc:TODO |
| D.3 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Ampliación MX** a todos los goals que el dueño decida, por lotes con go; `ORBIT 09` y `AUTO-07` avanzan en el tracker. | E/D.3; tracker anotado | D.2 | cc:TODO |
| B.1 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **US en sombra**: con 0.3 desplegada, goals US sembrados en `shadow`, cinco corridas leídas. | E/B.1 igual que D.1 | 0.3, D.1 | cc:TODO |
| B.2 | [stage:cierre-pr] [lane:release] [tdd:skip:ops] **Encendido US** de 3–5 productos con go y 14 días; luego ampliación. Cierra el plan; `ORBIT 09` `Done`. | E/B.2; tracker `Done` con evidencia | B.1, D.2 | cc:TODO |

## Clasificación (Required / Recommended / Optional / Reject)

- **Required**: A.0, A.1, A.2, A.3, A.4, A.5, A.6, R.1, D.1, D.2; 0.1, 0.2,
  0.3, B.1, B.2 para el alcance US decidido por el dueño.
- **Owner-gated**: 0.2 (política fiscal US), A.4, D.1, D.2, D.3, B.1, B.2
  (todo lo que toca producción o Amazon lleva go literal con `!`).
- **Reject** (decisión del dueño 2026-09-15): estrategias «match/beat
  competitor», «Buy Box oriented», «inventory aware», «time-based» y modos
  del traspaso; bajar precio por Buy Box perdida; explorar hacia arriba sin
  goal; aprobación manual por cambio; defaults de goal por plataforma;
  escalera de porcentaje fijo sin resolver el precio.
- **Reject** (reglas de Orbit): recalcular margen, costo o FX dentro del motor
  (regla 2); constantes en lugar de datos faltantes (regla 3); `UPDATE` sobre
  ledgers; reintentos en ráfaga; escrituras masivas sin ceremonia.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/área previstos | Restricción |
|---|---|---|
| 0.1–0.2 | `docs/evidencia/repricing-01/0.1/`, `docs/evidencia/margen-estimado-01/0.4/`, spec de márgenes (S9 nueva) | Solo lectura en producción; decisión literal del dueño |
| 0.3 | `app/estimacion_*.py` (universo y normalización US), `app/fx.py` (solo si 0.1 demostró fuente para el par), tests, `docs/` de estimación | No cambia FBA MX; una fila `null` con motivo antes que un cero |
| A.0 | `migrations/00NN_precio_goal.sql`, `tests/test_precio_migracion.py`, `tests/test_schema.py`, `docs/DATABASE.md`, `docs/DEPLOY.md` | Número de migración posterior al último en master al abrir el PR |
| A.1 | `app/precio/goals_write.py` (nuevo), `tools/precio_goal.py` (nuevo), `tests/test_architecture.py` | Solo `app_admin`; cero Amazon |
| A.2 | `app/precio/reglas.py`, `app/precio/objetivo.py`, `app/precio/ventas.py`, `app/precio/tipos.py`, `tests/test_precio_reglas.py` | Puro: sin I/O, sin fecha del sistema (la fecha entra como argumento) |
| A.3 | `app/spapi/precio_write.py` (nuevo), `app/spapi/client.py` (solo si falta el verbo PATCH), `tools/precio_reversa.py`, `tests/test_precio_write.py`, `tests/test_architecture.py` | Único camino al PATCH; reversa antes que escritura en el orden de commits |
| A.5 | `app/precio/corrida.py`, `app/cli.py` (subcomando), `app/quota.py` o equivalente (motor `precio`), `docs/DEPLOY.md` §cron, `tests/test_precio_corrida.py` | No toca `app/cycle.py` ni las cuotas de Ads |
| A.6 | `app/api_dashboard.py`, plantillas, `app/notifica.py` (senders nuevos), tests | `notifica_*` existentes intactos; bloque dentro de `plataformas` |
| R.1/D.x/B.x | Evidencia, `docs/DEPLOY.md`, PRs | Revisor solo lectura; deploy, siembra y encendido los corre el dueño con `!` |

Choca potencialmente con: **`fabrica-02` D.3** (primer harvest en vivo desde
el 19-sep: no comparte archivos, pero comparte ventana de despliegue y la
atención del dueño; D.1 de este plan va después de D.3 de F2); **`bids-01`
lote de inertes (4-oct)**: sin archivos en común; **`margen-estimado-01`**
(0.2/0.3 amplían su spec y código: se coordinan como spec delta, no se
reabren sus filas).

## Aceptación verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | Producto con goal y todos los insumos, `m = 24%`, goal 30% | `subir` a `min(P*, P·1.10)`, cuenta con `I, C, F, L, R` y `escenario_id`, ≤ 3 cotizaciones | test A.2 + fila `precio_decision` en D.1 |
| AC2 | Mismo producto sin cotización de fees hoy | `no_evaluado(fee_ausente)`, cero PATCH, visible en `/precios` y `/salud` | test A.2/A.5/A.6 + D.1 |
| AC3 | `m = 35%`, goal 30%, `u15` 50% abajo del promedio de 60 | `bajar` a `P_goal` exacto, nunca menor | test A.2 |
| AC4 | Igual que AC3 con 74 días de historia | `mantener`, señal `sin_dato` | test A.2 |
| AC5 | Cambio escrito, readback distinto | `no_confirmado`, cero reintentos, aviso una vez, cuota consumida | test A.3/A.5/A.6 |
| AC6 | Reversa de un cambio real | `precio_cambio` `es_reversa` confirmado por readback; `spapi_price_observation` del día siguiente en el precio original | A.4 |
| AC7 | Tercer cambio consecutivo sin acercarse al goal | `frenado(no_converge)`, aviso; goal nuevo reinicia | test A.2 |
| AC8 | Cuota `precio` en cap | `mantener(cuota)` registrado; las de Ads intactas | test A.5 |
| AC9 | PATCH sembrado fuera de `precio_write.py` | candado de arquitectura falla | test A.3 |
| AC10 | Segunda corrida del mismo día | ninguna decisión ni escritura nueva | test A.5 |
| AC11 | Cinco corridas en sombra en producción | N propuestas con cuenta reproducida a mano en 3 productos; cero `precio_cambio` | D.1 |
| AC12 | 14 días de 3–5 productos en vivo | tabla antes/después; decisión literal del dueño | D.2 |

## Secuencia de despliegue y reversa

1. Migración `00NN` en una transacción, después del backup del schema
   (`pre00NN_precio_*.sql`), verificación como `orbit_read` (tablas, enum,
   triggers, GRANTs por columna, índice parcial). Reversa: restaurar el dump
   en una transacción; no recrear tablas a mano.
2. Deploy del código por `git archive` del SHA aprobado, md5 idéntico,
   rebuild, `/health`, `/precios` 200, `/salud` con el bloque `precios`.
   Reversa: `predeploy-<stamp>/` y rebuild.
3. Cron `precio` instalado en el crontab de `gon` (aditivo, `flock`, log).
   Reversa: quitar la línea.
4. Siembra de goals en `shadow` (dueño, go). Reversa: cerrar goals
   (`valid_to`), sin tocar Amazon.
5. Encendido de 3–5 productos (dueño, go). Reversa: `--mode shadow` sin
   ceremonia y, si hubo cambios, `tools/precio_reversa.py`.

## Divergencias y residuales declarados

- Traspaso §Módulo 1: una sola estrategia por decisión del dueño (spec,
  Divergencias). No es deuda.
- Inventario: se lee, no decide. Un plan posterior puede agregar «subir con
  stock bajo» como regla nueva con su propia decisión del dueño.
- Unidades vendidas desde el ledger contable, no desde Orders: el ledger
  llega con un día de rezago y depende del snapshot de accounting de las
  08:15 UTC; un día sin snapshot deja `ventas_sin_dato` (no evaluado), no una
  bajada indebida.
- La forma exacta del PATCH de precio se sella en A.4 con producto real; el
  plan no la asume del documento de la API.
- Fase 0 puede terminar en «US no liberado» si 0.1 no encuentra fuente para
  algún componente; entonces B.x queda `blocked` con motivo y el plan cierra
  con MX. Se declara, no se fuerza.

## Snippet para `plans/manifest.json` (aplicado en este PR; no cambia `active`)

```json
{
  "name": "repricing-01",
  "path": "plans/repricing-01.md",
  "description": "REPRICING 01 - motor de precios por goal de margen (M1/AUTO-07/ORBIT 09). Spec aprobado por el dueno 2026-09-15: proteger margen; sube si el margen estimado no llega al goal por producto; baja solo si las unidades de 15d caen vs promedio de 60d, nunca bajo el goal; automatico dentro de limites con sombra primero; cuota propia; ningun silencio. Fases: 0 margenes US (acta 0.4), A motor FBA MX (migracion, goals sellados, reglas puras, escritura+reversa con sonda real, corrida diaria, /precios, R.1, sombra, encendido 3-5 productos y 14 dias), B US. Cero implementacion."
}
```

## Estado para la siguiente sesión

- Plan v1.0 sellado sobre el spec del 2026-09-15; validado por cinco
  perspectivas (evidencia en `docs/evidencia/repricing-01/plan-validacion.md`).
- Nada implementado. Primer movimiento: 0.1 (lead, solo lectura) y A.0 (brief
  para Muse) pueden ir en paralelo; A.4 exige un producto controlado elegido
  por el dueño.
- Este plan no arranca antes de cerrar D.3 de `fabrica-02` (19-sep) por
  atención del dueño, no por dependencia técnica.
