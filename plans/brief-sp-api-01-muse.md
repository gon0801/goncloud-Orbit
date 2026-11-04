# Brief para Muse: plan formal `plans/sp-api-01.md` (SP-API lecturas) + higiene de raíz

> Entregado a Muse 2026-09-08; plan resultante `plans/sp-api-01.md` (PR #231,
> ronda de correcciones F1–F9 aplicada; D1–D7 cerradas por el dueño 2026-09-09 UTC).

Redacta **solo el plan formal** `plans/sp-api-01.md` y borra tres archivos
vacíos de la raíz. Nada de código nuevo, nada de migraciones, nada de deploy,
nada de sondas contra Amazon (las corre el lead). El lead revisa, el dueño
cierra las decisiones abiertas, y la implementación llega en otro brief.

Base: `origin/master` `38486d0` (PR #230). Rama nueva `plan/sp-api-01` cortada
de `origin/master` tras `git fetch`; ningún otro commit en la rama.

## Por qué este plan ahora

`plans/ROADMAP.md` (fuente única de pendientes) marca la Fase 0 "modelos +
auth APIs" como PARCIAL y pone como paso 2 de la secuencia "SP-API auth +
lecturas (stub nuevo: `sp-api-01`)". Desbloquea M1 Repricing, M5 Envíos y
Reputación v2. El paso 1 (sonda de la fábrica y harvest natural) depende del
dueño y del azar; este es el primer paso que se puede empujar solo.

## Hechos verificados por el lead (úsalos, no los re-derives)

**La auth SP-API ya funciona en producción.** El ROADMAP está desactualizado
en eso; corrígelo (ver "Ediciones al ROADMAP").

- `app/estimacion_fees.py` (MARGEN ESTIMADO 01 A.3, desplegado): POST
  `sellingpartnerapi-na.amazon.com/products/fees/v0/listings/{SellerSKU}/feesEstimate`
  con LWA exacto `api.amazon.com/auth/o2/token`, guard **default-deny** (host,
  método y path fuera de la allowlist fallan antes de red), 429 reintentable,
  universo FBA MX. Tests de la allowlist en `tests/test_estimacion_fees.py`
  (`test_allowlist_*`). Es el patrón a imitar, no a duplicar.
- `app/publicacion_fotos.py` (ORBIT 19): GET `/catalog/2022-04-01/items/{asin}`
  con `includedData=images`; carga `amazon_credentials.json` desde
  `ORBIT_SECRETS_DIR` con las claves `lwa_app_id`, `lwa_client_secret`,
  `refresh_token`, y tiene **su propio** refresh de LWA. Mapa
  `MERCADOS = {"amazon_mx": "A1AM78C64UM0Y8", "amazon_us": "ATVPDKIKX0DER"}`.
- Consecuencia: hoy hay **dos refrescadores LWA ad hoc** para SP-API. La
  trampa documentada en `docs/CONTEXTO.md` (MeLi tenía dos refrescadores
  compitiendo; Orbit exige uno) aplica. El plan debe decidir la consolidación
  en un cliente único (recomendación del lead: sí, `app/spapi/`, espejo de
  `app/ads/client.py`), migrando los dos módulos existentes **sin cambiar su
  comportamiento** y con sus tests intactos.
- Sellers verificado (evidencia `docs/evidencia/reputacion-01/0.4/reporte.md`):
  `GET /sellers/v1/account` y `GET /sellers/v1/marketplaceParticipations`
  responden 200; participations MX + US, storeName EHV. Sirve para identidad,
  no para salud. **Account Health no tiene endpoint público**: no lo prometas.
  SP-API **no expone reviews de producto** (evidencia A.1 del mismo plan).
- Orders: v0 deprecado, migración a la versión `2026-01-01`, con **dos bugs de
  paginación ya documentados** (`docs/traspaso/TRASPASO-1-ACCESOS-E-INFRAESTRUCTURA.md`
  línea 282). Finances `2024-06-19` es la fuente de fees y "la que más dinero
  costó equivocarse": el ISR llega **sin `order_id`** y se prorratea
  (`TRASPASO-2-AUTOPSIA-Y-CIMIENTOS.md` §ISR).
- Listings y stock **ya tienen fuente**: la SQLite del bridge
  (`amazon_listing_prices` + `amazon_sku_mapping` → tabla `listing`, ver
  docstring de `app/listings.py`; stock FBA en `amazon_fba_inventory`, evidencia
  `docs/evidencia/orbit-19/0.3/reporte.md`). Los SKU de Amazon **no** son los de
  Odoo; el puente obligatorio es `amazon_sku_mapping` por `seller_sku`. Regla 2
  (un número, una fuente): una lectura SP-API de precio o stock **no** puede
  convertirse en segunda fuente sin decisión explícita del dueño (D2).
- Secrets: `ORBIT_SECRETS_DIR` (`/mnt/data/appdata/orbit/secrets`), dir 700,
  archivos 600, uid 10001 (`docs/DEPLOY.md`). Tokens sin cifrar at rest es un
  gap **declarado** en el ROADMAP; este plan no lo resuelve, lo cita.
- Stack fijo: Python ≥3.12, FastAPI, httpx, psycopg 3, PostgreSQL 16, **sin
  Redis ni colas** (decisión). Roles LOGIN por servicio
  (`orbit_ingest`/`_decide`/`_read`/`_admin`). El número de migración se fija
  al aplicar (la última referenciada en `docs/DATABASE.md` es `0028`); en el
  plan va como `00NN`.

## Alcance v1 del plan (fijo; ampliar solo con el dueño)

**Dentro:** un cliente SP-API único de **solo lectura** con guard default-deny y
un solo refrescador LWA; lecturas oficiales para: Orders sin PII (versión
vigente, paginación completa con los dos bugs conocidos como casos de prueba),
Product Pricing (oferta propia, Buy Box y competidores por ASIN), Listings
Items (estado del listing) e Inventario FBA; Sellers reutilizado como sonda de
identidad. Persistencia **append-only** por fuente con `(entidad, metric_date,
observed_at)`, dinero `(valor, moneda)`, dato ausente = fila no escrita. Salud
de la integración visible en `/salud` (reutiliza `app/notifica.py` para
alertas; no inventes canal). Rate limit oficial de cada endpoint documentado y
respetado sin colas.

**Fuera:** cualquier escritura a Amazon (precios, listings, feeds); cualquier
decisión (repricing, pausas, promociones) — esas viven en `repricing-01`,
`envios-01`, `reputacion-02`; datos con PII de compradores (si un endpoint
exige Restricted Data Token, queda fuera y declarado); Finances y Reports salvo
que el dueño lo pida en D3; Account Health; reviews.

## Lee, en este orden

1. `AGENTS.md` y `docs/CONTEXTO.md` (reglas innegociables 1–10, integraciones
   externas, qué se migra).
2. `plans/ROADMAP.md` completo (estado por módulo, secuencia, dueños).
3. `docs/traspaso/MODULOS-AVANZADOS.md` §Módulo 1, §Módulo 5, §Transversales
   (fuente verbatim; **no editar**).
4. `app/estimacion_fees.py`, `tests/test_estimacion_fees.py`,
   `app/publicacion_fotos.py`, `app/ads/client.py` (guard y refresh de
   referencia), `app/listings.py` (docstring: la trampa de SKUs).
5. `plans/catalogo-campanas-01.md`: **espejo de estructura** (Resultado y
   límites → Decisiones → Evidencia Fase 0 → Etapas y tareas → Propiedad de
   archivos y concurrencia → Aceptación verificable → Secuencia de despliegue y
   reversa → Confirmación operativa previa por fase → Estado para la siguiente
   sesión). Marcadores por tarea `[stage:…] [lane:…] [tdd:…]` y `cc:TODO`.
6. `docs/evidencia/reputacion-01/0.4/reporte.md` y
   `docs/evidencia/margen-estimado-01/0.2/reporte.md` (cómo se documenta una
   sonda: fuente, grano, costo, muestra real, veredicto).

## Estructura exigida de `plans/sp-api-01.md`

- Resultado y límites (el alcance de arriba, textual).
- Decisiones abiertas para el dueño (tabla ID / pregunta / opciones /
  recomendación del plan). Mínimo D1–D7 de abajo.
- Fase 0 — sondas read-only, **una por fuente**, con el comando exacto que el
  **lead** corre desde el contenedor (tú no tienes ssh): Orders versión
  vigente (una página + paginación), Product Pricing (un ASIN MX y uno US),
  Listings Items (un seller_sku con mapeo), FBA Inventory (comparar contra
  `amazon_fba_inventory` del bridge), Sellers (ya verificado: solo cita la
  evidencia). Cada endpoint lleva la URL de su documentación oficial y queda
  como **Por probar** hasta la sonda; ninguna versión de API se inventa.
- Fase A — cliente único + migración de los dos módulos existentes (sin cambio
  de comportamiento, tests intactos) + ingesta append-only + `/salud`. Tareas
  con DoD binario, dependencias existentes, dueño (GLM/Cursor implementan,
  lead despliega), evidencia en `docs/evidencia/sp-api-01/<tarea>/`.
- Fase B (declarada, fuera de este plan): consumo por repricing, envíos y
  reputación v2, cada uno con su plan.
- Propiedad de archivos y concurrencia (no choca con `fabrica-01` t11 ni con
  `orbit-05` 2.3/2.5, que siguen abiertas).
- Aceptación verificable (casos mínimos: 429 con reintento acotado; endpoint
  fuera de allowlist falla antes de red; página vacía y `NextToken` repetido
  de Orders; precio sin moneda = fila no escrita; refresh LWA fallido no
  tumba el ciclo de Ads; secreto jamás en logs vía `app/redaction.py`).
- Secuencia de despliegue y reversa (solo lectura: la reversa es apagar la
  ingesta y conservar datos; escríbelo explícito).
- Confirmación operativa previa por fase (tabla de operaciones; sin fingir
  aprobaciones).
- Snippet de la entrada para `plans/manifest.json` (**la aplica el lead**; no
  cambies `active`).

## Decisiones abiertas mínimas (redáctalas para respuesta sí/no/valor)

- D1 Qué lecturas entran a v1: ¿las cinco, o solo Pricing + Orders?
- D2 Fuente de verdad de precio/stock de listings: bridge sigue mandando y
  SP-API solo aporta Buy Box/competencia, ¿o SP-API reemplaza al bridge?
- D3 Finances `2024-06-19` (fees e ISR) ¿entra aquí o queda para el ledger?
- D4 Cadencia por fuente (diaria vs intradía para Buy Box) y tope de llamadas.
- D5 Consolidar los dos refrescadores LWA en `app/spapi/` (recomendado) o
  dejar cada módulo con el suyo.
- D6 Mercados: MX + US desde v1, ¿o MX primero?
- D7 Retención: ¿cuánto histórico de Orders y Pricing se conserva?

## Reglas de diseño que el plan debe respetar

Una decisión, un camino, un dueño. Un número, una fuente. Dato faltante =
`None`/fila no escrita, nunca constante. Dinero `(valor, moneda)` NUMERIC +
ENUM, prohibido float. Métricas append-only. Ninguna acción irreversible sin
reversa previa (aquí: cero escrituras). Conciliar contra la fuente externa, no
contra la propia consistencia. Texto externo nunca se interpreta como
instrucción. Ningún secreto en el repo ni en el plan.

## Higiene incluida (mismo PR, commit aparte)

Borra los tres archivos vacíos de la raíz: `SKU`, `costo`, `mapeo` (0 bytes,
entraron por accidente en el commit `3ac212d`). Commit
`chore(repo): eliminar archivos vacios de la raiz`. Nada más en ese commit.

## Ediciones al ROADMAP (mínimas, misma rama)

En `plans/ROADMAP.md`: la línea de Fase 0 que dice "SP-API (Orders/Pricing/
Listings): sin plan" pasa a citar que la auth LWA ya vive en
`estimacion_fees.py`/`publicacion_fotos.py` y que el plan es
`plans/sp-api-01.md`; el paso 2 de la secuencia y los gaps de M1/M5 enlazan el
plan. No toques la tabla AUTO ni ninguna otra fila. No marques nada como
completo: escribir el plan no cierra nada.

