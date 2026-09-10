# SP-API 01 / A.R — revisión independiente de Fase A

**Veredicto sobre `958c00f`: APPROVE.**

SHA fijado al empezar y al cerrar:

```
$ git fetch origin && git checkout 958c00f && git rev-parse HEAD
958c00f78f5133bb505c808a6e70ca886991ffcb
$ git log -1 --format='%H %s'
958c00f78f5133bb505c808a6e70ca886991ffcb Merge pull request #248 from gon0801/docs/sp-api-01-a5-cierre
```

`origin/master` al momento de esta revisión apunta al mismo SHA. Si master
avanza después, este APPROVE sigue siendo de `958c00f`, no de lo que venga.

Revisión de solo lectura: cero escrituras a Amazon, cero ssh, cero cambios
de código, cero commits. Suite local contra Postgres Homebrew en
`127.0.0.1:5432` (no es el túnel de producción). Mutantes restaurados con
`git checkout --`; el árbol de tracked queda como al fijar el SHA.

Ámbito leído contra el código, no contra los reportes de evidencia:
`app/spapi/*`, `app/notifica.py` (A.5), `app/api_dashboard.py::_spapi_de`,
`app/templates/salud.html`, `migrations/0030`–`0037`, propuesta de cron y
orden de deploy en `docs/DEPLOY.md`, tests de todo lo anterior, más los
migrados `app/estimacion_fees.py` / `app/publicacion_fotos.py` (D5).

Ningún hallazgo bloqueante abierto. Hay hallazgos de ops (cron) y de poder
discriminante de tests; no reabren el código de Fase A. A.6 no debe
instalar el bloque cron tal cual está escrito — detalle en el eje de
deploy y en Hallazgos.

---

## Eje 1 — Guard default-deny

**Veredicto del eje: cumple.** Allowlist de método/host/path en
`SpapiClient.get` / `validar_get` / `validar_post_fees`. Host SP-API no
se toma del caller: se interpola `SP_API_BASE`
(`https://sellingpartnerapi-na.amazon.com`) tras validar un path
relativo. `httpx` 0.28.1 no sigue redirects (`follow_redirects=False`).
Cero PUT/PATCH/DELETE. El único POST a SP-API es `feesEstimate` (lectura
implementada como POST, A.1). El POST a LWA es `https://api.amazon.com/auth/o2/token`, constante.

Métodos públicos de `SpapiClient`: `get`, `post_fees`, `invalidar_token`.
Las cuatro ingestas solo llaman `client.get(...)`.

Sonda local (cero red) de `validar_get`:

