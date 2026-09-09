# SP-API 01 — Plan formal de lecturas SP-API (solo lectura)

Version: 1.2, 2026-09-09 UTC. Estado: **Fase 0 cerrada; Fase A en curso (A.1, A.2/A.2b y A.3 mergeadas; A.4, A.5, A.R y A.6 pendientes)**. D1–D7 cerradas por el dueño 2026-09-09 UTC. Fase 0 verificada 2026-09-09 UTC: sondas y actas en `docs/evidencia/sp-api-01/0.1–0.5/` (PR #236, Muse; validadas por el lead).
Base: `origin/master` `38486d0` (PR #230). Rama: `plan/sp-api-01`.
Precedencia: `docs/CONTEXTO.md` (reglas 1–10) > `plans/ROADMAP.md` > este plan.
Fuente verbatim `docs/traspaso/MODULOS-AVANZADOS.md` (M1, M5, Transversales) **no se edita**;
donde contradice a CONTEXTO, se senala y gana CONTEXTO (ver "Divergencias detectadas").
No hay fechas ni presupuestos en este plan. Ningun secreto en el repo ni en el plan.

## Resultado y limites

Proposito: darle a Orbit lecturas oficiales SP-API de solo lectura, con un unico
cliente y un solo refrescador LWA, para desbloquear M1 Repricing, M5 Envios y
Reputacion v2 — sin crear una segunda fuente de ningun numero sin decision del dueno.

Dentro (v1, fijo; ampliar solo con el dueno):

- Un cliente SP-API unico de **solo lectura** (`app/spapi/`, espejo de
  `app/ads/client.py`): guard default-deny (host, metodo y path fuera de la
  allowlist fallan antes de red) y **un solo refrescador LWA**.
- Lecturas oficiales: Orders sin PII (version vigente, paginacion completa con
  los dos bugs conocidos como casos de prueba), Product Pricing (oferta propia,
  Buy Box y competidores por ASIN), Listings Items (estado del listing) e
  Inventario FBA; Sellers reutilizado como sonda de identidad (ya verificado).
- Persistencia **append-only** por fuente con `(entidad, metric_date,
  observed_at)`; dinero `(valor, moneda)` NUMERIC + ENUM, prohibido float; dato
  ausente = fila no escrita (reglas 3, 4, 5).
- Salud de la integracion visible en `/salud`; alertas reutilizando
  `app/notifica.py` (no se inventa canal).
- Rate limit oficial de cada endpoint documentado en su acta y respetado sin
  colas (decision de stack: sin Redis ni colas).

Fuera (D1–D7 cerradas 2026-09-09 UTC; Finances queda fuera por D3):

- Cualquier escritura a Amazon (precios, listings, feeds) y cualquier decision
  (repricing, pausas, promociones): viven en `repricing-01`, `envios-01`,
  `reputacion-02`.
- Datos con PII de compradores: si un endpoint exige Restricted Data Token,
  queda fuera y declarado.
- Finances `2024-06-19` y Reports (ver D3); Account Health (sin endpoint
  publico: no se promete); reviews de producto (SP-API no las expone).

## Decisiones (cerradas por el dueno 2026-09-09 UTC)

Respuesta literal del dueno: «1. si 2. si 3. no eso es aparte 4. si 5. si 6. si 7. no por ahora». Todas coinciden con la recomendacion del plan.

| ID | Pregunta | Opciones | Recomendacion del plan | Decision del dueno |
|---|---|---|---|---|
| D1 | Que lecturas entran a v1: las cinco (Orders, Pricing, Listings Items, Inventario FBA, Sellers) o solo Pricing + Orders | A) las cinco; B) solo Pricing + Orders | A: las cinco sondas son baratas (GET) y M5/Reputacion v2 necesitan las cinco tarde o temprano; recortarlas solo ahorra una ingesta, no un riesgo | A — entran las cinco lecturas (Orders, Pricing, Listings Items, Inventario FBA, Sellers) |
| D2 | Fuente de verdad de precio/stock de listings: el bridge sigue mandando y SP-API solo aporta Buy Box/competencia, o SP-API reemplaza al bridge | A) bridge manda, SP-API aporta Buy Box/competencia; B) SP-API reemplaza | A (regla 2: un numero, una fuente). Una lectura SP-API de precio o stock NO se convierte en segunda fuente sin esta decision explicita | A — el bridge sigue siendo la fuente de precio y stock; SP-API solo aporta Buy Box, competencia y estado del listing. Ninguna tarea de A.4 escribe precio ni stock en `listing` |
| D3 | Finances `2024-06-19` (fees e ISR sin `order_id`, se prorratea) entra aqui o queda para el ledger | A) entra aqui; B) queda para el ledger | B: es la fuente que mas dinero costo equivocarse; su prorrateo de ISR merece plan propio contra el ledger, no colarse en v1 | B — Finances `2024-06-19` queda fuera; literal del dueno: «no eso es aparte». Plan propio contra el ledger, no aqui |
| D4 | Cadencia por fuente y tope de llamadas (diaria vs intradia para Buy Box) | Valor: cadencia por fuente + tope diario de llamadas | Diaria para todo en v1; intradia para Buy Box solo si Repricing lo exige en su plan (el doc sugiere Buy Box cada pocas horas: eso se decide con el rate limit medido en Fase 0) | Diaria para todas las fuentes en v1 |
| D5 | Consolidar los dos refrescadores LWA ad hoc en `app/spapi/` o dejar cada modulo con el suyo | A) consolidar (recomendado); B) dejarlos | A: la trampa de CONTEXTO (MeLi tenia dos refrescadores compitiendo; Orbit exige uno) aplica tal cual; migracion sin cambio de comportamiento, tests intactos | A — consolidar los dos refrescadores LWA en `app/spapi/` |
| D6 | Mercados: MX + US desde v1, o MX primero | A) MX + US; B) MX primero | A: Sellers ya verifico participations MX + US y el mapa `MERCADOS` existe en `app/publicacion_fotos.py`; si la sonda 0.2 muestra friccion en US, se recorta a B sin drama | A — MX + US desde v1 |
| D7 | Retencion: hay alguna razon para purgar historico de Orders y Pricing, o v1 conserva todo | A) sin purga; B) purgar con valor dias/meses por fuente | A: v1 sin purga (append-only conserva todo); la retencion se decide con el volumen medido en Fase 0 (cada acta 0.x reporta filas/dia por fuente). Si no hay razon para purgar, D7 se cierra como "sin purga" | Sin purga en v1; literal del dueno: «no por ahora». La retencion se revisa cuando Fase 0 reporte volumen |

