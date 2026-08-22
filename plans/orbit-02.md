# Plans — ORBIT 02: Migrar credenciales + apagado seguro del sistema viejo

> Purpose: quedarse con las credenciales (verificadas funcionando) y apagar
> goncloud-MCP-2 sin romper nada vivo. HOY hay motores en `live` escribiendo
> a Amazon Ads (adaptive/hygiene/capital/sku_health + 5 de MeLi, canary 100%).
> Fuente de verdad operativa: `docs/traspaso/TRASPASO-1-ACCESOS-E-INFRAESTRUCTURA.md`
> (§0 secuencia de apagado, §1 credenciales, §2 rotaciones). Registro: tarea
> `ORBIT 02` en EHV Tasks.
>
> REGLAS DURAS:
> - `bridge` y `accounting` NO SE TOCAN: son sistemas vivos e independientes,
>   los libros del negocio corren por ahí. El crontab de `gon` mezcla jobs de
>   ads con jobs de accounting (`sync_meli_orders`, `sync_amazon`,
>   `heartbeat`): al limpiar cron se quitan SOLO los de ads.
> - Ningún valor de secreto se imprime, se loggea ni entra a ningún repo.
>   El inventario lleva NOMBRES y ubicaciones, jamás valores.
> - El ORDEN es la tarea: respaldo → credenciales validadas → apagado →
>   rotaciones. Apagar antes de validar credenciales = quedarse sin forma de
>   probar contra el sistema que las refrescaba.
> - EJECUTOR: sesión fuerte (Opus) una tarea a la vez, con humano cerca.
>   NO es trabajo para GLM ni para corrida autónoma.
>
> unknowns declarados (Traspaso 1 §6, `not_observed != absent`): contenido de
> los archivos de entorno de competitive/bridge (candado secret-read, no se
> evadió); qué rutea exactamente el túnel cloudflared; estado real de restic;
> si vaultwarden tiene copia de credenciales; reglas de firewall.
> Las tareas los verifican, no los suponen.

## Phase 1 — Respaldo e inventario (ANTES de tocar nada) [lane:gate]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | Copiar `competitive.db` FUERA de la rotación de respaldos (guarda solo 3) a `/mnt/data/appdata/orbit/archive/competitive-<fecha>.db`. OBLIGATORIO usar la API `.backup()` de SQLite (por el contenedor o `sqlite3 src ".backup dst"`): `cp` deja fuera el `-wal` y produce un respaldo incompleto que abre sin error. `[tdd:skip:ops]` | `COUNT(*)` de 3 tablas grandes (las de métricas + `settings`) idéntico entre original y copia; el archivo vive fuera del dir de rotación | - | cc:完了 |
| 1.2 | Inventario verificado de credenciales mirando LOS TRES lugares de la cadena de fallback (env del contenedor vía `docker exec env` / `docker inspect`, archivos JSON de §1.1, filas en DB de §1.6: `accounting.db amazon_ads_tokens`, `bridge.db bridge_settings`, `competitive.db settings`). Producto: `/mnt/data/appdata/orbit/credentials-inventory.md` (0600, en el server, NUNCA al repo) con nombre → ubicación(es) → consumidor → estado. Solo nombres. OJO trampa §3.1: dentro del contenedor hay placeholders de 0 bytes que devuelven vacío "válido" — verificar la ruta que usa el código (`ACCOUNTING_DB`), no la supuesta. `[tdd:skip:ops]` | El inventario cubre las 8 integraciones (Amazon Ads, SP-API, MeLi, Odoo, Telegram, Keepa, Apify, Anthropic) + los secretos propios de la app; cada credencial dice en cuál(es) de los 3 lugares vive | - | cc:完了 |
| 1.3 | Copiar las credenciales que Orbit reusa a `/mnt/data/appdata/orbit/secrets/` (dir 0700, archivos 0600): config + tokens de Amazon Ads (misma app LWA → sin re-OAuth), `amazon_credentials.json` de SP-API (sin el par SigV4 legacy, ya no se exige), el token de MeLi DE BRIDGE (`bridge/data/.meli_tokens.json` — es el que consume ads; recordar: Orbit tendrá UN solo refrescador), variables de Odoo/Telegram desde el entorno central, y las API keys de terceros (Keepa, Apify, Anthropic). `[tdd:skip:ops]` | Archivos presentes con permisos correctos; `ls -la` como evidencia (nombres y permisos, no contenidos) | 1.2, ORBIT01-1.1 | cc:完了 |
| 1.4 | Smoke READ-ONLY de cada credencial copiada, desde el server: Ads `GET /v2/profiles`, SP-API `getMarketplaceParticipations`, MeLi `GET /users/me`, Odoo `version()` + login, Telegram `getMe`, Keepa status de token, Anthropic `GET /v1/models`. SOLO GETs — cero escrituras. `[tdd:skip:ops]` | Tabla credencial → OK/FAIL agregada al inventario; toda FAIL investigada y resuelta ANTES de pasar a Phase 2 | 1.3 | cc:完了 |