```
ALLOW '/sellers/v1/account'
ALLOW '/orders/v0/orders'
ALLOW '/orders/2026-01-01/orders'
DENY  '/finances/2024-06-19/transactions'          # D3: Finances fuera
DENY  '/orders/v0/../orders'                       # traversal
DENY  '/orders/v0/%2e%2e/orders'                   # encoded .. (no calza allowlist)
DENY  '/listings/.../a%2Fb' y '.../a%2fb'          # encoding de barra
DENY  query/fragment embebidos (`?`, `#`)
DENY  URL absoluta `https://sellingpartnerapi-na.amazon.com/...`
DENY  '/feeds/2021-06-30/feeds'
DENY  '/listings/.../../secret'
DENY  SKU `%2e%2e` (decodifica a `..`)
ALLOW '/listings/.../a%252f'                       # SKU literal "%2f", no slash de path
```

`%2f`/`%2F` en el path se rechaza **antes** de parsear el SKU
(`client.py:201-202`). Eso no es cosmética: sin ese check, el segmento
`a%2Fb` pasa `_validar_segmento_sku` (unquote → `a/b`, requote coincide)
y el GET saldría con slash encoded. Mutante M1 (quitar el check):
`test_allowlist_rechaza_antes_de_red` MUERE (`DID NOT RAISE
SpapiNoPermitida` en `tests/test_spapi_client.py:85`).

Confirmación de verbo GET en el camino de ingesta: `SpapiClient.get`
hace `client.get(url, ...)` (`client.py:513`). No hay `.put` / `.patch` /
`.delete` en `app/spapi/`. Fees conserva su propio `_request` POST a
`feesEstimate` con allowlist propia (A.1, sin cambio de comportamiento).
Fotos interpola ASIN ya validado (`[A-Z0-9]{10}`) en una URL literal
Catalog Items y baja la miniatura de `m.media-amazon.com` /
`images-na.ssl-images-amazon.com` con `follow_redirects=False`. Ninguno
escribe listings ni precios.

El literal del contrato A.R («ningún verbo que no sea GET») no se cumple
al pie de la letra: existen POST LWA y POST `feesEstimate`. La intención
(cero escrituras a Amazon) sí. Es el mismo criterio que Ads
`recommend_bids`.

Hueco de tests, no de código: ningún test de `app/spapi` afirma
`request.method == "GET"` en el camino `get()`. Mutante M15
(`client.get` → `client.post` en `client.py:513`) SOBREvive a
`test_allowlist_*` y a `test_pide_secciones_en_cada_peticion`. El único
assert de método en `tests/test_spapi_client.py` es POST de
`post_fees` (línea 104). La propiedad vive en el código, no está pineada.

---

## Eje 2 — Un solo refrescador LWA (D5)

**Veredicto del eje: cumple.** Un POST a `api.amazon.com/auth/o2/token`
desde SP-API: `SpapiClient._acceso` (`client.py:451-458`).
`cliente_compartido` memoiza por credenciales (`client.py:623-649`).

Quién pega a LWA en `app/` (grep `auth/o2/token`):

| Módulo | Qué es |
|---|---|
| `app/spapi/client.py:56` | el refrescador único SP-API |
| `app/ads/client.py:48` | LWA de **Amazon Ads** (otra app, otro token). Fuera de D5 |
| `app/estimacion_fees.py:42,545` | constante + validador legado; el POST real sale de `self._spapi._acceso()` (`estimacion_fees.py:578-583`) |
| `app/publicacion_fotos.py:90-100` | `_acceso` delega a `SpapiClient._acceso()` |

`ProductFeesClient` en producción (red y reloj reales) toma
`cliente_compartido(...)`. `FotosPublicacion` igual. Test
`test_un_solo_refresh_con_dos_modulos_en_mismo_proceso`
(`tests/test_spapi_client.py:303`): fees + fotos, un solo POST LWA.

Los `test_allowlist_*` de `tests/test_estimacion_fees.py` y
`tests/test_publicacion_fotos.py` siguen en la suite y pasaron en la
corrida de este SHA (incluido el bloque de 292). No se tocó el
comportamiento de fees/fotos: fees sigue reintentando 429/5xx en su
capa; fotos sigue con throttle 0.6 s, cache y 404→None.

Ads conserva su refrescador. Es otra credencial (`AdsCredentials` vs
`amazon_credentials.json`). D5 no lo unifica, y no debe: MeLi/Ads vs
SP-API es el precedente de CONTEXTO (dos refrescadores compitiendo era
el caso **dentro** de un mismo API).

---

## Eje 3 — Append-only + GRANTs por columna

**Veredicto del eje: cumple.** No hay otra asimetría del tipo
0033→0034 (columna nueva con UPDATE por columna y GRANT faltante).

Tablas de observación (0030/0032/0035):

- Trigger `BEFORE UPDATE OR DELETE` → `prohibir_mutacion()` (también
  TRUNCATE por statement).
- `GRANT SELECT` a los 4 roles; `GRANT INSERT` solo a `app_ingest`.
  Cero `GRANT UPDATE`/`DELETE`.
- `ALTER DEFAULT PRIVILEGES` de 0001 solo da SELECT a tablas nuevas
  (`0001_initial.sql:1521-1522`). INSERT no se hereda por accidente.

`ingest_run` (excepción mutable deliberada, 0001):

```
GRANT UPDATE (finished_at, rows_written, rows_skipped, skip_reason, ok)
    ON ingest_run TO app_ingest;          -- 0001:1471