## Evidencia Fase 0 (verificada antes de este plan)

- E0.1 Auth LWA viva en produccion: `app/estimacion_fees.py` (MARGEN ESTIMADO 01
  A.3, desplegado) — POST Product Fees con LWA exacto
  `api.amazon.com/auth/o2/token`, guard default-deny, 429 reintentable;
  `app/publicacion_fotos.py` (ORBIT 19) — GET Catalog Items con su propio
  refresh LWA y mapa `MERCADOS`. Dos refrescadores ad hoc (ver D5).
- E0.2 Sellers verificado:
  `docs/evidencia/reputacion-01/0.4/reporte.md` — `GET /sellers/v1/account` y
  `GET /sellers/v1/marketplaceParticipations` responden 200, participations
  MX + US, storeName EHV. Sirve para identidad, no para salud.
- E0.3 Lo que NO existe: Account Health no tiene endpoint publico; SP-API no
  expone reviews (misma evidencia 0.4 / A.1). No se prometen.
- E0.4 Fuente actual de listings/stock: SQLite del bridge
  (`amazon_listing_prices` + `amazon_sku_mapping` -> tabla `listing`, docstring
  de `app/listings.py`; stock FBA en `amazon_fba_inventory`,
  `docs/evidencia/orbit-19/0.3/reporte.md`). Puente obligatorio
  `amazon_sku_mapping` por `seller_sku` (ver D2).

## Etapas y tareas