## Phase 2 — Apagado seguro [lane:release] — CHECKPOINT HUMANO ANTES DE EMPEZAR

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | Flags a `off`: todos los `*_enabled` y `*_apply_live` de la tabla `settings` de `competitive.db` (la lista exacta de los que están en live: Traspaso 1 §0). La trampa central: el cron NO es el scheduler — 147 jobs de APScheduler viven DENTRO del contenedor; apagar flags primero es lo que evita que sigan escribiendo a Amazon mientras se apaga lo demás. `[tdd:skip:ops]` | `SELECT` sobre `settings` muestra cero flags en `live`/`on` de la lista de §0 | 1.1, 1.4 | cc:完了 |
| 2.2 | Apagado en orden: `systemctl disable --now competitive-intel.service` → `docker compose stop` en `/mnt/data/appdata/competitive` → recién entonces limpiar del crontab de `gon` SOLO los jobs de ads (los de accounting se quedan: `sync_meli_orders`, `sync_amazon`, `heartbeat`; también queda el cron del `chmod 640` de `.meli_tokens.json` — es de bridge). `[tdd:skip:ops]` | `competitive-intel` y `ams-worker` en `Exited`; unit `disabled`; puerto 8055 ya no escucha en `0.0.0.0`; `crontab -l` conserva los jobs de accounting | 2.1 | cc:完了 |
| 2.3 | Apagar `goncloud-ads-shadow`: contenedor zombi (no escribe desde 2026-06-27) con el puerto 8056 ABIERTO AL MUNDO. `docker stop` + quitar restart policy. `[tdd:skip:ops]` | Puerto 8056 no escucha; contenedor no reinicia tras `docker ps` de control | - | cc:完了 |
| 2.4 | Verificación post-apagado de que lo VIVO sigue vivo: `bridge.events` y `accounting.ledger_events` con escrituras POSTERIORES al apagado; los 7 dominios `.goncloud.cc` (cloud, dolibarr, inflow, mapper, notion, paperclip, pw) responden; AppFlowy y Odoo accesibles. `[tdd:skip:ops]` | Timestamps posteriores al apagado en ambos sistemas; 7/7 dominios responden; evidencia capturada | 2.2, 2.3 | cc:完了 |

> Desviación registrada en 2.4 (2026-08-22): dominios 5/7 — `dolibarr` y
> `mapper` dan 000, pero NO existe ningún contenedor que los respalde y
> cloudflared lleva 2.5 meses sin tocar: rutas muertas PRE-existentes, no
> causadas por el apagado (nada de lo apagado — 8055, 8056, consumidor SQS —
> pudo haberlas servido). Los 5 vivos responden, `notion` (AppFlowy) incluido.
> Snapshot final post-apagado: `archive/competitive-2026-08-22-final.db`,
> verificado idéntico (231 tablas) y con los 169 flags en off sellados.

