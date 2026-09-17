# REPRICING 01 — Motor de precios por goal de margen (M1 / AUTO-07)

Fecha: 2026-09-15 UTC. Versión 1.3 (2026-09-16): diseño aprobado por el dueño
en diálogo el 2026-09-15 (decisiones 1–12 en S1), corregido con la revisión de
cinco perspectivas del 2026-09-16 (S9), ampliado el mismo día con las
decisiones 13–15 del dueño (**FBM con envío medido, toda publicación activa
contemplada, Mercado Libre en el alcance**, S10 y S11), y corregido de nuevo
con la **segunda ronda de cinco perspectivas sobre E, B y M** del 2026-09-16,
que invalidó cuatro afirmaciones que este spec daba por medidas (S9). Sellado
para el plan `plans/repricing-01.md`. Base de lectura `origin/master`
`1a2a8c2`.

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

El alcance final es **toda publicación activa del negocio**: Amazon México y
Estados Unidos en sus dos canales (FBA y FBM) y Mercado Libre. Se llega por
fases, y ninguna publicación queda invisible mientras tanto: la que aún no
tiene motor aparece contada, con su motivo y con la fase que la habilita
(S10).

Lo que este spec NO cubre y queda declarado, no rellenado con ceros:
promociones, kits, decidir por inventario (solo invalida la señal de ventas),
elasticidad, modos «agresivo/equilibrado» del traspaso, reversa automática.

## S1. Decisiones del dueño (textos literales)

Decisiones 1–12, cerradas el 2026-09-15:

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
| 9 | Alcance inicial Amazon; MeLi y FBM fuera **(ampliada por 13 y 15)** | «b» |
| 10 | Enfoque: motor por goal con **precio resuelto** (no escalera ciega) | «si, la 1 y ese orden» |
| 11 | **Ningún silencio**: todo producto no evaluado se registra con motivo y se muestra | «si un dia falta un dato ... tiene que mostrarlo claramente no puede fallas en silencio» |
| 12 | El repricing tiene **su propia cuota**, separada de las de Ads | «tiene su propia cuota» |

Decisiones 13–15, cerradas el 2026-09-16 tras medir el catálogo (S11):

| # | Decisión | Texto literal |
|---|---|---|
| 13 | **FBM entra**: el costo del envío se toma de lo que Amazon ya cobra por las etiquetas, que llega en los reportes | «el precio de los envios fmb vienen en los reportes de amazon por que ahi se compran las etiquetas» |
| 14 | **Toda publicación activa queda contemplada**, con motor o con motivo declarado | «todas las publicaciones tienen que ser contempladas» |
| 15 | **Mercado Libre entra al alcance** | «tambien agregar meli» |

Umbrales del lead, aceptados como **valores de config, no constantes**
(claves y cotas en el plan): caída de ventas ≥ 40% sostenida 3 corridas;
volumen mínimo `u60 ≥ 20`; escalón máximo por movimiento 10% en ambas
direcciones; movimiento mínimo el mayor de 1% o 1.00 MXN / 0.10 USD; un
cambio por producto cada 7 días; tope diario de cambios por plataforma 5;
banda de goal admisible 10%–60%; freno tras 3 cambios en la misma dirección
sin acercarse al goal; aviso tras 3 días seguidos sin evaluar; reintento de
escritura una vez por día, freno al tercer día; envíos mínimos por producto
para estimar `L` en FBM: 6. Todo umbral fuera de su cota al leer la config es
un error ruidoso, nunca un valor corregido en silencio.

## S2. Fuentes: una por número, todas existentes

