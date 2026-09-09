# SP-API 01 — Plan formal de lecturas SP-API (solo lectura)

Version: 1.0, 2026-09-09 UTC. Estado: **PLAN (no implementado)**.
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

Fuera (salvo que el dueno lo pida en D1–D7):

- Cualquier escritura a Amazon (precios, listings, feeds) y cualquier decision
  (repricing, pausas, promociones): viven en `repricing-01`, `envios-01`,
  `reputacion-02`.
- Datos con PII de compradores: si un endpoint exige Restricted Data Token,
  queda fuera y declarado.
- Finances `2024-06-19` y Reports (ver D3); Account Health (sin endpoint
  publico: no se promete); reviews de producto (SP-API no las expone).

## Decisiones abiertas para el dueno (D1–D7, respuesta si/no/valor)

| ID | Pregunta | Opciones | Recomendacion del plan |
|---|---|---|---|
| D1 | Que lecturas entran a v1: las cinco (Orders, Pricing, Listings Items, Inventario FBA, Sellers) o solo Pricing + Orders | A) las cinco; B) solo Pricing + Orders | A: las cinco sondas son baratas (GET) y M5/Reputacion v2 necesitan las cinco tarde o temprano; recortarlas solo ahorra una ingesta, no un riesgo |
| D2 | Fuente de verdad de precio/stock de listings: el bridge sigue mandando y SP-API solo aporta Buy Box/competencia, o SP-API reemplaza al bridge | A) bridge manda, SP-API aporta Buy Box/competencia; B) SP-API reemplaza | A (regla 2: un numero, una fuente). Una lectura SP-API de precio o stock NO se convierte en segunda fuente sin esta decision explicita |
| D3 | Finances `2024-06-19` (fees e ISR sin `order_id`, se prorratea) entra aqui o queda para el ledger | A) entra aqui; B) queda para el ledger | B: es la fuente que mas dinero costo equivocarse; su prorrateo de ISR merece plan propio contra el ledger, no colarse en v1 |
| D4 | Cadencia por fuente y tope de llamadas (diaria vs intradia para Buy Box) | Valor: cadencia por fuente + tope diario de llamadas | Diaria para todo en v1; intradia para Buy Box solo si Repricing lo exige en su plan (el doc sugiere Buy Box cada pocas horas: eso se decide con el rate limit medido en Fase 0) |
| D5 | Consolidar los dos refrescadores LWA ad hoc en `app/spapi/` o dejar cada modulo con el suyo | A) consolidar (recomendado); B) dejarlos | A: la trampa de CONTEXTO (MeLi tenia dos refrescadores compitiendo; Orbit exige uno) aplica tal cual; migracion sin cambio de comportamiento, tests intactos |
| D6 | Mercados: MX + US desde v1, o MX primero | A) MX + US; B) MX primero | A: Sellers ya verifico participations MX + US y el mapa `MERCADOS` existe en `app/publicacion_fotos.py`; si la sonda 0.2 muestra friccion en US, se recorta a B sin drama |
| D7 | Retencion: cuanto historico de Orders y Pricing se conserva | Valor: dias/meses por fuente | Orders 180 dias, Pricing 90 dias (punto de partida; el numero final lo fija el dueno con el costo de disco a la vista) |

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
| 0.1 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Orders version vigente: una pagina + paginacion completa (los dos bugs de paginacion de TRASPASO-1 como casos: `NextToken` repetido y pagina vacia). Doc: https://developer-docs.amazon.com/sp-api/docs/orders-api-v0-reference (pinear version exacta en el acta). Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET /orders/<version>/orders?MarketplaceIds=A1AM78C64UM0Y8 + CreatedAfter reciente + MaxResults bajo; seguir NextToken hasta agotar>'` | E/0.1 con version pineada, una pagina + recorrido completo, los dos bugs reproducidos o declarados ausentes en la version vigente, rate limit oficial copiado del doc, cero PII persistida | - | cc:TODO |
| 0.2 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Product Pricing: un ASIN MX y uno US (oferta propia + Buy Box + competidores). Doc: https://developer-docs.amazon.com/sp-api/docs/product-pricing-api-v0-reference. Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET pricing para 1 ASIN MX + 1 ASIN US>'` | E/0.2 con muestra real por mercado, campos de Buy Box/competencia identificados, precio sin moneda tratado como ausente, rate limit pineado | - | cc:TODO |
| 0.3 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Listings Items: un `seller_sku` con mapeo en `amazon_sku_mapping`. Doc: https://developer-docs.amazon.com/sp-api/docs/listings-items-api-v2021-08-01-reference. Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET listings item por seller_sku con mapeo>'` | E/0.3 con estado del listing leido, conciliado contra `listing` de Orbit por `seller_sku` (sin escribir nada), D2 citada | 0.2 | cc:TODO |
| 0.4 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sonda Inventario FBA: comparar contra `amazon_fba_inventory` del bridge. Doc: https://developer-docs.amazon.com/sp-api/docs/fba-inventory-api-v1-reference. Comando lead: `docker exec orbit-app-1 python3 -c '<sonda patron: GET inventario FBA + SELECT de comparacion al snapshot bridge>'` | E/0.4 con comparacion externa (regla 10: conciliar contra la fuente externa, no contra la propia consistencia), D2 citada | 0.3 | cc:TODO |
| 0.5 | [stage:investigacion] [lane:gate] [tdd:skip:investigacion] Sellers: ya verificado, solo citar evidencia | E0.2 citada (`docs/evidencia/reputacion-01/0.4/reporte.md`); sin re-sonda salvo que el dueno la pida | - | cc:TODO |