GRANT UPDATE (llamadas) ...               -- 0034:18 (hotfix del bug 145/147)
```

Lo que las 4 ingestas realmente UPDATE-an:

| Fuente | Columnas del sello |
|---|---|
| orders | finished_at, rows_written, rows_skipped, skip_reason, ok |
| pricing / listings / inventario | las cinco + `llamadas` |

`platform` (0036): se fija en el INSERT (GRANT INSERT a nivel **tabla**
de 0001, que sí cubre columnas nuevas). UPDATE de `platform` está
cerrado a propósito. El DO de 0036 truena si `app_ingest` tiene UPDATE.
Test `test_migracion_0036_platform_y_grants` hace `SET ROLE app_ingest`,
INSERT ok, UPDATE → `InsufficientPrivilege`.

0037: índice `(source, platform, id DESC)` + candado
`has_column_privilege(..., 'INSERT')`. El candado vive en 0037 y no en
0036 porque 0036 ya estaba mergeada y no es re-runnable — correcto.

Cacería de asimetrías 0033-like en 0030–0037: la única columna de
`ingest_run` que se UPDATE-a y nació después de 0001 es `llamadas`, y
tiene su GRANT en 0034 (ya en producción). `platform` no se UPDATE-a.
Ninguna observación nueva se UPDATE-a.

Mutantes:

- Quitar `GRANT UPDATE (llamadas)` de 0034 → la migración misma RAISE
  (`0034: app_ingest sigue sin UPDATE en ingest_run.llamadas`). MUERE.
- Añadir `GRANT UPDATE (platform)` en 0036 → RAISE
  `app_ingest NO debe poder reatribuir plataforma`. MUERE.
- `GRANT INSERT, UPDATE, DELETE` en `spapi_listing_estado_observation`
  → `test_migracion_clave_append_only_y_grants` MUERE (línea 224:
  `assert not True`).

El SELECT suelto que dejaba INTRANS (bloqueante de A.5) está cerrado:
`evaluar_alertas` lee dentro de `with conn.transaction()` y notifica
fuera (`salud.py:150-163`). Mutante M2 (SELECT suelto):
`test_evaluar_alertas_deja_conexion_idle` ve `INTRANS` en vez de `IDLE`;
`test_dos_ingestas_persisten_desde_otra_conexion` ve 1 fila en vez de 2.

---

## Eje 4 — Dinero

**Veredicto del eje: cumple.** `(valor, moneda)` con dominio
`money_amount = NUMERIC(14,4)` (`0001_initial.sql:45`) + enum
`currency`. Cero `float` en importes. Cero conversión FX. El
`quantize(0.0001)` recorta al grano del dominio, no mueve de moneda.

Checks de pares (NULL a ambos lados o a ninguno):

- orders: `spapi_order_total_con_moneda` (0030:32-33)
- pricing: `spapi_price_propio_con_moneda`, `_buybox_con_moneda`,
  `_minimo_con_moneda` (0032:36-41)

Parser: `Decimal(str(...))`; moneda fuera de `{MXN, USD}` o monto
ilegítimo → `(None, None)` (regla 3). Pricing: precio sin moneda = fila
no escrita (`PrecioOmitido` / skip `precio_sin_moneda`). Orders: la fila
se escribe igual porque la identidad es orden+tiempo; el total queda
NULL+NULL, no un número huérfano.

Mutantes:

- Inventar `MXN` cuando no hay moneda (`pricing.py _dinero`) →
  `test_precio_sin_moneda_se_omite` MUERE.
- Quitar `CONSTRAINT spapi_price_propio_con_moneda` →
  `test_migracion_clave_append_only_y_grants` MUERE (`DID NOT RAISE
  CheckViolation` en línea 402).

`CuboTasa` usa `float` para tokens de rate-limit, no para dinero.

---

## Eje 5 — Redacción

**Veredicto del eje: cumple.** Credenciales LWA y el `access_token`
pasan por `register_secret` al cargar y al refrescar
(`client.py:429-430`, `470`). `SpapiError.__init__` scrubbea el mensaje
(`client.py:107`). Las 4 ingestas sellan
`prefijo_motivo(exc): scrub(str(exc))`. Logs de sello abierto y de
`evaluar_alertas` pasan por `scrub`. Telegram: `aviso_spapi_fallo` hace
`scrub(motivo)` otra vez (`notifica.py:620`) y el sender es fail-silent.

Test `test_motivo_con_secreto_no_sale_por_telegram`: el secreto no
aparece en el mensaje; sí `***REDACTED***`.

`skip_reason` de éxito es un conteo de motivos internos
(`3x duplicada`, `1x precio_sin_moneda`): no hay secretos ahí.
`/salud` pinta `skip_reason` con Jinja (autoescape on).

Mutante M5 (sello de listings **sin** `scrub(str(exc))`): SOBREVIVE a
`test_listings_lwa_caido_sella_lwa_fallido` y a
`test_motivo_con_secreto_no_sale_por_telegram`. Motivo: (1) `SpapiError`
ya scrubbea al construirse; (2) el test de Telegram inyecta el secreto
ya escrito en `skip_reason` y vuelve a scrubbear en el aviso; (3) el
mensaje de `SpapiRechazoLWA` es `status=N`, sin token. Defensa en
profundidad del sello no está pineada. No hay camino demostrado de fuga:
los valores registrados (client_id, secret, refresh, access_token) no
salen en esos mensajes.

---

## Eje 6 — Sin PII

**Veredicto del eje: cumple.** Orders no persiste dirección ni
destinatario.

- Params: `includedData=FULFILLMENT,PROCEEDS` y jamás BUYER/RECIPIENT
  (`orders.py:285-286`). Tests
  `test_pide_secciones_en_cada_peticion` y
  `test_jamas_pide_buyer_ni_recipient` lo pinea contra los params reales
  de `parametros_ventana`, no contra un dict armado a mano.
- Tabla `spapi_order_observation` (0030+0031): sin columnas de
  comprador, email, teléfono, dirección, nombre. `parsear_orden` solo
  extrae identidad, tiempos, estados, canal, total, ítems.
- `sanear(..., "orders")` corre sobre el JSON **antes** de parsear
  (`orders.py:346`): drop de `buyer*`, `shippingaddress`/`shipaddress`/
  `recipient`, y de las claves de dirección.

Mutante M4 (`includedData` += `BUYER`): ambos tests MUEREN
(`'BUYER' not in 'FULFILLMENT,PROCEEDS,BUYER'`). Mutante M13 (sanear
sin `startswith("buyer")`):
`test_scrub_en_errores_y_rate_limit_y_paginacion` MUERE (`BuyerEmail`
sobrevive).

`sanear` no trata `recipientName` ni `Name` como PII. No hay camino de
persistencia: esas claves no se insertan. Defensa en profundidad, no
fuga.

D2 (no pisa `listing`): `grep UPDATE listing|INSERT INTO listing` en
`app/spapi/` = 0. `test_listing_intacto_tras_ambas_ingestas` compara 7
columnas (id, product_id, platform, external_id, seller_sku,
listing_price, price_currency) antes/después de listings+inventario.

---

## A.5 — salud, flanco, transacción

Zona con más historia (PR #246 mergeó 5 bloqueantes; #247/#248 los
cierran). Verificado contra el código de `958c00f`:

1. `evaluar_alertas` ya no deja INTRANS (M2 muere, ver eje 3).
2. Flanco: inmediata LWA/429 una vez por racha de esa clase; resto
   espera la segunda fallida; cambio de clase re-alerta; tercera
   seguida no. Tests `test_429_tres_seguidos_una_sola_alerta`,
   `test_cambio_de_clase_dentro_de_racha_re_alerta`,
   `test_una_sola_contrato_sin_historia_cero_mensajes`. Mutante M8
   (inmediata siempre alerta) → 3 mensajes en vez de 1. MUERE.
3. Historial solo selladas (`ok IS NOT NULL`) y ancladas
   (`id <= run_id`). Mutante M3 (sin filtro ok) → huerfana enmascara
   la racha, 0 mensajes. MUERE.
4. Cableado: las 4 ingestas llaman `evaluar_alertas` **después** del
   `with conn.transaction()` del sello (orders.py:504-506 y equivalentes
   en pricing/listings/inventario).
5. `/salud` degrada si `platform` no existe (`_spapi_de` try/except,
   `api_dashboard.py:850-865`); la plantilla omite la sección si
   `datos.spapi` es falsy. Ads no se importa en AST de las ingestas;
   el residual de runtime `app.ads.config` está pineado
   (`test_aislamiento_grafo_runtime_solo_config_inerte`).

---

## Cron y orden de deploy (A.6 va a ejecutar esto)

Propuesta en `docs/DEPLOY.md:388-428`, marcada **PROPUESTA — NO
instalada**. Flags del CLI verificados: `ingest {spapi_pricing,spapi_orders,spapi_listings,spapi_inventario} --platform {amazon_mx,amazon_us}`
existen y `--platform` es required (`choices` de `MERCADOS`). El
dispatcher de `app/cli.py:194-205` encaja. `python` (no `python3`)
coincide con los crons de Ads ya instalados. Cero `%` sin escapar
(Vixie).

Orden de deploy escrito (`0035` → `0036` → `0037` → rebuild): correcto.

- Sin `0036`, las 4 ingestas truena al INSERT `platform` y `/salud`
  degrada el bloque (no tumba la pantalla).
- Sin `0035`, listings/inventario truena al INSERT en tablas
  inexistentes; orders/pricing siguen.
- `0037` no bloquea el rebuild; sin él `/salud` hace seq scans. El
  índice calza las 3 consultas (`source, platform, id DESC`).
- Patrón de apply: `docker exec -i orbit-db-1 psql ... -v ON_ERROR_STOP=1 -1`
  (`DEPLOY.md` «Aplicar migraciones»). `-1` envuelve 0037
  (`CREATE INDEX` no CONCURRENTLY, válido dentro de transacción).
  Lock de escritura en `ingest_run` durante el índice: aceptable en
  deploy, la tabla aún no tiene el volumen de D7-sin-purga a años.

**No instalar el bloque cron tal cual.** Tres defectos del texto, no
del código:

1. **No está en serie.** Ocho líneas independientes de Vixie. El
   párrafo dice «en serie a propósito» (limitadores por proceso; dos
   procesos se canibalizan la quota). El artefacto no encadena. Si
   pricing MX se pasa de las 05:25, US arranca encima.
2. **Colchón de pricing MX: ~2 min.** Presupuesto declarado 342 ASIN ×
   2 llamadas / 0.5 rps ≈ 22.8 min en un slot de 25 min (05:00→05:25).
   Un 429 con `Retry-After` de 60 s (tope del cliente) o un universo
   +10% pisa el job siguiente. Escenario: MX sigue a las 05:25 → US
   pricing comparte quota → 429 absorbido una vez y luego
   `ok=false` / alerta inmediata `http_429` en US, con MX todavía
   corriendo.
3. **Sin redirect a `logs/`.** Todos los crons de Orbit ya instalados
   hacen `>> /mnt/data/appdata/orbit/logs/....log 2>&1`. Estas ocho
   líneas mandan stdout a mail de cron (o lo pierden). Además, el
   instalador idempotente de Ads (`DEPLOY.md:496`) filtra
   `grep -v "app.cli ingest"`: re-aplicar el bloque ORBIT 03 **borra**
   las líneas SP-API en silencio.

A.6: un wrapper (`flock` + las 8 corridas en un proceso, cada una con
`|| true` para no tumbar las siguientes, logs en `logs/spapi-*.log`) y
meterlo al `ORBIT_BLOCK` o a un instalador que no se pise con el de
Ads. El resto de la ventana (acabar antes de `ingest:structure` 06:45)
sigue siendo el lugar correcto.

Orders diario con burst 20 y 0.0056 rps cabe en 5 min mientras sean
≤20 páginas (la corrida incremental ya vista en A.6 parcial). Un
`--desde` de backfill en el cron no: no está en la propuesta.

---

## Residuales declarados

Ambos aceptables; no se re-litigan.

1. **Taxonomía `red` vs `contrato`.** `prefijo_motivo` clasifica
   `httpx.HTTPError` como `red`. Una corrida que muere por excepción
   envuelta (`IngestaPricingError`/`IngestaListingsError` de umbral
   sin `status=NNN` en el mensaje) sella `contrato`. Mutante M11
   (HTTPError → contrato) SÍ lo caza el test de inventario. El hueco
   es el wrap, no el clasificador. Diferido, documentado en E/A.5.
2. **`import app.spapi.salud` arrastra `app.ads.config`.** Pineado:
   `test_aislamiento_grafo_runtime_solo_config_inerte` exige exactamente
   `["app.ads", "app.ads.config"]` en un subproceso limpio. Módulo
   inerte, cero IO. El ciclo de Ads es otro proceso, otras credenciales.

---

## Mutación

Baseline (este SHA, Postgres local `orbit:orbit@localhost:5432`):

```
$ PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_spapi_client.py \
    tests/test_spapi_salud.py tests/test_spapi_orders.py \
    tests/test_spapi_pricing.py tests/test_spapi_listings.py \
    tests/test_spapi_inventario.py tests/test_estimacion_fees.py \
    tests/test_publicacion_fotos.py tests/test_notifica.py \
    tests/test_sonda_spapi.py
