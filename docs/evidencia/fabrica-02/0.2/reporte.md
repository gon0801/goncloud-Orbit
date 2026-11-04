# FABRICA 02 / 0.2 — Inventario de existentes (evidencia)

Fecha: 2026-09-12 UTC. Ejecuta: el lead. **Solo lectura** como `orbit_read`:
cero mutaciones, cero llamadas a Amazon. Base del plan: `plans/fabrica-02.md`
v1.1 (`origin/master` `361658e`).

## Resultado en una línea

El inventario **encontró un agujero en el diseño del propio plan y lo corrigió
antes de que se escribiera una línea de código**: el paso 3 del resolutor, tal
como estaba redactado, habría dejado **241 de 246 campañas sin destino de
harvest** el día del deploy — incluidas las únicas 4 que cosechan de verdad.

## 1. El hallazgo: las campañas que cosechan no tienen goal propio

Las campañas que han producido una decisión `kind = harvest` en toda la
historia son cuatro, y **ninguna** tiene goal de scope `campaign`, ni terna
`harvest_*`, ni pertenece a un grupo:

| plataforma | campaña | nombre | goal propio | terna propia | en grupo | ad groups |
|---|---|---|---|---|---|---|
| `amazon_mx` | `1928166522818` | AC - Category Phrase - MX | SIN GOAL | no | no | 1 |
| `amazon_mx` | `79296609147301` | AGMX - Category Phrase - MX | SIN GOAL | no | no | 1 |
| `amazon_us` | `114399001706085` | AU2 - Category Phrase - US | SIN GOAL | no | no | 1 |
| `amazon_us` | `140602818838686` | USPerNog - Auto Discovery - US | SIN GOAL | no | no | 1 |

Cosechan porque `resuelve_goal` (`app/optimizer/goals.py:175-180`) cae al goal
de **`platform`** cuando la campaña no tiene el suyo, y esos dos goals sí traen
terna:

| goal | scope | plataforma | modo | terna destino | ad group | bid |
|---|---|---|---|---|---|---|
| 4 | `platform` | `amazon_mx` | live | `97835222467967` (Arras Manual) | `272585315669297` | 2.5000 MXN |
| 5 | `platform` | `amazon_us` | live | `251723662158466` (USPerNog - Category Exact) | `522582072501798` | 0.6800 USD |

Los últimos harvests reales confirman el patrón (`decision` más recientes de
`kind = harvest`): 2276 desde `USPerNog - Auto Discovery - US`, 2205 y 1475
desde `AU2 - Category Phrase - US`, 863 desde `AGMX - Category Phrase - MX`,
862/861 desde `AC - Category Phrase - MX`.

### El tamaño del agujero

Clasificando las 246 campañas por cómo resolverían el destino con F2:

| situación | campañas |
|---|---|
| 1. en grupo (resuelve por grupo) | **5** |
| 3. solo goal de plataforma → `sin_destino_de_harvest` con el paso 3 acotado | **241** |

El plan decía, literal: *«Terna del goal **de scope `campaign`** (nunca la de
plataforma)»*. Esa redacción nació de una corrección anterior —un hallazgo de
CodeRabbit sobre `resuelve_goal` y las comparaciones «terna vs grupo»— y al
acotarla se llevó por delante el caso real. **Confundí resolver con comparar.**

### La corrección aplicada

- **Resolver**: el paso 3 usa la terna **vigente** por el mismo camino que
  `resuelve_goal` (goal propio si existe; si no, el de plataforma), con motivo
  `migracion_pendiente`.
- **Comparar**: `destino_inconsistente` sigue mirando **solo** `scope =
  campaign`. La terna de plataforma nunca contradice al grupo, porque es el
  default de la cuenta y no una decisión sobre esa campaña.

Aplicado en `plans/fabrica-02.md` (resolutor, promesa de compatibilidad, hechos
9 y 13, DoD (c'') de A.1) y en la precisión 2 del §7 del spec de fábrica.

## 2. Campañas con terna propia: solo el grupo 1, y todas consistentes

Las únicas 5 campañas con goal `enabled` de scope `campaign` y terna son
exactamente las del grupo `kit_arras`, y las 5 verifican parentesco y
plataforma:

| campaña | rol en grupo | terna → campaña | terna → ad group | bid | modo | ad group existe | parentesco ok | plataforma ok |
|---|---|---|---|---|---|---|---|---|
| `145787501515469` | `category_exact` | `145787501515469` | `182421284463033` | 11.6200 | shadow | sí | sí | sí |
| `146133635461259` | `category_phrase` | `145787501515469` | `182421284463033` | 11.6200 | shadow | sí | sí | sí |
| `166729699150154` | `auto_discovery` | `145787501515469` | `182421284463033` | 11.6200 | shadow | sí | sí | sí |
| `208065490960987` | `category_broad` | `145787501515469` | `182421284463033` | 11.6200 | shadow | sí | sí | sí |
| `70314694808265` | `product_targeting` | `145787501515469` | `182421284463033` | 11.6200 | shadow | sí | sí | sí |

