---
name: verify-orbit
description: Verifica Orbit (dashboard web Jinja2 de Amazon Ads) como lo toca un usuario — Resumen, Campanas, Decisiones, Salud y Cortes. Usala para probar UI, regresiones de pantallas o el camino real del dashboard, nunca el bind de produccion 10.13.13.1:8010.
---

# Verificar Orbit

Skill de control para el dashboard web de **Orbit**. El siguiente agente la lee en frio: lanza una instancia local, confirma que es la suya, conduce una pantalla como usuario, guarda evidencia y limpia solo lo que el arranco.

Produccion (`10.13.13.1:8010` / `127.0.0.1:8010` en goncloud) **no es el blanco**. Si el checkout no arranca, reporta el fallo y para: no parches producto.

Mantenimiento del mapa: `/maintain-verification-skill`.

## Entrevista (lo que hay en este repo)

- **Surface.** Dashboard HTML server-rendered (Jinja2). El usuario toca el `<nav>` de `app/templates/base.html`: Resumen `/`, Campanas `/campanas`, Decisiones `/decisiones`, Salud `/salud`, Cortes `/cortes`. El `<body>` lleva `data-pantalla`. Secundario: JSON en `/api/dashboard/*` (la UI lo consume; un camino) y CLI `python -m app.cli` (crons; no es la superficie de esta skill).
- **Run.** No hay `npm run dev`. El entorno Cursor deja Postgres 16 en `127.0.0.1:5432` con `orbit`/`orbit` via `.cursor/start.sh`. La app es `uvicorn app.main:app --host 127.0.0.1 --port <libre>`. Las pantallas HTML exigen `ORBIT_DSN_READ` (sin DSN → 503). `/health` no necesita DB. No hay seed de producto: esta skill crea una base desechable y siembra el fixture. Auth de lectura: ninguna (en prod el candado es VPN). Escritura (`POST /api/ads-optimizer/veto`) pide header `x-orbit-token`; **no la conduzcas** en el baseline de lectura.
- **Drive.** No hay Playwright/Cypress. El harness existente es curl (y TestClient en pytest). Receta: curl a las rutas HTML reales. Chrome headless solo para capturar el canvas de Chart.js.
- **Observe.** HTML (`data-pantalla`, `aria-current="page"`, h2, chips, celdas), JSON gemelo `/api/dashboard/...`, headers CSP/`no-store`, screenshot, log de uvicorn en `/tmp/orbit-verify/<run_id>/`.
- **Isolate.** Si: otra base `orbit_verify_<run_id>` + otro puerto en `127.0.0.1`. El cluster 5432 se comparte. Nunca 8010 ni `10.13.13.1`. Rehusa conducir una instancia que esta skill no lanzo.

## Launch

Desde la raiz del repo:

```bash
chmod +x .cursor/skills/verify-orbit/helpers/orbit-verify
.cursor/skills/verify-orbit/helpers/orbit-verify launch
```

Override: `ORBIT_VERIFY_RUN_ID=mi-run` y/o `--port 18011` (default 18010; 8010 esta prohibido).

Que hace, en orden:

1. Habla Postgres en `postgresql://orbit:orbit@127.0.0.1:5432/postgres`. Si no responde, corre `.cursor/start.sh` (idempotente) y reintenta.
2. `CREATE DATABASE orbit_verify_<run_id>` y aplica `migrations/0001`…`0005` (0001 no es re-runnable: por eso la base es nueva).
3. Siembra el fixture: campana `Campana A` (amazon_us, ENABLED), metrica D-15 (12.3400 / 45.6700 USD), goal de plataforma 25% (`goal_plataforma`), decision bid, corte `pending_veto` con search_term `zapato blanco`.
4. Arranca `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port <puerto>` con `ORBIT_DSN_READ` apuntando a esa base. No setea `ORBIT_PG_HOST` ni DSN de escritura.

**Listo cuando** `GET http://127.0.0.1:<puerto>/health` devuelve exactamente `{"status":"ok"}` y el helper imprime JSON con `base_url`, `pid`, `db`, `evidence_dir`. El mismo sello queda en `.cursor/skills/verify-orbit/evidence/<run_id>/launch-ready.txt`.

Scratch (se borra en cleanup): `/tmp/orbit-verify/<run_id>/state.json` y `uvicorn.log`.

**Teardown** (solo lo que este run arranco):

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup
```

## Doctor

Chequeo de solo lectura. Correrlo antes de conducir y otra vez si algo se ve raro.

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify doctor
```

Pasa solo si TODO esto es verdad:

- `base_url` es `http://127.0.0.1:<puerto>` y el puerto no es 8010 ni un host `10.13.13.1`.
- El `pid` del state vive y su `/proc/<pid>/cmdline` contiene `uvicorn` y `app.main:app`.
- `127.0.0.1:<puerto>` escucha y el inode del listener en `/proc/net/tcp` pertenece a ese pid.
- `GET /health` → `{"status":"ok"}`.
- `GET /` → 200, `data-pantalla="resumen"`, titulo Orbit, `Content-Security-Policy: default-src 'self'`, `Cache-Control` con `no-store`. Un 503 aqui significa DSN ausente o base muerta: no conduzcas.

El reporte se escribe en `.cursor/skills/verify-orbit/evidence/<run_id>/doctor.json`. Exit distinto de 0 = no conducir.

## Drive

Harness: **curl** (mismo transporte que `docs/DEPLOY.md` y que TestClient). Selectores reales de este repo, no ejemplos:

| Handle | Donde |
|---|---|
| `body[data-pantalla="resumen"\|"campanas"\|"decisiones"\|"salud"\|"cortes"]` | `app/templates/base.html` |
| `nav a[href="/"]`, `/campanas`, `/decisiones`, `/salud`, `/cortes` | mismo |
| `nav a[aria-current="page"]` | pagina activa |
| `h1` = `Orbit — Dashboard` | header |
| `h2` Resumen / Campanas / Decisiones / Salud / Cortes | cada template |
| `canvas#serie-amazon_us`, `#serie-amazon_mx`, `#serie-acos-amazon_us` | `resumen.html` |
| `script#datos-serie-amazon_us[type=application/json]` | datos de grafica |
| `button#btn-mas[data-cursor]` | `decisiones.html` (solo si hay mas paginas) |
| `button[data-vetar="<id>"]`, `form[data-veto="<id>"]` | `cortes.html` |
| `GET /api/dashboard/campanas` (y series/decisiones/salud/cortes) | JSON gemelo; no es endpoint de test |

Receta minima (tras doctor OK):

```bash
BASE=$(python3 -c "import json,pathlib; print(json.loads(pathlib.Path('/tmp/orbit-verify/' + sorted(__import__('os').listdir('/tmp/orbit-verify'))[-1] + '/state.json').read_text())['base_url'])")
curl -sS -D - "$BASE/" | tee /tmp/orbit-resumen.headers
curl -sS "$BASE/campanas" | tee /tmp/orbit-campanas.html
# El <a href="/campanas"> del nav es el camino de usuario. No saltes a un setter interno.
```

O la receta empaquetada de Campanas:

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas
```

No uses coordenadas ni tab order. No conduzcas `POST /api/ads-optimizer/veto` en el baseline (token + `ORBIT_DSN_ADMIN`; es escritura).

## Evidence

Directorio nombrado (sobrevive cleanup):

```
.cursor/skills/verify-orbit/evidence/<run_id>/
  launch-ready.txt
  doctor.json
  campanas/
    01-resumen.html          # estado ANTES (accion: partir de /)
    02-campanas.html         # estado DESPUES (GET /campanas)
    02-campanas.headers.txt
    03-campanas.json         # lado JSON del mismo camino
    04-campanas.png          # canvas/table visibles
    PROOF.json
```

Estandares de prueba:

- Camino de usuario real: nav HTML `/campanas`, no un fixture interno ni un endpoint solo-de-test.
- Captura accion + estado resultante (Resumen → Campanas), no solo la pantalla final.
- Efecto colateral: el JSON `/api/dashboard/campanas` debe listar la misma `Campana A` / `ENABLED` / `goal_plataforma` que el HTML.
- Mocks solo en el borde que el producto ya aisla (esta skill no habla con Amazon Ads; la semilla es el borde).
- No commitees secretos, `.env` ni datos de produccion. El fixture `Campana A` / `zapato blanco` no es dato vivo.

## Cleanup

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup --run-id <run_id>
```

Mata el **pid del state** (SIGTERM, luego SIGKILL). Nunca `pkill -f uvicorn`. DROP de la base `orbit_verify_<run_id>`. Borra `/tmp/orbit-verify/<run_id>/`. **No borra** `.cursor/skills/verify-orbit/evidence/<run_id>/`. Tras cleanup, confirma que ese directorio sigue ahi.

Si un intento falla a mitad, corre cleanup de ese run antes de relanzar.

## Helpers

Todos viven en `.cursor/skills/verify-orbit/helpers/orbit-verify` (ejecutable):

```bash
.cursor/skills/verify-orbit/helpers/orbit-verify launch [--run-id ID] [--port 18010]
.cursor/skills/verify-orbit/helpers/orbit-verify doctor [--run-id ID]
.cursor/skills/verify-orbit/helpers/orbit-verify drive-campanas [--run-id ID]
.cursor/skills/verify-orbit/helpers/orbit-verify cleanup [--run-id ID]
```

Sin `--run-id` usa `ORBIT_VERIFY_RUN_ID` o el state mas reciente en `/tmp/orbit-verify/`.