292 passed, 1 warning in 9.67s
```

Cada mutante se restauró con `git checkout --` antes del siguiente.
`git status` de tracked vacío tras el lote. HEAD sigue `958c00f`.

| ID | Mutación | Resultado | Qué lo caza (o por qué sobrevive) |
|---|---|---|---|
| M1 | Quitar check `%2f` en `validar_get` | MUERE | `test_allowlist_rechaza_antes_de_red:85` |
| M2 | SELECT suelto en `evaluar_alertas` | MUERE | `deja_conexion_idle` (INTRANS) + persistencia desde otra conexión (1≠2) |
| M3 | Historial sin `ok IS NOT NULL` | MUERE | `test_huerfana_abierta_no_enmascara_racha` (0 mensajes) |
| M4 | `includedData` += BUYER | MUERE | `test_jamas_pide_buyer_ni_recipient` / `test_pide_secciones_*` |
| M5 | Sello listings sin `scrub(str(exc))` | **SOBREVIVE** | `SpapiError` ya scrubbea; Telegram vuelve a scrubbear; LWA no pone el token en el mensaje |
| M6 | Quitar `GRANT UPDATE (llamadas)` | MUERE | DO de 0034 RAISE al aplicar |
| M7 | Quitar check `?`/`#` en `validar_get` | **SOBREVIVE** | el path deja de calzar `RUTAS_FIJAS`/plantillas y igual DENY (check redundante hoy) |
| M8 | Inmediata LWA/429 siempre alerta | MUERE | tres 429 → 3 mensajes, no 1 |
| M9 | Precio sin moneda → inventar MXN | MUERE | `test_precio_sin_moneda_se_omite` |
| M10 | Historial sin `id <= run_id` | MUERE | ancla + huerfana + flanco (placeholders / 0 mensajes) |
| M11 | `HTTPError` → `contrato` | MUERE | `test_inventario_red_sella_red` + taxonomía |
| M12 | `GRANT UPDATE (platform)` | MUERE | DO de 0036 RAISE |
| M13 | sanear sin `buyer*` | MUERE | `BuyerEmail` sobrevive en `sanear` |
| M14 | Quitar DO candado INSERT de 0037 | **SOBREVIVE** | el privilegio lo da 0001 (INSERT de tabla); el test afirma el privilegio, no que 0037 lo compruebe |
| M15 | `SpapiClient.get` hace POST | **SOBREVIVE** | ningún test de ingesta/cliente afirma `request.method == "GET"` |
| M16 | `UPDATE listing SET listing_price = listing_price` | **SOBREVIVE** | snapshot D2 compara valores; no-op no los cambia |
| M16b | `UPDATE listing SET listing_price = 0` | MUERE | `listing_precio_positivo` (CHECK), antes del snapshot |
| M17 | Quitar CHECK moneda propio en 0032 | MUERE | `test_migracion_clave_append_only_y_grants:402` |
| M18 | GRANT UPDATE/DELETE en observación 0035 | MUERE | `has_table_privilege(..., 'UPDATE')` debía ser false |