### Fase A — Cliente unico + migracion + ingesta + salud

Duenos: GLM/Cursor implementan, el lead despliega. Migracion `00NN` (el numero
se fija al aplicar; la ultima referenciada en `docs/DATABASE.md` es `0028`).
Rol de ingesta: `orbit_ingest`.

| Task | Contenido | DoD | Depends | Status |
|---|---|---|---|---|
| A.1 | [stage:implementacion] [lane:gate] [tdd:required] Cliente unico `app/spapi/` (espejo de `app/ads/client.py`): guard default-deny + un solo refrescador LWA; migrar `app/estimacion_fees.py` y `app/publicacion_fotos.py` **sin cambiar su comportamiento**, con sus tests intactos (`tests/test_estimacion_fees.py` incl. `test_allowlist_*`) | pytest_focal del cliente + de ambos modulos pasa; `test_allowlist_*` intactos y en verde; cero llamadas duplicadas de refresh en logs; E/A.1 con diff de comportamiento vacio | 0.1–0.5, D5 | cc:TODO |
| A.2 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta Orders append-only `(order_id?, metric_date, observed_at)` sin PII; paginacion completa con `NextToken` repetido y pagina vacia como tests | pytest_focal pasa; re-corrida no duplica (idempotencia por clave); E/A.2 con conciliacion contra la muestra de 0.1 | A.1 | cc:TODO |
| A.3 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta Pricing append-only por (ASIN, mercado); dinero `(valor, moneda)`, precio sin moneda = fila no escrita | pytest_focal pasa; fixture sin moneda no escribe fila; E/A.3 conciliada contra 0.2 | A.1 | cc:TODO |
| A.4 | [stage:implementacion] [lane:gate] [tdd:required] Ingesta Listings Items + Inventario FBA append-only, respetando D2 (si D2=A, SP-API no pisa precio/stock del bridge: solo Buy Box/competencia/estado) | pytest_focal pasa; test de que precio/stock SP-API no sobrescribe `listing` salvo D2=B; E/A.4 conciliada contra 0.3/0.4 | A.1, D2 | cc:TODO |
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
plan-preapprovals.json con decision approved sin respuesta explicita del dueno
(D1–D7) y revision del lead.

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
  "description": "SP-API 01 — PLAN 2026-09-09: lecturas solo-lectura (Orders, Pricing, Listings Items, Inventario FBA, Sellers) con cliente unico app/spapi/ y un solo refrescador LWA; Fase 0 Por probar, D1-D7 abiertas para el dueno"
}
```

## Estado para la siguiente sesion

- Plan redactado, sin codigo ni sondas: Fase 0 entera `cc:TODO`, D1–D7 abiertas.
- Siguiente paso: el lead revisa contra repo y base viva; el dueno cierra D1–D7;
  solo entonces otro brief implementa la Fase 0 y la Fase A.
- No se marco nada como completo: escribir el plan no cierra nada (regla del ROADMAP).