| Número | Fuente (única) | Frescura exigida | Si falta |
|---|---|---|---|
| **Precio actual `P_actual`**, contribución estimada y componentes `P, I, C, F, L, R` | El **escenario vigente de la estimación por venta** (`estimacion_*`, acta 0.3): oferta ≤ 6 h, costo vigente de la corrida `ok` del día, FX exacto o ≤ 7 días, cotización de fees ligada a esa oferta. `P_actual` es el precio de esa oferta: es el precio al que se cotizaron las fees y se calculó la contribución | escenario `disponible` del día UTC | el motivo que ya declara la estimación (`oferta_desactualizada`, `costo_desactualizado`, `fx_ausente`, `fee_ausente`, `impuesto_fee_pendiente`…) |
| **Costo de envío `L` en FBM** (decisión 13) | `ledger_event` `kind='fee'`, `fee_type='shipping_fee'`, ligado por `order_id` a las ventas del producto. Por producto y plataforma, el **percentil declarado** (S10) sobre los envíos de la ventana vigente | ≥ 6 envíos en la ventana; ledger fresco (día cubierto) | `envio_sin_historia`, `envio_sin_dato` |
| **Cobro de envío al cliente** (FBM) | `ledger_event` `kind='sale'`, columna `shipping_price` con su moneda; entra al ingreso cuando no es cero | misma venta | se trata como `0` **solo** cuando la fila existe y trae cero; si la fila falta, la venta no cuenta para el promedio |
| Buy Box (precio, dueño, `is_own`), precio más bajo, ofertas; **control de coherencia** del precio | `spapi_price_observation` (pase diario de Pricing v0, 05:00 UTC) | fila del día | `precio_sin_observar` |
| Unidades vendidas por **producto** y día | `ledger_event` `kind = 'sale'` (`quantity`, `product_id`, `event_date`). El ledger va por producto: **la señal de ventas es por `(product_id, platform)`** y la comparten todas las publicaciones de ese producto en esa plataforma | día *cubierto* = `event_date ≤` la fecha máxima cargada por un `ingest_run` `ok` del ledger de la plataforma; último día cubierto ≤ 3 días de antigüedad | `ventas_sin_dato` |
| Publicación (SKU, ASIN, `product_type`, `status`, **canal**) | `listing` + `spapi_listing_estado_observation`; el canal (`AMAZON_NA` = FBA, `DEFAULT` = FBM) viene de la ingesta de listings del bridge | fila del día; `seller_sku` no nulo | `listing_sin_estado`, `listing_sin_sku`, `listing_inactivo`, `canal_sin_dato` |
| Inventario | `spapi_inventario_observation` (`total_quantity`) | fila diaria; solo **invalida la señal** de ventas (S4 #5) | `inventario_sin_observar` |
| Goal de margen, modo y límites | `precio_goal` (S3) y `config_version` | vigente en la fecha | `sin_goal` → no se evalúa |
| Cotización de fees a un precio candidato | `precio_cotizacion` (S5): Product Fees a `P*`, guardada aparte de la estimación para no invalidar el escenario del día siguiente | ligada a la oferta del escenario | `fee_error:<code>` |

Regla 2 de Orbit: el motor **no** recalcula margen ni costo ni FX; consume el
escenario tal cual. Su cuenta se guarda con `escenario_id`,
`fee_observation_id` (la del escenario), `cotizacion_id` (la propia) y, en
FBM, `envio_muestra_id` (la muestra de envíos que produjo `L`), con la moneda
de cada precio (regla 4). Si la observación de Pricing y el escenario difieren
en más de `precio_divergencia_max_pct` (1%), el día es
`no_evaluado(precio_divergente)` con los dos precios y sus horas; si persiste
3 días entra en el aviso y en un bloque propio de `/precios`. Moneda de la
observación distinta de la del escenario → `no_evaluado(moneda_divergente)`.

## S3. Goal de precio por producto (`precio_goal`)

Tabla append-only con vigencia, patrón de `sku_cost`:

```
precio_goal(id, listing_id, platform, margen_goal_pct NUMERIC(6,4),
            mode precio_mode ('shadow'|'live'), valid_from DATE, valid_to DATE NULL,
            creado_por TEXT, go_literal TEXT NULL, created_at)
FK (listing_id, platform) REFERENCES listing (id, platform)   -- UNIQUE (id, platform) en listing
CHECK (margen_goal_pct BETWEEN precio_goal_min_pct y precio_goal_max_pct)  -- 0.10–0.60 por config, replicado en goals_write
CHECK ((mode = 'live') = (go_literal IS NOT NULL AND btrim(go_literal) <> ''))
UNIQUE (listing_id, platform, valid_from); un solo vigente por (listing, platform) (índice parcial valid_to IS NULL)
```

- **Sin fila vigente no hay decisión.** No hay default por plataforma ni
  herencia (decisión 3; regla 3 de Orbit). Una publicación sin goal **sí
  aparece** en el reporte de cobertura (S10), como `sin_goal`.
- `margen_goal_pct` se expresa sobre el ingreso sin impuesto (`I`), igual que
  la contribución estimada. La herramienta lo recibe **como porcentaje con dos
  decimales** (`--goal-pct 30.00`), nunca como fracción.
- `mode` por producto con la ceremonia de los goals de Ads (PR #283):
  **subir a `live` exige go literal** (y el CHECK lo impone también contra
  `psql`), bajar a `shadow` no. Cerrar un goal es fijar `valid_to` una sola vez
  (trigger «solo cierra vigencia», `GRANT UPDATE (valid_to)` a `app_admin`).
- Escritor único `app/precio/goals_write.py`; `tools/precio_goal.py` solo pasa
  por él, acepta **`listing_id`** (un `--sku` se resuelve en dry-run y aborta
  si mapea a más de una publicación), deduplica el CSV antes de la huella,
  imprime por fila `m_actual` y `P*`, y aborta si `|P* − P_actual| > 25%` salvo
  `--confirmar-salto <listing_id>`. Candado de escritor único propio
  (`_IDENT_PRECIO_GOAL`), sin tocar el de Ads.
- `platform` admite `amazon_mx`, `amazon_us` y `meli`; el motor solo evalúa
  las combinaciones `(plataforma, canal)` habilitadas por la fase vigente y
  declara el resto como `fuera_de_alcance(fase)`.

## S4. Reglas de decisión (lo que el implementador no decide)

Cada corrida diaria evalúa cada `(listing, platform)` con goal vigente y
produce **exactamente una** fila `precio_decision` (S5): `subir`, `bajar`,
`mantener(motivo)`, `no_evaluado(motivo)`, `goal_inalcanzable(motivo)` o
`frenado(motivo)`. Toda aritmética en `Decimal`.

1. **Insumos completos o nada.** Si cualquier fuente de S2 falta o está
   vencida, `no_evaluado` con el motivo exacto. Nunca una constante ni el
   valor de ayer.
2. **Margen actual.** `m_actual = contribución / I` del escenario vigente a
   `P_actual` (el precio de la oferta del escenario).
3. **Subir** si `m_actual < goal − tol` (`tol` en fracción, 0.005). Precio
   objetivo `P*` por **forma cerrada** con la fórmula del acta:
   `contribución = I − C − F − L − R`, `I = P / (1 + iva)` (o `I = P` donde no
   hay impuesto al precio), `R = r_isr · I`, `F = ref · P + fijo` con `ref` y
   `fijo` de `fee_details` del escenario, y `L` por canal: `0` en FBA (ya está
   dentro de `F`) o el envío medido en FBM (S2, S10). Luego **una cotización
   real** de Product Fees a `P*` (`precio_cotizacion`) y recálculo; si
   `|m(P₁) − goal| > tol`, una segunda y última; si sigue fuera,
   `goal_inalcanzable(fee_no_lineal)`. Cotización con `TaxAmount ≠ 0` →
   `no_evaluado(impuesto_fee_pendiente)`; que no concilia o falla →
   `no_evaluado(fee_error:<code>)`. Movimiento del día =
   `min(P*, P_actual · (1 + escalón))`; `P*` se redondea al centavo **hacia
   arriba**, el tope del escalón **hacia abajo**.
4. **Bajar** solo si `m_actual > goal + tol` **y** `perdiendo_ventas`
   (regla 5) **y** no aplica la regla 6. Un movimiento a
   `max(P_goal, P_actual · (1 − escalón))`, con `P_goal` hacia arriba: nunca
   menor que el goal, nunca más de un escalón.
5. **Perdiendo ventas** (por `(product_id, platform)`): `u15 = Σ quantity`
   de los 15 días cubiertos hasta ayer (`[hoy−15, hoy−1]`), `u60 = Σ` de los
   60 cubiertos anteriores (`[hoy−75, hoy−16]`); los rangos en
   `precio_fechas_excluidas` (config) no cuentan y el promedio se escala por
   días contados. `perdiendo = u15 < (u60 / n60 · n15) · (1 − caída)` con `<`
   estricto, **solo si**: `u60 ≥ precio_u60_min`; `n15 ≥ 10`; historia ≥ 75
   días desde la primera venta del producto en el ledger; y en cada uno de los
   15 días hay observación de inventario con `total_quantity > 0` y el listing
   estuvo activo. Si falla algo, `sin_dato(motivo)` y no dispara nada. Debe ser
   `true` en **3 corridas consecutivas**. Se guardan `u15`, `u60`, `n15`,
   `n60` y la racha.

   **Residual declarado: con el volumen de hoy esta rama casi no se dispara.**
   Medido el 2026-09-16 sobre la ventana `[hoy−75, hoy−16]`: MX movió 199
   unidades repartidas en 74 productos (2.7 de promedio) y US 120 en 35. **Un
   solo producto de todo el negocio** llega a `u60 ≥ 20` (MX 1621, `u60 = 25`).
   La regla no está mal — es la que el dueño selló, y protege contra bajar por
   ruido — pero el criterio (c) de D.2, E.5, B.2 y M.6 va a salir «no ocurrió»
   en las cuatro mediciones de 30 días, y eso se declara **antes** de
   implementar, no después. El motor de esta primera tanda sube o mantiene;
   bajar es una rama que existe para cuando el volumen la alcance. Bajar
   `precio_u60_min` para «activarla» sería bajar precios con ruido estadístico:
   no se hace sin acta nueva del dueño.
6. **Freno por ventas tras subida propia.** Si `perdiendo_ventas` es `true` y
   hay un cambio propio `confirmado` (subida) en los últimos 22 días, no se
   sube ni se baja: `frenado(perdiendo_tras_subida)` con sus números y aviso.
   Se reanuda con un goal nuevo.
7. **Buy Box.** Se registra `buy_box_is_own` y el precio de la Buy Box del día
   del cambio y del siguiente. Perderla **no** frena ni revierte (decisión 5);
   genera `buy_box_perdida` en flanco (S7). En MeLi el equivalente observable
   se define en su acta (S11); si no existe, la columna queda nula y se
   declara, no se inventa.
8. **Movimiento mínimo.** Si `|P_objetivo − P_actual|` es menor que el mayor
   de `precio_movimiento_min_pct` (1%) y `precio_movimiento_min_abs`,
   `mantener(movimiento_minimo)` sin consumir cooldown ni cuota.
9. **Cooldown.** Un cambio por `(listing, platform)` cada 7 días, medido
   contra **cualquier** fila `precio_cambio` no-reversa con `enviado_at`
   (pendiente, enviado, confirmado, no_confirmado o error). Un goal nuevo
   reinicia el freno (#10), **no** el cooldown.
10. **Freno por no convergencia.** Tres cambios consecutivos en la misma
    dirección sin que `|m_actual − goal|` disminuya → `frenado(no_converge)`,
    aviso; se reanuda con un goal nuevo.
11. **Goal inalcanzable.** `P* > 2 · P_actual` o `P* ≤ C + L` (costo mal
    cargado, o envío que se come el precio) → `goal_inalcanzable(motivo)`,
    sin movimiento, aviso.
12. **Cuota y prioridad.** Tope diario por plataforma bajo la cuota propia
    `motor = 'precio:<platform>'`; un `no_confirmado`, un `error` y **las
    reversas** también consumen. Cuando hay más candidatos que cupo:
    `prioridad = |m_actual − goal| · ingreso_60d`, desempate por `listing_id`;
    el resto `mantener(cuota)`.
13. **Sombra fiel.** En `shadow` se calcula, se cotiza y se registra todo, y
    se escribe un `precio_cambio` **virtual** (`aplicado = false`) que consume
    cooldown y freno igual que uno real, sin PATCH y sin cuota. Al pasar a
    `live` los virtuales no cuentan. Las frases de sombra van en condicional.

## S5. Persistencia, identidad y reproducibilidad

- `precio_decision` (append-only; `UNIQUE (listing_id, platform,
  decision_date)`; `decision_date` por trigger a `(now() AT TIME ZONE
  'UTC')::date`): resultado, motivo, `product_id`, `canal`, `m_actual`,
  `goal`, `P_actual`, `P_objetivo`, `P_aplicado` (cada uno con
  `currency NOT NULL`), componentes `I, C, F, L, R`, `escenario_id`,
  `fee_observation_id`, `cotizacion_id`, `envio_muestra_id`, `u15`, `u60`,
  `n15`, `n60`, `racha_senal`, `perdiendo`, `buy_box_is_own`, `mode`,
  `prioridad`, `config_version_id`.
- `precio_envio_muestra` (append-only, FBM): `product_id`, `platform`,
  `ventana_desde`, `ventana_hasta`, `envios`, `percentil`, `valor` + moneda,
  `mediana`, `p90`, `maximo`, `calculada_at`. Es la evidencia de `L`: con esa
  fila se reproduce el número sin volver a recorrer el ledger.
- `precio_cotizacion` (append-only, `INSERT` a `app_decide`): `decision_id`,
  `oferta_observation_id`, `quoted_price` + moneda, `total_fees`,
  `fee_details JSONB`, `fees_estimated_at`, `estado`, `error_code`,
  `source_event_id UNIQUE`. Separada de `estimacion_fee_observation`.
- `precio_cambio` (append-only en sus valores): `decision_id`, `listing_id`,
  `platform`, `precio_antes` (leído por GET del item inmediatamente antes) y
  `precio_observado_antes`, `precio_despues`, monedas, `aplicado`, `estado`,
  `enviado_at`, `ack JSONB`, `readback_precio`, `readback_estado`,
  `readback_at`, `confirmado_por`, `error_code`, `es_reversa`, `reversa_de`.
  Transiciones por trigger: `pendiente → enviado | error`;
  `enviado → confirmado | no_confirmado` (solo por la observación del día
  siguiente). `GRANT UPDATE` por columna a `app_decide`. Índice único parcial
  `(listing_id, platform) WHERE estado IN ('pendiente','enviado')`.
- **Nunca se edita el precio en `listing`** desde el motor.
- Idempotencia y concurrencia: `pg_advisory_xact_lock(hashtext('precio:' ||
  platform))` más el claim en `ads_optimizer_lock`; orden inmutable
  `INSERT precio_decision` + `INSERT precio_cambio pendiente` + `COMMIT` →
  escritura externa → sello.

## S6. Escritura y reversa (regla 7: la reversa antes)

- **Cliente de escritura aparte por plataforma**, default-deny, sin instancia
  compartida, importado solo por su módulo de escritura:
  `app/spapi/write_client.py` (Amazon: `PATCH` a la ruta de listings, con
  `seller_id ∈ VENDEDORES_PROPIOS`) y, cuando llegue su fase,
  `app/meli/write_client.py` (MeLi: `PUT` al item, ruta única). Los clientes
  de lectura existentes (`SpapiClient`, `ClienteMeli`, ambos GET-only por
  diseño) **no ganan verbos de escritura**. Candados en
  `tests/test_architecture.py` con fuga sembrada, uno por plataforma.
- **La escritura es asíncrona en Amazon**: el PATCH responde `202` con
  `submissionId` y el precio se aplica después. Por eso: (1) fila `pendiente`
  con `precio_antes` leído justo antes; (2) escritura; (3) `ack` literal →
  `enviado` (o `error` si HTTP 4xx/5xx o estado distinto de aceptado, aunque
  una lectura posterior muestre el precio nuevo); (4) lectura informativa
  (`readback_*`), que **no** decide el estado; (5) la observación del día
  siguiente cierra `confirmado | no_confirmado`. Un readback fallido es
  `readback_estado = fallido`, nunca `no_confirmado`. En MeLi la forma y la
  sincronía se sellan en su sonda antes de escribir nada.
- **Reversa** = un `precio_cambio` con `es_reversa = true` y
  `precio_despues = precio_antes` del revertido, por el mismo camino y con el
  mismo cierre. **Existe y se prueba con ids reales antes** del primer cambio
  real, en cada plataforma. **No hay reversa automática**: un `no_confirmado`
  produce `frenado(no_confirmado)` y aviso; la reversa la corre el dueño con
  `tools/precio_reversa.py --cambio-id …` (dry-run, huella, go), que lee el
  precio vivo antes de escribir y salta lo que ya no coincide.
- **Errores**: `error` con código (método + ruta + status, sin cuerpo); un
  reintento por día; al tercer día `frenado(api_error)` y aviso.
- El primer encendido de cada `(plataforma, canal)` se limita a 3–5 productos
  elegidos por el dueño y se mide 30 días antes de ampliar.
- Logs: el cuerpo de la escritura y los secretos jamás se registran.

## S7. Visibilidad y avisos (decisión 11)

- `/precios`: (a) el **recuadro de cobertura** de S10, arriba de todo;
  (b) productos con goal: margen de hoy, goal, precio actual, precio objetivo,
  canal, modo, último cambio y su estado; (c) lo que el motor hizo o habría
  hecho hoy, una frase por fila; (d) **no evaluados del día con su motivo** en
  palabras; (e) bloque «precio divergente». Las frases con margen y costo
  viven **solo aquí**.
- `/api/dashboard/salud` → bloque `precios` **dentro de
  `plataformas.<plataforma>`**: `cobertura` (S10), `evaluados`, `movidos`,
  `sombra`, `no_evaluados` por motivo, `frenados`, `goal_inalcanzable`, cuota.
- Telegram, **un** sender `notifica_precio(tipo, …)`, fail-silent, en flanco
  una vez por racha: por **(plataforma, motivo)** con conteo y hasta 5 SKUs
  para `no_evaluado`, `goal_inalcanzable` y `frenado`; por **producto** para
  `no_confirmado` y `buy_box_perdida`. El texto lleva solo SKU/ASIN,
  plataforma, precios, estado y motivo: **nunca costo, margen, goal ni cuerpos
  de error**.

## S10. Cobertura del catálogo (decisión 14)

Ninguna publicación activa puede ser invisible. Cada corrida produce, por
plataforma, un **recuadro de cobertura** que cuadra con el catálogo:

```
publicaciones activas = con_goal_evaluadas
                      + con_goal_no_evaluadas (por motivo)
                      + sin_goal
                      + fuera_de_alcance (por fase que la habilita)
```

- El denominador es **una sola fuente canónica**, nombrada aquí y en ninguna
  otra parte: la observación propia de Orbit
  (`spapi_listing_estado_observation`, estado vendible) cruzada con `listing`.
  No es un detalle: las dos fuentes disponibles **no coinciden** — la propia
  dice 264 vendibles en MX y 106 en US; la caché del bridge dice 284 y 109. La
  diferencia de 20 en MX (7%) no es hueco de carga, son dos definiciones
  distintas de «activa», y mientras el spec no elija una, AC17 no puede pasar
  en ninguna fase. Se elige la propia porque Orbit la produce y la fecha; la
  del bridge se muestra al lado como contraste, con su `updated_at`, y una
  diferencia mayor al 5% se avisa. Una publicación que dejó de reportarse por
  más de 3 días cuenta como `catalogo_desactualizado` y se avisa: el recuadro
  nunca se cuadra escondiendo filas.
- `sin_goal` no es un error: es trabajo del dueño y aparece listado con su
  precio y su canal para que decida. `fuera_de_alcance` nombra la fase (por
  ejemplo `fase_E_envio_fbm` o `fase_M_meli`), no un genérico.
- La suma se verifica en cada fase como criterio de aceptación: si no cuadra,
  la fase no cierra.

### Envío medido en FBM (decisión 13)

`L` para una publicación FBM sale del costo real de las etiquetas que Amazon
ya cobra y que el ledger recibe como `shipping_fee`. La revisión del 2026-09-16
midió que las cuatro reglas siguientes no son detalle de implementación: sin
ellas `L` queda mal por construcción.

- **Signo.** Los fees se guardan **negativos** (`ledger_convencion_signos`). El
  percentil y cualquier orden se calculan sobre `abs(amount)`; un percentil
  sobre el monto crudo elige el envío **más barato**, que es lo contrario de lo
  que se quiere. Test obligatorio con montos negativos sembrados.
- **Unidad: la orden, no la fila.** Un envío no es una fila de `shipping_fee`.
  Medido a 90 días en US: 152 de 173 órdenes traen **2** cargos y 18 traen
  **3**. Un percentil sobre filas subestima el costo por orden ~19%; sobre la
  suma cruda infla el p90 hasta 82%. La muestra agrupa por `order_id` primero y
  percentila órdenes.
- **Tres fuentes que se solapan, y no está resuelto si duplican.** El costo de
  un envío llega por `finance:ShippingHB`, `finance:LabmanLabelPurchase` y el
  reporte `shipping_label`. **Dos mediciones independientes del 2026-09-16
  discrepan y ninguna se sella todavía**:

  | | Lo que midió |
  |---|---|
  | Revisor escéptico | 18 órdenes de US (10.4%) con la misma etiqueta por dos fuentes, 7 701 MXN duplicados, 8.2% del total de 90 días |
  | Verificación independiente | **Cero** órdenes con el mismo monto por dos fuentes, en US y MX, a 90 y a 180 días |

  Lo que **sí** coincide en las dos: en US 3 órdenes traen 1 cargo, **152 traen
  2 y 18 traen 3**; los promedios por fuente a 90 días son ShippingHB −85.23
  (170 filas), LabmanLabelPurchase −441.60 (54) y `shipping_label` −462.78
  (137). Y la aritmética es sugerente: 137 + 54 = 191 = 173 órdenes + 18, que es
  exactamente el número en disputa. Pero una orden de muestra con tres cargos
  trae −127.12, −102.99 y −2 149.87: montos que no se parecen, lo que explica
  que la prueba de monto exacto no encuentre nada y deja abiertas dos lecturas
  — o son tres componentes distintos de un mismo envío, o es el mismo cobro
  informado dos veces con importes que no casan.

  **No se resuelve escribiendo.** La resolución es el primer entregable de la
  fase E (tarea E.0): clasificar qué representa cada fuente antes de que `L`
  entre a una cuenta. Hasta entonces, `L` en US no se sella. El origen está
  **aguas arriba** (la contabilidad que alimenta el ledger), no en Orbit.
- **Por unidad, no por orden.** El margen se calcula por unidad y `L` se mide
  por orden. `L_unidad = L_orden / unidades_de_la_orden`. Medido: 98.2% de las
  órdenes MX y 99.1% de las US son de una sola unidad, así que casi siempre
  coinciden — pero la regla se escribe, no se supone.

Y sobre el valor:

- **Atribución**: el cargo trae `order_id` y no `product_id`; el producto sale
  de las filas `sale` de esa misma orden. Una orden con más de un producto
  **no** se reparte: se descarta de la muestra y se cuenta aparte.
- **Valor: tarifa vigente, no percentil de dispersión.** La dispersión que
  justificaba un p75 **no existe**: MX es tarifa plana (p50 = p75 = 95.00 de
  dic-25 a may-26; 91.00 desde jun-26) y en US el p75 − p50 va de 2 a 13 MXN.
  Elegir p75 en vez de p50 mueve el margen entre 0.00 y 0.43 puntos (mediana
  0.17) en 15 de 16 productos: no es una decisión que merezca acta. Lo que sí
  importa es el **rezago**: el p75 móvil de 90 días tardó ~75 días en registrar
  el cambio de 95 a 91 del 1-jun (el p50 tardó 45), así que ante una **subida**
  de tarifa el p75 subestima `L` durante ~75 días, justo al revés del
  argumento «que el motor nunca crea que gana más de lo que gana». Por eso el
  valor sellado es la **mediana de los últimos N envíos del producto**, y p90 y
  máximo se muestran como dispersión, sin entrar a la cuenta.
- **Ventana con rezago, que son DOS medidas distintas.** (a) *Rezago de
  ingesta*: entre el `event_date` del cargo y la corrida que lo trajo, 1 a 11
  días; dice cuánto tarda Orbit en enterarse. (b) *Rezago de emisión*: entre la
  fecha del envío y el `event_date` con que Amazon lo cobra, p50 27 días en MX
  y 22 en US, p90 ~57–59, máximo 73; es la que deja la cola a medio cargar. Una
  ventana de 90 días que termina hoy son ~80 días completos más esa cola. La
  ventana termina en `hoy − precio_envio_rezago_dias`, que se alimenta del
  **p90 de (b)**, y la muestra guarda su ventana efectiva. E.1 mide las dos por
  separado y E.2 sella el estadístico.
- **Mínimo con histéresis.** Mínimo 6 envíos para entrar; **sale con 3**. Sin
  histéresis el mínimo parpadea: de 20 productos que alcanzan 6 en alguna de
  seis ventanas móviles, solo 11 se mantienen en las seis, y los otros 9
  alternan `evaluado` / `envio_sin_historia` con aviso recurrente. Debajo del
  mínimo: `envio_sin_historia` y el producto no se evalúa.
- **La cotización de fees en FBM no se pide como FBA.** La cotización de
  referral hoy se pide con cumplimiento FBA; en FBM eso devuelve la comisión de
  logística de Amazon, que sumada a `L` **cuenta el envío dos veces**. La
  cotización FBM se pide con su propio cumplimiento y su `F` es solo referral.
- **La rama de inventario no aplica en FBM.** La fuente de inventario que usa la
  regla de «se están perdiendo ventas» es de FBA; en FBM nunca se puebla, así
  que el criterio de stock queda `sin_dato` por diseño y así se declara, en vez
  de fabricar una rama que no puede dispararse.
- **Evidencia**: cada decisión guarda `envio_muestra_id` con la ventana
  efectiva, el número de **órdenes** (no filas), las fuentes usadas, los
  duplicados descartados, el valor sellado y la dispersión (p90, máximo).
- **Ingreso por envío**: donde el cliente paga envío, ese cobro
  (`shipping_price` de la venta) entra al ingreso; donde el dato **no está**, no
  se supone cero: la publicación lleva `ingreso_envio_sin_dato` y el motivo
  viaja a la pantalla. Medido en US: de 351 ventas en 180 días, **ninguna** trae
  `shipping_price` ni `item_price`, y la causa es estructural —
  `_money_from_payload` descarta el desglose fiscal cuando el `CurrencyCode` del
  payload (USD) no coincide con la moneda del `amount` (MXN). Eso es **dato
  ausente, no cobro cero**; una versión anterior de este spec afirmaba «el
  cliente pagó cero en las 349 ventas» y era falso. En MX el dato sí existe en
  parte: 68 de 641 ventas traen `shipping_price`, y las que lo traen son > 0.
  Confirmado por dos mediciones independientes, con la causa localizada en
  `app/ledger.py:228`. Mientras la ingesta contable
  no cargue el desglose, el DoD de E.3 sobre ingreso de envío **no es
  implementable en US** y así queda declarado.
- **FX en US.** El par MXN→USD no se carga en este repo y no se va a inventar:
  la conversión usa `fx_resolve(fecha, 'USD', 'MXN')` y **divide**. Sin tasa
  para la fecha: `fx_ausente`, nunca un valor constante.
- `L` es un costo **medido, no cotizado**, y así se declara en la pantalla y
  en la evidencia. Es la única componente del margen que no viene de una
  cotización, y por eso lleva su muestra adjunta.

## S11. Mercado Libre (decisión 15)

MeLi entra al alcance, y entra con la verdad por delante: **hoy Orbit no tiene
de MeLi ninguno de los insumos del margen**. Medido el 2026-09-16:

| Pieza | Estado hoy |
|---|---|
| Acceso a la API | **Existe**: `app/reputacion_clientes.py` (`ClienteMeli`, GET-only por diseño) con credenciales y refresco de token; la reputación corre a diario |
| Publicaciones y precios | 137 items en la caché del bridge (`meli_listings_cache`), `updated_at` más reciente **2026-05-01**: la caché lleva meses sin refrescarse |
| SKU → producto | `meli_sku_mapping` **vacío**: sin ese puente no hay costo por publicación |
| Ventas, comisiones, envíos | **Cero filas** de `meli` en `ledger_event` — pero **no porque el dato no llegue**: la ingesta contable lo recibe a diario y lo descarta en una rama. `ingest_run` reporta cada día `5126x plataforma meli excluida`, creciendo 15–20 filas por día. Abrir el ledger de MeLi es quitar esa rama y mapear, no construir una ingesta nueva |
| Escritura de precio | No existe camino; el cliente actual rechaza cualquier método que no sea GET |

Por eso la fase M no es «encender MeLi»: es **traer MeLi a Orbit** y recién
entonces aplicarle el mismo motor. Y no es un bloque homogéneo: el dinero ya
llega y se tira en una rama (trabajo de horas), mientras que el catálogo, el
mapeo SKU→producto y la escritura de precio son el trabajo real. Por eso M.3 se
parte en dos, y el mapeo SKU→producto se adelanta como tarea propia: sin ese
puente no hay costo por publicación y ninguna otra pieza de MeLi sirve.
`estimacion_canal` hoy solo admite `fba|fbm`; MeLi necesita su propio valor. Sus rangos y su fórmula (comisión por
categoría, envío, impuesto sobre el precio) se sellan en su propia acta con el
dueño, con el mismo contrato que el acta de márgenes: sin fuente verificable
no se asigna cero. Mientras la fase no cierre, las 137 publicaciones aparecen
en el recuadro de cobertura como `fuera_de_alcance(fase_M_meli)`.

### Lo que la fase B va a producir de verdad (medido 2026-09-16)

La premisa de que el margen de Estados Unidos está mal **y que el envío se lo
come** es falsa, y se corrige aquí para que nadie espere de la fase B lo que no
va a dar. Por unidad, sobre los nueve productos US con muestra, la contribución
va de **33% a 63%, mediana ~50%**. El envío (94 035 MXN a 90 días) sí supera a
la comisión (65 067), pero no se come el margen.

El hueco real está en publicidad: **Ads US gastó 106 721 sobre 446 129 de
ventas, un TACoS de 23.9%, contra 9.0% en México**. Un goal pre-Ads de 30% en
US es ~6% después de Ads. Consecuencias, todas de redacción y de pantalla, no
de motor:

- La fase B va a producir **`mantener` en la mayoría** de las publicaciones, no
  `subir`. Su criterio de éxito es que la cuenta cuadre y que las excepciones
  salgan con motivo, no un número de subidas.
- `/precios` y el cierre de B.1 muestran el **TACoS de 90 días** junto a
  `m_actual`, porque un margen de 50% con 24% de Ads encima no es el mismo
  negocio que uno de 50% con 9%.
- Datos ya medidos que entran al acta 0.2 sin volver a sondear: retención ISR
  US **2.07%** de ventas (MX 1.99%) y `tax_withheld` US **6.56%**.
- El precio no es la palanca de US. Decir cuál es queda **fuera** de este spec.

## S8. Fases, horario, encendido y aceptación

- **Horario.** La estimación se refresca cada 6 h a :45 y el costo del día se
  sella a las 08:15 UTC: el primer escenario `disponible` del día nace a las
  12:45. El motor corre a las **13:10 UTC** (`10 13 * * *`). Un solo valor en
  spec, plan y test.
- **Fase A — Amazon México, FBA** (171 publicaciones activas): migración,
  reglas puras, cliente de escritura, escritura y reversa con sonda real,
  corrida diaria con lock y cuota propia, `/precios` con cobertura, revisión
  independiente, sombra con **≥ 80% de los goals evaluados**, encendido de
  3–5 productos y medición de 30 días en dos cortes.
- **Fase E — envío medido y FBM** (habilita **~10 activas en MX y ~10 en US**
  con la ventana de 90 días; 113 y 109 son el techo TEÓRICO del canal FBM, no
  lo que la fase enciende: el límite lo pone el volumen de ventas, no el
  umbral, y subirlo es cuestión de ventana, que sella E.2):
  medición del envío por producto, acta del percentil con el dueño,
  ampliación del margen estimado a FBM, y encendido FBM en México.
- **Fase 0 — política fiscal de Estados Unidos**: `I = P` sin IVA, retenciones
  según lo que demuestre la sonda, FX MXN→USD para `C` (hoy el sync solo carga
  USD→MXN), `marketplace_id` parametrizado en la cotización de fees. **Nota
  que reordena el plan**: US **no tiene publicaciones activas en FBA** (0 de
  109), así que US depende de la fase E, no de la ruta FBA.
- **Fase B — Amazon Estados Unidos** (**106** activas en la fuente canónica,
  109 en la caché del bridge, todas FBM): sombra y
  encendido con la política de la fase 0 y el envío de la fase E.
- **Fase M — Mercado Libre** (137 publicaciones): inventario de insumos, acta
  de la fórmula, ingesta de publicaciones, ventas y cargos, margen estimado,
  cliente de escritura con su sonda, sombra y encendido.
- **Aceptación verificable** (por fase, en el plan): cada regla de S4 con su
  test rojo-primero y su mutante, incluidos los bordes; **el recuadro de
  cobertura cuadra** con el catálogo activo; dry-run de las herramientas no
  escribe; la reversa deshace con cierre por observación; `/salud` cuenta lo
  mismo que la base; ningún camino de escritura fuera de su módulo; un día sin
  insumos produce N filas `no_evaluado` y cero escrituras; dos corridas
  simultáneas producen una sola escritura.

## S9. Revisión de cinco perspectivas (2026-09-16) y qué cambió

Cinco revisores independientes (producto, arquitectura, seguridad, QA,
escéptico) leyeron el borrador v1.0 y el código real. Detalle y disposición de
los 40 hallazgos en `docs/evidencia/repricing-01/plan-validacion.md`. Cambios
que el dueño verá, todos dentro de S1:

1. **Quedarse sin inventario o un listing inactivo ya no cuenta como «perder
   ventas»**; y la señal exige volumen mínimo, tres corridas seguidas y fechas
   excluidas configurables.
2. **Si el producto pierde ventas justo después de una subida propia, el motor
   deja de subir y avisa.**
3. **El escalón del 10% aplica también al bajar**, hay banda de goal y
   movimiento mínimo.
4. **La confirmación de un cambio se cierra al día siguiente**, porque la
   escritura de Amazon es asíncrona; y **no hay reversa automática**.
5. **La cotización de fees a un precio candidato vive en su propia tabla.**
6. **El motor corre a las 13:10 UTC**, no a las 09:30.
7. **Los avisos van por plataforma y motivo con conteo.**
8. **La medición del encendido es de 30 días**, no 14.

### Segunda ronda: fases E, B y M (2026-09-16)

La ampliación de las decisiones 13–15 se mandó a su propia ronda de cinco
perspectivas antes de implementarse. Volvió con hallazgos que **invalidan
afirmaciones que este spec declaraba medidas**, no con observaciones de
redacción. Ninguna decisión del dueño (S1 #1–#15) cambia; lo que cambia es lo
que el spec creía saber del sistema. Las cuatro correcciones de hecho:

1. **«En Estados Unidos el cliente pagó cero de envío en las 349 ventas» era
   falso.** El dato es **NULL**, no cero: la ingesta contable descarta el
   desglose fiscal cuando la moneda del payload no coincide con la del monto.
   Ausencia ≠ cero, que es justamente la regla que este spec exige en todo lo
   demás. Ahora es `ingreso_envio_sin_dato` y el DoD correspondiente de E.3
   queda declarado como no implementable en US hasta que la ingesta cargue el
   desglose.
2. **El envío medido estaba mal por construcción en cuatro puntos**: el
   percentil corría sobre montos **negativos** (habría elegido el envío más
   barato); la unidad era la fila y no la **orden** (152 de 173 órdenes de US
   traen 2 cargos y 18 traen 3); tres fuentes se **solapan** y no está resuelto
   si duplican dinero (dos mediciones discrepan, S10); y `L` se medía por orden
   mientras el margen es por unidad.
3. **La dispersión que justificaba el p75 no existe** (México es tarifa plana;
   elegir p75 en vez de p50 mueve el margen ≤ 0.43 puntos), y el p75 móvil de
   90 días **rezaga ~75 días** un cambio de tarifa, al revés del argumento. Se
   sella la mediana de los últimos envíos, con ventana corregida por rezago.
4. **La fase E no habilita 222 publicaciones, habilita ~20 hoy**: solo 7
   productos de MX y 9 de US llegan a 6 envíos en 90 días, y cruzados con
   publicaciones activas son 10 y 10. El techo lo pone el volumen de ventas,
   no el umbral, y la palanca medida es **la ventana** (180 días duplica la
   cobertura; 365 la triplica), no el percentil.

Y tres correcciones de alcance, que abaratan el plan sin tocar ninguna
decisión: el canal por publicación **ya se ingiere** (A.7 no necesita
migración); el dinero de MeLi **ya llega a diario** y se descarta en una rama
(M.3 se parte en dos); y la rama de **bajar precio** no se dispara con el
volumen actual (residual declarado en S4 regla 5).

Detalle de los hallazgos de esta ronda, con el comando que probó cada uno, en
`docs/evidencia/repricing-01/plan-validacion-ebm.md`.

## Divergencias con el traspaso, declaradas

`docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1 pide estrategias «match/beat
competitor», «Buy Box oriented», «inventory aware», «time-based» y modos
agresivo/equilibrado/conservador. Por decisión del dueño (S1 #1, #5, #6) este
motor tiene **una** estrategia: margen goal con bajada solo por pérdida de
ventas. El traspaso también pide Amazon y MeLi como ciudadanos de primera
clase: con la decisión 15 eso vuelve al alcance, por fases y con la verdad de
lo que falta (S11). El historial completo, el floor por margen, el
anti-thrashing y el dry-run obligatorio sí están (S4 #8–#12, S5, S6).

Lecciones del sistema viejo que este spec convierte en regla: la fórmula que
ignoraba comisiones y dejó 46 días sin propuestas (aquí `F` viene de una
cotización real, `L` de una muestra con su evidencia, y la ausencia bloquea en
vez de callar); el paso de «sustain» que midió −12% (aquí el precio objetivo
se resuelve y el escalón acota en ambas direcciones); el stop-loss prometido
que nunca corrió (aquí la reversa se prueba con ids reales antes del primer
cambio y no depende de una lectura que puede fallar).
