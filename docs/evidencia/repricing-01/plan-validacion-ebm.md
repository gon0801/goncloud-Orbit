# Validación de las fases E, B y M — segunda ronda (2026-09-16)

Cinco perspectivas de contexto fresco (producto, arquitectura, seguridad, QA,
escéptico) sobre `plans/repricing-01.md` v1.1 y
`docs/superpowers/specs/2026-09-15-repricing-01-design.md` v1.2, con alcance
explícito: **la fase A ya estaba validada en la primera ronda y no se revisó**.
Los revisores **ejecutaron** contra producción con el rol lector; no leyeron.

Después de la ronda se mandó una **verificación independiente** de las tres
afirmaciones que contradecían hechos ya reportados al dueño. Esa verificación
confirmó dos y **refutó la tercera**, y por eso una de ellas viaja aquí como
disputa abierta y no como hecho.

Cómo leer esta tabla: cada fila lleva el comando o la consulta que la probó.
Quien lea el plan en un mes y pregunte «¿por qué esta regla es así?» encuentra
aquí la respuesta, y el siguiente revisor no vuelve a gastar en lo ya medido.

## Corregido en el spec v1.3 y el plan v1.2

| # | Hallazgo | Cómo se probó | Dónde quedó |
|---|---|---|---|
| 1 | **«El cliente pagó cero de envío en las 349 ventas de US» era falso: es NULL, no cero.** 351 ventas de US en 180 días, **0** con `shipping_price` y **0** con `item_price`. En MX, 68 de 641 sí lo traen y las 68 son > 0. Causa estructural: `_money_from_payload` descarta el desglose cuando el `CurrencyCode` del payload no coincide con la moneda del `amount`; todas las ventas de US están en MXN. | `select platform, count(*), count(shipping_price), count(*) filter (where shipping_price=0), count(item_price) from ledger_event where kind='sale' and event_date >= current_date - interval '180 days' group by platform;` más lectura de `app/ledger.py:222-249`, línea 228 `if code != moneda_amount: return None`. **Dos mediciones independientes coinciden.** | Spec S10 «Ingreso por envío»; plan hecho 16; AC26; residual declarado |
| 2 | **El percentil corría sobre montos negativos.** Los fees se guardan negativos por `ledger_convencion_signos`; un percentil sobre el monto crudo elige el envío **más barato**. | Convención en la migración del ledger; confirmado en las salidas de `shipping_fee`, todas negativas (`finance:ShippingHB` −14 489.61 sobre 170 filas, etc.) | Spec S10 «Signo»; AC23; DoD de E.3 con mutante |
| 3 | **Un envío no es una fila.** En US, 3 órdenes traen 1 cargo, **152 traen 2 y 18 traen 3**. Percentilar filas subestima el costo por orden ~19%. | `with c as (select order_id, count(*) n from ledger_event where fee_type='shipping_fee' and platform='amazon_us' and event_date >= current_date - interval '90 days' and order_id is not null group by order_id) select n, count(*) from c group by n order by n;` **Dos mediciones independientes coinciden.** | Spec S10 «Unidad»; plan hecho 14; DoD de E.3 |
| 4 | **`L` se medía por orden y el margen es por unidad.** 98.2% de las órdenes MX y 99.1% de las US son de una unidad, así que casi siempre coinciden, pero la regla no estaba escrita. | Conteo de `quantity` por orden en las filas `sale` ligadas a órdenes con etiqueta | Spec S10 «Por unidad»; DoD de E.3 |
| 5 | **La dispersión que justificaba el p75 no existe.** MX es tarifa plana: p50 = p75 = 95.00 de dic-25 a may-26, 91.00 desde jun-26. En US p75 − p50 va de 2 a 13 MXN. Elegir p75 mueve el margen entre 0.00 y 0.43 puntos (mediana 0.17) en 15 de 16 productos. | `percentile_cont` sobre `abs(amount)` por producto y mes; recálculo del margen con cada percentil candidato sobre 16 productos | Spec S10 «Valor»; plan hecho 15; E.2 sella la **ventana**, no el percentil |
| 6 | **El p75 móvil de 90 días rezaga ~75 días un cambio de tarifa** (el p50, 45). Ante una **subida** subestima `L`, al revés del argumento «que el motor nunca crea que gana más de lo que gana». | Serie del p50 y p75 móviles de 90 días alrededor del cambio 95 → 91 del 1-jun-2026 | Spec S10 «Valor» |
| 7 | **El cargo llega tarde, y son DOS medidas distintas que la v1 mezcló en una.** (a) *Rezago de ingesta*: entre el `event_date` del cargo y la corrida que lo trajo, 1 a 11 días — dice cuánto tarda Orbit en enterarse. (b) *Rezago de emisión*: entre la fecha del envío y el `event_date` con que Amazon lo cobra, p50 27 días en MX y 22 en US, p90 ~57–59, máximo 73 — es la que deja la cola a medio cargar. La ventana efectiva la cierra **(b)**, y `precio_envio_rezago_dias` se alimenta de su **p90**. | Dos consultas separadas: (a) `min(observed_at) − event_date` por fila; (b) `event_date − fecha_de_envío` por orden | Spec S10 «Ventana con rezago»; **E.1 las mide por separado**; E.2 sella el estadístico; AC20 exige ventana efectiva |
| 8 | **El mínimo de 6 parpadea.** De 20 productos que alcanzan 6 en alguna de seis ventanas móviles, solo 11 se mantienen en las seis; los otros 9 alternan `evaluado` / `envio_sin_historia` con aviso recurrente. | Seis ventanas móviles de 90 días, conteo de órdenes por producto en cada una | Spec S10 «Mínimo con histéresis» (entra con 6, sale con 3); AC19 |
| 9 | **La cotización de fees en FBM se pedía como FBA**, lo que devuelve la comisión de logística de Amazon y, sumada a `L`, **cuenta el envío dos veces**. | Lectura del llamado a `post_fees` y de su cumplimiento fijo | Spec S10; AC24 con mutante |
| 10 | **La rama de inventario no puede dispararse en FBM.** La fuente que la alimenta es de FBA y en FBM nunca se puebla. | Origen de la observación de inventario usada por la regla de «perder ventas» | Spec S10; AC25 → `sin_dato(inventario_no_aplica_fbm)` |
| 11 | **La fase E habilita ~20 publicaciones, no 222.** Con mínimo 6 envíos en 90 días: 7 productos de MX y 9 de US, que cruzados con activas son 10 y 10. El techo lo pone el volumen: de 264 activas de MX solo 117 vendieron algo en 90 días y 15 vendieron ≥ 6 unidades; US, 48 de 106 y 10. La palanca es **la ventana**: 180 días lleva a 14 y 17; 365 días a 17 y 22. | Conteo de órdenes con etiqueta por producto en ventanas de 90/180/365 días, cruzado con publicaciones activas | Encabezado de la fase E; plan hecho 13; E.1 mide las tres ventanas y E.2 la sella |
| 12 | **Un solo producto del negocio llega a `u60 ≥ 20`** (MX 1621, `u60 = 25`); 9 llegan a ≥ 10 y 13 a ≥ 5, sobre 109 productos con venta. MX movió 199 unidades entre 74 productos, US 120 entre 35. La rama de bajar precio no se va a ejercer. | `select count(*) filter (where u60>=20), count(*) filter (where u60>=10), count(*) filter (where u60>=5), count(*) from (select product_id, platform, sum(quantity) u60 from ledger_event where kind='sale' and product_id is not null and event_date between current_date - interval '75 days' and current_date - interval '16 days' group by 1,2) t;` **Dos mediciones independientes coinciden.** | Spec S4 regla 5, residual; plan hecho 17 y residuales |
| 13 | **El margen de US no es el problema; Ads sí.** Contribución por unidad 33%–63%, mediana ~50%, sobre los 9 productos de US con muestra. Envío 94 035 a 90 días contra comisión 65 067: supera, pero no se come el margen. **Ads US 106 721 sobre 446 129 de ventas = TACoS 23.9%**, contra 9.0% en MX. Retención ISR US 2.07% (MX 1.99%), `tax_withheld` US 6.56%. | Contribución por unidad reconstruida desde el ledger; gasto de Ads y ventas por plataforma a 90 días | Spec «Lo que la fase B va a producir de verdad»; plan hecho 18, encabezado de B, B.1 muestra TACoS |
| 14 | **Orbit sí trae ya el canal por publicación.** `estimacion_oferta_observation` tiene `canal` y `mapear_canal` traduce `AMAZON_NA` → `fba`, `DEFAULT` → `fbm`: 7 514 filas, 221 listings, fresca al 2026-09-17 **UTC** (fecha efectiva de extracción de toda esta tanda: la ronda se fechó el 16 en hora local y las lecturas cayeron ya en el 17 UTC). Solo faltan ~8 líneas de `_motivo_universo`. La v1.1 presupuestaba una migración y un cambio en `app/listings.py` que no hacen falta. | Lectura de `estimacion_oferta_observation` y de `mapear_canal`; conteo de filas y `observed_at` | Plan hecho 5; A.7 pierde la migración |
| 15 | **El dinero de MeLi ya llega a diario y se tira en una rama.** `ingest_run` reporta `5126x plataforma meli excluida` cada día, creciendo 15–20 filas. Abrir el ledger es quitar la rama y mapear, no construir una ingesta. `estimacion_canal` solo admite `fba\|fbm`. | Lectura de `ingest_run` de varios días consecutivos; enum de `estimacion_canal` | Spec S11; plan hecho 6; M.3 partida en M.3a y M.3b |
| 16 | **Hay dos denominadores de «publicación activa» y no coinciden.** La fuente propia de Orbit (`spapi_listing_estado_observation`) dice 264 vendibles en MX y 106 en US; la caché del bridge dice 284 y 109. 20 de diferencia en MX (7%): dos definiciones, no hueco de carga. AC17 no podía pasar en ninguna fase. | Conteo de vendibles en las dos fuentes, mismo día | Spec S10 elige la propia como canónica; plan hecho 19; AC17; D.1 y E.5 usan 264 |
| 17 | **`meli_sku_mapping` vacío bloquea todo lo demás de MeLi**: sin ese puente no hay costo por publicación. Estaba dentro de M.3, junto con trabajo que no depende de él. | Conteo de filas en `meli_sku_mapping` | M.0, adelantada al frente de la fase M y owner-gated |
| 18 | **AC18 protegía contra casi nada.** En 180 días hay 1 orden multi-producto en MX (de 442) y 1 multi-unidad en US (de 348), 98.2% y 99.1% usables. Lo que no estaba contado: las órdenes con cargos de varias fuentes y las ~109 filas diarias que la ingesta descarta por violar la convención de signos, cuya plataforma y `fee_type` no son visibles desde Orbit. | Conteo de productos y unidades por orden; `ingest_run` diario | AC18 reescrita; E.0 clasifica los descartes |
| 19 | **E.3 y 0.3 editan la misma función** separadas por dos mediciones de 30 días. | Lectura de `_motivo_universo` y del alcance de las dos tareas | 0.3 declarada como edición sobre el SHA de E.3, con fusión si 0.2 llega antes |
| 20 | **El par MXN→USD no se carga en este repo.** La conversión correcta es `fx_resolve(fecha, 'USD', 'MXN')` y dividir. | Lectura del sync de FX y de `fx_resolve` | Tarea 0.1 y DoD de 0.3; `fx_ausente` nunca es constante |