DoD = criterio binario de terminado. `E/<task>/` abrevia
`docs/evidencia/sp-api-01/<task>/`: comando corrido, salida sanitizada (sin
secretos, sin PII), URL oficial pineada, reporte con veredicto. Ninguna version
de API se inventa: cada sonda pinea la URL exacta vigente en su acta.
`pytest_focal` = `PYTHONPATH=. .venv/bin/python -m pytest -q <archivo modificado>`.
Cada regresion se demuestra fallando contra el codigo previo (regla 9). La suite
completa corre en CI sobre el PR, no se duplica local (decision del dueno
2026-08-29). La implementacion llega en otro brief; aqui solo el plan.
Las sondas las corre el **lead** desde el contenedor (nombre atestiguado en
`docs/evidencia/margen-estimado-01/0.2/reporte.md`: `orbit-app-1`); quien
redacta no tiene ssh y no corre sondas.
Patron de sonda (las cinco tareas lo usan): cargar `amazon_credentials.json`
desde `ORBIT_SECRETS_DIR`, refresh LWA contra
`https://api.amazon.com/auth/o2/token`, GET con header `x-amz-access-token`
(mismo patron del codigo desplegado), imprimir solo status + claves de la
respuesta, jamas tokens ni PII. Redaccion via `app/redaction.py`.

### Fase 0 — Sondas read-only, una por fuente (todas Por probar salvo 0.5)

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| 0.1 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Orders: la sucesora 2026-01-01 existe (referencia, changelog y guia de migracion); v0 se retira el 2027-03-27; la sonda cubre ambas. La version 2026-01-01 de TRASPASO-1 linea 282 ya tiene referencia oficial. Una pagina + paginacion completa (los dos bugs de paginacion de TRASPASO-1 como casos: `NextToken` repetido y pagina vacia, reproducidos contra la version que responda). Doc: https://developer-docs.amazon/sp-api/docs/orders-api-v0-reference. La sonda confirma con que version responde Amazon y si hay aviso de deprecacion en headers o cuerpo (pinear version exacta en el acta). Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET /orders/<version>/orders?MarketplaceIds=A1AM78C64UM0Y8 + CreatedAfter reciente + MaxResults bajo; seguir NextToken hasta agotar>'` | E/0.1 con version pineada, una pagina + recorrido completo, los dos bugs reproducidos o declarados ausentes en la version vigente, rate limit oficial copiado del doc, cero PII persistida | - | cc:完了 [2026-09-09 (lead): E/0.1 verificada — v0 y 2026-01-01 responden 200 x3 páginas, sin los bugs de paginación, cero PII; v0 exige Zulu (400 con +00:00, corregido); aviso fijo de retiro 2027-03-27] |
| 0.2 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Product Pricing: un ASIN MX y uno US. Operaciones candidatas (confirmar en el doc): la de ofertas por ASIN que trae Buy Box y la de precios competitivos. Doc: https://developer-docs.amazon/sp-api/docs/product-pricing-api-v0-reference. Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET pricing para 1 ASIN MX + 1 ASIN US>'` | E/0.2 con muestra real por mercado, campos de Buy Box/competencia identificados, precio sin moneda tratado como ausente, rate limit pineado | - | cc:完了 [2026-09-09 (lead): E/0.2 verificada — MX con Buy Box y competitivos; US 200 con 0 ofertas declarado como ausencia; rate limit 0.5] |
| 0.3 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Listings Items: un `seller_sku` con mapeo en `amazon_sku_mapping`. Operacion candidata (confirmar en el doc): la lectura de un item por `sellerId` + `sku` (la ruta lleva `sellerId`: ver 0.5). Doc: https://developer-docs.amazon/sp-api/docs/listings-items-api-v2021-08-01-reference. Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET listings item por seller_sku con mapeo>'` | E/0.3 con estado del listing leido, conciliado contra `listing` de Orbit por `seller_sku` (sin escribir nada), D2 citada, rate limit oficial pineado del doc | 0.5, 0.2 | cc:完了 [2026-09-09 (lead): E/0.3 verificada — 200 con summaries del listing; sellerId por parámetro (accountInfo.id del perfil Ads MX, ver E/0.5)] |
| 0.4 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Inventario FBA: comparar contra `amazon_fba_inventory` del bridge. Operacion candidata (confirmar en el doc): la de resumenes de inventario y su paginacion. Doc: https://developer-docs.amazon/sp-api/docs/fbainventory-api-v1-reference. Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET inventario FBA + SELECT de comparacion al snapshot bridge>'` | E/0.4 con comparacion externa (regla 10: conciliar contra la fuente externa, no contra la propia consistencia), D2 citada, rate limit oficial pineado del doc | 0.3 | cc:完了 [2026-09-09 (lead): E/0.4 verificada tras corrección de paginación (pagination hermana de payload): 22 páginas, 1071 summaries, universo conciliado 1071/1071 contra el bridge, 8 SKUs difieren por 1 unidad (intradía)] |
| 0.5 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sellers: ya verificado, solo citar evidencia | E0.2 citada (`docs/evidencia/reputacion-01/0.4/reporte.md`); sin re-sonda salvo que el dueno la pida | - | cc:完了 [2026-09-09 (lead): E/0.5 verificada — participations MX+US y account 200; getAccount no trae sellerId (solo EU): sellerId = accountInfo.id del perfil Ads] |