Sobrevivientes que importan para A.6 / higiene, no para el veredicto:

- **M15**: pinear `request.method == "GET"` en el mock de al menos una
  ingesta. Hoy el eje 1 vive del código, no del test.
- **M5**: un test de `ejecutar_ingesta` con secreto en la excepción y
  assert de `skip_reason` en DB (no solo Telegram) cerraría la defensa
  del sello.
- **M14**: el candado de 0037 es futuro-proofing; si alguien lo borra,
  0001 sigue cubriendo INSERT. Aceptable.

---

## Hallazgos

Ninguno bloqueante. Los que hay, con escenario:

### H1 — propuesta de cron no es instalable tal cual
- **Archivo:** `docs/DEPLOY.md:398-407` (y el instalador Ads en `:496`)
- **Severidad:** media (ops). No bloquea el SHA de código.
- **Escenario:** A.6 pega las 8 líneas. Día con 429 en pricing MX
  (~23 min + 60 s) → a las 05:25 arranca pricing US sobre el mismo
  proceso-quota. US sella `http_429` / alerta inmediata. Más adelante,
  alguien re-corre el instalador ORBIT 03 → `grep -v "app.cli ingest"`
  borra las 8 líneas y las ingestas SP-API desaparecen del crontab sin
  error. Stdout no queda en `logs/`.