## Disputa abierta, que por eso es una tarea y no un hecho

**¿Las tres fuentes de `shipping_fee` duplican dinero?** Dos mediciones
independientes del mismo día no coinciden, y el plan **no sella ninguna**:

| Medición | Resultado |
|---|---|
| Revisor escéptico | 18 órdenes de US (10.4%) con la misma etiqueta por dos fuentes; 7 701 MXN duplicados; 8.2% del total de 90 días |
| Verificación independiente, prueba de **monto exacto** | **Cero** órdenes con el mismo monto por dos fuentes distintas, en las cuatro combinaciones de plataforma (US, MX) y ventana (90, 180 días) |

Consulta de la segunda, **mostrada para una de las cuatro combinaciones**
(`amazon_us`, 90 días); las otras tres son la misma con `platform` y el
intervalo cambiados, y las cuatro dieron cero:

```sql
with base as (
  select order_id, amount,
         split_part(source_event_id,'|',2) || ':' ||
         nullif(split_part(source_event_id,'|',6),'') as fuente
  from ledger_event
  where fee_type='shipping_fee' and platform='amazon_us'
    and event_date >= current_date - interval '90 days'
    and order_id is not null
)
select count(*) from (
  select order_id, amount
  from base group by order_id, amount
  having count(distinct fuente) >= 2
) dup;
-- 0
```