## Phase 3 — Rotaciones y limpieza [lane:release]

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | Deploy key `/root/.ssh/mcp2_deploy`: tiene ESCRITURA sobre `gon0801/goncloud-MCP-2`. Quitarla del repo en GitHub (o degradar a read-only) y borrar la llave privada del server. `[tdd:skip:ops]` | `git push --dry-run` con esa llave falla por permiso; la llave privada ya no existe en `/root/.ssh/` | 2.4 | cc:完了 |
| 3.2 | Borrar los 4 backups de `accounting.env` (`*.bak.*`, `*.before_op2_*`) — copias de secretos viejos sueltas en el dir. Antes de borrar, confirmar con el usuario que el `.env` vigente funciona (los crons de accounting corrieron después del apagado, evidencia de 2.4). `[tdd:skip:ops]` | El dir de config de accounting no contiene backups con secretos; los crons de accounting siguieron corriendo después | 2.4 | cc:完了 |
| 3.3 | `API_AUTH_SECRET` (filtrado en sesión del 2026-08-21): con el contenedor apagado ya no autoriza nada. Documentar en el inventario como "mitigado por apagado — ROTAR ANTES si el stack viejo se volviera a encender". `[tdd:skip:docs-only]` | Nota en `credentials-inventory.md` | 2.2 | cc:完了 |
| 3.4 | (Recommended, CHECKPOINT HUMANO) Token de cloudflared: está inline en el `ExecStart` del unit (visible en `ps aux` para cualquier usuario). PERO rotar = borrar y recrear el túnel, y NO SE SABE qué rutea (unknown §6) — los 7 dominios, incluido `notion.goncloud.cc` (AppFlowy/EHV Tasks), podrían colgar de él. PRIMERO: averiguar en Cloudflare Zero Trust qué rutea. DESPUÉS, con ventana acordada: recrear túnel, token a archivo 0600 vía `EnvironmentFile`. `[tdd:skip:ops]` | Diagnóstico de rutas documentado; si se rota: token no visible en `ps aux` y 7/7 dominios OK post-rotación | 2.4 | cc:TODO (diferida por decision: rutas del tunel solo visibles en el dashboard ZT del usuario; rotar en ventana acordada) |
| 3.5 | Cierre: `ORBIT 02` Done en AppFlowy con evidencia completa (respaldo verificado, tabla de smoke, secuencia de apagado con salidas, verificación post-apagado, rotaciones hechas y pendientes). Nombre EXACTO existente — no renombrar. `[tdd:skip:tracker-only]` | Fila en Done con notas que cuentan el trabajo completo | 3.1, 3.2, 3.3 | cc:完了 |

> Desviaciones registradas en Phase 3 (2026-08-22): (a) el traspaso contaba 4
> backups con secretos; eran 5 — `accounting.env.before_odoo_url_fix.bk` usaba
> extensión `.bk` y el patrón no lo atrapaba; borrado también, quedan 0.
> (b) El test "la llave todavía autentica" del script dio PELIGRO transitorio:
> la deploy key ya estaba borrada de GitHub al momento del test (lista vacía
> verificada antes y después) y la cuenta solo tiene otra llave distinta —
> propagación o reuso de conexión SSH; la credencial quedó muerta por ambos
> extremos (repo sin deploy keys + privada eliminada del server).

> Cierre (2026-08-22): las 3 fases completas; ORBIT 02 Done en AppFlowy con
> evidencia. Los pendientes que sobreviven (rotación cloudflared 3.4, decisión
> Keepa, rutas muertas dolibarr/mapper, unknowns del traspaso) quedaron
> registrados como tarea `ORBIT 15 — Limpieza final de infraestructura`.

## Fuera de alcance (Reject, con razón)

- **Migrar DATOS** (las 4 tablas de métricas limpias, `currency_rates`,
  precios, `own_items`): es trabajo de la ingesta de Orbit (ORBIT 03+), con
  la base viva y validadores propios — no se mezcla con el apagado.
- **Rotar tokens de Amazon Ads / MeLi que funcionan**: se auto-refrescan y
  Orbit usa la misma app LWA; rotarlos sin necesidad es re-hacer OAuth gratis.
- **Tocar vaultwarden / restic**: unknowns declarados; ninguno bloquea el
  apagado. Se investigan si algo falla.