**Consecuencia para D.2**: no hay candidatas masivas a `harvest_excepcion`. La
tarea deja de ser «migrar 241 campañas con 241 `go`» y pasa a ser: limpiar la
terna del grupo 1, y migrar a excepción solo lo que el dueño decida (candidatas
naturales: las 4 que cosechan). El resto se queda en `migracion_pendiente`, que
es un estado legítimo y declarado, no deuda.

## 3. Cardinalidad de ad groups: la generalización de 0.1 era falsa

Como sospechó CodeRabbit en el PR #255:

| plataforma | campañas | con más de un ad group | máximo |
|---|---|---|---|
| `amazon_mx` | 172 | 5 | **8** |
| `amazon_us` | 74 | 3 | 3 |

Las ocho, con su situación:

| plataforma | campaña | nombre | ad groups | tiene goal | en grupo |
|---|---|---|---|---|---|
| `amazon_mx` | `247752387730232` | Advertise Catalog | 8 | no | no |
| `amazon_mx` | `41838110423690` | Advertise New Products | 6 | no | no |
| `amazon_mx` | `197847609934755` | Advertise Items with Low Traffic | 5 | no | no |
| `amazon_mx` | `61913708769422` | Advertise Top Selling Items | 4 | no | no |
| `amazon_mx` | `36317630043575` | Advertise Excess FBA Inventory | 3 | no | no |
| `amazon_us` | `251878853657127` | Advertise Catalog | 3 | no | no |
| `amazon_us` | `260356723846228` | Arras - Manual keywords targeting | 3 | no | no |
| `amazon_us` | `27366144191311` | Arras Productos | 2 | no | no |

**Ninguna tiene goal ni está en grupo**, así que hoy no entran al camino del
harvest y `primer_ad_group_de_campana` no puede equivocarse con ellas. El flag
de ad group **no vuelve al alcance** de A.0/A.3, pero queda como **condición
vigilada**: si alguna de estas ocho gana un goal, o si una campaña de grupo
gana un segundo ad group, el flag es obligatorio.

## 4. Estado de las tablas de F2

| tabla | filas |
|---|---|
| `campana_grupo` | 1 |
| `harvest_excepcion` | 0 |
| `harvest_job` | 0 |
| `keyword_biblioteca` | 0 |
| `negative_biblioteca` | 0 |
| `keyword_archivo_manual` | 0 |

Todo lo que F2 va a escribir está vacío: no hay estado previo que migrar ni con
el que chocar.

## 5. Límite declarado: las páginas del LIST no son medibles desde la base

El DoD pedía «conteo de negativos por perfil (páginas que ocupa el LIST)».
**No se puede cumplir con una lectura de la base**: Orbit no espeja los
negativos de Amazon. El enum `ad_entity_kind` tiene `campaign`, `ad_group`,
`keyword`, `product_target`, `placement` y `product_ad` — **no hay
`negative_keyword`**, y las únicas tablas con «negativ» en el nombre son
`negative_biblioteca` (de F1, vacía) y `keyword_archivo_manual` (vacía).

El único camino sería un LIST contra Amazon, y 0.2 es explícitamente sin
llamadas. **No se inventa el número.** El riesgo que motivaba la medición —el
truncado de `_lista_todos` a `TOPE_PAGINAS_LIST = 20`, con MX estimado en ~2 600
negativos ≈ 26 páginas— sigue vigente y ya está cubierto por diseño: A.3 exige
un solo LIST filtrado por job y **fail-closed** si trunca (`list_truncado`),
con su test de paginación por encima del tope.

## Consultas corridas

Las cinco, como `orbit_read` vía `docker exec -i orbit-db-1 psql "$ORBIT_DSN_READ"`:

1. Goals por `scope`, con `enabled` y con terna.
2. Campañas con goal `enabled` de scope `campaign` y terna: rol en grupo,
   destino, existencia del ad group, parentesco y plataforma.
3. Cardinalidad de ad groups por campaña, y el detalle de las que tienen más de
   uno con su goal/grupo.
4. Clasificación de las 246 campañas por cómo resolverían el destino.
5. Campañas que alguna vez produjeron `decision.kind = 'harvest'`, con su goal,
   terna y grupo; y las últimas 6 decisiones de harvest con su campaña origen.

Más los conteos de las tablas de F2 y la inspección del enum `ad_entity_kind`.

## Residual

La clasificación de la sección 1 cuenta **todas** las campañas de `ad_entity`,
incluidas pausadas o archivadas: 241 es el techo del impacto, no el número de
campañas que cosecharían mañana. El punto no cambia — las 4 que cosechan están
entre ellas — pero el número exacto de campañas activas afectadas se mediría
con el estado vivo, y no hacía falta para la decisión.