- **Qué hacer en A.6:** wrapper en serie + `flock` + logs + meterlo
  donde el instalador de Ads no lo pise. No es un fix de `958c00f`.

### H2 — tests no pinean el verbo GET
- **Archivo:** `app/spapi/client.py:513` (código correcto) /
  `tests/test_spapi_client.py` (sin assert GET)
- **Severidad:** baja (poder discriminante).
- **Escenario:** un cambio de `client.get(...)` a `client.post(...)` en
  `SpapiClient.get` pasa la suite focal del cliente. En producción sería
  un POST a rutas de lectura; Amazon lo rechazaría, no escribiría. No
  es una escritura silenciosa, es un test ciego al verbo.

No se reabren estilo, nits de gusto, ni los dos residuales declarados.

---

## Cierre

`958c00f` cumple los seis ejes del contrato A.R. Los cinco bloqueantes
de A.5 que sobrevivieron al PR #246 están cerrados en este SHA (INTRANS,
flanco, candado INSERT como `app_ingest`, historial de selladas con
ancla, cableado de las 4 ingestas). No hay asimetría GRANT 0033-like
pendiente. A.6 puede aplicar `0035` → `0036` → `0037` → rebuild en ese
orden, y **no** debe copiar el bloque cron de `DEPLOY.md` sin wrapper,
logs y convivencia con el instalador de Ads.
