# Traspaso 1 — Accesos e infraestructura

> **Proyecto:** goncloud-MCP-2 (motores de Amazon Ads) — **CANCELADO el 2026-08-21**.
> Este documento existe para que un sistema nuevo no empiece de cero.
> Todo lo de aquí está **verificado contra el servidor**, no recordado.
>
> **Ningún valor de credencial aparece en este documento, a propósito.** Sólo qué existe,
> dónde vive, qué lo consume y cómo se rota. Un documento con secretos adentro es un
> problema nuevo, y además queda obsoleto en cuanto rotás algo.

---

## 0. APAGADO SEGURO — leer antes que nada

**Hay motores en `live` escribiendo a la API de Amazon Ads en este momento.** No están
todos en shadow.

En `live` confirmado (tabla `settings` de `competitive.db`, 206 flags):

| motor | estado |
|---|---|
| `adaptive_motor_enabled` | `live`, `canary_pct=100`, con apply_live en keyword, placement, campaign_budget, asin_target y segment_target |
| `hygiene_motor_enabled` | `live` |
| `hygiene_motor_sb_enabled` + `_apply_live` | `live` vía transporte `mcp` |
| `bid_motor_sb_enabled` + `_apply_live` | `live` vía `mcp` |
| `capital_motor_sb_enabled` + `_apply_live` | `live` |
| `capital_motor_apply_live` | `live`, `aggressiveness=aggressive` |
| `sku_health_motor_mode` | `live` |
| los 5 motores de MeLi | `live` |
| `dayparting_apply_mode`, `ads_daily_cap_breaker_enabled`, `ads_auto_abort_mode` | `live` |

En `shadow` (inertes): `bid_motor_enabled`, `capital_motor_enabled`,
`cannibalization_motor_enabled`, `adaptive_motor_meli_enabled`,
`adaptive_motor_shopify_enabled`, `realtime_reflex_enabled`, `repricing_v3_mode`,
`hour_dayparting_mode`, `ads_capital_v2_enabled`.

### La trampa: el cron no es el scheduler

`crontab -l` muestra 5 jobs. El scheduling real son **147 jobs de APScheduler registrados
dentro del contenedor** al arrancar la app (`app/main.py`, líneas 10673–13118). Conteo
confirmado por dos vías independientes: parseo AST del código (147) y líneas `Added job`
en el log (147).

**Si apagás el cron y los timers pero dejás el contenedor arriba, 147 tareas siguen
corriendo, y varias escriben a Amazon en modo live.**

### Secuencia correcta

```
0. Copiar competitive.db FUERA de la rotación de respaldos (sólo guarda 3).
1. Poner los flags *_enabled y *_apply_live en 'off' (tabla settings o la UI).
2. systemctl disable --now competitive-intel.service
3. docker compose stop   (en /mnt/data/appdata/competitive)
4. Recién entonces limpiar el cron.
```

El orden importa. Al revés, el paso 4 no apaga nada.

### Qué NO se apaga

`bridge` y `accounting` son sistemas **independientes** y siguen vivos:
`bridge.events` escribió a las 23:02 y `accounting.ledger_events` a las 22:45 del 21-ago.
Ninguno depende de que ads siga prendido. **Los libros del negocio corren por ahí.**

Y ninguno de los 7 dominios publicados (`cloud`, `dolibarr`, `inflow`, `mapper`,
`notion`, `paperclip`, `pw` — todos en `.goncloud.cc`) apunta al 8055. Apagar ads no
rompe ningún dominio.

---

## 1. Credenciales — qué existe y dónde

### 1.1 Archivos JSON en el servidor

Todos fuera de git; el contenedor los ve por bind-mount de sólo lectura.

| ruta | servicio | claves que contiene (nombres) |
|---|---|---|
| `/mnt/data/appdata/accounting/config/amazon_credentials.json` (0600) | Amazon SP-API | `refresh_token`, `lwa_app_id`, `lwa_client_secret`, `aws_access_key`, `aws_secret_key` |
| `/mnt/data/appdata/accounting/config/amazon_ads_config.json` (0664) | Amazon Ads API (app LWA) | `client_id`, `client_secret`, `redirect_uri` |
| `/mnt/data/appdata/accounting/data/.amazon_ads_tokens.json` (0640) | tokens vivos de Amazon Ads | `access_token`, `refresh_token`, `token_type`, `expires_in`, `obtained_at`, `expires_at` |
| `/mnt/data/appdata/accounting/config/meli_tokens.json` (0600) | MercadoLibre (lado accounting) | `access_token`, `refresh_token`, `scope`, `user_id`, `expires_at`, … |
| `/mnt/data/appdata/bridge/data/.meli_tokens.json` (0640) | MercadoLibre (lado bridge) — **este es el que consume la app de ads** vía `ML_TOKENS` | mismas claves |