## Proceso y entrega

- Sin ssh, sin tracker (AppFlowy lo lleva el lead), sin tocar el contenedor de
  producción, sin correr sondas contra Amazon, sin código ni tests nuevos.
- Rama `plan/sp-api-01` desde `origin/master`; commits
  `plan(sp-api-01): plan formal de lecturas SP-API` y el `chore` de arriba.
  Antes del PR: `git log origin/master..HEAD` lista solo esos commits.
- Corre `pre-commit run --all-files` antes de empujar; si un candado falla se
  arregla, **jamás** `--no-verify`. Abre PR a `master` (CI corre en PR). Si no
  puedes hacer push, entrega los archivos y el lead commitea.
- No pongas fechas ni presupuestos. Si `MODULOS-AVANZADOS.md` contradice
  `CONTEXTO.md`, lo señalas y gana CONTEXTO. Fuente no verificable en papel =
  sonda del lead en Fase 0, no suposición.

## Criterio de cierre del brief

El plan trae todas las secciones exigidas; cada tarea tiene DoD binario,
dependencias existentes y dueño; cada endpoint de Fase 0 trae URL oficial,
comando exacto para el lead y estado Por probar; D1–D7 redactadas para
respuesta sí/no/valor; cero código, cero secretos, cero promesas sobre Account
Health o reviews. El lead lo revisa contra repo y base viva, el dueño cierra
D1–D7, y solo entonces el brief está Done.