### Fase A — Cliente unico + migracion + ingesta + salud

Duenos: GLM/Cursor implementan, el lead despliega. Migracion `00NN` (el numero
se fija al aplicar; la ultima referenciada en `docs/DATABASE.md` es `0028`).
Rol de ingesta: `orbit_ingest`.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] Cliente unico `app/spapi/` (espejo de `app/ads/client.py`): guard default-deny + un solo refrescador LWA; migrar `app/estimacion_fees.py` y `app/publicacion_fotos.py` **sin cambiar su comportamiento**, con sus tests intactos (`tests/test_estimacion_fees.py` incl. `test_allowlist_*` + `tests/test_publicacion_fotos.py`) | pytest_focal del cliente + de ambos modulos pasa; `test_allowlist_*` y `tests/test_publicacion_fotos.py` intactos y en verde; cero llamadas duplicadas de refresh en logs; E/A.1 con diff de comportamiento vacio | 0.1–0.5 | cc:完了 [2026-09-09 (lead): PR #237 — `app/spapi/client.py` (SpapiClient + cliente_compartido): un refrescador LWA por proceso, allowlist default-deny (GET de las 5 fuentes + Catalog Items + POST fees), 401 un refresh coordinado, 429 acotado; fees y fotos migrados sin cambio de comportamiento (tests intactos); sonda importa de app.spapi; 15 tests nuevos verificados por mutación; cross-review kimi 1 ronda; CodeRabbit: 3 hallazgos atendidos (invalidar token en 401/403 de fees, refresh coordinado, assert muerto). E/A.1] |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta Orders append-only sin PII (clave definida en E/0.1 antes de A.2; candidata: identificador de orden de Amazon + `LastUpdateDate` + `observed_at`); paginacion completa con `NextToken` repetido y pagina vacia como tests | pytest_focal pasa; clave documentada en E/0.1; re-corrida no duplica (idempotencia probada contra esa clave); E/A.2 con conciliacion contra la muestra de 0.1 | A.1 | cc:完了 [2026-09-09 (lead): PR #239 — migración 0030 `spapi_order_observation` (clave (platform, amazon_order_id, last_updated_time), dinero con moneda, trigger purchase<=updated, append-only por prohibir_mutacion, grants mínimos con asserts negativos); `app/spapi/orders.py`: Orders 2026-01-01 sin PII, ventana lastUpdatedAfter=max−1d (primera createdAfter 30d, Zulu), paginación con guardas, contrato estricto (sin lista orders = ok=false), token bucket 20/0.0056; CLI `ingest spapi_orders --platform`; e2e idempotente en Postgres (CI); fixtures largos + `register_secret` ignora <8 chars (rompía tests ajenos). Mutaciones verificadas por el lead. E/A.2. **A.2b (PR #240, mergeado 2026-09-09):** pedidos con estado y total vía secciones FULFILLMENT + PROCEEDS (jamas BUYER/RECIPIENT), migración 0031 (clave bitemporal de 4 columnas `(platform, amazon_order_id, last_updated_time, observed_at)`, columna `fulfillment_status`, vista `v_spapi_order_ultima`, reversa con guarda doble), backfill `--desde`; rondas del lead y de CodeRabbit atendidas (respaldo de moneda por None, sello con detalle de skips, COMMENT refrescado, tests que muerden). Pendiente A.6: aplicar 0030+0031 en producción, deploy A.1+A.2/A.2b y primera corrida real (dueño)] |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta Pricing append-only por (ASIN, mercado); dinero `(valor, moneda)`, precio sin moneda = fila no escrita | pytest_focal pasa; fixture sin moneda no escribe fila; E/A.3 conciliada contra 0.2 | A.1 | cc:完了 [2026-09-09 (lead): PR #241 — migraciones 0032 `spapi_price_observation` (clave (asin, platform, observed_at), dinero NUMERIC+enum, trigger `spapi_price_tiempo_coherente` con UTC fijado: metric_date = dia UTC de observed_at, append-only por prohibir_mutacion, grants minimos) y 0033 (`ingest_run.llamadas`); `app/spapi/pricing.py`: pase diario por ASIN propio de `listing` (MX+US), offers + competitivePrice a 0.5/s con cubo compartido `CuboTasa`, 0 ofertas = fila con conteos 0 y precios NULL, precio sin moneda = fila no escrita + rows_skipped, CLI `ingest spapi_pricing --platform`; `register_secret` sin piso (cambio global declarado en el PR). Rondas del lead y CodeRabbit atendidas (conteo FBA solo condicion New, except redundante, premisas documentadas). E/A.3. Pendiente A.6: aplicar 0032+0033 en producción y primera corrida real (dueño)] |
| A.4 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta Listings Items + Inventario FBA append-only (D2=A cerrada: SP-API no pisa precio/stock del bridge; solo Buy Box, competencia y estado del listing; ninguna escritura de precio ni stock en `listing`) | pytest_focal pasa; test de que precio/stock SP-API no sobrescribe `listing`; E/A.4 conciliada contra 0.3/0.4 | A.1 | cc:TODO |
| A.5 | [stage:implementacion] [lane:gate] [tdd:required] Salud en `/salud` + alertas via `app/notifica.py` (fallo sostenido, 429 persistente, refresh LWA fallido — este ultimo no tumba el ciclo de Ads) | pytest_focal pasa; refresh LWA caido deja traza en `/salud` y el ciclo de Ads sigue; E/A.5 | A.2–A.4 | cc:TODO |
| A.R | [stage:revision] [lane:gate] [tdd:skip:revision] Revision independiente de A | Reviewer devuelve APPROVE sobre SHA concreto (guard, un solo refrescador, append-only, dinero, redaccion, sin PII); ningun hallazgo bloqueante abierto; E/A.R enlaza informe | A.5 | cc:TODO |
| A.6 | [stage:cierre-pr] [lane:release] [tdd:skip:validacion-entrega] CI, integracion y despliegue A | Ruff/pre-commit pasan; PR con suite completa verde; migracion `00NN` aplicada con backup; smoke de lectura sin mutaciones; reversa ensayada (apagar ingesta); E/A.6 con SHA y resultados | A.R | cc:TODO |

### Fase B — Declarada, fuera de este plan

Consumo por `repricing-01` (M1), `envios-01` (M5) y `reputacion-02` (v2), cada
uno con su plan y su brief. Este plan no decide nada de precios, envios ni
reputacion: solo deja las lecturas disponibles y sanas.

## Propiedad de archivos y concurrencia

| Tasks | Archivos/area previstos | Restriccion |
|---|---|---|
| 0.1–0.5 | E/0.x, sin codigo | Solo lectura contra Amazon; no toca el contenedor de produccion salvo el `docker exec` de sonda del lead |
| A.1 | `app/spapi/` nuevo, `app/estimacion_fees.py`, `app/publicacion_fotos.py`, `tests/test_estimacion_fees.py` + tests del cliente | Un solo editor en `app/spapi/`; los dos modulos migrados sin cambio de comportamiento |
| A.2–A.4 | Modulos de ingesta nuevos bajo `app/spapi/`, migracion `00NN`, tests nuevos | Numero de migracion reservado antes de implementar; no editar migraciones selladas |
| A.5 | Superficie de `/salud`, `app/notifica.py` (solo consumo) | No inventar canal de alerta; no cambiar el contrato de `notifica_*` |
| A.R/A.6 | Evidencia, `docs/DEPLOY.md` (runbook), PR | Reviewer solo lectura; lead integra y despliega |

No choca con `fabrica-01` t11 (sonda real de creacion, pendiente del lead) ni
con `orbit-05` 2.3/2.5 (harvest natural, pendientes): archivos y dominios
disjuntos; la unica superficie compartida es el patron LWA, que A.1 unifica sin
tocar Ads.

## Aceptacion verificable

| ID | Caso | Resultado esperado | Evidencia |
|---|---|---|---|
| AC1 | 429 sostenido | Reintento acotado con backoff, luego fallo declarado en `/salud`; jamas loop infinito | Test con transporte simulado + `/salud` |
| AC2 | Endpoint fuera de allowlist | Falla antes de red (sin HTTP emitido) | `test_allowlist_*` espejo + contador HTTP=0 |
| AC3 | Orders pagina vacia / `NextToken` repetido | Termina sin duplicar ni colgarse | Tests de paginacion + E/0.1 |
| AC4 | Precio sin moneda | Fila no escrita (regla 3), contada como ausencia | Test + conteo de skips |
| AC5 | Refresh LWA fallido | No tumba el ciclo de Ads; alerta via `app/notifica.py` | Test de aislamiento + `/salud` |
| AC6 | Secreto en logs/errores | Jamas aparece (redaccion `app/redaction.py`) | Test de scrub + revision de E/*. |
| AC7 | PII de comprador en Orders | Campo fuera; si exige RDT, endpoint declarado fuera | Revision de campos persistidos |

## Secuencia de despliegue y reversa

1. Backup verificable; migracion `00NN` expansiva y compatible.
2. Desplegar cliente + ingesta en modo lectura; smoke GET sin mutaciones a Amazon.
3. Ante fallo: la reversa es **apagar la ingesta y conservar los datos**
   (solo lectura: no hay nada que deshacer en Amazon). Explícito: ningun deploy
   de este plan escribe en Amazon, asi que ningun rollback necesita reversa
   externa; los datos append-only ya escritos se conservan y se marcan por
   `observed_at`.
4. Re-encendido: reanudar ingesta; los huecos quedan visibles como huecos
   (regla 3: jamas rellenar con constantes).

## Confirmacion operativa previa por fase

Inventario harness-plan; no es aprobacion concedida. Sin
plan-preapprovals.json con decision approved sin la revision del lead
(D1–D7 ya cerradas por el dueno 2026-09-09 UTC).

| Asunto/operacion | Motivo | Scope y limites |
|---|---|---|
| Sondas GET SP-API desde `orbit-app-1` | Verificar contratos reales (Fase 0) | 0.1–0.4; solo lectura, volumen minimo (una pagina + un ASIN/SKU por mercado); sin PII persistida |
| Carga de `amazon_credentials.json` via `ORBIT_SECRETS_DIR` | Auth LWA de las sondas y la ingesta | Solo lectura del secreto; nunca imprimir valores ni copiar al repo |
| SELECT a Orbit y snapshots bridge | Conciliacion externa (regla 10) | 0.3/0.4/A.2–A.4; read-only contra bridge/accounting (no se tocan) |
| git push, PR y lectura de CI | Revision y calidad | A.6; rama desde `origin/master`; sin force push ni `--no-verify` |
| Backup + migracion `00NN` + deploy lectura | Publicar ingesta recuperable | A.6; runbook `docs/DEPLOY.md`; reversa = apagar ingesta |

## Divergencias detectadas (MODULOS-AVANZADOS vs CONTEXTO; gana CONTEXTO)

- Transversales piden "tokens encrypted at rest": CONTEXTO/ROADMAP declaran
  tokens 600/uid sin cifrar como gap conocido. Este plan no lo resuelve, lo cita.
- Transversales piden "colas para llamadas a APIs": decision vigente es sin
  Redis ni colas; rate limits se respetan con limitador local por proceso.
- M3 lista Account Health como metrica Amazon: no hay endpoint publico (E0.3);
  reputacion-02 debera degradarlo igual que hizo reputacion-01.

## Snippet para `plans/manifest.json` (lo aplica el lead; no cambia `active`)

```json
{
  "name": "sp-api-01",
  "path": "plans/sp-api-01.md",
  "description": "SP-API 01 — PLAN (no implementado) 2026-09-09 UTC: lecturas solo-lectura (Orders, Pricing, Listings Items, Inventario FBA, Sellers) con cliente unico app/spapi/ y un solo refrescador LWA; D1-D7 cerradas por el dueno 2026-09-09; Fase 0 Por probar; implementacion solo con brief nuevo del lead. PR #231."
}
```

## Estado para la siguiente sesion

- Fase 0 cerrada (0.1–0.5 `cc:完了`, actas en docs/evidencia/sp-api-01/); `tools/sonda_spapi.py` queda como herramienta de sondas de solo lectura.
- Fase A en curso: A.1 (PR #237), A.2 (PR #239), A.2b (PR #240) y A.3 (PR #241) mergeadas 2026-09-09. Siguen A.4 (Listings + Inventario), A.5 (salud y alertas), A.R (revision) y A.6 (deploy: aplicar 0030-0033 en producción y primeras corridas reales — dueño, con runbook).