> **Gotcha real:** un cron cada 30 min hace `chmod 640` sobre `.meli_tokens.json` porque el
> refrescador lo reescribe en 0600 y el contenedor (UID 10001) deja de poder leerlo.
> Hay **dos refrescadores independientes** del token de MeLi escribiendo sobre archivos
> distintos. Un sistema nuevo debe tener **uno solo**.

`aws_access_key` / `aws_secret_key` en `amazon_credentials.json` son el par SigV4 legacy
de SP-API. Amazon ya no lo exige desde 2023; el archivo lo conserva.

### 1.2 Entorno central

**`/etc/goncloud/accounting.env`** (0640, root:gon). Hay un symlink desde
`/mnt/data/appdata/accounting/config/accounting.env`.

Define (sólo nombres): `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`,
`MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`TELEGRAM_SALES_BOT_TOKEN`.

Lo consume el crontab de `gon` con `source` antes de `sync_meli_orders.py`,
`sync_amazon.py` y `heartbeat.py`.

> Hay **4 backups viejos de este archivo** en el mismo directorio (`*.bak.*`,
> `*.before_op2_*`). Son copias de secretos que un sistema nuevo debería borrar.

### 1.3 Otros archivos de entorno (rutas confirmadas, contenido no leído)

```
/mnt/data/appdata/competitive/.env        el .env del docker-compose de ads
/mnt/data/appdata/bridge/.env             (0600) + .env.before_c4_bak
/mnt/data/appdata/bridge/.env.meli        MELI_CLIENT_ID / MELI_CLIENT_SECRET
/mnt/data/appdata/bridge/data/.env.odoo   (0600)
/mnt/data/appdata/accounting/config/accounting.env.example   plantilla sin valores
```

Los nombres de las variables del `.env` de competitive no hay que adivinarlos: están
declarados en `docker-compose.yml` y verificados contra el `os.environ` real del
contenedor (§1.4).

### 1.4 Variables del contenedor `competitive-intel` (verificadas en runtime)

**Rutas de datos:** `COMP_DB`, `BRIDGE_DB`, `ACCOUNTING_DB`, `ML_TOKENS`, `PORT`,
`GONCLOUD_GIT_SHA`, `BRIDGE_API_URL`.

**API keys de terceros:**

| variable | servicio |
|---|---|
| `KEEPA_KEY` | Keepa (histórico de precios/BSR de Amazon) |
| `APIFY_TOKEN` | Apify (scraping de reviews y discover) |
| `ANTHROPIC_API_KEY` | Claude (semantic_judge, motor_decision_auditor, reverse_asin_research) |
| `DHL_API_KEY` | DHL Unified Tracking (inerte si vacío) |

**Secretos propios de la app (autenticación de su propia API):**

| variable | alcance |
|---|---|
| `API_AUTH_SECRET` | token maestro de escritura |
| `API_READ_SECRET` | read-only (sólo GET), para agentes externos |
| `API_AGENT_WRITE_SECRET` | scoped: **sólo** `/api/agent/meli/item-title` |
| `EXTERNAL_REPORT_TOKEN` | read-only scoped a `/api/external/*` |
| `EXTERNAL_WRITE_TOKEN` | escritura scoped a `/api/external-write/*` (RFQs draft en Odoo) |
| `DASHBOARD_PASSWORD` | login del dashboard (emite JWT de 30 días) |
| `JWT_SECRET` | firma de esos JWT |
| `ML_MODEL_HMAC_KEY` | integridad de los modelos ML serializados |
| `GONCLOUD_API_SECRET` | autoriza escrituras de SKU mapping contra bridge-api |

**Alertas:** `TELEGRAM_SALES_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (fail-silent si faltan).

> `AMAZON_ADS_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` **no** están en el contenedor.
> El código las lee de env y cae al fallback de archivos y de base de datos (§1.6).

### 1.5 Los otros dos contenedores

**`ams-worker`** (Amazon Marketing Stream — la única fuente intra-día):
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `SQS_QUEUE_URL`, `COMP_DB`.
Sin las tres primeras hace `exit 78` al arrancar. La suscripción AMS se crea con
`tools/ams_subscribe.py`.

**`bridge-api`**: expone el 8099; le pasa `GONCLOUD_API_SECRET` a competitive.

### 1.6 Credenciales guardadas como filas en bases de datos

- `accounting.db` → tabla **`amazon_ads_tokens`**: tokens vivos de Amazon Ads.
- `bridge.db` → tabla **`bridge_settings`**.
- `competitive.db` → tabla **`settings`** (370 filas; además de flags, guarda credenciales).

Cadena de fallback de Amazon Ads: **env → archivo JSON → fila en base de datos.** Si vas
a migrar, tenés que mirar los tres lugares o vas a creer que falta una credencial que sí
está.

### 1.7 Accesos de infraestructura

**SSH:** alias `goncloud` configurado en la máquina local.

**GitHub — corrección importante:** el servidor tiene **tres deploy keys distintas con
permisos distintos**, verificado con `git push --dry-run`:

| repo | llave | permiso |
|---|---|---|
| `gon0801/goncloud-MCP-2` (ads) | `/root/.ssh/mcp2_deploy` | **ESCRITURA** ⚠️ |
| `gon0801/goncloud-bridge-in-out` | `/root/.ssh/bridge_deploy` | read-only |
| `gon0801/goncloud-accounting` | llave por defecto de root | read-only |

> Durante la sesión afirmé que "la llave del servidor es read-only". Es cierto para
> **accounting**, que fue la que probé. **Para el repo de ads NO lo es.** Esa llave hay que
> rotarla o degradarla al cerrar el proyecto.

---

## 2. Qué rotar al cerrar — en orden de urgencia

1. **`API_AUTH_SECRET`** — se filtró en un comando fallido durante la sesión del
   2026-08-21. Mientras el contenedor siga arriba, ese token da escritura completa a la API.
2. **Deploy key `/root/.ssh/mcp2_deploy`** — tiene escritura sobre el repo de ads.
3. **Token del túnel `cloudflared`** — está **inline en `ExecStart` del unit de systemd** y
   por lo tanto visible en `ps aux` para cualquier usuario del servidor. No existe
   `/etc/cloudflared/`: toda la config del túnel vive en ese argumento. Se rota desde
   Cloudflare Zero Trust (borrar y recrear el túnel) y debería guardarse en un archivo 0600
   referenciado por `EnvironmentFile`.
4. Los **4 backups de `accounting.env`** con secretos viejos: borrar.
5. Los tokens de Amazon Ads y MeLi se auto-refrescan; si el sistema nuevo usa otra app LWA,
   hay que re-hacer el OAuth de cero.

> **No hay scripts de rotación en ningún repo.** Todo es manual y no está documentado en
> ningún lado salvo aquí.

---

## 3. Infraestructura

**Servidor:** Ubuntu 24.04.3 LTS, 301 GB de disco al 45%, 72 días de uptime.
28 contenedores Docker corriendo; **sólo 3 son del proyecto de ads.**

**`/mnt/data/appdata`** tiene 20 directorios. El stack de ads son 3 de ellos y pesan 8.5 GB.

### 3.1 Bases de datos — y la trampa que costó un hallazgo falso

16 archivos `.db`: **4 reales grandes, 5 placeholders vacíos.**

| ruta | qué es |
|---|---|
| `/data/competitive.db` (en el contenedor) | **REAL** — 231 tablas |
| `/mnt/data/appdata/accounting/data/accounting.db` | **REAL** — 99 MB, `ledger_events` 12,875 filas |
| `/mnt/data/appdata/bridge/data/bridge.db` | **REAL** — ⚠️ WAL de 2.3 GB sin checkpoint |
| `/data/accounting.db` (dentro del contenedor) | **PLACEHOLDER de 0 bytes** |
| `/data/bridge.db` (dentro del contenedor) | **PLACEHOLDER, 0 tablas** |

> Consultar los placeholders devuelve resultados vacíos que **parecen válidos**. Esto ya
> produjo un hallazgo falso completo durante la auditoría (se reportó que el tipo de cambio
> estaba roto porque se consultó un archivo de 0 bytes). La variable de entorno
> `ACCOUNTING_DB` apunta a la ruta buena; **verificar la ruta que usa el código, no la que
> uno supone.**

### 3.2 Respaldos

Tres capas: rotación local de competitive (**sólo 3, retención corta**), respaldos con
fecha en accounting, y `restic` con retención larga (OneDrive y B2 — no se pudo verificar
su estado real).

> **Antes de apagar, sacar una copia de `competitive.db` fuera de la rotación de 3.**
>
> Y para copiar bases en WAL: `cp` **no sirve** —deja fuera el archivo `-wal`— y produce un
> respaldo incompleto que abre sin error. Usar la API `.backup()` de SQLite y comparar
> `COUNT(*)` contra el original antes de confiar.

### 3.3 Piezas que corren pero ya están muertas

- **`goncloud-ads-shadow`**: contenedor arriba hace 7 semanas, **puerto 8056 abierto al
  mundo**, y su tabla `decisions` no escribe desde el **2026-06-27**. Consume RAM y expone
  un puerto sin hacer nada. Apagar sin pensarlo.
- **`decision_audit`** en competitive.db: 117,827 filas, congelada desde el **2026-05-19**.
  Cualquier análisis que la use está leyendo historia muerta y **va a parecer válido**.

### 3.4 Exposición de red

Escuchando en `0.0.0.0`: **8055** (app de ads), **8056** (el muerto), **8099** (bridge-api),
**8050** (dashboard de contabilidad, proceso Python suelto, no en Docker), 80/81/443
(nginx-proxy-manager) y 22.

Sólo en loopback: 8080 nextcloud, 8081 vaultwarden, 8082 odoo, 9080/9443 appflowy.

No se verificaron reglas de firewall.

---

## 4. Integraciones externas — y sus trampas ya pagadas

Esta sección es dinero: son errores que ya se pagaron una vez.

### Amazon Ads API
- **Reporting v3 asíncrono** (SP/SB/SD): es el pipeline de métricas de todos los motores.
- **Escritura (campaign management v3)**: lo más frágil del stack.
- **Los endpoints v4 unificados rechazan Bearer LWA.** El workaround en uso es el MCP público.

### Amazon Marketing Stream (AMS) + SQS
La **única** fuente intra-día. Long-poll de una cola SQS que Amazon publica.

### Amazon SP-API
- **Orders**: v0 deprecado, migración a `2026-01-01`, con **dos bugs de paginación** ya documentados.
- **Finances `2024-06-19`**: la fuente de fees, y **la que más dinero costó equivocarse**.
  La migración de 2026-05-09 sólo procesaba Shipment/Refund/ServiceFee y perdía el ISR.
- **Reports** (Brand Analytics, Buy Box, listings): flujo de 4 pasos con timeouts largos.

### MercadoLibre
- Token rotativo con **dos refrescadores independientes** (ver §1.1).
- **MeLi retiró la ruta sin `site`**: hay que usar `/advertising/MLM/advertisers/...`.
- **La escritura de Ads está bloqueada a nivel cuenta** → MeLi Ads es *proposal-only*.

### Odoo 17
Dos protocolos distintos, credenciales cacheadas al arranque, y **la trampa del IVA doble**
si no se mandan `tax_ids`.

### Otros
Keepa (se paga por token, el CSV es **posicional**), Apify, Telegram, DHL, Frankfurter,
AppFlowy.

**Canales declarados pero inexistentes:** Shopify, Google Ads y Meta son **esqueleto sin
cuenta**. No busques credenciales que no existen.

---

## 5. Lo que se puede reusar tal cual

- **Los tokens y credenciales** de todas las integraciones (si el sistema nuevo usa la
  misma app LWA de Amazon y la misma app de MeLi, no hay que rehacer ningún OAuth).
- **`bridge` y `accounting` completos** — son sistemas separados, vivos y sanos.
- **`currency_rates`** en accounting.db: 204 filas, fresca, cadencia diaria continua desde
  2025-10-31 sin huecos mayores a 5 días.
- **Las 4 tablas de métricas de ads** de competitive.db: 0 duplicados y **cero huecos de
  días**. Es el activo de datos más limpio del proyecto (detalle en el Traspaso 2).
- **El mapa de cadencias reales** de cada API, para dimensionar el sistema nuevo sin
  descubrirlas de nuevo.

---

## 6. Lo que no se pudo verificar

`not_observed != absent`. Esto es lo que quedó abierto:

- Nombres de variables de `/mnt/data/appdata/competitive/.env` y los `.env` de bridge: el
  guard de lectura de secretos los bloqueó y **no se evadió**.
- Contenido de `/root/.ssh/` y su config.
- Si `vaultwarden` tiene copia de estas credenciales (requiere master password).
- Estado real de los repositorios `restic`.
- Qué hace `/opt/goncloud/daily_agent.py` (15.5 KB, corre por `cron.d`).
- Los repos `/mnt/data/appdata/ehv` y `/mnt/data/appdata/goncloud-capital`: git los rechaza
  por *dubious ownership*.
- Reglas de firewall y grupos de seguridad del proveedor.
- Qué rutea exactamente el túnel de cloudflared (su directorio de config está vacío).
- Costos de infraestructura (VPS, B2, OneDrive, Cloudflare, Keepa, Apify, Anthropic).