**Esa consulta trae un hueco declarado**, y por eso tampoco cierra la disputa
por sí sola: `nullif(split_part(...))` produce `NULL` cuando `source_event_id`
es nulo o no trae las partes esperadas, y PostgreSQL no cuenta los `NULL` en
`count(distinct fuente)`. Una orden con dos cargos de fuente **desconocida**
queda fuera del `having` y aparenta no tener duplicados. E.0 marca esos grupos
como `fuente_desconocida` y no cierra mientras queden sin resolver.

Lo que **sí** coincide en las dos mediciones, y es lo que mantiene la sospecha
viva: 152 órdenes de US con 2 cargos y 18 con 3; y la aritmética
`137 + 54 = 191 = 173 órdenes + 18`, donde 18 es exactamente el número en
disputa. En contra: una orden de muestra con tres cargos
(`111-0818188-2803467`) trae −127.12, −102.99 y −2 149.87, montos que no se
parecen en nada, lo que explica que la prueba exacta salga en cero y admite la
lectura de que son tres componentes distintos de un mismo envío.

**No se resuelve desde Postgres.** El `raw_payload` no persiste en
`ledger_event`; vive en la contabilidad de origen. Resolverlo es el primer
entregable de la fase E (tarea E.0), contra el documento de origen, y hasta
entonces `L` en US no se sella.

## Verificado y correcto (no volver a gastar aquí)

- La atribución del cargo al producto **sí** es exacta: 431 de 439 órdenes con
  etiqueta en MX y 344 de 347 en US son de un solo producto y una sola unidad.
- El acceso a la API de MeLi funciona a diario y es GET-only por diseño.
- `ads_optimizer_lock`, la herencia `orbit_admin` → `app_decide` y los candados
  de arquitectura existentes sirven tal cual para el motor nuevo.
- Las decisiones 1–15 del dueño no cambian con esta ronda. Lo que cambió es lo
  que el plan creía saber del sistema, no lo que el dueño pidió.
